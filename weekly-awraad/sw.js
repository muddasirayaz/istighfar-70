'use strict';
// The whole text edition is precached on install. Pages are served from cache and refreshed in the background,
// so content updates reach readers on their next visit without a cache-version bump.
const CACHE = 'awraad-living';
const PRECACHE_LIST = './precache.json';
const scoped = path => new URL(path, self.registration.scope).href;

self.addEventListener('install', event => event.waitUntil((async () => {
  const response = await fetch(PRECACHE_LIST, { cache: 'reload' });
  if (!response.ok) throw new Error('precache list unavailable: HTTP ' + response.status);
  const files = await response.json();
  const cache = await caches.open(CACHE);
  await cache.addAll(files.map(f => new Request(scoped(f), { cache: 'reload' })));
  await cache.put(scoped(PRECACHE_LIST), new Response(JSON.stringify(files), { headers: { 'Content-Type': 'application/json' } }));
  await self.skipWaiting();
})()));

self.addEventListener('activate', event => event.waitUntil((async () => {
  for (const key of await caches.keys()) {
    if (key.startsWith('awraad-') && key !== CACHE) await caches.delete(key);
  }
  await self.clients.claim();
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
    const cached = await cache.match(key) || (request.mode === 'navigate' && url.pathname.endsWith('/') && await cache.match(scoped('index.html')));
    const refresh = fetch(request).then(response => {
      if (response.ok && response.type === 'basic') cache.put(key, response.clone());
      return response;
    });
    if (cached) {
      event.waitUntil(refresh.catch(() => undefined));
      return cached;
    }
    return refresh;
  })());
});
