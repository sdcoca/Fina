// web/js/sw-register.js
//
// WP-16 (PWA-4.4): registers web/service-worker.js and implements the explicit "update
// available -- reload to apply" prompt. Never calls skipWaiting()/reloads the page on its own
// initiative -- only in direct response to the user's own click on the update banner's button,
// so a chart mid-tooltip-interaction (open, per R-10.2) is never silently swapped under the
// user just because a new service-worker version finished installing. This is the standard
// updatefound -> new worker statechange === "installed" (with an existing controller already
// present, i.e. not the very first install) -> show prompt -> on click, message the waiting
// worker to skipWaiting() -> reload once controllerchange fires pattern the design doc (PWA-
// 4.4) specifies verbatim.
//
// Kept as its own module (matching web/js/install.js's own precedent of a small, self-
// contained browser-chrome affordance) rather than folded into app.js: it owns only the SW
// lifecycle and its own tiny banner UI, and contains no financial logic or pipeline state.

const updateBanner = document.getElementById("update-banner");
const updateReloadButton = document.getElementById("update-reload-button");

// Set to true only inside the reload button's own click handler, immediately before asking the
// waiting worker to skipWaiting() -- the "controllerchange" listener below reloads the page if
// and only if this is true, so a controllerchange that fires for any other reason (e.g.
// clients.claim() on the very first install, which has nothing installed yet to preserve) never
// triggers a reload nobody asked for.
let _reloadRequestedByUser = false;

function _showUpdateBanner(registration) {
  if (!updateBanner || !updateReloadButton) {
    return;
  }
  updateBanner.hidden = false;
  updateReloadButton.addEventListener(
    "click",
    () => {
      const waiting = registration.waiting;
      if (!waiting) {
        // The waiting worker already took over by some other path (e.g. every other tab
        // controlled by the old version was closed in the meantime) -- a plain reload alone
        // still lands on the current version, so this is never a dead end for the user.
        window.location.reload();
        return;
      }
      updateReloadButton.disabled = true;
      _reloadRequestedByUser = true;
      waiting.postMessage("SKIP_WAITING");
    },
    { once: true }
  );
}

async function _registerServiceWorker() {
  if (!("serviceWorker" in navigator)) {
    return; // no offline/update story on a browser without SW support; the rest of the app
    // (import, run, chart) works identically online regardless.
  }

  navigator.serviceWorker.addEventListener("controllerchange", () => {
    // The one and only moment this file ever reloads the page -- and only when the user's own
    // click above requested it (see _reloadRequestedByUser's own comment).
    if (_reloadRequestedByUser) {
      window.location.reload();
    }
  });

  // Relative, not root-relative: "/service-worker.js" only works when the app is served from
  // the origin's root, which a GitHub Pages *project* site (served under "/<repo-name>/") is
  // not -- a real deployment surfaced exactly this (see pyodide-bridge.js's VENDOR_BASE comment
  // for the sibling bug this shares). A relative path here also gives the correct default scope
  // (this script's own directory), matching the app's real root, without passing `scope`
  // explicitly.
  const registration = await navigator.serviceWorker.register("./service-worker.js");

  // A worker may already be sitting in "waiting" from an update that finished installing
  // before this particular page load (e.g. it happened while this tab was inactive) -- surface
  // the prompt immediately rather than waiting for an "updatefound" event that will never fire
  // again for an installation this registration object did not itself just start.
  if (registration.waiting && navigator.serviceWorker.controller) {
    _showUpdateBanner(registration);
  }

  registration.addEventListener("updatefound", () => {
    const newWorker = registration.installing;
    if (!newWorker) {
      return;
    }
    newWorker.addEventListener("statechange", () => {
      const isUpdateToAnAlreadyRunningApp =
        newWorker.state === "installed" && navigator.serviceWorker.controller;
      // An existing controller means this is a genuine update -- the very first install has no
      // prior version running for the user to lose, so there is nothing to prompt "update" for;
      // that case activates on its own with no prompt, exactly as a first install should.
      if (isUpdateToAnAlreadyRunningApp) {
        _showUpdateBanner(registration);
      }
    });
  });
}

_registerServiceWorker().catch((err) => {
  // Never let a SW registration failure break the rest of the app -- Pyodide/import/storage all
  // work identically without a service worker, just without the offline/installable story.
  console.error("sw-register.js: service worker registration failed:", err);
});

// Marks this module as loaded/parsed for tests, matching app.js's / install.js's own
// convention.
window.__finaSwRegisterJsLoaded = true;
