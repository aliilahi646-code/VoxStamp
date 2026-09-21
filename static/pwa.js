if ("serviceWorker" in navigator) {
  window.addEventListener("load", function () {
    navigator.serviceWorker.register("/static/sw.js").catch(function () {});
  });
}

// Capture the browser's install prompt so we can trigger it from our own
// banner instead of only relying on the browser's small built-in icon.
let deferredInstallPrompt = null;

function showBanner() {
  const banner = document.getElementById("install-banner");
  if (banner) banner.style.display = "flex";
}

function hideBanner() {
  const banner = document.getElementById("install-banner");
  if (banner) banner.style.display = "none";
}

window.addEventListener("beforeinstallprompt", function (e) {
  e.preventDefault();
  deferredInstallPrompt = e;
  showBanner();
});

document.addEventListener("DOMContentLoaded", function () {
  const btn = document.getElementById("install-app-btn");
  if (!btn) return;
  btn.addEventListener("click", function () {
    if (!deferredInstallPrompt) return;
    deferredInstallPrompt.prompt();
    deferredInstallPrompt.userChoice.finally(function () {
      deferredInstallPrompt = null;
      hideBanner();
    });
  });
});

window.addEventListener("appinstalled", hideBanner);
