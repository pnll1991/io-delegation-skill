import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT=Path(__file__).resolve().parents[1]/'benchmarks/v1-validation/validators.py'
spec=importlib.util.spec_from_file_location('v1_validators',SCRIPT)
mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

class ValidatorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        subprocess.run(['git','init'],cwd=self.root,check=True,capture_output=True)
        (self.root/'src/app/a').mkdir(parents=True)
        (self.root/'src/app/a/page.tsx').write_text("const x=process.env.NEXT_PUBLIC_X; ipcRenderer.invoke('go')\n",encoding='utf-8')
        (self.root/'src/app/layout.tsx').write_text('export default 1\n',encoding='utf-8')
        (self.root/'index.html').write_text('<title>x</title><h1>y</h1><link rel="canonical" href="https://example.com/"><meta name="description" content="z">',encoding='utf-8')
        (self.root/'package.json').write_text(json.dumps({'scripts':{'build':'x'},'dependencies':{'next':'1'}}),encoding='utf-8')
        subprocess.run(['git','add','.'],cwd=self.root,check=True,capture_output=True)
        subprocess.run(['git','-c','user.name=x','-c','user.email=x@y','commit','-m','x'],cwd=self.root,check=True,capture_output=True)

    def test_next_routes_and_env(self):
        routes=mod.next_routes(self.root)
        self.assertEqual(routes['counts']['pages'],1); self.assertEqual(routes['counts']['layouts'],1)
        env=mod.env_names(self.root); self.assertEqual(env['env_names'],['NEXT_PUBLIC_X'])

    def test_html_seo_and_security(self):
        seo=mod.html_seo(self.root); self.assertEqual(seo['html_files'],1); self.assertEqual(seo['missing']['canonical'],[])
        sec=mod.security_patterns(self.root); self.assertTrue(sec['patterns']['ipc_renderer_invoke']['present'])

    def test_literal_package_and_counts(self):
        lit=mod.literal_files(self.root,'process.env',['.tsx']); self.assertEqual(lit['occurrences'],1)
        pkg=mod.package_fields(self.root,['scripts.build']); self.assertEqual(pkg['fields']['scripts.build'],'x')
        counts=mod.extension_counts(self.root); self.assertEqual(counts['tracked_files'],4)

    def test_normalization_makes_set_like_lists_order_independent(self):
        self.assertEqual(mod.normalize({'x':['b','a']}),mod.normalize({'x':['a','b']}))

if __name__=='__main__': unittest.main()
