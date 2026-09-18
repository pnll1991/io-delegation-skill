#!/usr/bin/env python3
"""Install/remove project-scoped Codex/Cursor hooks for Jev context recovery."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile

CONFIG = {"codex": ".codex/hooks.json", "cursor": ".cursor/hooks.json"}
RUNTIME_DIR = ".io-delegation-hooks"


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def safe_path(root, relative):
    path = root / relative
    for component in [path, *path.parents]:
        if component == root:
            break
        if component.is_symlink():
            raise ValueError("Refusing a symlinked compaction hook path")
    path.resolve().relative_to(root)
    return path


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".io-compaction-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def invocation(python, runtime_script, host):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", python):
        raise ValueError("--python must be a single executable name")
    argv = [python, str(runtime_script), "--host", host]
    if os.name == "nt":
        if any(any(c in arg for c in '%!\r\n"') for arg in argv):
            raise ValueError("Runtime path is not supported by the Windows command launcher")
        return subprocess.list2cmdline(argv)
    return shlex.join(argv)


def entries_for(host, command):
    if host == "codex":
        def handler(timeout=5, *, context_limit=None):
            row = {"type": "command", "command": command, "timeout": timeout}
            if context_limit is not None:
                row["additionalContextLimit"] = context_limit
            return row
        return [
            ("UserPromptSubmit", {"hooks": [handler()]}),
            ("PreToolUse", {"matcher": "*", "hooks": [handler()]}),
            ("PostToolUse", {"matcher": "*", "hooks": [handler()]}),
            ("PreCompact", {"matcher": "manual|auto", "hooks": [handler(30)]}),
            ("SessionStart", {"matcher": "compact", "hooks": [handler(5, context_limit=6500)]}),
            ("SessionEnd", {"matcher": "other", "hooks": [handler()]}),
        ]
    if host == "cursor":
        return [
            ("beforeSubmitPrompt", {"command": command, "timeout": 5}),
            ("preToolUse", {"command": command, "timeout": 5}),
            ("postToolUse", {"command": command, "timeout": 5}),
            ("postToolUseFailure", {"command": command, "timeout": 5}),
            ("preCompact", {"command": command, "timeout": 30}),
            ("stop", {"command": command, "timeout": 5, "loop_limit": 1}),
            ("sessionEnd", {"command": command, "timeout": 5}),
        ]
    raise ValueError("Unsupported host")


def merge_entries(config, host, entries, remove=False):
    value = copy.deepcopy(config)
    if not isinstance(value, dict):
        raise ValueError("Host hooks configuration must be a JSON object")
    if host == "cursor" and value.get("version", 1) != 1:
        raise ValueError("Unsupported Cursor hooks schema")
    hooks = value.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("Expected hooks object")
    for event, entry in entries:
        rows = hooks.setdefault(event, [])
        if not isinstance(rows, list):
            raise ValueError(f"Expected hook event list for {event}")
        if remove:
            hooks[event] = [item for item in rows if item != entry]
            if not hooks[event]:
                hooks.pop(event, None)
        elif entry not in rows:
            rows.append(entry)
    if host == "cursor":
        value.setdefault("version", 1)
    if not hooks:
        value.pop("hooks", None)
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", choices=list(CONFIG), required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--python", default="python")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--remove", action="store_true")
    args = parser.parse_args(argv)
    try:
        root = Path(args.project).resolve(strict=True)
        if not root.is_dir():
            raise ValueError("Project must be a directory")
        runtime_script = Path(args.runtime).expanduser().resolve()
        config_path = safe_path(root, CONFIG[args.agent])
        record_dir = safe_path(root, RUNTIME_DIR)
        record = safe_path(root, RUNTIME_DIR + "/" + args.agent + ".compaction.json")
        gitignore = safe_path(root, RUNTIME_DIR + "/.gitignore")

        raw = config_path.read_bytes() if config_path.exists() else None
        current = json.loads(raw.decode("utf-8-sig")) if raw is not None else {}
        command = invocation(args.python, runtime_script, args.agent)
        entries = entries_for(args.agent, command)

        if args.remove:
            if not record.is_file():
                print(json.dumps({
                    "status": "absent", "changed": False, "config": str(config_path),
                    "agent": args.agent, "host_verified": False
                }))
                return 0
            installed = json.loads(record.read_text(encoding="utf-8"))
            entries = [(row["event"], row["entry"]) for row in installed.get("entries", [])]
        elif record.exists():
            installed = json.loads(record.read_text(encoding="utf-8"))
            previous = [(row["event"], row["entry"]) for row in installed.get("entries", [])]
            if previous != entries:
                raise ValueError("Existing compaction hook installation differs; remove it before reinstalling")

        updated = merge_entries(current, args.agent, entries, remove=args.remove)
        data = json_bytes(updated)
        changed = updated != current
        backup = None

        if not args.dry_run:
            if not args.remove:
                if not runtime_script.is_file():
                    raise ValueError("Compaction runtime is missing")
                if not shutil.which(args.python):
                    raise ValueError("Python launcher not found; use --python python3 where appropriate")
                check = subprocess.run(
                    [args.python, str(runtime_script), "--host", args.agent, "--self-test"],
                    capture_output=True, text=True, encoding="utf-8", timeout=8
                )
                row = json.loads(check.stdout)
                if check.returncode or row.get("status") != "ok":
                    raise ValueError("Compaction runtime preflight failed")
                record_dir.mkdir(parents=True, exist_ok=True)
                if not gitignore.exists():
                    atomic_write(
                        gitignore,
                        b"# Machine-local hook records and backups.\n*\n"
                    )
            if changed:
                if raw is not None:
                    digest = hashlib.sha256(raw).hexdigest()[:12]
                    backup = safe_path(
                        root, RUNTIME_DIR + "/" + args.agent + ".compaction." + digest + ".backup.json"
                    )
                    if not backup.exists():
                        atomic_write(backup, raw)
                if (config_path.read_bytes() if config_path.exists() else None) != raw:
                    raise ValueError("Configuration changed during compaction hook installation")
                atomic_write(config_path, data)
            if args.remove:
                if record.exists():
                    record.unlink()
            else:
                atomic_write(
                    record,
                    json_bytes({
                        "version": 1,
                        "agent": args.agent,
                        "runtime": str(runtime_script),
                        "entries": [{"event": event, "entry": entry} for event, entry in entries],
                    }),
                )

        print(json.dumps({
            "status": "dry_run" if args.dry_run else "removed" if args.remove else "installed",
            "changed": changed,
            "config": str(config_path),
            "agent": args.agent,
            "entries": len(entries),
            "backup": str(backup) if backup else None,
            "host_verified": False,
        }, ensure_ascii=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=True), file=os.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
