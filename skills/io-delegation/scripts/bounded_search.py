#!/usr/bin/env python3
"""Literal search over explicitly selected files; never emit a whole minified line."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from io_delegate import snapshots, DelegateError


def search(root, paths, needle, max_bytes=6000, max_matches=20):
    if not needle or len(needle)>500: raise ValueError('needle must be 1..500 characters')
    if not 500<=max_bytes<=64000 or not 1<=max_matches<=100: raise ValueError('invalid output limits')
    rows=[]; total=0
    for file in snapshots(root,paths):
        text=file['content']; start=0
        while True:
            at=text.find(needle,start)
            if at<0: break
            total+=1; start=at+len(needle)
            if len(rows)>=max_matches: continue
            row=dict(path=file['path'],line=text.count('\n',0,at)+1,
                     excerpt=text[max(0,at-60):at+len(needle)+60],sha256=file['sha256'])
            trial=dict(matches=rows+[row],match_count=total,omitted=0)
            if len(json.dumps(trial,ensure_ascii=True).encode())<=max_bytes-200: rows.append(row)
    return dict(matches=rows,match_count=total,omitted=total-len(rows))


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--root',default='.'); p.add_argument('--paths',nargs='+',required=True)
    p.add_argument('--text',required=True); p.add_argument('--max-bytes',type=int,default=6000); p.add_argument('--max-matches',type=int,default=20)
    a=p.parse_args()
    try: print(json.dumps(search(Path(a.root).resolve(),a.paths,a.text,a.max_bytes,a.max_matches),ensure_ascii=True)); return 0
    except (OSError,ValueError,DelegateError) as e: print(str(e),file=sys.stderr); return 2


if __name__=='__main__': raise SystemExit(main())
