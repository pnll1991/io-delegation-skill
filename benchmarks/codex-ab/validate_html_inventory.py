#!/usr/bin/env python3
"""Independent HTML inventory/unchanged-source validator. UTF-8/BOM portable."""
from __future__ import annotations
import argparse
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import subprocess
import sys


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_title=False; self.in_h1=False; self.seen=False
        self.t=[]; self.h=[]
    def handle_starttag(self,tag,attrs):
        if tag=='title': self.in_title=True
        elif tag=='h1' and not self.seen: self.in_h1=True; self.seen=True
        elif tag=='br' and self.in_h1: self.h.append(' ')
    def handle_startendtag(self,tag,attrs):
        if tag=='br' and self.in_h1: self.h.append(' ')
    def handle_endtag(self,tag):
        if tag=='title': self.in_title=False
        elif tag=='h1': self.in_h1=False
    def handle_data(self,data):
        if self.in_title: self.t.append(data)
        if self.in_h1: self.h.append(data)
    @staticmethod
    def clean(parts):
        # HTMLParser already decoded entities once. Never decode twice.
        return re.sub(r'\s+',' ',''.join(parts)).strip()
    @property
    def title(self): return self.clean(self.t)
    @property
    def h1(self): return self.clean(self.h)


def inventory(root,pattern):
    result=[]
    for path in sorted(root.glob(pattern),key=lambda x:x.as_posix()):
        if not path.is_file(): continue
        p=PageParser(); p.feed(path.read_text(encoding='utf-8-sig')); p.close()
        result.append(dict(path=path.relative_to(root).as_posix(),title=p.title,h1=p.h1))
    if not result: raise ValueError('No source pages matched; empty inventory is not a passing task')
    return result


def normalize(rows):
    if not isinstance(rows,list): raise ValueError('Expected JSON array')
    seen=set(); result={}
    for row in rows:
        if not isinstance(row,dict) or set(row)!= {'path','title','h1'}:
            raise ValueError('Each entry needs exactly path/title/h1')
        if not all(isinstance(v,str) for v in row.values()): raise ValueError('Values must be strings')
        if row['path'] in seen: raise ValueError('Duplicate path')
        seen.add(row['path']); result[row['path']]=row
    return result


def differences(actual, expected):
    a,b=normalize(actual),normalize(expected)
    errors=[]
    for path in sorted(a.keys()|b.keys()):
        if path not in a: errors.append(dict(path=path,issue='missing'))
        elif path not in b: errors.append(dict(path=path,issue='unexpected'))
        else:
            for key in ('title','h1'):
                if a[path][key]!=b[path][key]:
                    errors.append(dict(path=path,field=key,expected=b[path][key],actual=a[path][key]))
    return errors


def regression(root, output):
    cp=subprocess.run(['git','status','--porcelain=v1','--untracked-files=all'],cwd=root,
                      capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=30)
    if cp.returncode: raise ValueError('git status failed')
    lines=cp.stdout.splitlines()
    return lines == ['?? '+output.replace('\\','/')],lines


def main(argv=None):
    ap=argparse.ArgumentParser(); ap.add_argument('--root',default='.'); ap.add_argument('--glob',default='herramientas/**/index.html')
    ap.add_argument('--output',default='benchmark-output/html-inventory.json'); ap.add_argument('--regression',action='store_true')
    args=ap.parse_args(argv); root=Path(args.root).resolve()
    try:
        if args.regression:
            ok,status=regression(root,args.output)
            print(json.dumps(dict(ok=ok,git_status=status),ensure_ascii=True)); return 0 if ok else 1
        actual=json.loads((root/args.output).read_text(encoding='utf-8-sig'))
        expected=inventory(root,args.glob); errors=differences(actual,expected)
        # Return only mismatches, not two huge inventories that hide the true error.
        print(json.dumps(dict(ok=not errors,entries=len(expected),mismatch_count=len(errors),
                              mismatches=errors[:8]),ensure_ascii=True))
        return 1 if errors else 0
    except (ValueError,OSError) as exc:
        print(json.dumps(dict(ok=False,error=str(exc)),ensure_ascii=True),file=sys.stderr); return 2


if __name__=='__main__': raise SystemExit(main())
