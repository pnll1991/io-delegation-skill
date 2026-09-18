import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

BASE=Path(__file__).resolve().parents[1]/'benchmarks/v1-validation'
record_spec=importlib.util.spec_from_file_location('record',BASE/'record.py')
record=importlib.util.module_from_spec(record_spec); record_spec.loader.exec_module(record); sys.modules['record']=record
spec=importlib.util.spec_from_file_location('worker_ab',BASE/'worker_ab.py')
mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

class WorkerABTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); (self.root/'src').mkdir()
        (self.root/'src/a.py').write_text('alpha=1\nneedle=42\nomega=3\n',encoding='utf-8')
        subprocess.run(['git','init'],cwd=self.root,check=True,capture_output=True)
        subprocess.run(['git','add','.'],cwd=self.root,check=True,capture_output=True)
        subprocess.run(['git','-c','user.name=x','-c','user.email=x@y','commit','-m','x'],cwd=self.root,check=True,capture_output=True)
        self.task={
            'id':'t','repo':'r','question':'What is needle?','prompt':'Return {"needle":42}.','output':'benchmark-output/x.json',
            'allow_prefixes':['src'],'selections':[{'path':'src/a.py','select':{'kind':'literal','needle':'needle','window':8}}],
            'validator':[['python','-c','pass']],
        }

    def test_selection_bundle_is_deterministic(self):
        _,first=mod.selection_bundle(self.root,self.task)
        _,second=mod.selection_bundle(self.root,self.task)
        self.assertEqual(mod.selection_digest(first),mod.selection_digest(second))
        self.assertEqual(mod.direct_payload(first)['fragments'],mod.direct_payload(second)['fragments'])
        self.assertGreater(first['selected_bytes'],0)

    def test_manifest_has_exact_two_causal_arms_in_plan(self):
        data={'version':1,'repositories':{'r':{'path':str(self.root),'commit':'HEAD'}},'worker_config':'x','tasks':[self.task],'repetitions':2}
        mod.validate_manifest(data)
        rows=mod.plan(data,seed=1)
        self.assertEqual(len(rows),4)
        self.assertEqual({arm for _,arm,_ in rows},set(mod.ARMS))

    def test_duplicate_task_rejected(self):
        data={'version':1,'repositories':{'r':{'path':str(self.root),'commit':'HEAD'}},'worker_config':'x','tasks':[self.task,self.task]}
        with self.assertRaisesRegex(ValueError,'unique task ids'):
            mod.validate_manifest(data)

    def test_safe_output_rejects_escape(self):
        with self.assertRaisesRegex(ValueError,'unsafe'):
            mod.safe_output(self.root,'../x.json',{'x':1})

    def test_principal_command_disables_tools_and_multi_agent(self):
        cmd=mod.principal_command('codex',self.root,'gpt-5.6-luna','medium')
        text=' '.join(str(x) for x in cmd)
        self.assertIn('features.shell_tool=false',text)
        self.assertIn('features.multi_agent=false',text)
        self.assertIn('--sandbox read-only',text)
        self.assertIn('--ignore-user-config',text)

if __name__=='__main__': unittest.main()
