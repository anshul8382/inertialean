/**
 * Mobile App Configuration
 * Detects Capacitor native shell (iOS/Android) vs mobile browser vs desktop web.
 */
(function () {
  "use strict";

  var cap = typeof window !== "undefined" && window.Capacitor;
  var isCapacitor =
    !!cap ||
    (typeof window !== "undefined" && /Capacitor/i.test(navigator.userAgent));

  // Same origin as the loaded web app (Capacitor server.url); empty string = relative URLs on web.
  var apiBaseUrl = isCapacitor && window.location && window.location.origin
    ? window.location.origin
    : "";

  window.APP_CONFIG = {
    isMobileApp: isCapacitor,
    isCapacitorShell: isCapacitor,
    apiBaseUrl: apiBaseUrl,
    platform: isCapacitor
      ? (cap && cap.getPlatform ? cap.getPlatform() : "unknown")
      : "web",
  };

  if (
    window.location &&
    (window.location.hostname === "localhost" ||
      window.location.hostname === "127.0.0.1")
  ) {
    console.log("Mobile app config:", window.APP_CONFIG);
  }

  function applyMobileShellClasses() {
    if (!document.body || !isCapacitor) return;
    document.body.classList.add("mobile-app", "capacitor-shell");
    var platform = window.APP_CONFIG.platform;
    if (platform) {
      document.body.classList.add("platform-" + platform);
    }
  }

  if (document.body) {
    applyMobileShellClasses();
  } else {
    document.addEventListener("DOMContentLoaded", applyMobileShellClasses);
  }

  // Android hardware back: navigate history or exit app.
  if (isCapacitor && cap && cap.Plugins && cap.Plugins.App) {
    cap.Plugins.App.addListener("backButton", function (ev) {
      var canGoBack = ev && ev.canGoBack;
      if (!canGoBack && window.history.length <= 1) {
        cap.Plugins.App.exitApp();
      } else {
        window.history.back();
      }
    });
  } else if (isCapacitor && cap && cap.App && cap.App.addListener) {
    cap.App.addListener("backButton", function (ev) {
      var canGoBack = ev && ev.canGoBack;
      if (!canGoBack) {
        cap.App.exitApp();
      } else {
        window.history.back();
      }
    });
  }

  // Status bar styling (native shell only).
  if (isCapacitor && cap && cap.Plugins && cap.Plugins.StatusBar) {
    try {
      cap.Plugins.StatusBar.setBackgroundColor({ color: "#2563eb" });
      cap.Plugins.StatusBar.setStyle({ style: "LIGHT" });
    } catch (e) {
      /* optional plugin */
    }
  }

  window.INERTIA_SKIP_SERVICE_WORKER = isCapacitor;
})();
