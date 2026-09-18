#!/usr/bin/env python3
"""Cross-agent Jev compaction bridge for Codex and Cursor.

The bridge records prompts and tool I/O in a machine-local journal. At the
host's pre-compaction event it asks TypeSafe Jev which old tool calls/results
must survive the host's native compaction. Tool result bodies are never sent to
TypeSafe; only their size/error metadata is included in Jev state.

Codex rehydrates the verbatim snapshot through SessionStart(source=compact).
Cursor cannot replace or modify preCompact, so it injects the snapshot on the
first postToolUse after compaction, or uses one bounded stop follow-up when no
tool call occurs after compaction.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Callable
import urllib.error
import urllib.parse
import urllib.request

API_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
DATA_SCOPE = "conversation_text_and_tool_inputs"
MAX_RESPONSE_BYTES = 200_000
MAX_INPUT_EVENT_BYTES = 8_000_000
DEFAULT_MAX_REHYDRATE_CHARS = 24_000


class CompactionError(Exception):
    """Controlled failure that must not contain credentials or full tool output."""


def encode(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.chmod(0o700)
    except OSError:
        pass


def atomic_write(path: Path, value: Any) -> None:
    _private_dir(path.parent)
    data = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    fd, tmp = tempfile.mkstemp(prefix=".io-compaction-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


@contextmanager
def journal_lock(path: Path):
    lock = path.with_name(path.name + ".lock")
    _private_dir(lock.parent)
    acquired = False
    for _ in range(100):
        try:
            lock.mkdir(mode=0o700)
            acquired = True
            break
        except FileExistsError:
            try:
                age = time.time() - lock.stat().st_mtime
                if age > 30:
                    lock.rmdir()
                    continue
            except (FileNotFoundError, OSError):
                pass
            time.sleep(0.02)
    if not acquired:
        raise CompactionError("Timed out waiting for the session compaction journal.")
    try:
        yield
    finally:
        try:
            lock.rmdir()
        except OSError:
            pass


def read_json(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def estimate_tokens(value: Any) -> int:
    # Conservative enough for Jev request budgeting without adding a tokenizer.
    return max(1, math.ceil(len(encode(value)) / 3))


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        return str(value)


def _clean_session_id(value: Any) -> str:
    raw = value if isinstance(value, str) and value else "unknown-session"
    safe = "".join(ch for ch in raw if ch.isalnum() or ch in "-_.")
    if 1 <= len(safe) <= 100:
        return safe
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:32]


def _policy_candidates(event: dict[str, Any]) -> list[Path]:
    rows: list[Path] = []
    cwd = event.get("cwd")
    if isinstance(cwd, str) and cwd:
        rows.append(Path(cwd).expanduser())
    roots = event.get("workspace_roots")
    if isinstance(roots, list):
        for item in roots:
            if isinstance(item, str) and item:
                rows.append(Path(item).expanduser())
    return rows


def find_project(event: dict[str, Any], host: str) -> tuple[Path, dict[str, Any], str] | None:
    seen: set[Path] = set()
    for candidate in _policy_candidates(event):
        try:
            base = candidate.resolve(strict=True)
        except OSError:
            continue
        if base.is_file():
            base = base.parent
        for root in (base, *base.parents):
            if root in seen:
                continue
            seen.add(root)
            policy_path = root / ".io-delegation" / "compaction.json"
            marker_path = root / ".io-delegation" / "project.json"
            if not policy_path.is_file() or not marker_path.is_file():
                continue
            policy = read_json(policy_path, {})
            marker = read_json(marker_path, {})
            if not isinstance(policy, dict) or not isinstance(marker, dict):
                continue
            if (
                policy.get("version") != 1
                or policy.get("enabled") is not True
                or policy.get("provider") != "typesafe"
                or policy.get("approved_data_scope") != DATA_SCOPE
            ):
                return None
            hosts = policy.get("hosts")
            if isinstance(hosts, list) and host not in hosts:
                return None
            project_id = marker.get("project_id")
            if not isinstance(project_id, str) or not project_id:
                return None
            return root, policy, project_id
    return None


def journal_path(project_id: str, host: str, session_id: str) -> Path:
    home = Path(
        os.environ.get("IO_DELEGATION_HOME", str(Path.home() / ".io-delegation"))
    ).expanduser()
    return home / "compaction" / project_id / host / f"{_clean_session_id(session_id)}.json"


def prune_stale_journals(path: Path, max_age_seconds: int = 86_400) -> None:
    folder = path.parent
    if not folder.is_dir():
        return
    now = time.time()
    for candidate in folder.glob("*.json"):
        if candidate == path:
            continue
        try:
            if now - candidate.stat().st_mtime > max_age_seconds:
                candidate.unlink()
        except OSError:
            pass


def new_journal(session_id: str) -> dict[str, Any]:
    return {
        "version": 1,
        "session_id": session_id,
        "seq": 0,
        "prompts": [],
        "calls": [],
        "pending": None,
        "last_compaction": None,
    }


def load_journal(path: Path, session_id: str) -> dict[str, Any]:
    value = read_json(path, None)
    if not isinstance(value, dict) or value.get("version") != 1:
        return new_journal(session_id)
    value.setdefault("seq", 0)
    value.setdefault("prompts", [])
    value.setdefault("calls", [])
    value.setdefault("pending", None)
    return value


def next_seq(journal: dict[str, Any]) -> int:
    seq = int(journal.get("seq", 0)) + 1
    journal["seq"] = seq
    return seq


def record_prompt(journal: dict[str, Any], prompt: Any) -> None:
    text = _text(prompt)
    if not text:
        return
    journal["prompts"].append({"seq": next_seq(journal), "text": text})
    journal["prompts"] = journal["prompts"][-40:]


def _call_id(event: dict[str, Any], journal: dict[str, Any]) -> str:
    value = event.get("tool_use_id") or event.get("tool_call_id")
    if isinstance(value, str) and value:
        return value
    seed = f"{journal.get('session_id')}:{journal.get('seq')}:{event.get('tool_name')}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


def record_pre_tool(journal: dict[str, Any], event: dict[str, Any]) -> None:
    call_id = _call_id(event, journal)
    calls = journal["calls"]
    if any(row.get("tool_use_id") == call_id for row in calls):
        return
    seq = next_seq(journal)
    calls.append(
        {
            "id": f"t{len(calls) + 1}",
            "seq": seq,
            "tool_use_id": call_id,
            "tool": _text(event.get("tool_name") or "tool"),
            "input": event.get("tool_input", {}),
            "result": None,
            "is_error": False,
        }
    )


def record_post_tool(
    journal: dict[str, Any], event: dict[str, Any], *, failed: bool = False
) -> None:
    call_id = _call_id(event, journal)
    row = next(
        (x for x in reversed(journal["calls"]) if x.get("tool_use_id") == call_id),
        None,
    )
    if row is None:
        record_pre_tool(journal, event)
        row = journal["calls"][-1]
    next_seq(journal)
    if failed:
        result = event.get("error_message") or event.get("tool_output") or "tool failed"
    else:
        result = event.get("tool_output")
    row["result"] = _text(result)
    row["is_error"] = bool(failed)


def _goal(journal: dict[str, Any]) -> str:
    prompts = [
        row.get("text", "")
        for row in journal.get("prompts", [])[-3:]
        if isinstance(row, dict)
    ]
    return "\n\n".join(x for x in prompts if x)


def _history_entries(
    journal: dict[str, Any], input_chars: int
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for prompt in journal.get("prompts", []):
        if isinstance(prompt, dict) and prompt.get("text"):
            entries.append(
                {
                    "seq": int(prompt.get("seq", 0)),
                    "role": "user",
                    "text": str(prompt["text"]),
                }
            )
    for call in journal.get("calls", []):
        if not isinstance(call, dict):
            continue
        input_text = _text(call.get("input"))
        if len(input_text) > input_chars:
            input_text = input_text[:input_chars] + f" [… {len(input_text) - input_chars} chars omitted …]"
        result = call.get("result")
        result_chars = len(result) if isinstance(result, str) else 0
        status = "error" if call.get("is_error") else "ok"
        entries.append(
            {
                "seq": int(call.get("seq", 0)),
                "role": "tool",
                "id": call.get("id"),
                "tool": call.get("tool"),
                "input": input_text,
                "result": f"{status}, {result_chars} chars (body omitted)",
            }
        )
    entries.sort(key=lambda row: int(row.get("seq", 0)))
    return entries


def build_state(
    journal: dict[str, Any], policy: dict[str, Any]
) -> tuple[dict[str, Any], int]:
    max_tokens = int(policy.get("max_state_tokens", 25_000))
    base = {
        "context": (
            "Cross-agent context compaction. Preserve exact tool evidence needed after the "
            "host's native compaction. Tool result bodies are not present in this state."
        ),
        "goal": _goal(journal),
    }
    for limit in (1000, 200, 60):
        history = _history_entries(journal, limit)
        state = {**base, "history": history}
        tokens = estimate_tokens(state)
        if tokens <= max_tokens:
            return state, tokens
    history = _history_entries(journal, 60)
    # Prefer dropping old prompt-only entries. Tool metadata stays visible to Jev.
    history = [row for row in history if row.get("role") == "tool"] + [
        row for row in history[-8:] if row.get("role") != "tool"
    ]
    history.sort(key=lambda row: int(row.get("seq", 0)))
    state = {**base, "history": history}
    tokens = estimate_tokens(state)
    if tokens > max_tokens:
        raise CompactionError("Jev compaction state exceeds the configured token budget.")
    return state, tokens


def _question_pair(call: dict[str, Any]) -> dict[str, Any]:
    label = str(call["id"])
    return {
        f"{label}_call": {
            "type": "noul",
            "instructions": (
                "After the coding agent compacts its conversation, does knowing that this "
                "tool call happened, including its exact input, still materially matter?"
            ),
            "criteria": {
                "true": "The call, target, command, path, or exact input remains relevant to the active task.",
                "false": "The call is stale, superseded, exploratory, or safe to forget.",
            },
        },
        f"{label}_result": {
            "type": "noul",
            "instructions": (
                "Should this tool result be re-injected verbatim after host compaction because "
                "its exact contents may still be needed and re-running the tool is not equivalent?"
            ),
            "criteria": {
                "true": "Exact output, error text, identifiers, paths, values, or evidence may be needed later.",
                "false": "Only the fact that the call happened matters, or the result is stale/re-runnable.",
            },
        },
    }


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CompactionError("TypeSafe redirect blocked.")


def _endpoint(policy: dict[str, Any]) -> str:
    url = policy.get("url", API_URL)
    if not isinstance(url, str):
        raise CompactionError("Invalid TypeSafe endpoint.")
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    official = (
        parts.scheme == "https"
        and host == "api.typesafe.ai"
        and parts.path.rstrip("/") == "/v1/systemone"
    )
    loopback = host in {"127.0.0.1", "localhost", "::1"} and parts.scheme in {"http", "https"}
    if parts.username or parts.password or parts.query or parts.fragment or not (official or loopback):
        raise CompactionError("TypeSafe endpoint is not allowed.")
    return url


def call_typesafe(
    state: dict[str, Any], questions: dict[str, Any], policy: dict[str, Any]
) -> dict[str, Any]:
    env_name = policy.get("api_key_env", "TYPESAFE_API_KEY")
    if not isinstance(env_name, str) or not env_name:
        raise CompactionError("Invalid TypeSafe API key environment variable.")
    key = os.environ.get(env_name)
    if not key:
        raise CompactionError("TypeSafe API key environment variable is missing.")
    payload = {
        "model": policy.get("model", MODEL),
        "state": state,
        "questions": questions,
    }
    request = urllib.request.Request(
        _endpoint(policy),
        data=encode(payload),
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "io-delegation/context-compaction",
        },
    )
    timeout = float(policy.get("timeout_seconds", 12))
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise CompactionError(f"TypeSafe HTTP {exc.code}.") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise CompactionError("TypeSafe request failed.") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise CompactionError("TypeSafe response exceeded the size limit.")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        raise CompactionError("TypeSafe returned invalid JSON.") from None
    if not isinstance(value, dict) or not isinstance(value.get("answers"), dict):
        raise CompactionError("TypeSafe response is missing answers.")
    return value


def _noul(value: Any, name: str) -> float:
    if not isinstance(value, dict):
        raise CompactionError(f"Missing Jev answer for {name}.")
    number = value.get("noul")
    if type(number) not in (int, float) or not math.isfinite(number) or not 0 <= number <= 1:
        raise CompactionError(f"Invalid Jev answer for {name}.")
    return float(number)


def ask_candidates(
    state: dict[str, Any],
    candidates: list[dict[str, Any]],
    policy: dict[str, Any],
    asker: Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], dict[str, Any]],
) -> tuple[dict[str, tuple[float, float]], int]:
    if not candidates:
        return {}, 0
    max_request = int(policy.get("max_request_tokens", 30_000))
    batches: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for call in candidates:
        pair = _question_pair(call)
        trial = {**current, **pair}
        request_shape = {
            "model": policy.get("model", MODEL),
            "state": state,
            "questions": trial,
        }
        if current and estimate_tokens(request_shape) > max_request:
            batches.append(current)
            current = pair
        else:
            current = trial
        single_shape = {
            "model": policy.get("model", MODEL),
            "state": state,
            "questions": current,
        }
        if estimate_tokens(single_shape) > max_request:
            raise CompactionError("A Jev compaction question batch exceeds the request budget.")
    if current:
        batches.append(current)

    answers: dict[str, Any] = {}
    workers = min(8, len(batches))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        responses = list(pool.map(lambda batch: asker(state, batch, policy), batches))
    for response in responses:
        block = response.get("answers")
        if not isinstance(block, dict):
            raise CompactionError("TypeSafe response is missing answers.")
        answers.update(block)

    result: dict[str, tuple[float, float]] = {}
    for call in candidates:
        label = str(call["id"])
        result[label] = (
            _noul(answers.get(f"{label}_call"), f"{label}_call"),
            _noul(answers.get(f"{label}_result"), f"{label}_result"),
        )
    return result, len(batches)


def _format_call(call: dict[str, Any], action: str, truncate_chars: int) -> str:
    input_text = _text(call.get("input"))
    result = call.get("result")
    result_text = result if isinstance(result, str) else ""
    if action == "drop_result" and len(result_text) > truncate_chars:
        kept = result_text[:truncate_chars]
        result_text = kept + f"\n[… {len(result_text) - truncate_chars} result chars omitted by Jev …]"
    status = "error" if call.get("is_error") else "ok"
    return (
        f"{call.get('id')} {call.get('tool')}\n"
        f"input: {input_text}\n"
        f"result ({status}):\n{result_text}"
    )


def _bounded_snapshot(blocks: list[str], max_chars: int) -> tuple[str, int]:
    header = (
        "[io-delegation Jev context recovery]\n"
        "The following is verbatim tool evidence retained before the host's native "
        "conversation compaction. Treat it as prior context, not as a new user request.\n\n"
    )
    if len(header) >= max_chars:
        return "", len(blocks)
    selected: list[str] = []
    used = len(header)
    omitted = 0
    # Prefer recent evidence when the host injection channel has a smaller budget.
    for block in reversed(blocks):
        cost = len(block) + 6
        if used + cost > max_chars:
            omitted += 1
            continue
        selected.append(block)
        used += cost
    selected.reverse()
    if not selected:
        return "", len(blocks)
    note = f"\n\n[{omitted} older retained tool item(s) omitted by host rehydration cap.]" if omitted else ""
    return header + "\n\n---\n\n".join(selected) + note, omitted


def compact_journal(
    journal: dict[str, Any],
    policy: dict[str, Any],
    asker: Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], dict[str, Any]] = call_typesafe,
) -> dict[str, Any]:
    calls = [row for row in journal.get("calls", []) if isinstance(row, dict)]
    if not calls:
        return {
            "pending": None,
            "stats": {"calls": 0, "kept": 0, "results_dropped": 0, "calls_dropped": 0, "requests": 0},
        }
    preserve = max(0, int(policy.get("preserve_recent_messages", 6)))
    pinned_ids = {str(row.get("id")) for row in calls[-preserve:]} if preserve else set()
    state, state_tokens = build_state(journal, policy)
    candidates = [row for row in calls if str(row.get("id")) not in pinned_ids]
    scores, requests = ask_candidates(state, candidates, policy, asker)
    threshold = float(policy.get("keep_threshold", 0.5))
    truncate_chars = max(0, int(policy.get("truncate_head_chars", 300)))

    blocks: list[str] = []
    decisions: list[dict[str, Any]] = []
    kept = results_dropped = calls_dropped = 0
    for call in calls:
        label = str(call.get("id"))
        if label in pinned_ids:
            action = "keep"
            keep_call = keep_result = 1.0
            reason = "pinned"
        else:
            keep_call, keep_result = scores[label]
            if keep_result >= threshold:
                action, reason = "keep", "kept"
            elif keep_call >= threshold:
                action, reason = "drop_result", "result_dropped"
            else:
                action, reason = "drop_call", "call_dropped"
        decisions.append(
            {
                "id": label,
                "tool": call.get("tool"),
                "keep_call": keep_call,
                "keep_result": keep_result,
                "action": action,
                "reason": reason,
            }
        )
        if action == "keep":
            kept += 1
            blocks.append(_format_call(call, action, truncate_chars))
        elif action == "drop_result":
            results_dropped += 1
            blocks.append(_format_call(call, action, truncate_chars))
        else:
            calls_dropped += 1

    before_chars = sum(len(_text(row.get("input"))) + len(_text(row.get("result"))) for row in calls)
    max_rehydrate = max(2_000, int(policy.get("max_rehydrate_chars", DEFAULT_MAX_REHYDRATE_CHARS)))
    snapshot, host_omitted = _bounded_snapshot(blocks, max_rehydrate)
    after_chars = len(snapshot)
    reduction = 1.0 if before_chars <= 0 else max(0.0, 1.0 - (after_chars / before_chars))
    min_reduction = float(policy.get("min_reduction_ratio", 0.25))
    pending = None
    if snapshot and reduction >= min_reduction:
        digest = hashlib.sha256(snapshot.encode("utf-8")).hexdigest()[:16]
        pending = {
            "id": digest,
            "text": snapshot,
            "delivered": False,
            "created_seq": int(journal.get("seq", 0)),
            "created_at": int(time.time()),
        }
    stats = {
        "calls": len(calls),
        "kept": kept,
        "results_dropped": results_dropped,
        "calls_dropped": calls_dropped,
        "pinned": len(pinned_ids),
        "requests": requests,
        "state_tokens": state_tokens,
        "before_chars": before_chars,
        "after_chars": after_chars,
        "reduction_ratio": reduction,
        "below_minimum": pending is None,
        "host_omitted": host_omitted,
        "decisions": decisions,
    }
    return {"pending": pending, "stats": stats}


def _event_name(host: str, event: dict[str, Any]) -> str:
    value = event.get("hook_event_name")
    if isinstance(value, str) and value:
        return value
    return ""


def _session_id(host: str, event: dict[str, Any]) -> str:
    value = event.get("session_id") if host == "codex" else event.get("conversation_id") or event.get("session_id")
    return _clean_session_id(value)


def _deliver_codex(journal: dict[str, Any]) -> dict[str, Any]:
    pending = journal.get("pending")
    if not isinstance(pending, dict) or pending.get("delivered") is True or not pending.get("text"):
        return {}
    pending["delivered"] = True
    return {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": pending["text"],
        }
    }


def _deliver_cursor_context(journal: dict[str, Any]) -> dict[str, Any]:
    pending = journal.get("pending")
    if not isinstance(pending, dict) or pending.get("delivered") is True or not pending.get("text"):
        return {}
    pending["delivered"] = True
    return {"additional_context": pending["text"]}


def _deliver_cursor_stop(journal: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    pending = journal.get("pending")
    if (
        not isinstance(pending, dict)
        or pending.get("delivered") is True
        or not pending.get("text")
        or event.get("status") != "completed"
        or int(event.get("loop_count", 0) or 0) > 0
    ):
        return {}
    pending["delivered"] = True
    return {
        "followup_message": (
            pending["text"]
            + "\n\n[io-delegation] Resume the original task using the preserved evidence above. "
            "Do not interpret this system-generated recovery message as a new task."
        )
    }


def handle_event(
    host: str,
    event: dict[str, Any],
    *,
    asker: Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], dict[str, Any]] = call_typesafe,
) -> tuple[dict[str, Any], Path | None]:
    project = find_project(event, host)
    if project is None:
        return {}, None
    _root, policy, project_id = project
    session_id = _session_id(host, event)
    path = journal_path(project_id, host, session_id)
    prune_stale_journals(path)
    with journal_lock(path):
        journal = load_journal(path, session_id)
        name = _event_name(host, event)
        output: dict[str, Any] = {}

        if (host == "codex" and name == "UserPromptSubmit") or (
            host == "cursor" and name == "beforeSubmitPrompt"
        ):
            record_prompt(journal, event.get("prompt"))
        elif name in {"PreToolUse", "preToolUse"}:
            record_pre_tool(journal, event)
        elif name in {"PostToolUse", "postToolUse"}:
            record_post_tool(journal, event)
            if host == "cursor":
                output = _deliver_cursor_context(journal)
        elif name in {"postToolUseFailure"}:
            record_post_tool(journal, event, failed=True)
            if host == "cursor":
                output = _deliver_cursor_context(journal)
        elif name in {"PreCompact", "preCompact"}:
            compacted = compact_journal(journal, policy, asker)
            journal["pending"] = compacted["pending"]
            journal["last_compaction"] = compacted["stats"]
            if host == "cursor":
                stats = compacted["stats"]
                if compacted["pending"]:
                    output = {
                        "user_message": (
                            f"io-delegation: Jev retained {stats['kept'] + stats['results_dropped']}/"
                            f"{stats['calls']} tool calls for exact context recovery."
                        )
                    }
        elif host == "codex" and name == "SessionStart" and event.get("source") == "compact":
            output = _deliver_codex(journal)
        elif host == "cursor" and name == "stop":
            output = _deliver_cursor_stop(journal, event)
        elif name in {"SessionEnd", "sessionEnd"}:
            if not bool(policy.get("retain_session_cache", False)):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                return {}, path

        atomic_write(path, journal)
        return output, path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", choices=["codex", "cursor"], required=True)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        print(json.dumps({"status": "ok", "component": "io-delegation-context-compaction"}))
        return 0
    raw = os.read(0, MAX_INPUT_EVENT_BYTES + 1)
    if len(raw) > MAX_INPUT_EVENT_BYTES:
        return 0
    try:
        event = json.loads(raw or b"{}")
        if not isinstance(event, dict):
            return 0
        output, _path = handle_event(args.host, event)
        if output:
            print(json.dumps(output, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
        return 0
    except Exception as exc:
        # Hooks are fail-open: native host compaction always remains available.
        safe = exc if isinstance(exc, CompactionError) else CompactionError(type(exc).__name__)
        print(f"io-delegation compaction skipped: {safe}", file=os.sys.stderr)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
