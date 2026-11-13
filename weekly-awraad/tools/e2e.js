'use strict';
// Browser checks for the built site. Start a server on the site first (npx wrangler dev), then:
//   node tools/e2e.js [http://127.0.0.1:8787] [path/to/older/site]
// The optional second argument is a checkout of the previously released site, used to check that a reader holding
// half-updated files (old page with new script, or the reverse) still gets a working page.
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const BASE = (process.argv[2] || 'http://127.0.0.1:8787').replace(/\/$/, '');
const OLD = process.argv[3];
const SITE = path.join(__dirname, '..');
const SECTIONS = ['opening', 'dardir', 'asma', 'manzumah', 'dua'];
const PHONE = { width: 390, height: 844 };

let failed = 0;
const check = (name, ok, detail) => {
  if (!ok) failed++;
  console.log((ok ? 'PASS  ' : 'FAIL  ') + name + (detail === undefined || ok ? '' : '  -> ' + JSON.stringify(detail)));
};
const watch = (page, errors) => {
  page.on('pageerror', e => errors.push(page.url() + ' ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push(page.url() + ' ' + m.text()); });
};
const ready = page => page.waitForFunction(
  () => /^(Saved|Offline)/.test(document.getElementById('offline-status')?.textContent || ''), null, { timeout: 30000 });

(async () => {
  const browser = await chromium.launch();

  /* 1. Every page number resolves to a section that really contains it */
  {
    const home = fs.readFileSync(path.join(SITE, 'index.html'), 'utf8');
    const map = JSON.parse(home.match(/window\.PAGE_MAP=(\[.*?\]);/)[1]);
    const missing = [];
    for (let n = 1; n <= 140; n++) {
      const slug = map.find(([last]) => n <= last)[1];
      const file = slug === '.' ? 'index.html' : slug + '.html';
      if (!fs.readFileSync(path.join(SITE, file), 'utf8').includes('id="p' + n + '"')) missing.push(n + '->' + file);
    }
    check('page map: all 140 pages resolve to an existing anchor', !missing.length, missing.slice(0, 8));
  }

  /* 2. Online, offline, cache hygiene, 404 */
  {
    const ctx = await browser.newContext({ viewport: PHONE });
    const page = await ctx.newPage();
    const errors = [];
    watch(page, errors);
    await page.goto(BASE + '/', { waitUntil: 'networkidle' });
    await ready(page);
    check('offline copy reports saved', (await page.textContent('#offline-status')).startsWith('Saved'), await page.textContent('#offline-status'));

    const cache = await page.evaluate(async () => {
      const c = await caches.open('awraad-living');
      const out = { redirected: [], has404: false };
      for (const k of await c.keys()) {
        if ((await c.match(k)).redirected) out.redirected.push(new URL(k.url).pathname);
        if (k.url.endsWith('/404.html')) out.has404 = true;
      }
      return out;
    });
    check('no redirected responses in the cache', !cache.redirected.length, cache.redirected);
    check('404 page is in the offline copy', cache.has404);

    for (const s of SECTIONS) {
      const r = await page.goto(BASE + '/' + s + '.html', { waitUntil: 'load' });
      check('online ' + s, r.status() === 200 && await page.locator('.folio').count() > 0, r.status());
    }
    const before = errors.length;
    const nf = await page.goto(BASE + '/no-such-page', { waitUntil: 'load' });
    check('unknown address gives a 404 with a way back', nf.status() === 404 && await page.locator('a[href="./"]').count() === 1, nf.status());
    errors.length = before; // the browser logs the 404 itself as a console error; that one is expected

    await ctx.setOffline(true);
    for (const p of ['/', ...SECTIONS.map(s => '/' + s + '.html'), ...SECTIONS.map(s => '/' + s)]) {
      let ok = false, detail;
      try { const r = await page.goto(BASE + p, { waitUntil: 'load', timeout: 10000 }); ok = r.status() === 200; detail = r.status(); }
      catch (e) { detail = e.message.split('\n')[0]; }
      check('offline ' + p, ok, detail);
    }
    await ctx.setOffline(false);
    check('no script or console errors (online + offline)', !errors.length, errors.slice(0, 5));
    await ctx.close();
  }

  /* 3. Go to page */
  {
    const ctx = await browser.newContext({ viewport: PHONE, serviceWorkers: 'block' });
    const page = await ctx.newPage();
    const inView = n => page.evaluate(n => {
      const r = document.getElementById('p' + n).getBoundingClientRect();
      return r.top < innerHeight && r.bottom > 0;
    }, n);

    await page.goto(BASE + '/opening.html', { waitUntil: 'load' });
    await page.keyboard.press('g');
    check('g opens settings and focuses the page box', await page.evaluate(() => !document.getElementById('settings').hidden && document.activeElement.id === 'goto-page'));
    await page.fill('#goto-page', '100');
    await Promise.all([page.waitForURL(/#p100$/), page.keyboard.press('Enter')]);
    await page.waitForLoadState('load');
    check('typing 100 lands on page 100 in another section', !/opening/.test(page.url()) && await inView(100), page.url());

    let loads = 0;
    page.on('load', () => loads++);
    await page.goto(BASE + '/?p=100', { waitUntil: 'load' });
    await page.waitForURL(/#p100$/);
    await page.waitForLoadState('load');
    check('shared link ?p=100 from home reaches page 100', await inView(100), page.url());

    for (const form of ['/opening.html?p=5', '/opening?p=5']) {
      loads = 0;
      await page.goto(BASE + form, { waitUntil: 'load' });
      await page.waitForURL(/#p5$/);
      await page.waitForTimeout(400);
      check('shared link ' + form + ' scrolls in place, one load', loads === 1 && await inView(5) && !/[?]p=/.test(page.url()), { loads, url: page.url() });
    }
    await page.goto(BASE + '/opening.html?p=9999', { waitUntil: 'load' });
    await page.waitForURL(/#p140$/);
    check('out-of-range ?p clamps to the last page', true);
    await ctx.close();
  }

  /* 4. Recite mode and the counter dock */
  {
    const ctx = await browser.newContext({ viewport: PHONE, serviceWorkers: 'block', hasTouch: false });
    const page = await ctx.newPage();
    const errors = [];
    watch(page, errors);
    const shown = sel => page.evaluate(sel => { const e = document.querySelector(sel); return !!e && getComputedStyle(e).display !== 'none'; }, sel);

    await page.goto(BASE + '/opening.html', { waitUntil: 'load' });
    check('dock hidden outside recite mode', !(await shown('.dock')));
    await page.click('#recite-btn');
    check('recite on: button pressed, dock shown, pager/footer hidden',
      await page.getAttribute('#recite-btn', 'aria-pressed') === 'true' && await shown('.dock') && !(await shown('.pager')) && !(await shown('.footer')));

    await page.click('#settings-btn');
    check('settings still open in recite mode', await shown('#settings') && !(await page.evaluate(() => document.getElementById('settings').hidden)));
    await page.click('#settings-btn');

    await page.mouse.move(195, 400);
    for (let i = 0; i < 4; i++) { await page.mouse.wheel(0, 400); await page.waitForTimeout(120); }
    check('top bar hides while scrolling down', await page.evaluate(() => document.querySelector('.bar').classList.contains('bar-hidden')));
    await page.mouse.wheel(0, -200); await page.waitForTimeout(200);
    check('top bar returns on scrolling up', await page.evaluate(() => !document.querySelector('.bar').classList.contains('bar-hidden')));

    // A short screen makes passages taller than the viewport, which is when the dock is needed.
    await page.setViewportSize({ width: 390, height: 520 });
    const idx = await page.evaluate(() => [...document.querySelectorAll('.counter')].findIndex(c => c.parentElement.offsetHeight > innerHeight + 150));
    const dockOn = () => page.evaluate(() => getComputedStyle(document.getElementById('dock')).visibility === 'visible');
    const state = () => page.evaluate(i => {
      const c = document.querySelectorAll('.counter')[i];
      return { flow: c.querySelector('.counter-n').textContent, target: c.dataset.target,
               dock: document.querySelector('.dock-n').textContent, of: document.querySelector('.dock-of').textContent };
    }, idx);
    await page.evaluate(i => document.querySelectorAll('.counter')[i].scrollIntoView({ block: 'center' }), idx);
    await page.waitForTimeout(400);
    check('dock stays out of the way while the passage counter is on screen', idx >= 0 && !(await dockOn()), idx);
    await page.evaluate(i => document.querySelectorAll('.counter')[i].parentElement.scrollIntoView({ block: 'start' }), idx);
    await page.waitForTimeout(400);
    let s = await state();
    check('dock appears for the passage being read once its counter is off screen', await dockOn() && s.of === '/' + s.target && s.dock === s.flow, s);
    await page.click('#dock-btn'); await page.click('#dock-btn'); await page.click('#dock-btn');
    s = await state();
    check('three taps on the dock count three on both', s.dock === '3' && s.flow === '3', s);

    const box = await page.locator('#dock-btn').boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down(); await page.waitForTimeout(800); await page.mouse.up();
    await page.waitForTimeout(150);
    s = await state();
    check('holding the dock resets to zero and stays zero on release', s.dock === '0' && s.flow === '0', s);
    await page.click('#dock-btn');
    s = await state();
    check('dock counts again after a reset', s.dock === '1' && s.flow === '1', s);

    const overlap = await page.evaluate(() => {
      scrollTo(0, document.documentElement.scrollHeight);
      const dock = document.getElementById('dock-btn').getBoundingClientRect();
      const last = [...document.querySelectorAll('.text .folio')].pop().getBoundingClientRect();
      return { lastBottom: Math.round(last.bottom), dockTop: Math.round(dock.top) };
    });
    await page.waitForTimeout(400);
    check('dock does not cover the last lines of the section', overlap.lastBottom <= overlap.dockTop || !(await dockOn()), overlap);
    await page.setViewportSize(PHONE);

    await page.reload({ waitUntil: 'load' });
    check('recite mode survives a reload', await page.evaluate(() => document.documentElement.classList.contains('recite')) && await shown('.dock'));
    await page.goto(BASE + '/dardir.html', { waitUntil: 'load' });
    check('section without counters shows no dock', await page.locator('.dock').count() === 0);
    await page.goto(BASE + '/', { waitUntil: 'load' });
    check('home page is never put in recite mode', await page.evaluate(() => !document.documentElement.classList.contains('recite')) && await shown('.card'));
    await page.goto(BASE + '/opening.html', { waitUntil: 'load' });
    await page.click('#recite-btn');
    check('recite off: pager and footer back, dock gone', await shown('.pager') && await shown('.footer') && !(await shown('.dock')));
    check('no script or console errors in recite flow', !errors.length, errors.slice(0, 5));
    await ctx.close();
  }

  /* 5. Layout: nothing wider than the screen, in either mode */
  for (const width of [320, 390, 768, 1280]) {
    const ctx = await browser.newContext({ viewport: { width, height: 800 }, serviceWorkers: 'block' });
    const page = await ctx.newPage();
    const wide = [];
    for (const p of ['/', '/opening.html', '/asma.html', '/dua.html', '/no-such-page']) {
      await page.goto(BASE + p, { waitUntil: 'load' });
      for (const recite of [false, true]) {
        if (recite) { if (!await page.locator('#recite-btn').count()) continue; await page.click('#recite-btn'); }
        const over = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
        if (over > 0) wide.push(p + (recite ? ' recite' : '') + ' +' + over + 'px');
        if (await page.locator('#settings-btn').count()) {
          await page.click('#settings-btn');
          const over2 = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
          if (over2 > 0) wide.push(p + (recite ? ' recite' : '') + ' settings +' + over2 + 'px');
          await page.click('#settings-btn');
        }
        if (recite) await page.click('#recite-btn');
      }
    }
    check('no sideways scroll at ' + width + 'px', !wide.length, wide);
    await ctx.close();
  }

  /* 6. Contrast of small text, light and dark */
  for (const scheme of ['light', 'dark']) {
    const ctx = await browser.newContext({ viewport: PHONE, colorScheme: scheme, serviceWorkers: 'block' });
    const page = await ctx.newPage();
    const low = [];
    for (const [p, sels] of [['/', ['.card-num', '.contents-h', '.footer-small', '#offline-status']], ['/opening.html', ['.toc-page', '.settings-label', '.counter-of', '.dock-of']]]) {
      await page.goto(BASE + p, { waitUntil: 'load' });
      if (p !== '/') { await page.click('#settings-btn'); await page.click('#recite-btn'); await page.evaluate(() => document.querySelector('.toc')?.setAttribute('open', '')); }
      const found = await page.evaluate(sels => {
        const cv = document.createElement('canvas').getContext('2d', { willReadFrequently: true });
        const rgb = c => { cv.clearRect(0, 0, 1, 1); cv.fillStyle = c; cv.fillRect(0, 0, 1, 1); return [...cv.getImageData(0, 0, 1, 1).data]; };
        const lum = ([r, g, b]) => [r, g, b].map(v => { v /= 255; return v <= .03928 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; }).reduce((a, v, i) => a + v * [.2126, .7152, .0722][i], 0);
        return sels.map(sel => {
          const el = document.querySelector(sel);
          if (!el) return [sel, null];
          let bg = null;
          for (let e = el; e && !bg; e = e.parentElement) { const c = rgb(getComputedStyle(e).backgroundColor); if (c[3] === 255) bg = c; }
          bg = bg || rgb(getComputedStyle(document.documentElement).backgroundColor);
          const a = lum(rgb(getComputedStyle(el).color)), b = lum(bg);
          return [sel, Math.round((Math.max(a, b) + .05) / (Math.min(a, b) + .05) * 100) / 100];
        });
      }, sels);
      for (const [sel, ratio] of found) if (ratio === null || ratio < 4.5) low.push(p + ' ' + sel + ' ' + ratio);
    }
    check('small text meets 4.5:1 in ' + scheme + ' mode', !low.length, low);
    await ctx.close();
  }

  /* 7. Half-updated reader: old page with new script, and new page with old script */
  if (OLD) {
    for (const [label, oldFiles] of [['old pages + new script', /\/(opening|dua|index)(\.html)?$|\/$/], ['new pages + old script', /\/app\.js$/]]) {
      const ctx = await browser.newContext({ viewport: PHONE, serviceWorkers: 'block' });
      const page = await ctx.newPage();
      const errors = [];
      watch(page, errors);
      await page.route(oldFiles, route => {
        const name = new URL(route.request().url()).pathname.replace(/^\/$/, '/index').replace(/^\/([a-z]+)$/, '/$1.html');
        const file = path.join(OLD, name);
        return fs.existsSync(file) ? route.fulfill({ path: file }) : route.continue();
      });
      for (const p of ['/', '/opening.html', '/dua.html']) {
        await page.goto(BASE + p, { waitUntil: 'load' });
        if (await page.locator('.counter').count()) await page.locator('.counter').first().click();
        await page.click('#settings-btn');
      }
      check(label + ': pages work without errors', !errors.length, errors.slice(0, 5));
      await ctx.close();
    }
  } else {
    console.log('SKIP  half-updated reader check (pass the older site as the second argument)');
  }

  await browser.close();
  console.log(failed ? '\n' + failed + ' check(s) FAILED' : '\nAll checks passed');
  process.exit(failed ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
