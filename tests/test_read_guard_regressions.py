"""Regression checks for literal read ranges and multiline command routing.

Synthetic local files only; no agent client, network or proposed shell execution.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "skills/io-delegation/scripts/read_guard.py"
spec = importlib.util.spec_from_file_location("read_guard_regressions", GUARD)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class ReadGuardRegressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / "big.txt").write_bytes(b"row\n" * 500)
        (self.root / "small.txt").write_bytes(b"row\n" * 20)

    def check(self, command):
        return guard.shell_request(command, self.root, self.root, guard.DEFAULTS)

    def test_tail_plus_is_from_start_not_last_n(self):
        for option in ("-n", "--lines"):
            with self.subTest(option=option):
                row = self.check(f"tail {option} +1 big.txt")
                self.assertTrue(row["covered"])
                self.assertTrue(row["would_block"])

    def test_tail_plus_near_end_is_bounded(self):
        row = self.check("tail -n +499 big.txt")
        self.assertFalse(row["would_block"])
        self.assertEqual(row["lines"], 2)
        self.assertEqual(row["bytes"], 8)

    def test_regular_tail_still_reads_last_n(self):
        for command in ("tail -n 3 big.txt", "tail --lines=3 big.txt"):
            with self.subTest(command=command):
                row = self.check(command)
                self.assertFalse(row["would_block"])
                self.assertEqual(row["lines"], 3)

    def test_repeated_tail_range_uses_last_option(self):
        for option in ("-n 3", "--lines=3"):
            with self.subTest(option=option):
                row = self.check(f"tail -n +1 {option} big.txt")
                self.assertFalse(row["would_block"])
                self.assertEqual(row["lines"], 3)
        self.assertTrue(self.check("tail -n 3 -n +1 big.txt")["would_block"])

    def test_multiline_read_is_inspected(self):
        for separator in ("\n", "\r\n", ";\n", "&&\n", "\n\n"):
            with self.subTest(separator=separator):
                row = self.check("git status" + separator + "cat big.txt")
                self.assertTrue(row["covered"])
                self.assertTrue(row["would_block"])

    def test_comment_does_not_hide_next_line(self):
        for command in ("git status # note\ncat big.txt",
                        "git status\n# note\ncat big.txt"):
            with self.subTest(command=command):
                self.assertTrue(self.check(command)["would_block"])

    def test_multiline_reads_share_aggregate_budget(self):
        (self.root / "first.txt").write_bytes(b"row\n" * 200)
        (self.root / "second.txt").write_bytes(b"row\n" * 200)
        row = self.check("cat first.txt\ncat second.txt")
        self.assertEqual(row["code"], "aggregate_budget")
        self.assertTrue(row["would_block"])

    def test_multiline_small_read_still_passes(self):
        row = self.check("git status\nhead -n 10 big.txt")
        self.assertTrue(row["covered"])
        self.assertFalse(row["would_block"])
        self.assertEqual(row["lines"], 10)

    def test_powershell_multiline_reader_is_inspected(self):
        row = self.check("Write-Output ok\nGet-Content -LiteralPath 'big.txt' -Raw")
        self.assertTrue(row["covered"])
        self.assertTrue(row["would_block"])

    def test_real_hook_subprocess_denies_without_source_output(self):
        for host in ("claude-code", "codex", "cursor"):
            with self.subTest(host=host):
                event = {"hook_event_name": "preToolUse" if host == "cursor" else "PreToolUse",
                         "tool_name": "Shell" if host == "cursor" else "Bash",
                         "cwd": str(self.root),
                         "tool_input": {"command": "git status\ntail -n +1 big.txt"}}
                result = subprocess.run(
                    [sys.executable, str(GUARD), "--host", host, "--root", str(self.root)],
                    input=json.dumps(event).encode("utf-8"), capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                output = json.loads(result.stdout)
                decision = output.get("permission") if host == "cursor" else (
                    output["hookSpecificOutput"]["permissionDecision"])
                self.assertEqual(decision, "deny")
                self.assertNotIn(b"row\n", result.stdout + result.stderr)
                self.assertNotIn(str(self.root).encode(), result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
