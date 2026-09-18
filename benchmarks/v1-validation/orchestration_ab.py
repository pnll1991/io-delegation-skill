#!/usr/bin/env python3
"""Paired A/B evaluator for Jev host-aware model orchestration.

Input is measured run metadata only. It does not execute models. Each pair compares
the same task on the same commit with orchestration disabled vs enabled.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import statistics

SCHEMA = "io-orchestration-ab/v1"
MIN_RELEASE_PAIRS = 20
MIN_MEDIAN_SAVING = 0.10


class EvidenceError(ValueError):
    pass


def _nonnegative_int(value, name):
    if type(value) is not int or value < 0:
        raise EvidenceError(f"{name} must be a non-negative integer")
    return value


def _arm(row, name):
    if not isinstance(row, dict):
        raise EvidenceError(f"{name} arm must be an object")
    quality = row.get("quality_pass")
    if type(quality) is not bool:
        raise EvidenceError(f"{name}.quality_pass must be boolean")
    complete = row.get("accounting_complete")
    if type(complete) is not bool:
        raise EvidenceError(f"{name}.accounting_complete must be boolean")
    principal = row.get("principal_tokens")
    worker = row.get("worker_tokens", 0)
    control = row.get("control_tokens", 0)
    if complete:
        principal = _nonnegative_int(principal, f"{name}.principal_tokens")
        worker = _nonnegative_int(worker, f"{name}.worker_tokens")
        control = _nonnegative_int(control, f"{name}.control_tokens")
        total = principal + worker + control
    else:
        total = None
    wall = row.get("wall_ms")
    if wall is not None and (type(wall) not in (int, float) or not math.isfinite(wall) or wall < 0):
        raise EvidenceError(f"{name}.wall_ms must be non-negative")
    return {
        "quality_pass": quality,
        "accounting_complete": complete,
        "principal_tokens": principal,
        "worker_tokens": worker,
        "control_tokens": control,
        "system_tokens": total,
        "wall_ms": wall,
    }


def validate(data):
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise EvidenceError(f"Expected schema {SCHEMA}")
    pairs = data.get("pairs")
    if not isinstance(pairs, list) or not pairs:
        raise EvidenceError("pairs must be a non-empty list")
    seen = set()
    out = []
    for item in pairs:
        if not isinstance(item, dict):
            raise EvidenceError("pair must be an object")
        pid = item.get("id")
        if not isinstance(pid, str) or not pid or pid in seen:
            raise EvidenceError("pair ids must be unique non-empty strings")
        seen.add(pid)
        baseline = _arm(item.get("baseline"), "baseline")
        orchestrated = _arm(item.get("orchestrated"), "orchestrated")
        tier = item.get("tier")
        if tier not in ("T0", "T1", "T2"):
            raise EvidenceError("tier must be T0, T1 or T2")
        escalated = item.get("escalated", False)
        if type(escalated) is not bool:
            raise EvidenceError("escalated must be boolean")
        initial_profile = item.get("initial_profile")
        final_profile = item.get("final_profile")
        for name,value in (("initial_profile",initial_profile),("final_profile",final_profile)):
            if value is not None and (not isinstance(value,str) or not value):
                raise EvidenceError(f"{name} must be null or a non-empty string")
        attempts = item.get("model_attempts", 0)
        if type(attempts) is not int or attempts < 0:
            raise EvidenceError("model_attempts must be a non-negative integer")
        out.append({
            "id": pid,
            "family": item.get("family") if isinstance(item.get("family"), str) else "unknown",
            "baseline": baseline,
            "orchestrated": orchestrated,
            "tier": tier,
            "escalated": escalated,
            "initial_profile": initial_profile,
            "final_profile": final_profile,
            "model_attempts": attempts,
        })
    return out


def percent_delta(new, old):
    if old == 0:
        return None
    return (new - old) / old


def evaluate(data):
    pairs = validate(data)
    quality_regressions = [
        p["id"] for p in pairs
        if p["baseline"]["quality_pass"] and not p["orchestrated"]["quality_pass"]
    ]
    quality_gains = [
        p["id"] for p in pairs
        if not p["baseline"]["quality_pass"] and p["orchestrated"]["quality_pass"]
    ]
    comparable = [
        p for p in pairs
        if p["baseline"]["quality_pass"]
        and p["orchestrated"]["quality_pass"]
        and p["baseline"]["accounting_complete"]
        and p["orchestrated"]["accounting_complete"]
    ]
    token_deltas = [
        percent_delta(p["orchestrated"]["system_tokens"], p["baseline"]["system_tokens"])
        for p in comparable
    ]
    token_deltas = [x for x in token_deltas if x is not None]
    wall_deltas = [
        percent_delta(p["orchestrated"]["wall_ms"], p["baseline"]["wall_ms"])
        for p in comparable
        if p["baseline"]["wall_ms"] is not None and p["orchestrated"]["wall_ms"] is not None
    ]
    wall_deltas = [x for x in wall_deltas if x is not None]
    incomplete = [
        p["id"] for p in pairs
        if not p["baseline"]["accounting_complete"] or not p["orchestrated"]["accounting_complete"]
    ]
    tiers = {tier: sum(p["tier"] == tier for p in pairs) for tier in ("T0", "T1", "T2")}
    escalations = sum(p["escalated"] for p in pairs)
    profile_counts = {}
    for p in pairs:
        profile = p.get("final_profile")
        if profile:
            profile_counts[profile] = profile_counts.get(profile, 0) + 1
    model_attempts = sum(p.get("model_attempts", 0) for p in pairs)
    t1 = [p for p in pairs if p["tier"] == "T1"]
    release_ready = (
        len(comparable) >= MIN_RELEASE_PAIRS
        and not quality_regressions
        and not incomplete
        and bool(token_deltas)
        and statistics.median(token_deltas) <= -MIN_MEDIAN_SAVING
        and len(t1) > 0
    )
    return {
        "schema": SCHEMA,
        "pairs": len(pairs),
        "comparable_quality_pairs": len(comparable),
        "quality_regressions": quality_regressions,
        "quality_gains": quality_gains,
        "accounting_incomplete": incomplete,
        "tiers": tiers,
        "escalations": escalations,
        "escalation_rate": escalations / len(pairs),
        "final_profiles": profile_counts,
        "model_attempts": model_attempts,
        "t1_rate": len(t1) / len(pairs),
        "median_system_token_delta": statistics.median(token_deltas) if token_deltas else None,
        "median_wall_delta": statistics.median(wall_deltas) if wall_deltas else None,
        "release_gate": {
            "minimum_comparable_pairs": MIN_RELEASE_PAIRS,
            "minimum_median_saving": MIN_MEDIAN_SAVING,
            "pass": release_ready,
        },
    }


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--output", type=Path)
    a = p.parse_args(argv)
    data = json.loads(a.input.read_text(encoding="utf-8-sig"))
    result = evaluate(data)
    raw = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if a.output:
        a.output.write_text(raw, encoding="utf-8")
    print(raw, end="")
    return 0 if result["release_gate"]["pass"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
