/*
  Service worker: makes the dashboard open instantly and survive a dead connection.

  Strategy is network-first for the page and cache-first for the shell. That ordering
  matters here: prices are the whole point, so a fresh page always wins when the
  network can supply one, and the cache is a fallback rather than the default. A
  cache-first page would happily show yesterday's prices while online.

  BUILD_ID is rewritten by render.py on every deploy, which is what evicts the old
  cache — without it a stale shell could outlive several updates.
*/

const BUILD_ID = "__BUILD_ID__";
const CACHE = `commodities-${BUILD_ID}`;
const SHELL = ["./", "./index.html", "./style.css", "./app.js", "./icon-192.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(SHELL))
      // A missing asset must not wedge the worker in a failed install.
      .catch(() => undefined)
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))),
      )
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;

  // Never cache anything but our own GETs; cross-origin news links are not ours.
  if (request.method !== "GET" || new URL(request.url).origin !== self.location.origin) {
    return;
  }

  event.respondWith(
    fetch(request)
      .then((response) => {
        const copy = response.clone();
        caches.open(CACHE).then((cache) => cache.put(request, copy));
        return response;
      })
      .catch(() =>
        caches.match(request).then((hit) => hit || caches.match("./index.html")),
      ),
  );
});
