#!/usr/bin/env python3
"""Optional, local I/O budget gate. Not a sandbox or a general shell interpreter."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shlex
import stat
import sys

DEFAULTS = {"version": 1, "mode": "enforce", "max_lines": 350, "max_bytes": 64000}
READERS = {"Read", "read_file"}
SHELLS = {"Bash", "Shell", "exec_command", "shell_command", "shell"}
MESSAGE = ("I/O Delegation: read exceeds the configured budget. Locate symbols with search, "
           "read a bounded range, or use bulk-read with an already approved worker. "
           "Do not load the whole file first. Without a worker, use targeted reading.")
MAX_INPUT = 1048576


def policy_from(path: str | None) -> dict:
    result = dict(DEFAULTS)
    if path:
        with open(path, encoding="utf-8-sig") as f:
            value = json.load(f)
        if not isinstance(value, dict) or set(value) - set(DEFAULTS):
            raise ValueError("Unknown policy fields")
        result.update(value)
    if type(result["version"]) is not int or result["version"] != 1:
        raise ValueError("Unsupported policy version")
    if result["mode"] not in {"observe", "enforce"}:
        raise ValueError("Invalid mode")
    for name in ("max_lines", "max_bytes"):
        if type(result[name]) is not int or not 1 <= result[name] <= 10000000:
            raise ValueError("Invalid budget")
    return result


def verdict(code: str, covered: bool, exceeded: bool = False, **counts) -> dict:
    return {"code": code, "covered": covered, "would_block": exceeded, **counts}


def positive(value, default=None):
    if value is None:
        return default
    if type(value) is not int or value < 1:
        raise ValueError("Range must use positive integers")
    return value


def measure(path: str, root: Path, cwd: Path, budget: dict,
            offset: int = 1, limit: int | None = None, tail: bool = False) -> dict:
    """Count only locally; no file content leaves this function. Stop at budget+1."""
    if not isinstance(path, str) or not path or "\x00" in path:
        raise ValueError("Missing file path")
    root = Path(root).resolve()
    cwd = Path(cwd).resolve()
    p = Path(path).expanduser()
    p = (cwd / p if not p.is_absolute() else p).resolve()
    try:
        p.relative_to(root)
    except ValueError:
        return verdict("outside_project", True, True)
    try:
        info = p.stat()
    except FileNotFoundError:
        return verdict("missing_file", True)
    if not stat.S_ISREG(info.st_mode):
        return verdict("not_regular_file", True, True)
    if limit is None and offset == 1 and info.st_size > budget["max_bytes"]:
        return verdict("byte_budget", True, True, bytes_lower_bound=info.st_size)
    if tail:
        # Bounded tail inspection: do not read an arbitrarily large file into memory.
        with p.open("rb") as f:
            f.seek(max(0, info.st_size - budget["max_bytes"] - 2))
            chunk = f.read(budget["max_bytes"] + 2)
        rows = chunk.splitlines(keepends=True)
        if info.st_size > len(chunk):
            rows = rows[1:]
            if len(rows) < (limit or 10):
                return verdict("byte_budget", True, True)
        selected = rows[-(limit or 10):]
        lines, size = len(selected), sum(map(len, selected))
    else:
        lines = size = 0
        with p.open("rb") as f:
            index = 1
            scanned = 0
            while True:
                chunk = f.readline(budget["max_bytes"] + 1)
                if not chunk:
                    break
                scanned += len(chunk)
                if scanned > 8000000:
                    return verdict("inspection_budget", True, True)
                if index >= offset:
                    size += len(chunk)
                    if size > budget["max_bytes"]:
                        return verdict("byte_budget", True, True, bytes_lower_bound=size)
                if chunk.endswith(b"\n") or len(chunk) < budget["max_bytes"] + 1:
                    if index >= offset:
                        lines += 1
                        if lines > budget["max_lines"]:
                            return verdict("line_budget", True, True, lines_lower_bound=lines)
                        if limit is not None and lines >= limit:
                            break
                    index += 1
    exceeded = lines > budget["max_lines"] or size > budget["max_bytes"]
    return verdict("read_budget" if exceeded else "within_budget", True, exceeded,
                   lines=lines, bytes=size)


def read_request(data: dict, root: Path, cwd: Path, budget: dict) -> dict:
    offset = positive(data.get("offset"), 1)
    limit = positive(data.get("limit"))
    return measure(data.get("file_path", data.get("path")), root, cwd, budget, offset, limit)


def reader_args(tokens: list[str]):
    """Recognize literal commands only. Never execute/expand shell input."""
    name = tokens[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
    if name.endswith(".exe"):
        name = name[:-4]
    if name not in {"cat", "head", "tail", "less", "more", "type", "get-content", "gc", "sed"}:
        return None
    args = tokens[1:]
    paths, limit, offset, tail = [], None, 1, name == "tail"
    if name in {"head", "tail"}:
        limit = 10
    if name == "sed":
        if len(args) != 3 or args[0] != "-n":
            raise ValueError("Use a literal bounded sed range")
        m = re.fullmatch(r"(\d+)(?:,(\d+))?p", args[1])
        if not m:
            raise ValueError("Use a literal bounded sed range")
        offset = positive(int(m[1]))
        end = int(m[2] or m[1])
        return [args[2]], offset, positive(end - offset + 1), False
    i = 0
    while i < len(args):
        item = args[i]
        lower = item.lower()
        if lower in {"-n", "--lines", "-totalcount", "-head", "-tail"}:
            if i + 1 == len(args):
                raise ValueError("Missing range")
            count = positive(int(args[i + 1]))
            if name == "tail" and lower in {"-n", "--lines"} and args[i + 1].startswith("+"):
                offset, limit, tail = count, None, False
            else:
                offset, limit = 1, count
                tail = name == "tail" or lower == "-tail"
            i += 2
            continue
        if re.fullmatch(r"-n\d+|--lines=\d+", item):
            offset, limit = 1, positive(int(re.search(r"\d+", item)[0]))
            tail = name == "tail" or lower == "-tail"
        elif lower in {"-path", "-literalpath"}:
            if i + 1 == len(args):
                raise ValueError("Missing path")
            paths.append(args[i + 1])
            i += 2
            continue
        elif lower in {"--", "-raw", "-encoding", "utf8", "utf-8"}:
            pass
        elif item.startswith("-"):
            raise ValueError("Unsupported reader option")
        else:
            paths.append(item)
        i += 1
    if name in {"cat", "less", "more", "type"} and limit is not None:
        raise ValueError("Unsupported reader option")
    return paths, offset, limit, tail


def shell_request(command: str, root: Path, cwd: Path, budget: dict) -> dict:
    if not isinstance(command, str) or len(command) > 64000:
        raise ValueError("Invalid command")
    windows = bool(re.search(r"\b[A-Za-z]:\\|\b(?:Get-Content|gc)\b", command, re.I))
    lex = shlex.shlex(command, posix=not windows, punctuation_chars=";&|<>\n")
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    lex.commenters = ""
    tokens = [t[1:-1] if len(t) > 1 and t[0] == t[-1] and t[0] in "\"'" else t for t in lex]
    groups, current = [], []
    for t in tokens + [";"]:
        if t and set(t) <= set(";&|\n"):
            if current:
                groups.append(current)
            current = []
        else:
            current.append(t)
    results = []
    for group in groups:
        if group[0] == "cd" and len(group) == 2:
            cwd = (cwd / group[1]).resolve()
            continue
        parsed = reader_args(group)
        if parsed is None:
            continue
        paths, offset, limit, tail = parsed
        if not paths:
            continue
        if any(any(c in path for c in "$`*?<>!") or "%" in path for path in paths):
            return verdict("unsupported_reader_expression", True, True)
        for path in paths:
            row = measure(path, root, cwd, budget, offset, limit, tail)
            if row["would_block"]:
                return row
            results.append(row)
    if not results:
        return verdict("unclassified_shell", False)
    lines = sum(r.get("lines", 0) for r in results)
    size = sum(r.get("bytes", 0) for r in results)
    return verdict("aggregate_budget" if lines > budget["max_lines"] or size > budget["max_bytes"]
                   else "within_budget", True,
                   lines > budget["max_lines"] or size > budget["max_bytes"], lines=lines, bytes=size)


def evaluate(payload: dict, root: Path, policy: dict) -> dict:
    if not isinstance(payload, dict) or not isinstance(payload.get("tool_name"), str):
        raise ValueError("Expected a pre-tool event")
    root = Path(root).resolve()
    tool = payload["tool_name"]
    data = payload.get("tool_input", {})
    if not isinstance(data, dict):
        raise ValueError("Expected tool_input object")
    cwd = Path(payload.get("cwd") or root)
    cwd = (root / cwd if not cwd.is_absolute() else cwd).resolve()
    if tool in READERS:
        row = read_request(data, root, cwd, policy)
    elif tool in SHELLS:
        work = data.get("working_directory", data.get("workdir"))
        if work:
            cwd = (cwd / work).resolve()
        row = shell_request(data.get("command", data.get("cmd")), root, cwd, policy)
    else:
        row = verdict("unclassified_tool", False)
    row["mode"] = policy["mode"]
    row["decision"] = "deny" if row["would_block"] and policy["mode"] == "enforce" else "pass"
    return row


def host_output(host: str, row: dict) -> dict:
    if host == "generic":
        return row
    if row["decision"] != "deny":
        return {"permission": "allow"} if host == "cursor" else {}
    message = MESSAGE if row["code"] not in {"invalid_request", "outside_project", "not_regular_file"} else (
        "I/O Delegation: cannot validate this read (" + row["code"] + "). "
        "Use a regular project file with explicit paths and bounded ranges; review the hook configuration if needed.")
    if host == "cursor":
        return {"permission": "deny", "user_message": message, "agent_message": message}
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                    "permissionDecisionReason": message}}


def audit_event(root, payload, row, host):
    """Observe actual host-shaped invocations; not a proof of universal enforcement."""
    import os
    # Installer preflights and generic tests must not certify a host session.
    if host == 'generic' or not isinstance(payload, dict) or not payload.get('session_id'):
        return
    directory = root / '.io-delegation-hooks'
    path = directory / 'events.jsonl'
    if directory.is_symlink() or path.is_symlink():
        return
    # Only an installed runtime may record events. Do not create unrelated host state.
    if not (directory / 'policy.json').is_file():
        return
    event = {'host_event': True, 'host': host, 'component': 'io-delegation-read-guard',
             'code': row['code'], 'covered': row['covered'], 'decision': row['decision'],
             'would_block': row['would_block']}
    encoded = (json.dumps(event, ensure_ascii=True) + '\n').encode('utf-8')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, encoded)
    finally:
        os.close(fd)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", choices=["generic", "claude-code", "codex", "cursor"], default="generic")
    parser.add_argument("--root", required=True)
    parser.add_argument("--policy")
    args = parser.parse_args(argv)
    root = payload = None
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            raise ValueError("Event too large")
        root = Path(args.root).resolve(strict=True)
        if not root.is_dir():
            raise ValueError("Root must be a directory")
        payload = json.loads(raw)
        row = evaluate(payload, root, policy_from(args.policy))
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        row = {**verdict("invalid_request", True, True), "decision": "deny", "mode": "enforce"}
    if root is not None:
        try:
            audit_event(root, payload, row, args.host)
        except OSError:
            pass  # Failure to record is never reported as verified execution.
    output = host_output(args.host, row)
    if output:
        print(json.dumps(output, ensure_ascii=True, allow_nan=False))
    print(json.dumps({"component": "io-delegation-read-guard", **row}), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
