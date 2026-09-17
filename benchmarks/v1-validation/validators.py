#!/usr/bin/env python3
"""Independent static validators for real-repository dogfood tasks."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import urlparse

TEXT_EXTS={'.py','.js','.jsx','.ts','.tsx','.json','.toml','.yaml','.yml','.md','.html','.css','.scss','.go','.rs','.java','.kt','.rb','.php','.cs','.cpp','.c','.h','.hpp','.sh','.ps1','.sql','.vue','.svelte','.mjs','.cjs'}
SECURITY_PATTERNS={
    'eval':re.compile(r'\beval\s*\('),
    'child_process':re.compile(r"(?:from\s+['\"]child_process['\"]|require\(['\"]child_process['\"]\))"),
    'shell_true':re.compile(r'\bshell\s*:\s*true\b'),
    'node_integration_true':re.compile(r'\bnodeIntegration\s*:\s*true\b'),
    'context_isolation_false':re.compile(r'\bcontextIsolation\s*:\s*false\b'),
    'ipc_main_handle':re.compile(r'\bipcMain\.handle\s*\('),
    'context_bridge':re.compile(r'\bcontextBridge\.exposeInMainWorld\s*\('),
    'ipc_renderer_invoke':re.compile(r'\bipcRenderer\.invoke\s*\('),
}


def git(root,*args):
    cp=subprocess.run(['git','-c',f'safe.directory={Path(root).resolve()}','-C',str(root),*args],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=60)
    if cp.returncode: raise ValueError((cp.stderr or cp.stdout)[-600:])
    return cp.stdout


def tracked(root):
    return [x for x in git(root,'ls-tree','-r','--name-only','HEAD').splitlines() if x]


def read_text(root,rel):
    path=Path(root)/rel
    if path.suffix.lower() not in TEXT_EXTS: return None
    try:return path.read_text(encoding='utf-8-sig')
    except (OSError,UnicodeError):return None


def normalize(value):
    if isinstance(value,dict): return {k:normalize(value[k]) for k in sorted(value)}
    if isinstance(value,list):
        vals=[normalize(x) for x in value]
        return sorted(vals,key=lambda x:json.dumps(x,ensure_ascii=False,sort_keys=True))
    return value


def load_output(path):
    value=json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if not isinstance(value,dict): raise ValueError('output must be a JSON object')
    return value


def extension_counts(root):
    counts=Counter(Path(x).suffix.lower() or '<none>' for x in tracked(root))
    return {'extension_counts':dict(sorted(counts.items())),'tracked_files':sum(counts.values())}


def next_routes(root):
    files=tracked(root)
    pages=sorted(x for x in files if x.startswith('src/app/') and x.endswith('/page.tsx'))
    layouts=sorted(x for x in files if x.startswith('src/app/') and x.endswith('/layout.tsx'))
    handlers=sorted(x for x in files if x.startswith('src/app/') and x.endswith('/route.ts'))
    return {'pages':pages,'layouts':layouts,'route_handlers':handlers,'counts':{'pages':len(pages),'layouts':len(layouts),'route_handlers':len(handlers)}}


def literal_files(root,literal,extensions=None):
    exts=set(extensions or [])
    rows=[]; occurrences=0
    for rel in tracked(root):
        if exts and Path(rel).suffix.lower() not in exts: continue
        text=read_text(root,rel)
        if text is None: continue
        count=text.count(literal)
        if count: rows.append({'path':rel,'occurrences':count}); occurrences+=count
    return {'literal':literal,'files':rows,'file_count':len(rows),'occurrences':occurrences}


def package_fields(root,fields):
    data=json.loads((Path(root)/'package.json').read_text(encoding='utf-8-sig'))
    result={}
    for dotted in fields:
        cur=data
        for part in dotted.split('.'):
            cur=cur[part]
        result[dotted]=cur
    return {'fields':result}


def env_names(root):
    patterns=[re.compile(r'process\.env\.([A-Z][A-Z0-9_]*)'),re.compile(r"process\.env\[['\"]([A-Z][A-Z0-9_]*)['\"]\]")]
    names=set(); files={}
    for rel in tracked(root):
        text=read_text(root,rel)
        if text is None: continue
        found=set()
        for pattern in patterns: found.update(pattern.findall(text))
        if found:
            names.update(found); files[rel]=sorted(found)
    return {'env_names':sorted(names),'files':files}


def security_patterns(root):
    result={name:[] for name in SECURITY_PATTERNS}
    for rel in tracked(root):
        text=read_text(root,rel)
        if text is None: continue
        for name,pattern in SECURITY_PATTERNS.items():
            if pattern.search(text): result[name].append(rel)
    return {'patterns':{name:{'present':bool(paths),'files':sorted(paths)} for name,paths in sorted(result.items())}}


def html_seo(root,prefix=''):
    files=[x for x in tracked(root) if x.endswith('.html') and (not prefix or x.startswith(prefix))]
    miss={k:[] for k in ('title','h1','canonical','description')}; hosts=Counter()
    rx={
      'title':re.compile(r'<title\b[^>]*>.*?</title>',re.I|re.S),
      'h1':re.compile(r'<h1\b[^>]*>.*?</h1>',re.I|re.S),
      'canonical':re.compile(r'<link\b[^>]*rel=["\']canonical["\'][^>]*href=["\']([^"\']+)',re.I),
      'description':re.compile(r'<meta\b[^>]*name=["\']description["\'][^>]*content=["\'][^"\']*',re.I),
    }
    for rel in files:
        text=read_text(root,rel) or ''
        if not rx['title'].search(text): miss['title'].append(rel)
        if not rx['h1'].search(text): miss['h1'].append(rel)
        canon=rx['canonical'].search(text)
        if not canon: miss['canonical'].append(rel)
        else:
            host=urlparse(canon.group(1)).netloc
            if host: hosts[host]+=1
        if not rx['description'].search(text): miss['description'].append(rel)
    return {'html_files':len(files),'missing':{k:sorted(v) for k,v in miss.items()},'canonical_hosts':dict(sorted(hosts.items()))}


def top_directories(root):
    counts=Counter((x.split('/',1)[0] if '/' in x else '<root>') for x in tracked(root))
    return {'top_directories':[{'name':k,'files':v} for k,v in sorted(counts.items(),key=lambda kv:(-kv[1],kv[0]))]}


def expected(args):
    root=Path(args.root).resolve(strict=True)
    if args.mode=='extension-counts': return extension_counts(root)
    if args.mode=='next-routes': return next_routes(root)
    if args.mode=='literal-files': return literal_files(root,args.literal,args.ext)
    if args.mode=='package-fields': return package_fields(root,args.field)
    if args.mode=='env-names': return env_names(root)
    if args.mode=='security-patterns': return security_patterns(root)
    if args.mode=='html-seo': return html_seo(root,args.prefix or '')
    if args.mode=='top-directories': return top_directories(root)
    raise ValueError('unsupported mode')


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',required=True); p.add_argument('--output',required=True)
    p.add_argument('--mode',required=True,choices=['extension-counts','next-routes','literal-files','package-fields','env-names','security-patterns','html-seo','top-directories'])
    p.add_argument('--literal'); p.add_argument('--ext',action='append'); p.add_argument('--field',action='append',default=[]); p.add_argument('--prefix')
    a=p.parse_args(argv)
    if a.mode=='literal-files' and not a.literal: p.error('--literal required')
    if a.mode=='package-fields' and not a.field: p.error('--field required')
    got=load_output(a.output); gold=expected(a)
    if normalize(got)!=normalize(gold):
        print(json.dumps({'expected':gold,'actual':got},ensure_ascii=False,indent=2)); return 4
    return 0

if __name__=='__main__': raise SystemExit(main())
