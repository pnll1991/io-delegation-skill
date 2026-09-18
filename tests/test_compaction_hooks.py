import json
import unittest

import install_compaction_hooks as installer


class CompactionHookInstallerTests(unittest.TestCase):
    def test_codex_entries_cover_capture_compact_and_rehydrate(self):
        entries=dict(installer.entries_for('codex','python bridge.py --host codex'))
        self.assertIn('UserPromptSubmit',entries)
        self.assertIn('PreToolUse',entries)
        self.assertIn('PostToolUse',entries)
        self.assertIn('PreCompact',entries)
        self.assertIn('SessionStart',entries)
        self.assertEqual(entries['SessionStart']['matcher'],'compact')
        handler=entries['SessionStart']['hooks'][0]
        self.assertEqual(handler['additionalContextLimit'],6500)

    def test_cursor_entries_have_bounded_stop_fallback(self):
        entries=dict(installer.entries_for('cursor','python bridge.py --host cursor'))
        self.assertIn('preCompact',entries)
        self.assertIn('postToolUse',entries)
        self.assertIn('postToolUseFailure',entries)
        self.assertIn('stop',entries)
        self.assertEqual(entries['stop']['loop_limit'],1)

    def test_merge_is_idempotent_and_preserves_other_hooks(self):
        existing={'version':1,'hooks':{'preToolUse':[{'command':'other'}]}}
        entries=installer.entries_for('cursor','python bridge.py --host cursor')
        once=installer.merge_entries(existing,'cursor',entries)
        twice=installer.merge_entries(once,'cursor',entries)
        self.assertEqual(once,twice)
        self.assertIn({'command':'other'},twice['hooks']['preToolUse'])
        removed=installer.merge_entries(twice,'cursor',entries,remove=True)
        self.assertEqual(removed['hooks']['preToolUse'],[{'command':'other'}])

    def test_codex_schema_does_not_require_cursor_version(self):
        merged=installer.merge_entries({},'codex',installer.entries_for('codex','python bridge.py --host codex'))
        self.assertIn('hooks',merged)
        self.assertNotIn('version',merged)


if __name__=='__main__':
    unittest.main()
