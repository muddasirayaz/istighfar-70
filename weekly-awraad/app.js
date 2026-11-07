'use strict';

const PREFS_KEY = 'awraad-prefs';
const PLACE_KEY = 'awraad-place';
const COUNT_KEY = 'awraad-counts';
const SIZES = [0.8, 0.9, 1, 1.1, 1.25, 1.4, 1.6];
const root = document.documentElement;

function readJSON(key) {
  try { return JSON.parse(localStorage.getItem(key) || '{}') || {}; } catch { return {}; }
}
function writeJSON(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode: preferences last for this visit only */ }
}

const prefs = readJSON(PREFS_KEY);

/* ── Reading settings ───────────────────────────────────── */
function initSettings() {
  const btn = document.getElementById('settings-btn');
  const panel = document.getElementById('settings');
  btn.addEventListener('click', () => {
    const open = panel.hidden;
    panel.hidden = !open;
    btn.setAttribute('aria-expanded', String(open));
  });

  panel.querySelectorAll('[data-layer]').forEach(chip => {
    const key = chip.dataset.layer;
    chip.setAttribute('aria-pressed', String(prefs[key] !== false));
    chip.addEventListener('click', () => {
      const on = chip.getAttribute('aria-pressed') !== 'true';
      const visible = ['ar', 'tr', 'en'].filter(k => k === key ? on : prefs[k] !== false);
      if (!visible.length) return;
      prefs[key] = on;
      chip.setAttribute('aria-pressed', String(on));
      root.classList.toggle('hide-' + key, !on);
      writeJSON(PREFS_KEY, prefs);
    });
  });

  const out = document.getElementById('size-out');
  let sizeIndex = Math.max(0, SIZES.indexOf(Number(prefs.scale) || 1));
  const showSize = () => { out.textContent = Math.round(SIZES[sizeIndex] * 100) + '%'; };
  showSize();
  panel.querySelectorAll('[data-size]').forEach(chip => chip.addEventListener('click', () => {
    sizeIndex = Math.min(SIZES.length - 1, Math.max(0, sizeIndex + Number(chip.dataset.size)));
    prefs.scale = SIZES[sizeIndex];
    root.style.setProperty('--ar-scale', prefs.scale);
    showSize();
    writeJSON(PREFS_KEY, prefs);
  }));

  const themeChips = panel.querySelectorAll('[data-theme-set]');
  const showTheme = () => themeChips.forEach(c => c.setAttribute('aria-pressed', String(c.dataset.themeSet === (prefs.theme || 'auto'))));
  showTheme();
  themeChips.forEach(chip => chip.addEventListener('click', () => {
    const mode = chip.dataset.themeSet;
    if (mode === 'auto') { delete prefs.theme; root.removeAttribute('data-theme'); }
    else { prefs.theme = mode; root.setAttribute('data-theme', mode); }
    showTheme();
    writeJSON(PREFS_KEY, prefs);
  }));

  const proof = document.getElementById('proof-toggle');
  const folios = document.querySelectorAll('.text .folio');
  if (!folios.length) { proof.hidden = true; return; }
  const setProof = on => {
    root.classList.toggle('proof', on);
    proof.setAttribute('aria-pressed', String(on));
    if (on) folios.forEach(f => {
      if (f.querySelector('.proof-img')) return;
      const img = document.createElement('img');
      img.className = 'proof-img';
      img.loading = 'lazy';
      img.decoding = 'async';
      img.src = 'pages/' + String(f.dataset.page).padStart(3, '0') + '.webp';
      img.alt = 'Original page ' + f.dataset.page;
      img.width = 1728; img.height = 972;
      f.prepend(img);
    });
  };
  proof.addEventListener('click', () => setProof(!root.classList.contains('proof')));
  if (new URLSearchParams(location.search).has('proof')) setProof(true);
}

/* ── Recitation counters (per passage, remembered for the day) ── */
function initCounters() {
  const today = new Date().toDateString();
  let store = readJSON(COUNT_KEY);
  if (store.day !== today) store = { day: today, c: {} };
  const section = document.body.dataset.section || 'home';

  document.querySelectorAll('.counter').forEach((btn, i) => {
    const id = section + ':' + i;
    const target = Number(btn.dataset.target);
    const num = btn.querySelector('.counter-n');
    const render = n => {
      num.textContent = n;
      btn.style.setProperty('--p', Math.min(1, n / target));
      btn.classList.toggle('done', n >= target);
      btn.setAttribute('aria-label', 'Recitation counter, ' + n + ' of ' + target + (n >= target ? ', complete. Tap to reset.' : ''));
    };
    render(store.c[id] || 0);
    btn.addEventListener('click', () => {
      const n = (store.c[id] || 0) >= target ? 0 : (store.c[id] || 0) + 1;
      store.c[id] = n;
      writeJSON(COUNT_KEY, store);
      render(n);
      btn.classList.remove('pulse');
      void btn.offsetWidth;
      btn.classList.add('pulse');
      if (n === target && navigator.vibrate) navigator.vibrate([18, 60, 18]);
    });
  });
}

/* ── Progress line, reveal, and remembering the reading place ── */
function initReading() {
  const bar = document.querySelector('.progress span');
  const folios = [...document.querySelectorAll('.text .folio')];
  const section = document.body.dataset.section;
  let ticking = false;

  const update = () => {
    ticking = false;
    const max = document.documentElement.scrollHeight - innerHeight;
    if (bar) bar.style.setProperty('--read', max > 0 ? Math.min(1, scrollY / max) : 0);
  };
  addEventListener('scroll', () => { if (!ticking) { ticking = true; requestAnimationFrame(update); } }, { passive: true });
  update();

  if (!('IntersectionObserver' in window)) return;

  const reveal = new IntersectionObserver(entries => entries.forEach(e => {
    if (e.isIntersecting) { e.target.classList.add('in'); reveal.unobserve(e.target); }
  }), { rootMargin: '0px 0px -8% 0px' });
  document.querySelectorAll('.b-passage, .b-couplet, .b-names, .b-title, .b-image, .card').forEach(el => {
    if (el.getBoundingClientRect().top > innerHeight) { el.classList.add('reveal'); reveal.observe(el); }
  });

  if (!section || !folios.length) return;
  const label = document.querySelector('.bar-title').textContent;
  const seen = new IntersectionObserver(entries => {
    const top = entries.filter(e => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
    if (top) writeJSON(PLACE_KEY, { href: section + '.html#p' + top.target.dataset.page, label, page: top.target.dataset.page });
  }, { rootMargin: '-45% 0px -50% 0px' });
  folios.forEach(f => seen.observe(f));
}

function initResume() {
  const box = document.getElementById('resume');
  if (!box) return;
  const place = readJSON(PLACE_KEY);
  if (!place.href) return;
  const a = box.querySelector('a');
  a.href = place.href;
  a.textContent = 'Continue reading · ' + place.label + ' · page ' + place.page;
  box.hidden = false;
}

/* ── Offline ────────────────────────────────────────────── */
function initOffline() {
  const status = document.getElementById('offline-status');
  if (!('serviceWorker' in navigator) || !window.isSecureContext) {
    status.textContent = 'Offline reading needs the site to be opened over HTTPS.';
    return;
  }
  status.textContent = 'Preparing offline copy…';
  navigator.serviceWorker.register('./sw.js')
    .then(reg => {
      const pending = reg.installing || reg.waiting;
      if (!pending) return navigator.serviceWorker.ready;
      return new Promise(resolve => pending.addEventListener('statechange', () => {
        if (pending.state === 'activated' || pending.state === 'redundant') resolve();
      }));
    })
    .then(verifyOfflineCopy)
    .then(({ saved, total }) => {
      status.textContent = saved === total
        ? 'Saved for offline reading on this device.'
        : 'Offline copy incomplete (' + saved + ' of ' + total + ' files). Reload while online to finish.';
    })
    .catch(err => { status.textContent = 'Offline reading unavailable: ' + err.message; });
}

async function verifyOfflineCopy() {
  const cache = await caches.open('awraad-living');
  const listed = await cache.match(new URL('precache.json', location.href).href);
  if (!listed) return { saved: 0, total: 1 };
  const files = await listed.json();
  const hits = await Promise.all(files.map(f => cache.match(new URL(f, location.href).href)));
  return { saved: hits.filter(Boolean).length, total: files.length };
}

document.addEventListener('DOMContentLoaded', () => {
  initSettings();
  initCounters();
  initReading();
  initResume();
  initOffline();
});
