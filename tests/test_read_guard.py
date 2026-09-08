from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'skills/io-delegation/scripts/read_guard.py'
spec = importlib.util.spec_from_file_location('read_guard', SCRIPT)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        for name, text in [('small.py', 'x = 1\n' * 20), ('large.py', 'x = 1\n' * 1000),
                           ('minified.js', 'x' * 64001), ('exact.py', 'x\n' * 350),
                           ('space name.py', 'x\n' * 1000), ('crlf.py', 'x\r\n' * 351)]:
            (self.root / name).write_bytes(text.encode())

    def run_event(self, tool='Read', data=None, **policy):
        return guard.evaluate({'tool_name': tool, 'tool_input': data or {'path': 'large.py'},
                               'cwd': str(self.root)}, self.root, {**guard.DEFAULTS, **policy})

    def test_large_native_denied(self):
        self.assertEqual(self.run_event()['decision'], 'deny')

    def test_small_native_passes(self):
        self.assertEqual(self.run_event(data={'file_path': 'small.py'})['decision'], 'pass')

    def test_threshold_inclusive(self):
        self.assertEqual(self.run_event(data={'path': 'exact.py'})['decision'], 'pass')

    def test_minified_bytes_denied(self):
        self.assertEqual(self.run_event(data={'path': 'minified.js'})['decision'], 'deny')

    def test_crlf_lines_denied(self):
        self.assertEqual(self.run_event(data={'path': 'crlf.py'})['decision'], 'deny')

    def test_bounded_read_passes(self):
        r = self.run_event(data={'path': 'large.py', 'offset': 700, 'limit': 50})
        self.assertEqual((r['decision'], r['lines']), ('pass', 50))

    def test_offset_alone_not_exemption(self):
        self.assertEqual(self.run_event(data={'path': 'large.py', 'offset': 2})['decision'], 'deny')

    def test_offset_near_end_passes(self):
        self.assertEqual(self.run_event(data={'path': 'large.py', 'offset': 980})['decision'], 'pass')

    def test_oversized_limit_denied(self):
        self.assertEqual(self.run_event(data={'path': 'large.py', 'limit': 999})['decision'], 'deny')

    def test_small_limit_cannot_hide_huge_line(self):
        self.assertEqual(self.run_event(data={'path': 'minified.js', 'limit': 1})['decision'], 'deny')

    def test_missing_file_native_error(self):
        self.assertEqual(self.run_event(data={'path': 'absent'})['code'], 'missing_file')

    def test_directory_denied(self):
        self.assertEqual(self.run_event(data={'path': '.'})['decision'], 'deny')

    def test_outside_root_denied(self):
        self.assertEqual(self.run_event(data={'path': '../other'})['code'], 'outside_project')

    def test_absolute_path(self):
        self.assertEqual(self.run_event(data={'path': str(self.root / 'large.py')})['decision'], 'deny')

    def test_symlink_outside(self):
        with tempfile.TemporaryDirectory() as outside:
            p = Path(outside) / 'data';p.write_text('x')
            try:
                (self.root / 'link').symlink_to(p)
            except OSError:
                self.skipTest('Symlinks unavailable')
            self.assertEqual(self.run_event(data={'path': 'link'})['code'], 'outside_project')

    def test_observe_reports_without_denying(self):
        r = self.run_event(mode='observe')
        self.assertTrue(r['would_block'])
        self.assertEqual(r['decision'], 'pass')

    def test_custom_threshold(self):
        self.assertEqual(self.run_event(max_lines=1200)['decision'], 'pass')

    def test_search_not_blocked(self):
        self.assertFalse(self.run_event('Grep', {'pattern': 'x'})['covered'])

    def test_edits_not_blocked(self):
        self.assertFalse(self.run_event('Edit', {'file_path': 'large.py'})['covered'])

    def test_unknown_tool_not_claimed_covered(self):
        self.assertFalse(self.run_event('mcp__custom__read', {'path': 'large.py'})['covered'])

    def test_cat_denied(self):
        self.assertEqual(self.run_event('Bash', {'command': 'cat large.py'})['decision'], 'deny')

    def test_quoted_path_denied(self):
        self.assertEqual(self.run_event('Bash', {'command': 'cat "space name.py"'})['decision'], 'deny')

    def test_head_default_passes(self):
        self.assertEqual(self.run_event('Bash', {'command': 'head large.py'})['decision'], 'pass')

    def test_head_large_denied(self):
        self.assertEqual(self.run_event('Bash', {'command': 'head -n 900 large.py'})['decision'], 'deny')

    def test_tail_bounded_passes(self):
        self.assertEqual(self.run_event('Shell', {'command': 'tail -n 20 large.py'})['decision'], 'pass')

    def test_tail_huge_line_denied(self):
        self.assertEqual(self.run_event('Shell', {'command': 'tail -n 1 minified.js'})['decision'], 'deny')

    def test_sed_bounded_passes(self):
        self.assertEqual(self.run_event('Bash', {'command': "sed -n '400,430p' large.py"})['decision'], 'pass')

    def test_batch_aggregate_denied(self):
        (self.root / 'a.py').write_text('x\n' * 200)
        (self.root / 'b.py').write_text('x\n' * 200)
        self.assertEqual(self.run_event('Bash', {'command': 'cat a.py b.py'})['code'], 'aggregate_budget')

    def test_pipeline_not_blanket_exempt(self):
        self.assertEqual(self.run_event('Bash', {'command': 'cat large.py | cat'})['decision'], 'deny')

    def test_compound_cd(self):
        (self.root / 'sub').mkdir()
        shutil.copyfile(self.root / 'large.py', self.root / 'sub/other.py')
        self.assertEqual(self.run_event('Bash', {'command': 'cd sub && cat other.py'})['decision'], 'deny')

    def test_tool_working_directory(self):
        (self.root / 'sub').mkdir()
        self.assertEqual(self.run_event('exec_command', {'cmd': 'cat ../large.py', 'workdir': 'sub'})['decision'], 'deny')

    def test_get_content_denied(self):
        self.assertEqual(self.run_event('Shell', {'command': "Get-Content -LiteralPath 'space name.py'"})['decision'], 'deny')

    def test_get_content_bounded(self):
        self.assertEqual(self.run_event('Shell', {'command': "Get-Content 'large.py' -TotalCount 20"})['decision'], 'pass')

    def test_get_content_tail(self):
        self.assertEqual(self.run_event('Shell', {'command': "Get-Content 'large.py' -Tail 20"})['decision'], 'pass')

    def test_glob_not_silently_approved(self):
        self.assertEqual(self.run_event('Bash', {'command': 'cat *.py'})['decision'], 'deny')

    def test_unrelated_shell_not_covered(self):
        self.assertFalse(self.run_event('Bash', {'command': 'git status --short'})['covered'])

    def test_arbitrary_program_not_misrepresented(self):
        self.assertFalse(self.run_event('Bash', {'command': 'python program.py'})['covered'])

    def test_wrapper_protocols(self):
        for host in ['claude-code', 'codex', 'cursor']:
            with self.subTest(host=host):
                r = subprocess.run([sys.executable, str(SCRIPT), '--host', host, '--root', str(self.root)],
                    input=json.dumps({'tool_name': 'Read', 'tool_input': {'path': 'large.py'}}),
                    text=True, encoding='utf-8', capture_output=True)
                self.assertEqual(r.returncode, 0)
                data = json.loads(r.stdout)
                self.assertEqual(data.get('permission', data.get('hookSpecificOutput', {}).get('permissionDecision')), 'deny')
                self.assertNotIn('x = 1', r.stdout + r.stderr)

    def test_passthrough_does_not_grant_claude_codex_permissions(self):
        row = self.run_event('Bash', {'command': 'git status'})
        for host in ['claude-code', 'codex']:
            self.assertEqual(guard.host_output(host, row), {})

    def test_malformed_request_denies_without_traceback(self):
        r = subprocess.run([sys.executable, str(SCRIPT), '--host', 'codex', '--root', str(self.root)],
            input='{SECRET', text=True, capture_output=True)
        self.assertEqual(json.loads(r.stdout)['hookSpecificOutput']['permissionDecision'], 'deny')
        self.assertNotIn('SECRET', r.stdout + r.stderr)
        self.assertNotIn('Traceback', r.stderr)

    def test_invalid_range(self):
        for limit in [0, -1, True, '20']:
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                self.run_event(data={'path': 'large.py', 'limit': limit})

    def test_invalid_policy(self):
        p = self.root / 'policy.json'
        for value in [{'max_lines': True}, {'other': 1}, {'mode': 'disabled'}, {'version': 2}]:
            p.write_text(json.dumps(value))
            with self.subTest(value=value), self.assertRaises(ValueError):
                guard.policy_from(str(p))


class InstallHookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='io project ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def install(self, host='claude-code', *extra):
        return subprocess.run([sys.executable, str(ROOT / 'install_hooks.py'), '--agent', host,
            '--project', str(self.root), '--python', Path(sys.executable).name, *extra],
            text=True, encoding='utf-8', capture_output=True)

    def test_install_every_host_idempotent(self):
        paths = {'claude-code': '.claude/settings.json', 'codex': '.codex/hooks.json', 'cursor': '.cursor/hooks.json'}
        for host, path in paths.items():
            with self.subTest(host=host):
                first = self.install(host)
                self.assertEqual(first.returncode, 0, first.stderr)
                before = (self.root / path).read_bytes()
                second = self.install(host)
                self.assertEqual(second.returncode, 0, second.stderr)
                self.assertEqual((self.root / path).read_bytes(), before)
                self.assertFalse(json.loads(second.stdout)['changed'])
                self.assertFalse(json.loads(second.stdout)['host_verified'])

    def test_preserve_existing_settings_and_backup(self):
        p = self.root / '.claude/settings.json';p.parent.mkdir()
        original = b'{"permissions":{"deny":["Bash(rm:*)"]},"hooks":{"Stop":[]}}'
        p.write_bytes(original)
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(p.read_text())['permissions'], {'deny': ['Bash(rm:*)']})
        self.assertEqual(Path(json.loads(result.stdout)['backup']).read_bytes(), original)

    def test_dry_run_no_writes(self):
        self.assertEqual(self.install('codex', '--dry-run').returncode, 0)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_remove_preserves_other_hooks(self):
        self.assertEqual(self.install().returncode, 0)
        p = self.root / '.claude/settings.json'
        value = json.loads(p.read_text())
        other = {'matcher': 'Read', 'hooks': [{'type': 'command', 'command': 'other'}]}
        value['hooks']['PreToolUse'].append(other)
        p.write_text(json.dumps(value))
        r = self.install('claude-code', '--remove')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(p.read_text())['hooks']['PreToolUse'], [other])

    def test_invalid_json_not_replaced(self):
        p = self.root / '.codex/hooks.json';p.parent.mkdir();p.write_text('broken')
        self.assertEqual(self.install('codex').returncode, 2)
        self.assertEqual(p.read_text(), 'broken')

    def test_policy_not_overwritten(self):
        self.assertEqual(self.install().returncode, 0)
        p = self.root / '.io-delegation-hooks/policy.json'
        p.write_text(json.dumps({**guard.DEFAULTS, 'mode': 'observe'}))
        self.assertEqual(self.install('cursor').returncode, 0)
        self.assertEqual(json.loads(p.read_text())['mode'], 'observe')

    def test_modified_runtime_not_overwritten(self):
        self.assertEqual(self.install().returncode, 0)
        p = self.root / '.io-delegation-hooks/read_guard.py';p.write_text('# custom')
        self.assertEqual(self.install('cursor').returncode, 2)
        self.assertEqual(p.read_text(), '# custom')

    def test_installed_command_runs_from_other_directory(self):
        r = self.install('cursor');self.assertEqual(r.returncode, 0, r.stderr)
        command = json.loads(r.stdout)['entry']['command']
        (self.root / 'file.py').write_text('x\n' * 1000)
        result = subprocess.run(command, shell=True, cwd=tempfile.gettempdir(),
            input=json.dumps({'tool_name': 'Read', 'tool_input': {'path': 'file.py'}, 'cwd': str(self.root)}),
            text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['permission'], 'deny')

    def test_symlink_config_refused(self):
        with tempfile.TemporaryDirectory() as other:
            try:
                (self.root / '.claude').symlink_to(other, target_is_directory=True)
            except OSError:
                self.skipTest('Symlinks unavailable')
            self.assertEqual(self.install().returncode, 2)
            self.assertEqual(list(Path(other).iterdir()), [])


if __name__ == '__main__':
    unittest.main()
