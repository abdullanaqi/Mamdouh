/* Crewline crew PWA service worker.
 * Strategy:
 *  - Precache the app shell.
 *  - Network-first for navigations to the crew "today"/"shift" views, falling
 *    back to cache so the crew can see their day on bad job-site signal.
 *  - Mutations (clock-in/checklist/photo) are queued client-side in IndexedDB
 *    (see lib/offline/queue) and replayed by the page when back online; the SW
 *    intentionally does NOT swallow POSTs so server actions keep their semantics.
 */
const CACHE = 'crewline-v1';
const SHELL = ['/today', '/history', '/manifest.webmanifest'];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(SHELL).catch(() => undefined)),
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))),
    ),
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return; // never cache mutations

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  const isCrewView = url.pathname === '/today' || url.pathname.startsWith('/shift');
  if (request.mode === 'navigate' && isCrewView) {
    event.respondWith(
      fetch(request)
        .then((res) => {
          const copy = res.clone();
          caches.open(CACHE).then((cache) => cache.put(request, copy));
          return res;
        })
        .catch(() => caches.match(request).then((c) => c || caches.match('/today'))),
    );
    return;
  }

  // Cache-first for static assets.
  if (url.pathname.startsWith('/_next/static') || url.pathname.startsWith('/icons')) {
    event.respondWith(
      caches.match(request).then((cached) => cached || fetch(request)),
    );
  }
});
