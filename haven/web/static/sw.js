/* HAVEN service worker — shell-only offline support, no build step.
   Hard rules:
   - /api/* and /events NEVER pass through here. /events is the SSE live
     state stream; caching or intercepting it would break live state.
     Mutating endpoints go straight to the network too, so a POST can
     never be swallowed by a cache.
   - Non-GET requests are never touched. */

const SHELL_CACHE = 'haven-shell-v1';
const SHELL = [
  '/',
  '/index.html',
  '/styles.css',
  '/app.js',
  '/icon.svg',
  '/manifest.webmanifest',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE).then((cache) => cache.addAll(SHELL)),
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((names) =>
      Promise.all(
        names
          .filter((name) => name.startsWith('haven-') && name !== SHELL_CACHE)
          .map((name) => caches.delete(name)),
      ),
    ).then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin) return;

  // Live state + every API endpoint bypass the SW entirely: the browser
  // talks to the network. Caching /events would break live state; routing
  // mutations through a cache could swallow a POST.
  if (url.pathname.startsWith('/api/') || url.pathname === '/events') return;
  if (event.request.method !== 'GET') return;

  if (event.request.mode === 'navigate') {
    // Network-first: a navigation always tries the server; on failure the
    // installed app still opens from the cached shell.
    event.respondWith(
      fetch(event.request).catch(() =>
        caches.open(SHELL_CACHE).then((cache) => cache.match('/index.html')),
      ),
    );
    return;
  }

  // Shell assets: cache-first, revalidate in the background so a stale
  // shell refreshes itself on the next visit.
  event.respondWith(
    caches.match(event.request).then((cached) => {
      const revalidate = fetch(event.request)
        .then((response) => {
          if (response && response.ok) {
            const copy = response.clone();
            caches.open(SHELL_CACHE).then((cache) => cache.put(event.request, copy));
          }
          return response;
        })
        .catch(() => cached);
      return cached || revalidate;
    }),
  );
});
