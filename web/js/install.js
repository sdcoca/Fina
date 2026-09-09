// web/js/install.js
//
// WP-14 (PWA-1.4a): captures Chrome's `beforeinstallprompt` event and surfaces the shell's own
// "Install Fina" action instead of leaving install timing to the browser's default mini-infobar
// heuristic. Chrome only fires this event once every installability criterion is met, including
// a service worker registered with a `fetch` handler -- that is WP-16's job and does not exist
// in this build yet, so in this repo today the event never fires and the button stays hidden
// and inert. This module is written to be correct and ready the moment WP-16 lands, not to be
// exercised end-to-end yet (see this WP's own verification report for the honest scoping note).
//
// Contains no financial logic and touches no pipeline state -- purely a browser-chrome affordance.

const installButton = document.getElementById("install-button");

// Holds the captured event between `beforeinstallprompt` and the user's own click on
// `installButton` -- `event.prompt()` can only be called once per captured event, so it is
// cleared immediately after use (and on `appinstalled`) rather than reused.
let deferredInstallPrompt = null;

window.addEventListener("beforeinstallprompt", (event) => {
  // PWA-1.4a: suppress Chrome's own mini-infobar timing; the shell decides when to offer
  // installing instead (once the button below is actually shown to the user).
  event.preventDefault();
  deferredInstallPrompt = event;
  if (installButton) {
    installButton.hidden = false;
  }
});

if (installButton) {
  installButton.addEventListener("click", () => {
    if (!deferredInstallPrompt) {
      return;
    }
    const promptEvent = deferredInstallPrompt;
    deferredInstallPrompt = null;
    installButton.hidden = true;
    promptEvent.prompt();
  });
}

// Once the OS install actually completes, the affordance has nothing left to do.
window.addEventListener("appinstalled", () => {
  deferredInstallPrompt = null;
  if (installButton) {
    installButton.hidden = true;
  }
});

// Marks this module as loaded/parsed for tests, matching app.js's / pyodide-bridge.js's own
// convention.
window.__finaInstallJsLoaded = true;
