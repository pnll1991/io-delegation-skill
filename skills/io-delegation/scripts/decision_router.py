#!/usr/bin/env python3
"""Optional TypeSafe Jev router for I/O Delegation.

Sends task text plus corpus metadata only; source file contents are never included.
Python 3.10+, standard library only.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import math
import os
from pathlib import Path
import sys
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

API_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
ROUTES = ("deterministic", "targeted_read", "bulk_read", "principal")
MAX_FILES = 64
MAX_TASK_BYTES = 16_000
MAX_CONFIG_BYTES = 32_000
MAX_RESPONSE_BYTES = 200_000
BLOCKED_PARTS = {'.git', '.ssh', '.aws', '.azure', '.gnupg', 'node_modules',
                 '.venv', 'venv', '__pycache__', '.io-delegation'}
BLOCKED_NAMES = ('.env*', '*.pem', '*.key', '*.p12', '*.pfx', 'id_rsa*',
                 'id_ed25519*', '.npmrc', '.pypirc', 'credentials*',
                 'secrets.*', '*.local.json', '*.local.yaml', '*.local.yml')
QUESTIONS = {
    "route": {
        "type": "choice",
        "instructions": (
            "Choose one I/O route: principal for debugging/security/architecture/editing; "
            "deterministic only when a non-source tool fully answers; targeted_read for one "
            "or a few localized fragments; bulk_read for factual inventory/comparison across "
            "3+ selected files or many matches. Search that only locates source is not deterministic."
        ),
        "criteria": {
            "deterministic": "A test, count, parser, compiler, metadata check, or exact tool result fully answers without interpreting source bodies.",
            "targeted_read": "Source evidence is needed but already localized to one file or a few known symbols/fragments; a few bounded reads suffice.",
            "bulk_read": "Factual extraction, inventory, or comparison spans 3+ selected files, many matches, or a large corpus and can return compact evidence.",
            "principal": "Correctness depends on debugging, architecture, security judgment, causal reasoning, critical logic, or exact modification."
        }
    },
    "delegation_useful": {
        "type": "noul",
        "instructions": "Would bulk-read materially reduce main-agent context for factual extraction across several files, many matches, or a large corpus?",
        "criteria": {
            "true": "Several files or many matches can be summarized as compact factual evidence without making the final reasoning-sensitive decision.",
            "false": "The task is deterministic, localized to a few fragments, or requires main-agent reasoning or exact modification."
        }
    },
    "reasoning_required": {
        "type": "noul",
        "instructions": "Does correctness primarily require debugging, architecture, security judgment, causal reasoning, or exact editing?",
        "criteria": {
            "true": "The requested result is a diagnosis, design/security decision, causal conclusion, or exact modification.",
            "false": "The work is deterministic inspection, localization, or factual extraction, including delegable cross-file comparison."
        }
    }
}


class RouterError(Exception):
    """Controlled error that never includes task text, source contents, or credentials."""


def encode(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _number(value: Any) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
        raise RouterError("TypeSafe returned an invalid probability or confidence value.")
    return float(value)


def load_config(path: str) -> dict[str, Any]:
    raw = Path(path).read_bytes()
    if len(raw) > MAX_CONFIG_BYTES:
        raise RouterError("Router configuration is too large.")
    try:
        cfg = json.loads(raw)
    except json.JSONDecodeError:
        raise RouterError("Router configuration is not valid JSON.") from None
    if not isinstance(cfg, dict) or cfg.get("version") != 1:
        raise RouterError("Router configuration must use version 1.")
    if cfg.get("approved") is not True:
        raise RouterError("The TypeSafe router must be explicitly approved in its configuration.")
    if cfg.get("provider", "typesafe") != "typesafe":
        raise RouterError("Only the typesafe provider is supported by this router.")
    model = cfg.get("model", MODEL)
    if not isinstance(model, str) or not model.strip():
        raise RouterError("Configure a valid TypeSafe model name.")
    key_env = cfg.get("api_key_env", "TYPESAFE_API_KEY")
    if not isinstance(key_env, str) or not key_env:
        raise RouterError("api_key_env must name an environment variable.")
    timeout = cfg.get("timeout_seconds", 10)
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 60:
        raise RouterError("timeout_seconds must be greater than 0 and at most 60.")
    confidence = cfg.get("min_confidence", 0.75)
    if type(confidence) not in (int, float) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise RouterError("min_confidence must be between 0 and 1.")
    cfg = {**cfg, "model": model, "api_key_env": key_env,
           "timeout_seconds": float(timeout), "min_confidence": float(confidence)}
    endpoint(cfg)
    return cfg


def endpoint(cfg: dict[str, Any]) -> str:
    url = cfg.get("url", API_URL)
    if not isinstance(url, str):
        raise RouterError("Configure a valid TypeSafe endpoint URL.")
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise RouterError("Router endpoint must be an HTTP(S) URL.")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise RouterError("Router endpoint cannot contain credentials, query, or fragments.")
    host = parts.hostname.lower()
    official = (parts.scheme == "https" and host == "api.typesafe.ai"
                and parts.path.rstrip("/") == "/v1/systemone")
    loopback = host in {"127.0.0.1", "localhost", "::1"}
    if not official and not loopback:
        raise RouterError("Custom remote router endpoints are not allowed.")
    return url


def _safe_file(root: Path, name: str) -> tuple[int, str]:
    candidate = Path(name)
    if candidate.is_absolute() or not name or ".." in candidate.parts:
        raise RouterError("Use explicit project-relative file paths.")
    path = (root / candidate).resolve()
    try:
        rel = path.relative_to(root)
    except ValueError:
        raise RouterError("A selected file resolves outside the project root.") from None
    lowered_parts = [part.lower() for part in rel.parts]
    if any(part in BLOCKED_PARTS for part in lowered_parts):
        raise RouterError("A selected file is inside an excluded directory.")
    if any(fnmatch.fnmatchcase(part, pattern)
           for part in lowered_parts for pattern in BLOCKED_NAMES):
        raise RouterError("A selected file matches a sensitive-file pattern.")
    if not path.is_file():
        raise RouterError("Every selected path must be a regular file.")
    return path.stat().st_size, path.suffix.lower() or "<none>"


def build_state(root: Path, task: str, paths: list[str], *, operation: str = "unknown",
                search_results: int | None = None, known_symbols: int | None = None) -> dict[str, Any]:
    if not task.strip() or len(task.encode("utf-8")) > MAX_TASK_BYTES:
        raise RouterError("Task must contain between 1 and 16000 UTF-8 bytes.")
    if not paths or len(paths) > MAX_FILES:
        raise RouterError(f"Select between 1 and {MAX_FILES} explicit files.")
    rows = [_safe_file(root, name) for name in paths]
    sizes = [size for size, _ in rows]
    extensions: dict[str, int] = {}
    for _, ext in rows:
        extensions[ext] = extensions.get(ext, 0) + 1
    for name, value in (("search_results", search_results), ("known_symbols", known_symbols)):
        if value is not None and (type(value) is not int or value < 0):
            raise RouterError(f"{name} must be a non-negative integer.")
    return {
        "task": task,
        "operation_hint": operation,
        "corpus": {
            "file_count": len(rows),
            "total_bytes": sum(sizes),
            "largest_file_bytes": max(sizes),
            "files_over_64kb": sum(size > 64_000 for size in sizes),
            "extensions": extensions,
        },
        "local_signals": {
            "search_results": search_results,
            "known_symbols": known_symbols,
        },
        "privacy": "Source contents and file names are not included; only task text and aggregate file metadata are sent."
    }


def build_payload(state: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    return {"state": state, "model": cfg["model"], "questions": QUESTIONS}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RouterError("HTTP redirect blocked; review the TypeSafe endpoint.")


def call_typesafe(payload: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    key = os.environ.get(cfg["api_key_env"])
    if not key:
        raise RouterError("The configured TypeSafe API key environment variable is missing.")
    request = urllib.request.Request(endpoint(cfg), data=encode(payload), method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "Accept": "application/json", "User-Agent": "io-delegation/decision-router"})
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(request, timeout=cfg["timeout_seconds"]) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise RouterError(f"TypeSafe request failed with HTTP {exc.code}.") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RouterError("TypeSafe request failed before a valid response was received.") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise RouterError("TypeSafe response exceeded the configured size limit.")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        raise RouterError("TypeSafe returned invalid JSON.") from None
    return parse_response(value, cfg)


def parse_response(value: Any, cfg: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or not isinstance(value.get("answers"), dict):
        raise RouterError("TypeSafe response is missing answers.")
    answers = value["answers"]
    route = answers.get("route")
    delegation = answers.get("delegation_useful")
    reasoning = answers.get("reasoning_required")
    if not isinstance(route, dict) or route.get("type") != "choice":
        raise RouterError("TypeSafe route answer has an invalid shape.")
    choice = route.get("choice")
    if choice not in ROUTES:
        raise RouterError("TypeSafe returned an unknown route.")
    probabilities = route.get("probabilities")
    if not isinstance(probabilities, dict) or set(probabilities) != set(ROUTES):
        raise RouterError("TypeSafe route probabilities have an invalid shape.")
    parsed_probabilities = {name: _number(probabilities[name]) for name in ROUTES}
    if abs(sum(parsed_probabilities.values()) - 1.0) > 0.02:
        raise RouterError("TypeSafe route probabilities do not sum to 1.")
    confidence = _number(route.get("confidence"))
    if not isinstance(delegation, dict) or delegation.get("type") != "noul":
        raise RouterError("TypeSafe delegation answer has an invalid shape.")
    if not isinstance(reasoning, dict) or reasoning.get("type") != "noul":
        raise RouterError("TypeSafe reasoning answer has an invalid shape.")
    delegation_p = _number(delegation.get("noul"))
    reasoning_p = _number(reasoning.get("noul"))
    effective = choice if confidence >= cfg["min_confidence"] else "current_rules"
    usage = value.get("usage") if isinstance(value.get("usage"), dict) else {}
    def usage_int(name: str) -> int | None:
        item = usage.get(name)
        return item if type(item) is int and item >= 0 else None
    return {
        "status": "ok",
        "model": value.get("model") if isinstance(value.get("model"), str) else cfg["model"],
        "route": effective,
        "model_route": choice,
        "confidence": confidence,
        "probabilities": parsed_probabilities,
        "delegation_useful": delegation_p,
        "reasoning_required": reasoning_p,
        "min_confidence": cfg["min_confidence"],
        "usage": {"input_tokens": usage_int("input_tokens"),
                  "output_tokens": usage_int("output_tokens")},
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--paths", nargs="+", required=True)
    parser.add_argument("--operation", default="unknown",
                        choices=["unknown", "exploration", "factual", "generation", "debugging", "architecture", "security", "editing"])
    parser.add_argument("--search-results", type=int)
    parser.add_argument("--known-symbols", type=int)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        root = Path(args.root).resolve(strict=True)
        if not root.is_dir():
            raise RouterError("Project root must be a directory.")
        cfg = load_config(args.config)
        state = build_state(root, args.task, args.paths, operation=args.operation,
                            search_results=args.search_results, known_symbols=args.known_symbols)
        payload = build_payload(state, cfg)
        if args.dry_run:
            output = {"status": "dry_run", "state": state, "model": cfg["model"],
                      "question_ids": list(QUESTIONS)}
        else:
            output = call_typesafe(payload, cfg)
        print(json.dumps(output, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
        return 0
    except (OSError, RouterError, ValueError, TypeError, KeyError, OverflowError) as exc:
        print(json.dumps({"component": "io-delegation-decision-router",
                          "status": "error", "error": str(exc)}, ensure_ascii=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
