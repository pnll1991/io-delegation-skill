#!/usr/bin/env python3
"""Live Codex profile/orchestration probe for the dedicated self-hosted runner.

Synthetic corpus only. The report contains model/usage/routing metadata, never
credentials or source bodies.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "io-delegation" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import context_orchestrator as compute
import context_telemetry as telemetry
from context_engine import SemanticEngine
from context_query import QueryEngine
from context_sources import SourceScope
import io_delegate
import model_policy


def write_json(path: Path, value):
    path.write_text(json.dumps(value, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")


def synthetic_file(index: int) -> str:
    values = [
        dict(SERVICE_LIMIT=12, RETRY_COUNT=2, TIMEOUT_MS=800, FEATURE_FAST=True,
             CACHE_TTL=60, BATCH_SIZE=16, MODE="safe"),
        dict(SERVICE_LIMIT=12, RETRY_COUNT=3, TIMEOUT_MS=800, FEATURE_FAST=True,
             CACHE_TTL=45, BATCH_SIZE=16, MODE="safe"),
        dict(SERVICE_LIMIT=18, RETRY_COUNT=2, TIMEOUT_MS=1200, FEATURE_FAST=False,
             CACHE_TTL=60, BATCH_SIZE=32, MODE="compat"),
    ][index]
    rows = [f'{key} = {json.dumps(value)}' for key, value in values.items()]
    # Keep source_bytes large enough to exercise the normal bulk-factual heuristic.
    rows.extend(
        f'PAD_{i:02d} = "synthetic benchmark padding {index}-{i:02d} '
        'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"'
        for i in range(36)
    )
    return "\n".join(rows) + "\n"


def semantic_args(paths):
    return {
        "selections": [
            {"path": path, "select": {"kind": "lines", "start": 1, "end": 43}}
            for path in paths
        ],
        "question": (
            "List SERVICE_LIMIT for each selected fragment and state whether all "
            "three values match. Use literal evidence only."
        ),
    }


def usage_row(raw):
    raw = raw if isinstance(raw, dict) else {}
    inp = raw.get("input_tokens")
    out = raw.get("output_tokens")
    return {
        "input_tokens": inp,
        "output_tokens": out,
        "cached_input_tokens": raw.get("cached_input_tokens"),
        "reasoning_output_tokens": raw.get("reasoning_output_tokens"),
        "raw_tokens": (inp + out if isinstance(inp, int) and isinstance(out, int) else None),
    }


def profile_probe(project, worker_path, paths, profile, temp_root):
    audit = temp_root / ("audit-profile-" + profile["id"])
    audit.mkdir()
    scope = SourceScope(project, files=paths)
    engine = SemanticEngine(scope, audit, worker_path, cache=False)
    started = time.monotonic()
    result, metrics = engine.run(
        semantic_args(paths),
        execution={"tier": "T1", "host": "codex", "profile": profile,
                   "max_output_tokens": 900},
    )
    elapsed_ms = round((time.monotonic() - started) * 1000)
    return {
        "profile": profile["id"],
        "model": profile["model"],
        "effort": profile.get("effort"),
        "status": result.get("status"),
        "accepted": compute.accepted_worker_result(result),
        "model_calls": metrics.get("model_calls", 0),
        "usage": usage_row(metrics.get("usage")),
        "wall_ms": elapsed_ms,
        "reason": result.get("reason"),
    }


def orchestrated_case(project, audit, worker_path, orchestrator_path, paths, case):
    scope = SourceScope(project, files=paths)
    engine = QueryEngine(
        scope, audit, worker_config=worker_path,
        orchestrator_config=orchestrator_path,
        cache=False, host="codex", model_preset="balanced",
    )
    selections = [
        {"path": path, "select": {"kind": "lines", "start": 1, "end": 43}}
        for path in paths
    ]
    started = time.monotonic()
    result, metrics = engine.run({
        "selections": selections,
        "question": case["question"],
        "operation": "factual",
    })
    elapsed_ms = round((time.monotonic() - started) * 1000)
    orch = result.get("orchestration", {}) if isinstance(result, dict) else {}
    journal = audit / ".io-delegation" / "worker-events.jsonl"
    worker = telemetry.workers(journal)
    return {
        "id": case["id"],
        "status": result.get("status"),
        "route": result.get("route"),
        "decision": orch.get("decision"),
        "reason": orch.get("reason"),
        "preset": orch.get("preset"),
        "demand": orch.get("demand"),
        "model_tier": orch.get("model_tier"),
        "initial_profile": orch.get("initial_profile"),
        "final_profile": orch.get("final_profile"),
        "attempts": orch.get("attempts", []),
        "scores": orch.get("scores"),
        "jev_usage": usage_row(orch.get("usage")),
        "worker": {
            "calls": worker.get("calls"),
            "accepted": worker.get("accepted"),
            "failures": worker.get("failures"),
            "accounting_complete": worker.get("accounting_complete"),
            "raw_tokens": worker.get("raw_tokens"),
            "usage": worker.get("usage"),
        },
        "model_calls": metrics.get("model_calls"),
        "escalated": metrics.get("escalated"),
        "wall_ms": elapsed_ms,
    }


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args(argv)

    report = {
        "schema": "io-codex-live-ab/v1",
        "git_sha": os.environ.get("GITHUB_SHA"),
        "runner_name": os.environ.get("RUNNER_NAME"),
        "codex_executable": bool(shutil.which("codex")),
        "typesafe_key_available": bool(os.environ.get("TYPESAFE_API_KEY")),
        "profile_probes": [],
        "orchestration_cases": [],
        "errors": [],
    }

    base = Path(os.environ.get("RUNNER_TEMP") or tempfile.gettempdir())
    with tempfile.TemporaryDirectory(prefix="io-codex-live-ab-", dir=base) as tmp:
        temp_root = Path(tmp)
        project = temp_root / "project"
        project.mkdir()
        paths = ["a.py", "b.py", "c.py"]
        for i, name in enumerate(paths):
            (project / name).write_text(synthetic_file(i), encoding="utf-8")

        worker_path = temp_root / "worker.json"
        worker_cfg = {
            "approved": True,
            "adapter": "host-cli",
            "timeout_seconds": 120,
            "max_calls_per_workspace": 4,
            "model_policy": {"mode": "auto", "preset": "balanced"},
            "context_limits": {
                "max_selected_bytes": 24000,
                "max_request_bytes": 40000,
                "max_output_tokens": 1200,
                "max_task_tokens": 80000,
                "max_calls": 4,
            },
        }
        write_json(worker_path, worker_cfg)
        validated_worker = io_delegate.load_config(str(worker_path))
        policy = model_policy.resolve(validated_worker, "codex", "balanced")
        report["effective_policy"] = {
            "preset": policy["preset"],
            "order": policy["order"],
            "max_profile": policy["max_profile"],
        }

        for pid in policy["order"]:
            try:
                report["profile_probes"].append(
                    profile_probe(
                        project, worker_path, paths,
                        dict(policy["table"][pid]), temp_root,
                    )
                )
            except Exception as exc:
                report["profile_probes"].append({
                    "profile": pid,
                    "status": "exception",
                    "accepted": False,
                    "error": type(exc).__name__,
                })

        if report["typesafe_key_available"]:
            orchestrator_path = temp_root / "orchestrator.json"
            write_json(orchestrator_path, {
                "version": 1,
                "approved": True,
                "provider": "typesafe",
                "model": "jev-latest",
                "api_key_env": "TYPESAFE_API_KEY",
                "timeout_seconds": 10,
                "min_confidence": 0.75,
                "compute_policy": {
                    "cheap_sufficient_min": 0.78,
                    "risk_high_max": 0.30,
                    "uncertainty_high_max": 0.35,
                    "reasoning_required_max": 0.40,
                    "max_cheap_output_tokens": 900,
                },
            })
            cases = [
                {
                    "id": "easy",
                    "question": (
                        "List SERVICE_LIMIT for each selected module and say whether "
                        "the literal values match. Do not infer causes."
                    ),
                },
                {
                    "id": "medium",
                    "question": (
                        "Build a factual comparison of SERVICE_LIMIT, RETRY_COUNT, "
                        "TIMEOUT_MS and FEATURE_FAST across all selected modules. "
                        "Identify only literal mismatches; do not infer causes."
                    ),
                },
                {
                    "id": "hard-factual",
                    "question": (
                        "Produce a complete cross-file compatibility matrix for "
                        "SERVICE_LIMIT, RETRY_COUNT, TIMEOUT_MS, FEATURE_FAST, CACHE_TTL, "
                        "BATCH_SIZE and MODE. Identify every literal mismatch and exact "
                        "match, and mark unsupported claims explicitly. Do not diagnose "
                        "or recommend changes."
                    ),
                },
            ]
            for case in cases:
                audit = temp_root / ("audit-orchestrated-" + case["id"])
                audit.mkdir()
                try:
                    report["orchestration_cases"].append(
                        orchestrated_case(
                            project, audit, worker_path, orchestrator_path, paths, case
                        )
                    )
                except Exception as exc:
                    report["orchestration_cases"].append({
                        "id": case["id"],
                        "status": "exception",
                        "error": type(exc).__name__,
                    })
        else:
            report["orchestration_skipped"] = "TYPESAFE_API_KEY_not_available"

    probes = report["profile_probes"]
    report["summary"] = {
        "profiles_attempted": len(probes),
        "profiles_accepted": sum(x.get("accepted") is True for x in probes),
        "all_profiles_accepted": bool(probes) and all(x.get("accepted") is True for x in probes),
        "orchestration_cases": len(report["orchestration_cases"]),
    }

    a.output.parent.mkdir(parents=True, exist_ok=True)
    write_json(a.output, report)
    print(json.dumps(report["summary"], separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
