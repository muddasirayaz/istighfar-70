'use strict';
// The whole text edition is precached on install. Pages are served from cache and refreshed in the background,
// so content updates reach readers on their next visit without a cache-version bump.
const CACHE = 'awraad-living';
const PRECACHE_LIST = './precache.json';
const scoped = path => new URL(path, self.registration.scope).href;
const unredirected = async r => r.redirected
  ? new Response(await r.blob(), { status: r.status, statusText: r.statusText, headers: r.headers })
  : r;

self.addEventListener('install', event => event.waitUntil((async () => {
  const response = await fetch(PRECACHE_LIST, { cache: 'reload' });
  if (!response.ok) throw new Error('precache list unavailable: HTTP ' + response.status);
  const files = await response.json();
  const cache = await caches.open(CACHE);
  await Promise.all(files.map(async f => {
    const response = await fetch(scoped(f), { cache: 'reload' });
    if (!response.ok) throw new Error(f + ': HTTP ' + response.status);
    await cache.put(scoped(f), await unredirected(response));
  }));
  await cache.put(scoped(PRECACHE_LIST), new Response(JSON.stringify(files), { headers: { 'Content-Type': 'application/json' } }));
  await self.skipWaiting();
})()));

self.addEventListener('activate', event => event.waitUntil((async () => {
  let replacedOldEdition = false;
  for (const key of await caches.keys()) {
    if (key.startsWith('awraad-') && key !== CACHE) {
      await caches.delete(key);
      replacedOldEdition = true;
    }
  }
  await self.clients.claim();
  // Tabs opened under an earlier edition are still showing pages it served from its cache; reload them once into
  // this edition. Only runs when an older cache was just removed, so it cannot loop.
  if (replacedOldEdition) {
    for (const client of await self.clients.matchAll({ type: 'window' })) client.navigate(client.url);
  }
})()));

self.addEventListener('fetch', event => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== 'GET' || !request.url.startsWith(self.registration.scope)) return;
  // Original page scans and the PDF are large and online-only; let the network handle them.
  if (url.pathname.endsWith('.pdf') || url.pathname.includes('/pages/')) return;

  event.respondWith((async () => {
    const cache = await caches.open(CACHE);
    const key = new Request(url.origin + url.pathname);
    const navigate = request.mode === 'navigate';
    const cached = await cache.match(key)
      || (navigate && await cache.match(url.pathname.endsWith('/') ? scoped('index.html') : key.url + '.html'));
    const refresh = fetch(key.url, { cache: 'no-cache' }).then(async raw => {
      const response = await unredirected(raw);
      if (raw.ok && raw.type === 'basic') cache.put(key, response.clone());
      return response;
    });
    if (cached) {
      event.waitUntil(refresh.catch(() => undefined));
      return cached;
    }
    return refresh;
  })());
});
