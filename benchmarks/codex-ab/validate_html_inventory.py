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
        self._in_title = False
        self._in_first_h1 = False
        self._seen_h1 = False
        self._title: list[str] = []
        self._h1: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag == "title":
            self._in_title = True
        elif tag == "h1" and not self._seen_h1:
            self._in_first_h1 = True
            self._seen_h1 = True
        elif tag == "br" and self._in_first_h1:
            self._h1.append(" ")

    def handle_startendtag(self, tag: str, attrs) -> None:
        if tag.lower() == "br" and self._in_first_h1:
            self._h1.append(" ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
        elif tag == "h1" and self._in_first_h1:
            self._in_first_h1 = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title.append(data)
        if self._in_first_h1:
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
    for path in root.glob(pattern):
        if not path.is_file():
            continue
        parser = PageParser()
        parser.feed(path.read_text(encoding="utf-8", errors="strict"))
        rows.append({
            "path": path.relative_to(root).as_posix(),
            "title": parser.title,
            "h1": parser.h1,
        })
    return sorted(rows, key=lambda row: row["path"])


def canonical_actual(value) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise ValueError("output must be a JSON array")
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, row in enumerate(value):
        if not isinstance(row, dict):
            raise ValueError(f"entry {index} must be an object")
        if set(row) != {"path", "title", "h1"}:
            raise ValueError(f"entry {index} must contain exactly path, title and h1")
        if not all(isinstance(row[key], str) for key in ("path", "title", "h1")):
            raise ValueError(f"entry {index} fields must be strings")
        if row["path"] in seen:
            raise ValueError(f"duplicate path: {row['path']}")
        seen.add(row["path"])
        rows.append(row)
    return sorted(rows, key=lambda row: row["path"])


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
        raw_actual = json.loads(output.read_text(encoding="utf-8-sig"))
        actual = canonical_actual(raw_actual)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
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
