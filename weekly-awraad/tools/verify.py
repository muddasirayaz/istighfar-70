#!/usr/bin/env python3
"""Fidelity gate for content/pages/*.json.

1. Latin text: the multiset of non-whitespace Latin characters on each page must equal the original PDF's
   (requires pdfplumber). The PDF is the authority; nothing may be added, dropped or retyped.
2. Schema: only the documented block types and fields; balanced <em> markup.
3. Arabic encoding: Arabic fields contain Arabic only, use base letters (no presentation forms except ﷺ ﷻ),
   carry vowel marks, and use the source's Extended Arabic-Indic digits.
"""
import collections
import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / 'content' / 'pages'
PDF = ROOT / 'Weekly-Awraad-Original.pdf'
ARABIC_FONTS = ('Al_Mushaf', 'noorehidayat', 'AlQalamQuranMajeed', '_PDMS_Saleem_QuranFont', 'DiwanMishafiGold', 'CambriaMath')
ARABIC_RANGES = ((0x0600, 0x06FF), (0x0750, 0x077F), (0x08A0, 0x08FF), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF))
HARAKAT = set('ًٌٍَُِّْٰٖٗ')
ALLOWED_SYMBOLS = set('ﷺﷻ')
SCHEMA = {
    'title': ({'ar', 'en'}, set()),
    'heading': ({'en', 'ar'}, {'en'}),
    'passage': ({'kind', 'ar', 'tr', 'en', 'review'}, {'kind'}),
    'names': ({'items'}, {'items'}),
    'couplet': ({'ar', 'tr', 'en', 'review'}, set()),
    'note': ({'en'}, {'en'}),
    'image': ({'src'}, {'src'}),
}
IMAGES = {'dome', 'emblem', 'artwork-p12', 'artwork-p139'}
TAG = re.compile(r'</?em>')


def is_arabic(ch):
    o = ord(ch)
    return any(a <= o <= b for a, b in ARABIC_RANGES)


def latin_multiset(text):
    text = unicodedata.normalize('NFKC', text)
    return collections.Counter(ch for ch in text if not ch.isspace() and not is_arabic(ch))


def latin_strings(node, key=None):
    if isinstance(node, dict):
        for k, v in node.items():
            if k not in ('ar', 'type', 'kind', 'src', 'review'):
                yield from latin_strings(v, k)
    elif isinstance(node, list):
        for v in node:
            yield from latin_strings(v, key)
    elif isinstance(node, str):
        yield TAG.sub('', node)


def arabic_strings(node):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == 'ar':
                yield from ([v] if isinstance(v, str) else v)
            elif k != 'review':
                yield from arabic_strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from arabic_strings(v)


def check_schema(page, blocks, problems):
    for i, b in enumerate(blocks):
        where = 'p%03d block %d' % (page, i)
        allowed, required = SCHEMA.get(b.get('type'), (None, None))
        if allowed is None:
            problems.append('%s: unknown type %r' % (where, b.get('type')))
            continue
        fields = set(b) - {'type'}
        if fields - allowed:
            problems.append('%s: unexpected fields %s' % (where, sorted(fields - allowed)))
        if required - fields:
            problems.append('%s: missing fields %s' % (where, sorted(required - fields)))
        if b['type'] == 'passage' and b.get('kind') not in ('quran', 'prayer'):
            problems.append('%s: kind must be quran or prayer' % where)
        if b['type'] == 'image' and b.get('src') not in IMAGES:
            problems.append('%s: unknown image %r' % (where, b.get('src')))
        if b['type'] == 'couplet' and len(b.get('ar', [])) not in (0, 2):
            problems.append('%s: couplet needs two Arabic hemistichs' % where)
        for s in list(latin_strings(b)) + list(arabic_strings(b)):
            pass
        raw = json.dumps(b, ensure_ascii=False)
        if raw.count('<em>') != raw.count('</em>') or re.search(r'<(?!/?em>)', raw):
            problems.append('%s: unbalanced or unsupported markup' % where)


def check_arabic(page, blocks, problems):
    for s in arabic_strings(blocks):
        text = TAG.sub('', s)
        where = 'p%03d Arabic %r' % (page, text[:40])
        latin = [ch for ch in text if ch.isalpha() and not is_arabic(ch)]
        if latin:
            problems.append('%s: Latin letters in Arabic field: %s' % (where, ''.join(latin)))
        forms = [ch for ch in text if 0xFB50 <= ord(ch) <= 0xFEFF and ch not in ALLOWED_SYMBOLS]
        if forms:
            problems.append('%s: presentation-form characters %s' % (where, ' '.join('U+%04X' % ord(c) for c in forms)))
        if re.search('[٠-٩]', text):
            problems.append('%s: Arabic-Indic digits; the source uses U+06F0–06F9' % where)
        letters = sum(1 for ch in text if unicodedata.category(ch) == 'Lo')
        marks = sum(1 for ch in text if ch in HARAKAT)
        if letters >= 6 and marks < letters * 0.35:
            problems.append('%s: only %d vowel marks for %d letters' % (where, marks, letters))


def main():
    files = sorted(CONTENT.glob('p*.json'))
    problems = []
    pages = {}
    for f in files:
        data = json.loads(f.read_text(encoding='utf-8'))
        if f.name != 'p%03d.json' % data['page']:
            problems.append('%s: page number %d does not match file name' % (f.name, data['page']))
        pages[data['page']] = data['blocks']
        check_schema(data['page'], data['blocks'], problems)
        check_arabic(data['page'], data['blocks'], problems)
    missing = sorted(set(range(1, 141)) - set(pages))
    if missing:
        problems.append('missing pages: %s' % missing)

    try:
        import pdfplumber
    except ImportError:
        print('pdfplumber not installed: Latin fidelity check skipped (pip install pdfplumber)')
        pdfplumber = None
    if pdfplumber:
        with pdfplumber.open(PDF) as pdf:
            for n, blocks in sorted(pages.items()):
                page = pdf.pages[n - 1]
                want = latin_multiset(''.join(
                    c['text'] for c in page.chars
                    if not c['fontname'].split('+')[-1].startswith(ARABIC_FONTS) and not (c['x0'] < 175 and c['top'] > 465)))
                got = latin_multiset(''.join(latin_strings(blocks)))
                if want != got:
                    problems.append('p%03d Latin text differs from PDF: missing %s, extra %s' % (n, dict(want - got), dict(got - want)))

    for p in problems:
        print(p)
    print('%d pages checked, %d problems' % (len(pages), len(problems)))
    sys.exit(1 if problems else 0)


if __name__ == '__main__':
    main()
