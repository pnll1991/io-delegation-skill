#!/usr/bin/env python3
"""Validate an HTML title/H1 inventory without trusting the agent's implementation."""
from __future__ import annotations

import argparse
from html.parser import HTMLParser
import html
import json
from pathlib import Path
import re
import sys


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._title_depth = 0
        self._h1_depth = 0
        self._title: list[str] = []
        self._h1: list[str] = []
        self._seen_h1 = False

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag == "title":
            self._title_depth += 1
        elif tag == "h1" and not self._seen_h1:
            self._h1_depth += 1
            self._seen_h1 = True
        elif self._h1_depth:
            self._h1_depth += 1

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title" and self._title_depth:
            self._title_depth -= 1
        elif self._h1_depth:
            self._h1_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._title_depth:
            self._title.append(data)
        if self._h1_depth:
            self._h1.append(data)

    @staticmethod
    def clean(parts: list[str]) -> str:
        return re.sub(r"\s+", " ", html.unescape("".join(parts))).strip()

    @property
    def title(self) -> str:
        return self.clean(self._title)

    @property
    def h1(self) -> str:
        return self.clean(self._h1)


def inventory(root: Path, pattern: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path in sorted(root.glob(pattern), key=lambda p: p.as_posix()):
        if not path.is_file():
            continue
        parser = PageParser()
        parser.feed(path.read_text(encoding="utf-8", errors="strict"))
        rows.append({
            "path": path.relative_to(root).as_posix(),
            "title": parser.title,
            "h1": parser.h1,
        })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--glob", default="herramientas/**/index.html")
    ap.add_argument("--output", default="benchmark-output/html-inventory.json")
    args = ap.parse_args()
    root = Path(args.root).resolve()
    output = root / args.output
    if not output.is_file():
        print(f"missing output: {args.output}", file=sys.stderr)
        return 2
    try:
        actual = json.loads(output.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"invalid output: {exc}", file=sys.stderr)
        return 2
    expected = inventory(root, args.glob)
    if actual != expected:
        print(json.dumps({"expected": expected, "actual": actual}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "entries": len(expected)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
