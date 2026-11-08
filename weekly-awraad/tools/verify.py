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
ARABIC_FONTS = ('Al_Mushaf', 'noorehidayat', 'AlQalamQuranMajeed', '_PDMS_Saleem_QuranFont', 'DiwanMishafiGold')
ARABIC_RANGES = ((0x0600, 0x06FF), (0x0750, 0x077F), (0x08A0, 0x08FF), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF))
HARAKAT = set('ًٌٍَُِّْٰٖٗ')
ALLOWED_SYMBOLS = set('ﷺﷻ')
# Every block may also carry `review`: a transcriber's note on an uncertain reading, kept for proof-readers.
SCHEMA = {
    'title': ({'ar', 'en'}, set()),
    'heading': ({'en', 'ar'}, {'en'}),
    'passage': ({'kind', 'ar', 'tr', 'en'}, {'kind'}),
    'names': ({'items'}, {'items'}),
    'couplet': ({'ar', 'tr', 'en'}, set()),
    'note': ({'en'}, {'en'}),
    'image': ({'src'}, {'src'}),
}
IMAGES = {'dome', 'emblem', 'artwork-p12', 'artwork-p139'}
TAG = re.compile(r'</?em>')
# Latin characters the PDF holds that are deliberately not in the Latin fields, each verified against the page image.
KNOWN_ABSENT = {
    1: (collections.Counter('WEEKLYAWRAAD'), 'a second, hidden copy of the cover title sits behind the visible one'),
    2: (collections.Counter('WEEKLYAWRAAD'), 'a second, hidden copy of the cover title sits behind the visible one'),
    **{n: (collections.Counter('WEEKLYAWRAAD'), 'the section card holds a hidden copy of the cover title')
       for n in (53, 104, 108, 126, 140)},
}


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
        fields = set(b) - {'type', 'review'}
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
        raw = json.dumps({k: v for k, v in b.items() if k != 'review'}, ensure_ascii=False)
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
    content = Path(sys.argv[1]) if len(sys.argv) > 1 else CONTENT
    files = sorted(content.glob('p*.json'))
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
                problems.extend(check_latin(n, blocks, pdf.pages[n - 1]))
                problems.extend(check_standing_kasra(n, blocks, pdf.pages[n - 1]))

    for p in problems:
        print(p)
    print('%d pages checked, %d problems' % (len(pages), len(problems)))
    sys.exit(1 if problems else 0)


def check_standing_kasra(n, blocks, page):
    """The PDF's scrambled Arabic layer still encodes marks as real code points, and the standing kasra (U+0656)
    is never folded into a ligature glyph, so its count per page must match the transcription exactly."""
    want = sum(c['text'].count('ٖ') for c in page.chars if c['fontname'].split('+')[-1].startswith(ARABIC_FONTS))
    got = sum(TAG.sub('', s).count('ٖ') for s in arabic_strings(blocks))
    if want != got:
        return ['p%03d standing kasra (U+0656): PDF has %d, transcription has %d' % (n, want, got)]
    return []


def is_footer_label(c):
    """The gold 'WEEKLY AWRAAD' label in the logo box at the foot of every page."""
    return c['x0'] < 175 and c['top'] > 465 and c['fontname'].split('+')[-1].startswith('Calibri-Bold')


def check_latin(n, blocks, page):
    """Every Latin character the PDF prints must appear in the content, and nothing may be added.

    Punctuation the source prints inside an Arabic line (a dash before سَبْعًا, brackets) lives in the `ar`
    field, so Latin characters found in `ar` strings count too. The one tolerated addition is '.', which
    stands for the line-end diamonds the PDF draws in an Arabic font.
    """
    want = latin_multiset(''.join(
        c['text'] for c in page.chars
        if not c['fontname'].split('+')[-1].startswith(ARABIC_FONTS) and not is_footer_label(c)))
    in_fields = latin_multiset(''.join(latin_strings(blocks)))
    in_arabic = latin_multiset(''.join(TAG.sub('', s) for s in arabic_strings(blocks)))
    got = in_fields + in_arabic
    absent = KNOWN_ABSENT.get(n, (collections.Counter(), ''))[0]
    missing, extra = want - got, got - want
    tolerated = collections.Counter({'.': min(extra['.'], in_arabic['.'])})
    if missing != absent or extra - tolerated or in_fields - want:
        return ['p%03d Latin text differs from PDF: missing %s, extra %s' % (n, dict(missing - absent), dict(extra - tolerated))]
    return []


if __name__ == '__main__':
    main()
