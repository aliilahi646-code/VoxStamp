const CACHE_NAME = "voxstamp-shell-v1";
const SHELL_FILES = [
  "/static/style.css",
  "/static/app.js",
  "/static/convert.js",
  "/static/voiceover.js",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_FILES)).catch(() => {})
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((names) =>
      Promise.all(names.filter((n) => n !== CACHE_NAME).map((n) => caches.delete(n)))
    )
  );
  self.clients.claim();
});

// Cache-first for the static shell (CSS/JS/icons); everything else
// (pages, /transcribe, /api/*) always goes to the network -- this app's
// actual work (uploading and processing files) needs a live connection.
self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  const isShellAsset = SHELL_FILES.some((f) => url.pathname === f);

  if (isShellAsset) {
    event.respondWith(
      caches.match(event.request).then((cached) => cached || fetch(event.request))
    );
  }
  // else: let the browser handle it normally (network).
});
