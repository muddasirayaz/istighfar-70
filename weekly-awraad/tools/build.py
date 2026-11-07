#!/usr/bin/env python3
"""Generate the Weekly Awraad living-text edition from content/pages/*.json (stdlib only)."""
import html
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / 'content' / 'pages'
TOTAL_PAGES = 140

# Section boundaries follow the source's own divider pages (the Green Dome cards).
SECTIONS = [
    {'slug': 'opening', 'label': 'The Opening Silsilah', 'pages': (4, 52)},
    {'slug': 'dardir', 'label': 'Prayers of Shaykh ad-Dardīr', 'pages': (53, 103)},
    {'slug': 'asma', 'label': 'Asmāʾ al-Ḥusnā', 'pages': (104, 107)},
    {'slug': 'manzumah', 'label': 'The Manẓūmah', 'pages': (108, 125)},
    {'slug': 'dua', 'label': 'The Closing Duʿāʾ', 'pages': (126, 140)},
]
COVER_PAGES = (1, 3)
ART_ALT = {
    'dome': 'The Green Dome of the Prophet’s Mosque in Madinah',
    'emblem': 'Calligraphic emblem of the ʿAlawī Ḥusaynī Ninowī Zāwiyah',
    'artwork-p12': 'Illuminated medallion as printed in the original edition',
    'artwork-p139': 'Illuminated medallion as printed in the original edition',
}
REPEAT = re.compile(r'\[?\s*(\d+)\s*times?\s*\]?', re.I)
SHORT_ARABIC = 90


def load_pages():
    pages = {}
    for path in sorted(CONTENT.glob('p*.json')):
        data = json.loads(path.read_text(encoding='utf-8'))
        pages[data['page']] = data['blocks']
    missing = [n for n in range(1, TOTAL_PAGES + 1) if n not in pages]
    if missing:
        raise SystemExit('Missing content pages: %s' % missing)
    return pages


def rich(text):
    """Escape text, then re-allow the single <em> emphasis tag the content schema permits."""
    return html.escape(text, quote=False).replace('&lt;em&gt;', '<em>').replace('&lt;/em&gt;', '</em>')


def plain(text):
    return re.sub(r'</?em>', '', text)


def paras(items, cls, lang=None):
    if not items:
        return ''
    attrs = ' lang="ar" dir="rtl"' if lang == 'ar' else ''
    body = ''.join('<p>%s</p>' % rich(t) for t in items)
    return '<div class="%s"%s>%s</div>' % (cls, attrs, body)


def repeat_count(texts):
    for t in texts:
        m = REPEAT.search(plain(t))
        if m and int(m.group(1)) > 1:
            return int(m.group(1))
    return 0


def counter(n):
    return ('<button class="counter" type="button" data-target="%d" aria-label="Recitation counter, 0 of %d">'
            '<svg viewBox="0 0 36 36" aria-hidden="true"><circle class="counter-track" cx="18" cy="18" r="15.5"/>'
            '<circle class="counter-fill" cx="18" cy="18" r="15.5"/></svg>'
            '<span class="counter-n">0</span><span class="counter-of">/%d</span></button>') % (n, n, n)


def render_block(block, pending_repeat):
    t = block['type']
    if t == 'title':
        return ('<header class="b-title">%s%s</header>'
                % (''.join('<p class="ar" lang="ar" dir="rtl">%s</p>' % rich(a) for a in block.get('ar', [])),
                   ''.join('<p class="en">%s</p>' % rich(e) for e in block.get('en', [])))), 0
    if t == 'heading':
        ar = '<span class="ar" lang="ar" dir="rtl">%s</span>' % rich(block['ar']) if block.get('ar') else ''
        return '<h3 class="b-heading">%s<span class="en">%s</span></h3>' % (ar, rich(block['en'])), repeat_count([block['en']])
    if t == 'passage':
        ar = block.get('ar', [])
        short = ar and sum(len(plain(a)) for a in ar) <= SHORT_ARABIC
        n = repeat_count(block.get('en', []) + block.get('tr', [])) or pending_repeat
        cls = 'b-passage kind-%s%s' % (block['kind'], ' is-short' if short else '')
        return ('<section class="%s">%s%s%s%s</section>'
                % (cls, paras(ar, 'ar', 'ar'), paras(block.get('tr'), 'tr'), paras(block.get('en'), 'en'),
                   counter(n) if n else '')), 0
    if t == 'names':
        items = ''.join(
            '<li><span class="ar" lang="ar" dir="rtl">%s</span>%s%s</li>'
            % (rich(i['ar']),
               '<span class="tr" dir="ltr">%s</span>' % rich(i['tr']) if i.get('tr') else '',
               '<span class="en" dir="ltr">%s</span>' % rich(i['en']) if i.get('en') else '')
            for i in block['items'])
        return '<ol class="b-names" dir="rtl">%s</ol>' % items, 0
    if t == 'couplet':
        halves = lambda xs, cls, lang: ('<div class="%s"%s>%s</div>' % (
            cls, ' lang="ar" dir="rtl"' if lang else '', ''.join('<span>%s</span>' % rich(x) for x in xs))) if xs else ''
        return ('<section class="b-couplet">%s%s%s</section>'
                % (halves(block.get('ar'), 'ar', True), halves(block.get('tr'), 'tr', False),
                   paras(block.get('en'), 'en'))), 0
    if t == 'note':
        return '<aside class="b-note">%s</aside>' % ''.join('<p>%s</p>' % rich(e) for e in block['en']), 0
    if t == 'image':
        src = block['src']
        return ('<figure class="b-image b-image--%s"><img src="art/%s.webp" alt="%s" loading="lazy" decoding="async"></figure>'
                % (src, src, html.escape(ART_ALT[src]))), 0
    raise SystemExit('Unknown block type %r' % t)


def render_folio(n, blocks):
    out, pending = [], 0
    for b in blocks:
        markup, carry = render_block(b, pending)
        if b['type'] == 'heading':
            pending = carry
        elif b['type'] == 'passage':
            pending = 0
        out.append(markup)
    return ('<article class="folio" id="p%d" data-page="%d">'
            '<a class="folio-mark" href="pages/%03d.webp" title="View original page %d" aria-label="Original page %d">%d</a>%s</article>'
            % (n, n, n, n, n, n, ''.join(out)))


def head(title, description):
    return '''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="description" content="%s">
<meta name="theme-color" content="#0e4d36">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Weekly Awraad">
<title>%s</title>
<link rel="manifest" href="manifest.webmanifest">
<link rel="icon" href="icon-192.png">
<link rel="apple-touch-icon" href="icon-180.png">
<link rel="stylesheet" href="fonts/fonts.css">
<link rel="stylesheet" href="styles.css">
<script>%s</script>
<script src="app.js" defer></script>
</head>
''' % (html.escape(description), html.escape(title), PREFS_BOOT)


# Applies saved reading preferences before first paint so text never flashes in the wrong layout.
PREFS_BOOT = ("(function(){var d=document.documentElement;d.classList.add('js');try{var p=JSON.parse(localStorage.getItem('awraad-prefs')||'{}');"
              "if(p.theme)d.setAttribute('data-theme',p.theme);if(p.scale)d.style.setProperty('--ar-scale',p.scale);"
              "['ar','tr','en'].forEach(function(k){if(p[k]===false)d.classList.add('hide-'+k)})}catch(e){}})();")


def settings_panel():
    return '''<div class="settings" id="settings" hidden>
  <div class="settings-inner">
    <p class="settings-label">Show</p>
    <div class="chips" role="group" aria-label="Text layers">
      <button type="button" class="chip" data-layer="ar" aria-pressed="true" lang="ar">عربي</button>
      <button type="button" class="chip" data-layer="tr" aria-pressed="true">Transliteration</button>
      <button type="button" class="chip" data-layer="en" aria-pressed="true">Translation</button>
    </div>
    <p class="settings-label">Arabic size</p>
    <div class="chips" role="group" aria-label="Arabic size">
      <button type="button" class="chip" data-size="-1" aria-label="Smaller Arabic">A&minus;</button>
      <output class="size-out" id="size-out">100%</output>
      <button type="button" class="chip" data-size="1" aria-label="Larger Arabic">A+</button>
    </div>
    <p class="settings-label">Appearance</p>
    <div class="chips" role="group" aria-label="Theme">
      <button type="button" class="chip" data-theme-set="light">Day</button>
      <button type="button" class="chip" data-theme-set="dark">Night</button>
      <button type="button" class="chip" data-theme-set="auto">Auto</button>
    </div>
    <p class="settings-label">Source</p>
    <div class="chips">
      <button type="button" class="chip" id="proof-toggle" aria-pressed="false">Compare with original pages</button>
      <a class="chip" href="Weekly-Awraad-Original.pdf" download>Original PDF</a>
    </div>
    <p class="offline-status" id="offline-status" role="status"></p>
  </div>
</div>'''


def topbar(label, home=False):
    left = ('<span class="bar-brand">Weekly Awraad</span>' if home else
            '<a class="bar-home" href="./" aria-label="Contents"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 5l-7 7 7 7"/></svg><span>Contents</span></a>')
    return '''<header class="bar">
  %s
  <p class="bar-title">%s</p>
  <button type="button" class="bar-btn" id="settings-btn" aria-controls="settings" aria-expanded="false" aria-label="Reading settings">
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/></svg>
  </button>
  <div class="progress" aria-hidden="true"><span></span></div>
</header>
%s''' % (left, html.escape(label), settings_panel())


def contents_for(section, pages):
    entries = []
    a, b = section['pages']
    for n in range(a, b + 1):
        for blk in pages[n]:
            if blk['type'] == 'heading':
                entries.append((n, plain(blk['en'])))
            elif blk['type'] == 'title' and n != a and blk.get('en'):
                entries.append((n, plain(' '.join(blk['en']))))
    if len(entries) < 2:
        return ''
    items = ''.join('<li><a href="#p%d"><span>%s</span><span class="toc-page">%d</span></a></li>' % (n, html.escape(t), n)
                    for n, t in entries)
    return '<details class="toc"><summary>In this section</summary><ol>%s</ol></details>' % items


def footer():
    return '''<footer class="footer">
  <div class="rosette-rule" aria-hidden="true"></div>
  <p>Weekly Awraad &middot; ʿAlawī Ḥusaynī Ninowī Zāwiyah</p>
  <p class="footer-small">Every word reproduced from the original 140-page edition. Page numbers in the margin link to the original pages.</p>
</footer>
</body>
</html>
'''


def build_section(i, section, pages):
    a, b = section['pages']
    prev_s = SECTIONS[i - 1] if i > 0 else None
    next_s = SECTIONS[i + 1] if i + 1 < len(SECTIONS) else None
    folios = ''.join(render_folio(n, pages[n]) for n in range(a, b + 1))
    pager = '<nav class="pager" aria-label="Sections">%s%s</nav>' % (
        '<a class="pager-prev" href="%s.html"><small>Previous</small>%s</a>' % (prev_s['slug'], html.escape(prev_s['label'])) if prev_s else '<a class="pager-prev" href="./"><small>Back to</small>Contents</a>',
        '<a class="pager-next" href="%s.html"><small>Next</small>%s</a>' % (next_s['slug'], html.escape(next_s['label'])) if next_s else '')
    body = '''<body class="page-section" data-section="%s">
<a class="skip" href="#text">Skip to text</a>
%s
<main id="text" class="text">
%s
%s
</main>
%s
%s''' % (section['slug'], topbar(section['label']), contents_for(section, pages), folios, pager, footer())
    title = '%s — Weekly Awraad' % section['label']
    desc = '%s: Arabic, transliteration and translation from the Weekly Awraad, original pages %d–%d.' % (section['label'], a, b)
    (ROOT / ('%s.html' % section['slug'])).write_text(head(title, desc) + body, encoding='utf-8')


def first_arabic(pages, a, b):
    for n in range(a, b + 1):
        for blk in pages[n]:
            if blk['type'] in ('title', 'passage') and blk.get('ar'):
                return plain(blk['ar'][0])
    return ''


def build_index(pages):
    cover = ''.join(render_folio(n, pages[n]) for n in range(COVER_PAGES[0], COVER_PAGES[1] + 1))
    cards = ''.join(
        '<li><a class="card" href="%s.html"><span class="card-arch" aria-hidden="true"></span>'
        '<span class="card-num">%d</span><span class="card-label">%s</span>%s'
        '<span class="card-pages">Pages %d–%d</span></a></li>'
        % (s['slug'], i + 1, html.escape(s['label']),
           ('<span class="card-ar" lang="ar" dir="rtl">%s</span>' % html.escape(first_arabic(pages, *s['pages']))) if first_arabic(pages, *s['pages']) else '',
           s['pages'][0], s['pages'][1])
        for i, s in enumerate(SECTIONS))
    body = '''<body class="page-home">
<a class="skip" href="#contents">Skip to contents</a>
%s
<main>
  <section class="hero" aria-label="Cover">
    <div class="hero-arch"><img src="art/dome.webp" alt="%s" width="685" height="694" fetchpriority="high"></div>
    <div class="hero-cover text">%s</div>
  </section>
  <div class="resume" id="resume" hidden><a href="#"></a></div>
  <section class="contents" id="contents" aria-labelledby="contents-h">
    <h2 id="contents-h" class="contents-h">Contents</h2>
    <ol class="cards">%s</ol>
  </section>
</main>
%s''' % (topbar('', home=True), html.escape(ART_ALT['dome']), cover, cards, footer())
    desc = 'Weekly Awraad of the ʿAlawī Ḥusaynī Ninowī Zāwiyah — the complete litany as living text: Arabic, transliteration and translation.'
    (ROOT / 'index.html').write_text(head('Weekly Awraad', desc) + body, encoding='utf-8')


def build_precache():
    files = ['./', 'index.html'] + ['%s.html' % s['slug'] for s in SECTIONS]
    files += ['styles.css', 'app.js', 'manifest.webmanifest', 'icon-180.png', 'icon-192.png', 'icon-512.png', 'fonts/fonts.css']
    files += sorted('fonts/' + p.name for p in (ROOT / 'fonts').glob('*.woff2'))
    files += sorted('art/' + p.name for p in (ROOT / 'art').glob('*.webp'))
    (ROOT / 'precache.json').write_text(json.dumps(files, indent=1) + '\n', encoding='utf-8')


def main():
    pages = load_pages()
    covered = {n for s in SECTIONS for n in range(s['pages'][0], s['pages'][1] + 1)} | set(range(COVER_PAGES[0], COVER_PAGES[1] + 1))
    if covered != set(range(1, TOTAL_PAGES + 1)):
        raise SystemExit('Sections do not cover every page exactly: %s' % sorted(set(range(1, TOTAL_PAGES + 1)) - covered))
    build_index(pages)
    for i, s in enumerate(SECTIONS):
        build_section(i, s, pages)
    build_precache()
    print('Built index + %d sections from %d pages.' % (len(SECTIONS), len(pages)))


if __name__ == '__main__':
    main()
