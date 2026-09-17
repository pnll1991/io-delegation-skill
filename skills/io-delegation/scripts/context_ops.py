"""Deterministic static projections. Never run scripts, resolve network URLs or infer."""
from __future__ import annotations
from html.parser import HTMLParser
import json
import re

from context_sources import encoded, exact_keys, plain_int, source_table

VERSION = 'context-projections/1'
HTML_FIELDS = ('title', 'h1', 'canonical', 'description')


def strings(value, limit=16):
    if (not isinstance(value, list) or not 1 <= len(value) <= limit or
            any(not isinstance(x, str) or len(x) > 500 for x in value) or len(set(value)) != len(value)):
        raise ValueError('Expected a bounded list of unique strings')
    return value


class StaticHTML(HTMLParser):
    """Source text, not browser visibility. First occurrence; duplicates are declared."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.values = dict.fromkeys(HTML_FIELDS)
        self.counts = dict.fromkeys(HTML_FIELDS, 0)
        self.active = None
        self.parts = []
        self.suppressed = []

    def handle_starttag(self, tag, attrs):
        attrs_dict = {}
        for k, v in attrs:
            if k not in attrs_dict:
                attrs_dict[k] = v
        if tag in ('title', 'h1'):
            self.counts[tag] += 1
            if self.counts[tag] == 1 and self.active is None:
                self.active, self.parts = tag, []
        if self.active and tag in ('script', 'style', 'template'):
            self.suppressed.append(tag)
        if tag == 'br' and self.active and not self.suppressed:
            self.parts.append(' ')
        if tag == 'meta' and (attrs_dict.get('name') or '').lower() == 'description':
            self._attr('description', attrs_dict.get('content'))
        if tag == 'link' and 'canonical' in (attrs_dict.get('rel') or '').lower().split():
            self._attr('canonical', attrs_dict.get('href'))

    def _attr(self, key, value):
        self.counts[key] += 1
        if self.counts[key] == 1:
            self.values[key] = value

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag in ('script', 'style', 'template', 'title', 'h1'):
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.suppressed and tag == self.suppressed[-1]:
            self.suppressed.pop()
        if self.active == tag:
            self.values[tag] = ' '.join(''.join(self.parts).split())
            self.active, self.parts = None, []

    def handle_data(self, data):
        if self.active and not self.suppressed:
            self.parts.append(data)


def strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate JSON key')
            result[key] = value
        return result
    def bad_constant(_):
        raise ValueError('Non-finite JSON number')
    return json.loads(text, object_pairs_hook=pairs, parse_constant=bad_constant)


def pointer(value, path):
    if path == '':
        return True, value
    if not path.startswith('/') or re.search(r'~(?![01])', path):
        raise ValueError('Invalid JSON pointer')
    current = value
    for part in path[1:].split('/'):
        part = part.replace('~1', '/').replace('~0', '~')
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and re.fullmatch('0|[1-9][0-9]*', part) and int(part) < len(current):
            current = current[int(part)]
        else:
            return False, None
    return True, current


def project(text, projection):
    if not isinstance(projection, dict):
        raise ValueError('Projection must be an object')
    kind = projection.get('kind')
    if kind == 'html':
        exact_keys(projection, ('kind', 'fields'))
        fields = strings(projection['fields'], 4)
        if set(fields)-set(HTML_FIELDS):
            raise ValueError('Unknown static HTML field')
        parser = StaticHTML(); parser.feed(text); parser.close()
        missing = [f for f in fields if parser.values[f] is None]
        return dict(values={f: parser.values[f] for f in fields}, missing=missing,
                    duplicates={f: parser.counts[f] for f in fields if parser.counts[f] > 1},
                    scope='static-html-source-not-rendered-dom')
    if kind == 'json':
        exact_keys(projection, ('kind', 'pointers'))
        pointers = strings(projection['pointers'])
        value = strict_json(text)
        found = {p: pointer(value, p) for p in pointers}
        return dict(values={p: pair[1] for p, pair in found.items()},
                    missing=[p for p, pair in found.items() if not pair[0]], scope='json-document')
    if kind in ('lines', 'span'):
        exact_keys(projection, ('kind', 'start', 'end'))
        if kind == 'lines':
            rows = text.splitlines(keepends=True)
            start = plain_int(projection['start'], 1, max(1, len(rows)), 'start')
            end = plain_int(projection['end'], start, len(rows), 'end')
            lo, hi = sum(map(len, rows[:start-1])), sum(map(len, rows[:end]))
        else:
            lo = plain_int(projection['start'], 0, len(text), 'start')
            hi = plain_int(projection['end'], lo+1, len(text), 'end')
        return dict(text=text[lo:hi], start=lo, end=hi, scope='selected-region',
                    offset_unit='unicode-codepoints-in-lf-normalized-source', omitted_chars=len(text)-(hi-lo))
    raise ValueError('Unsupported projection kind')


def extract(sources, projection):
    data = []
    for i, source in enumerate(sources):
        data.append(dict(source=f's{i}', **project(source['content'], projection)))
    partial = any(x.get('missing') or x.get('omitted_chars', 0) for x in data)
    return dict(status='partial' if partial else 'ok', data=data, sources=source_table(sources),
                coverage=dict(files=len(sources), source_bytes=sum(f['bytes'] for f in sources),
                              scope='explicit-permitted-files'), model_calls=0)


def search(sources, needle=None, max_matches=20, window=96):
    plain_int(max_matches, 1, 100, 'max_matches'); plain_int(window, 0, 256, 'window')
    if needle is not None and (not isinstance(needle, str) or not needle or len(needle) > 500):
        raise ValueError('needle must be 1..500 characters')
    rows, count = [], 0
    for i, source in enumerate(sources):
        text = source['content']
        if needle is None:
            count += 1
            if len(rows) < max_matches:
                rows.append(dict(source=f's{i}'))
            continue
        offset = 0
        while True:
            at = text.find(needle, offset)
            if at < 0:
                break
            count += 1; offset = at+len(needle)
            if len(rows) < max_matches:
                lo, hi = max(0, at-window), min(len(text), offset+window)
                rows.append(dict(source=f's{i}', start=lo, end=hi, match_start=at,
                                 line=text.count('\n', 0, at)+1, excerpt=text[lo:hi]))
    return dict(status='partial' if count > len(rows) else 'ok', matches=rows, match_count=count,
                omitted=count-len(rows), sources=source_table(sources), model_calls=0,
                coverage=dict(scope='explicit-permitted-files-only', files=len(sources),
                              exhaustive_match_count=True, offset_unit='unicode-codepoints-in-lf-normalized-source'))


def extract_many(sources, projections):
    """Group compatible local projections without repeated source tables/model rounds."""
    if not isinstance(projections, list) or not 1 <= len(projections) <= 8:
        raise ValueError('Use 1..8 projections per batch')
    signatures = [encoded(p) for p in projections]
    if len(signatures) != len(set(signatures)):
        raise ValueError('Duplicate projections are not useful')
    data, partial = [], False
    for j, projection in enumerate(projections):
        rows = []
        for i, source in enumerate(sources):
            item = dict(source=f's{i}', **project(source['content'], projection))
            partial = partial or bool(item.get('missing') or item.get('omitted_chars', 0))
            rows.append(item)
        data.append(dict(projection=j, rows=rows))
    return dict(status='partial' if partial else 'ok', data=data, sources=source_table(sources),
                coverage=dict(files=len(sources), projections=len(projections),
                              scope='explicit-permitted-files', source_bytes=sum(f['bytes'] for f in sources)),
                model_calls=0)
