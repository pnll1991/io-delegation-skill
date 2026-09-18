from __future__ import annotations
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
SKILL = REPO / 'skills/io-delegation'


class PackageTests(unittest.TestCase):
    def test_frontmatter_and_size(self):
        text = (SKILL / 'SKILL.md').read_text(encoding='utf-8')
        self.assertTrue(text.startswith('---\n'))
        front = text.split('---', 2)[1]
        name = re.search(r'^name: (.+)$', front, re.M).group(1)
        description = re.search(r'^description: (.+)$', front, re.M).group(1)
        self.assertEqual(name, SKILL.name)
        self.assertRegex(name, r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
        self.assertLessEqual(len(name), 64)
        self.assertTrue(0 < len(description) <= 1024)
        self.assertLess(len(text.splitlines()), 500)
        self.assertNotIn('allowed-tools:', front)
        self.assertNotIn('context: fork', front)
        self.assertNotIn('${CLAUDE_', text)

    def test_skill_relative_links_exist(self):
        text = (SKILL / 'SKILL.md').read_text(encoding='utf-8')
        for link in re.findall(r'\]\(([^)]+)\)', text):
            if not link.startswith(('https://', 'http://', '#')):
                self.assertTrue((SKILL / link).is_file(), link)

    def test_all_local_markdown_links_exist(self):
        for file in REPO.rglob('*.md'):
            if 'node_modules' in file.parts:
                continue
            for link in re.findall(r'\]\(([^)]+)\)', file.read_text(encoding='utf-8')):
                if not link.startswith(('https://', 'http://', '#', 'mailto:')):
                    target = link.split('#')[0]
                    if target:
                        self.assertTrue((file.parent / target).exists(), f'{file}: {link}')

    def test_claude_compaction_manifest_matches_package_version(self):
        base = REPO / 'integrations/claude-code/jev-compaction'
        package = json.loads((base / 'package.json').read_text(encoding='utf-8'))
        plugin = json.loads((base / '.claude-plugin/plugin.json').read_text(encoding='utf-8'))
        self.assertEqual(plugin['version'], package['version'])

    def test_examples_are_not_approved(self):
        for file in (SKILL / 'assets').glob('worker.*.example.json'):
            self.assertFalse(json.loads(file.read_text())['approved'])

    def test_standalone_skill_has_license_and_resources(self):
        for relative in ['LICENSE', 'scripts/io_delegate.py', 'references/ADAPTERS.md',
                         'references/PLAYBOOK.md', 'references/SOURCES.md',
                         'references/VALIDATION.md']:
            self.assertTrue((SKILL / relative).is_file())

    def run_install(self, *args):
        return subprocess.run([sys.executable, str(REPO / 'install.py'), *args],
                              capture_output=True, text=True, encoding='utf-8', check=False)

    def test_install_each_agent_to_expected_directory(self):
        for agent, directory in [('claude-code', '.claude'), ('codex', '.agents'), ('cursor', '.agents')]:
            with self.subTest(agent=agent), tempfile.TemporaryDirectory() as temp:
                result = self.run_install('--agent', agent, '--project', temp)
                self.assertEqual(result.returncode, 0, result.stderr)
                destination = Path(temp) / directory / 'skills/io-delegation'
                self.assertEqual((destination / 'SKILL.md').read_bytes(), (SKILL / 'SKILL.md').read_bytes())
                self.assertTrue((destination / 'scripts/io_delegate.py').exists())

    def test_install_dry_run(self):
        with tempfile.TemporaryDirectory() as temp:
            result = self.run_install('--agent', 'codex', '--project', temp, '--dry-run')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((Path(temp) / '.agents').exists())

    def test_install_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            first = self.run_install('--agent', 'codex', '--project', temp)
            second = self.run_install('--agent', 'cursor', '--project', temp)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 2)

    def test_installed_script_is_self_contained(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'sample.py').write_text('x = 1\n')
            installed = self.run_install('--agent', 'codex', '--project', temp)
            self.assertEqual(installed.returncode, 0, installed.stderr)
            script = root / '.agents/skills/io-delegation/scripts/io_delegate.py'
            result = subprocess.run([sys.executable, str(script), 'inspect', '--root', temp,
                                      '--paths', 'sample.py'], capture_output=True,
                                      text=True, encoding='utf-8', check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['files'][0]['lines'], 1)
