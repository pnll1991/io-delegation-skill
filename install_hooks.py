#!/usr/bin/env python3
"""Install optional project-local read hooks; preserve existing host settings."""
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
import sys
import tempfile

CONFIG = {"claude-code": ".claude/settings.json", "codex": ".codex/hooks.json", "cursor": ".cursor/hooks.json"}
RUNTIME = ".io-delegation-hooks"
POLICY = {"version": 1, "mode": "enforce", "max_lines": 350, "max_bytes": 64000}


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def safe_path(root, relative):
    p = root / relative
    for component in [p, *p.parents]:
        if component == root:
            break
        if component.is_symlink():
            raise ValueError("Refusing a symlinked installation path")
    p.resolve().relative_to(root)
    return p


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".io-delegation-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def invocation(python, runtime, root, host):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", python):
        raise ValueError("--python must be a single executable name, e.g. python or python3")
    argv = [python, str(runtime / "read_guard.py"), "--host", host,
            "--root", str(root), "--policy", str(runtime / "policy.json")]
    if os.name == "nt":
        # cmd expands these even inside double quotes; refuse instead of guessing.
        if any(any(c in arg for c in '%!\r\n"') for arg in argv):
            raise ValueError("Project path is not supported by the Windows command launcher")
        return subprocess.list2cmdline(argv)
    return shlex.join(argv)


def entry_for(host, command):
    if host == "cursor":
        return "preToolUse", {"matcher": "Read|Shell", "command": command,
                              "timeout": 5, "failClosed": True}
    return "PreToolUse", {"matcher": "^(Read|read_file|Bash)$", "hooks": [
        {"type": "command", "command": command, "timeout": 5}]}


def merge_settings(config, host, event, entry, remove=False):
    value = copy.deepcopy(config)
    if not isinstance(value, dict):
        raise ValueError("Host configuration must be a JSON object")
    if host == "cursor" and value.get("version", 1) != 1:
        raise ValueError("Unsupported Cursor hooks schema")
    hooks = value.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("Expected hooks object")
    entries = hooks.setdefault(event, [])
    if not isinstance(entries, list):
        raise ValueError("Expected hook event list")
    if remove:
        hooks[event] = [item for item in entries if item != entry]
    elif entry not in entries:
        entries.append(entry)
    if host == "cursor":
        value.setdefault("version", 1)
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", choices=list(CONFIG), required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--python", default="python", help="Executable name available to the host")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--remove", action="store_true", help="Remove only the exact installed hook entry")
    args = parser.parse_args(argv)
    try:
        root = Path(args.project).resolve(strict=True)
        if not root.is_dir():
            raise ValueError("Project must be a directory")
        config_path = safe_path(root, CONFIG[args.agent])
        runtime = safe_path(root, RUNTIME)
        for name in ["read_guard.py", "policy.json", ".gitignore"]:
            safe_path(root, RUNTIME + "/" + name)
        record = safe_path(root, RUNTIME + "/" + args.agent + ".installation.json")
        raw = config_path.read_bytes() if config_path.exists() else None
        current = json.loads(raw.decode("utf-8-sig")) if raw is not None else {}
        command = invocation(args.python, runtime, root, args.agent)
        event, entry = entry_for(args.agent, command)
        if args.remove:
            if not record.exists():
                raise ValueError("No installation record; nothing will be removed")
            installed = json.loads(record.read_text(encoding="utf-8"))
            event, entry = installed["event"], installed["entry"]
        elif record.exists():
            installed = json.loads(record.read_text(encoding="utf-8"))
            if installed["entry"] != entry:
                raise ValueError("Existing hook differs; remove the recorded entry before reinstalling")
        updated = merge_settings(current, args.agent, event, entry, args.remove)
        data = json_bytes(updated)
        source = Path(__file__).resolve().parent / "skills/io-delegation/scripts/read_guard.py"
        code = source.read_bytes()
        target = runtime / "read_guard.py"
        if not args.remove and target.exists() and target.read_bytes() != code:
            raise ValueError("Existing runtime differs; back it up and review before replacing")
        changed = updated != current
        backup = None
        if not args.dry_run:
            if not args.remove:
                if not shutil.which(args.python):
                    raise ValueError("Python launcher not found; use --python python3 where appropriate")
                if not (runtime / ".gitignore").exists():
                    atomic_write(runtime / ".gitignore", b"# Machine-local runtime, installation records and private backups.\n*\n")
                if not target.exists():
                    atomic_write(target, code)
                if not (runtime / "policy.json").exists():
                    atomic_write(runtime / "policy.json", json_bytes(POLICY))
                # Validate the interpreter, copied runtime and policy BEFORE changing host config.
                check = subprocess.run([args.python, str(target), "--root", str(root), "--policy",
                                        str(runtime / "policy.json")], input=json.dumps({"tool_name": "Grep", "tool_input": {}}),
                                       capture_output=True, text=True, encoding="utf-8", timeout=8)
                row = json.loads(check.stdout)
                if check.returncode or row.get("code") != "unclassified_tool":
                    raise ValueError("Runtime preflight failed; host configuration was not changed")
                atomic_write(record, json_bytes({"event": event, "entry": entry}))
            if changed:
                if raw is not None:
                    digest = hashlib.sha256(raw).hexdigest()[:12]
                    backup = safe_path(root, RUNTIME + "/" + args.agent + "." + digest + ".backup.json")
                    if not backup.exists():
                        atomic_write(backup, raw)
                # Do not overwrite concurrent user changes.
                if (config_path.read_bytes() if config_path.exists() else None) != raw:
                    raise ValueError("Configuration changed during installation; retry after review")
                atomic_write(config_path, data)
            if args.remove and record.exists():
                record.unlink()
        print(json.dumps({"status": "dry_run" if args.dry_run else "removed" if args.remove else "installed",
                          "changed": changed, "config": str(config_path), "entry": entry,
                          "backup": str(backup) if backup else None, "host_verified": False}, ensure_ascii=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
