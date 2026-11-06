'use strict';

/* ═══════════════════════════════════════════════════════════════
   WEEKLY AWRAAD — Digital Taj Mahal Edition · Application Core
   ═══════════════════════════════════════════════════════════════ */

const TOTAL_PAGES = 140;
const CORNER_SVG = `<svg viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
  <g stroke="var(--gold)" stroke-width="0.9" opacity="0.8">
    <path d="M5 95V5h90"/>
    <path d="M12 80V12h68"/>
    <path d="M18 82C16 50 50 16 82 18C56 13 30 30 26 56C22 76 40 84 50 68C57 55 48 44 38 50C30 56 38 65 44 60"/>
    <path d="M24 76C50 81 74 60 76 34"/>
    <path d="M37 72C42 62 54 58 62 60"/>
    <path d="M52 62C52 48 64 42 74 43"/>
    <path d="M66 47C64 36 76 30 86 31"/>
    <path d="M38 72C28 70 26 60 28 55C34 58 39 63 38 72Z" fill="var(--gold)" fill-opacity="0.08"/>
    <path d="M53 61C43 58 43 50 46 45C51 49 55 53 53 61Z" fill="var(--gold)" fill-opacity="0.08"/>
    <path d="M67 45C57 42 58 35 62 30C67 34 70 38 67 45Z" fill="var(--gold)" fill-opacity="0.08"/>
    <circle cx="28" cy="28" r="5" fill="var(--primary)" fill-opacity="0.15"/>
    <path d="M28 22v12M22 28h12M24 24l8 8M24 32l8-8"/>
    <circle cx="28" cy="28" r="2" fill="var(--gold)"/>
  </g>
</svg>`;

/* ── Storage helpers ────────────────────────────────────────── */
function stored(k) { try { return localStorage.getItem(k); } catch { return null; } }
function save(k, v) { try { localStorage.setItem(k, v); } catch {} }

/* ── Build all 140 page sections ────────────────────────────── */
function buildPages() {
  const reader = document.getElementById('reader');
  const frag = document.createDocumentFragment();

  for (let i = 1; i <= TOTAL_PAGES; i++) {
    const num = String(i).padStart(3, '0');

    const section = document.createElement('section');
    section.className = 'leaf';
    section.id = `page-${i}`;
    section.dataset.page = i;
    section.setAttribute('aria-label', `Original PDF page ${i} of ${TOTAL_PAGES}`);

    section.innerHTML = `
      <div class="folio"><a href="#page-${i}">Page ${num} / ${TOTAL_PAGES}</a></div>
      <div class="page-scroll" tabindex="0" role="region" aria-label="Page ${i}; scroll sideways when zoomed">
        <div class="illuminated">
          <span class="leaf-corner leaf-corner--tl">${CORNER_SVG}</span>
          <span class="leaf-corner leaf-corner--tr">${CORNER_SVG}</span>
          <span class="leaf-corner leaf-corner--br">${CORNER_SVG}</span>
          <span class="leaf-corner leaf-corner--bl">${CORNER_SVG}</span>
          <img class="page-image"
               src="pages/${num}.webp"
               width="1728" height="972"
               loading="${i <= 3 ? 'eager' : 'lazy'}"
               decoding="async"
               alt="Original Weekly Awraad page ${i}, preserved as an image. Selectable text is available in the original PDF.">
        </div>
      </div>`;

    frag.appendChild(section);
  }

  reader.appendChild(frag);
}

/* ── Navigation ─────────────────────────────────────────────── */
let current = 1;
const field = () => document.getElementById('page-number');
const prevBtn = () => document.getElementById('previous');
const nextBtn = () => document.getElementById('next');
const resumeBtn = () => document.getElementById('resume');

function setPage(n) {
  current = Math.max(1, Math.min(TOTAL_PAGES, n));
  const f = field();
  if (document.activeElement !== f) f.value = current;
  prevBtn().disabled = current === 1;
  nextBtn().disabled = current === TOTAL_PAGES;
  save('awraad-page', String(current));
}

function go(n) {
  n = Math.max(1, Math.min(TOTAL_PAGES, parseInt(n, 10) || 1));
  field().value = n;
  setPage(n);
  document.getElementById(`page-${n}`).scrollIntoView({ block: 'start', behavior: 'smooth' });
  history.replaceState(null, '', `#page-${n}`);
}

function initNav() {
  const f = field();
  const p = prevBtn();
  const nx = nextBtn();
  const r = resumeBtn();

  const last = Number(stored('awraad-page'));
  if (last > 1 && last <= TOTAL_PAGES) {
    r.hidden = false;
    r.textContent = `Resume p.${last}`;
    r.onclick = () => { go(last); r.hidden = true; };
  }

  p.onclick = () => go(current - 1);
  nx.onclick = () => go(current + 1);
  f.onchange = () => go(f.value);
  f.onkeydown = e => { if (e.key === 'Enter') { go(f.value); f.blur(); } };

  document.addEventListener('keydown', e => {
    if (/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) return;
    const dlg = document.getElementById('install-dialog');
    if (dlg && dlg.open) return;
    if (e.key === 'ArrowRight') { e.preventDefault(); go(current + 1); }
    if (e.key === 'ArrowLeft') { e.preventDefault(); go(current - 1); }
  });

  setPage(1);
}

/* ── Zoom ───────────────────────────────────────────────────── */
function initZoom() {
  const slider = document.getElementById('zoom');
  const label = document.getElementById('zoom-value');
  slider.oninput = function () {
    document.documentElement.style.setProperty('--zoom', this.value + '%');
    label.textContent = this.value + '%';
  };
}

/* ── Theme ──────────────────────────────────────────────────── */
function initTheme() {
  const btn = document.getElementById('theme-toggle');
  const root = document.documentElement;

  function apply(mode) {
    root.setAttribute('data-theme', mode);
    btn.setAttribute('aria-pressed', mode === 'dark' ? 'true' : 'false');
    btn.innerHTML = mode === 'dark'
      ? '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="5"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/></svg> Day'
      : '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg> Night';
  }

  const saved = stored('awraad-theme');
  const prefer = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  apply(saved || prefer);

  btn.onclick = () => {
    const next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    apply(next);
    save('awraad-theme', next);
  };
}

/* ── Scroll-spy with IntersectionObserver ────────────────────── */
function initScrollSpy() {
  if (!('IntersectionObserver' in window)) return;
  const visible = new Map();
  const observer = new IntersectionObserver(entries => {
    for (const e of entries) {
      if (e.isIntersecting) visible.set(e.target, e.intersectionRatio);
      else visible.delete(e.target);
    }
    const best = [...visible].sort((a, b) => b[1] - a[1])[0];
    if (best) setPage(Number(best[0].dataset.page));
  }, {
    rootMargin: '-80px 0px -10% 0px',
    threshold: [0, 0.1, 0.25, 0.5, 0.75, 1]
  });

  document.querySelectorAll('.leaf').forEach(l => observer.observe(l));
}

/* ── Reveal-on-scroll animation ─────────────────────────────── */
function initReveal() {
  if (!('IntersectionObserver' in window)) {
    document.querySelectorAll('.leaf').forEach(l => l.classList.add('visible'));
    return;
  }
  const reveal = new IntersectionObserver(entries => {
    for (const e of entries) {
      if (e.isIntersecting) {
        e.target.classList.add('visible');
        reveal.unobserve(e.target);
      }
    }
  }, { rootMargin: '0px 0px -60px 0px', threshold: 0.01 });

  document.querySelectorAll('.leaf').forEach(l => reveal.observe(l));
}

/* ── Install dialog ─────────────────────────────────────────── */
function initDialog() {
  const dialog = document.getElementById('install-dialog');
  if (!dialog) return;
  const open = document.getElementById('install-help');
  const close = document.getElementById('close-install');
  if (open) open.onclick = () => dialog.showModal();
  if (close) close.onclick = () => dialog.close();
}

/* ── Offline / Service Worker ───────────────────────────────── */
function initOffline() {
  const statusEl = document.getElementById('offline-status');
  const download = document.getElementById('save-offline');
  const progress = document.getElementById('download-progress');
  let worker = null, busy = false;

  function communicate(type, onProgress) {
    return new Promise((resolve, reject) => {
      const channel = new MessageChannel();
      let timeout = setTimeout(() => reject(new Error('No response from offline storage. Reload and retry.')), 120000);
      channel.port1.onmessage = e => {
        clearTimeout(timeout);
        const data = e.data;
        if (data.type === 'PROGRESS') {
          if (onProgress) onProgress(data);
          timeout = setTimeout(() => reject(new Error('Download paused. Keep the app open, reconnect and retry.')), 120000);
        } else {
          channel.port1.close();
          if (data.type === 'ERROR') reject(new Error(data.message));
          else resolve(data);
        }
      };
      worker.postMessage({ type }, [channel.port2]);
    });
  }

  async function checkOffline() {
    if (!worker || busy) return;
    try {
      const data = await communicate('CHECK');
      if (data.ready) {
        statusEl.textContent = 'Ready offline — all 140 pages and the original PDF are saved on this device.';
        download.textContent = 'Verify offline copy';
        download.disabled = false;
        progress.hidden = true;
      } else {
        statusEl.textContent = navigator.onLine
          ? 'Save all 140 pages and the original PDF for offline use (about 24 MB).'
          : 'Incomplete offline copy. Reconnect to save remaining files.';
        download.textContent = 'Save all pages offline';
        download.disabled = !navigator.onLine;
      }
    } catch (e) {
      statusEl.textContent = e.message;
      download.disabled = false;
    }
  }

  download.onclick = async () => {
    if (busy || !worker) return;
    busy = true;
    download.disabled = true;
    progress.hidden = false;
    progress.value = 0;
    statusEl.textContent = 'Saving your complete offline edition…';
    try {
      await communicate('SAVE', data => {
        progress.max = data.total;
        progress.value = data.done;
        statusEl.textContent = `Saving offline: ${data.done} / ${data.total} files.`;
      });
      if (navigator.storage && navigator.storage.persist) {
        try { await navigator.storage.persist(); } catch {}
      }
      busy = false;
      await checkOffline();
    } catch (e) {
      busy = false;
      statusEl.textContent = 'Offline save incomplete. ' + e.message;
      download.textContent = 'Retry offline save';
      download.disabled = !navigator.onLine;
    }
  };

  async function setup() {
    if (!('serviceWorker' in navigator) || !window.isSecureContext) {
      statusEl.textContent = 'Offline mode requires HTTPS (or localhost for testing).';
      download.disabled = true;
      return;
    }
    try {
      await navigator.serviceWorker.register('./sw.js', { scope: './' });
      const reg = await navigator.serviceWorker.ready;
      worker = reg.active;
      download.disabled = false;
      await checkOffline();
    } catch (e) {
      statusEl.textContent = 'Offline setup failed. ' + e.message;
    }
  }

  window.addEventListener('online', checkOffline);
  window.addEventListener('offline', checkOffline);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) checkOffline(); });
  setup();
}

/* ── Handle initial hash ────────────────────────────────────── */
function handleHash() {
  const hash = location.hash;
  if (hash && hash.startsWith('#page-')) {
    const n = parseInt(hash.replace('#page-', ''), 10);
    if (n >= 1 && n <= TOTAL_PAGES) {
      requestAnimationFrame(() => go(n));
    }
  }
}

/* ── Initialize everything ──────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {
  buildPages();
  initNav();
  initZoom();
  initTheme();
  initScrollSpy();
  initReveal();
  initDialog();
  initOffline();
  handleHash();
});
