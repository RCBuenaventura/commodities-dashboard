/*
  The only client-side code in the project.

  The page is fully rendered server-side, so this exists purely to register the
  service worker that makes the home-screen app open offline. Everything still works
  with JavaScript disabled — you just lose the offline cache.
*/

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    // Relative path: the site is served from a repository subpath on GitHub Pages,
    // and the worker's scope follows its own location.
    navigator.serviceWorker.register("./sw.js").catch((error) => {
      console.warn("Service worker registration failed:", error);
    });
  });
}
