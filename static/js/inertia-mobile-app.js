/**
 * INERTIA Mobile App — shell, search, pull-to-refresh, list filters.
 */
(function () {
  "use strict";

  var MQ = window.matchMedia("(max-width: 991.98px)");
  var PULL_THRESHOLD = 72;

  function isMobileApp() {
    return (
      MQ.matches ||
      (document.body &&
        (document.body.classList.contains("mobile-app") ||
          document.body.classList.contains("capacitor-shell")))
    );
  }

  function initShell() {
    if (!document.body) return;
    var on = isMobileApp();
    document.body.classList.toggle("inertia-mobile-app", on);
    document.body.classList.toggle("im-auth-route", !!document.querySelector(".im-auth-mobile, #loginForm, #emailForm"));
  }

  function syncClientSearch() {
    var mobileSearch = document.getElementById("imMobileClientSearch");
    var desktopSearch = document.getElementById("searchInput");
    if (!mobileSearch || !desktopSearch) return;
    mobileSearch.addEventListener("input", function () {
      desktopSearch.value = mobileSearch.value;
      desktopSearch.dispatchEvent(new Event("input", { bubbles: true }));
      desktopSearch.dispatchEvent(new Event("keyup", { bubbles: true }));
    });
  }

  function initMobileListFilters() {
    document.querySelectorAll("[data-im-mobile-filter]").forEach(function (input) {
      var targetId = input.getAttribute("data-im-mobile-filter");
      var list = document.getElementById(targetId);
      if (!list) return;
      input.addEventListener("input", function () {
        var q = (input.value || "").toLowerCase().trim();
        list.querySelectorAll("[data-im-search]").forEach(function (card) {
          var text = (card.getAttribute("data-im-search") || card.textContent || "").toLowerCase();
          card.style.display = !q || text.indexOf(q) >= 0 ? "" : "none";
        });
      });
    });
  }

  function initFilterChips() {
    document.querySelectorAll("[data-im-filter-chip]").forEach(function (chip) {
      chip.addEventListener("click", function () {
        var group = chip.getAttribute("data-im-filter-group");
        if (!group) return;
        document.querySelectorAll('[data-im-filter-group="' + group + '"]').forEach(function (c) {
          c.classList.remove("is-active");
        });
        chip.classList.add("is-active");
      });
    });
  }

  function initScrollToDesktop() {
    document.querySelectorAll(".im-scroll-to-desktop").forEach(function (link) {
      link.addEventListener("click", function (e) {
        var href = link.getAttribute("href") || "";
        var target = null;
        if (href.indexOf("#") === 0 && href.length > 1) {
          try {
            target = document.querySelector(href);
          } catch (err) {
            target = document.getElementById(href.slice(1));
          }
        }
        if (!target) {
          target =
            document.getElementById("imDesktopDetailTabs") ||
            document.querySelector(".im-desktop-only:not(.im-collapsed-on-mobile)");
        }
        if (target) {
          e.preventDefault();
          target.scrollIntoView({ behavior: "smooth", block: "start" });
          var focusable = target.querySelector(
            "input:not([type=hidden]), select, textarea, button"
          );
          if (focusable) {
            setTimeout(function () {
              focusable.focus({ preventScroll: true });
            }, 400);
          }
        }
      });
    });
  }

  function initPullToRefresh() {
    if (!isMobileApp()) return;
    var main = document.querySelector("main.mobile-page-root");
    if (!main || main.dataset.imPullInit === "1") return;
    main.dataset.imPullInit = "1";

    var startY = 0;
    var pulling = false;

    main.addEventListener(
      "touchstart",
      function (e) {
        if (window.scrollY > 8) return;
        startY = e.touches[0].clientY;
        pulling = true;
      },
      { passive: true }
    );

    main.addEventListener(
      "touchmove",
      function (e) {
        if (!pulling || window.scrollY > 8) return;
        var dy = e.touches[0].clientY - startY;
        if (dy > 0 && dy < PULL_THRESHOLD * 1.5) {
          main.style.transform = "translateY(" + Math.min(dy * 0.35, 40) + "px)";
        }
      },
      { passive: true }
    );

    main.addEventListener(
      "touchend",
      function (e) {
        if (!pulling) return;
        pulling = false;
        main.style.transform = "";
        var dy = (e.changedTouches[0].clientY || startY) - startY;
        if (window.scrollY <= 8 && dy >= PULL_THRESHOLD) {
          window.location.reload();
        }
      },
      { passive: true }
    );

    var hint = document.createElement("div");
    hint.className = "im-pull-hint im-mobile-only";
    hint.textContent = "Pull down to refresh";
    if (main.firstChild) {
      main.insertBefore(hint, main.firstChild);
    }
  }

  function showSkeletons(containerId, count) {
    var el = document.getElementById(containerId);
    if (!el) return;
    el.innerHTML = "";
    for (var i = 0; i < (count || 4); i++) {
      var sk = document.createElement("div");
      sk.className = "im-skeleton im-skeleton-card";
      el.appendChild(sk);
    }
  }

  function init() {
    initShell();
    syncClientSearch();
    initMobileListFilters();
    initFilterChips();
    initScrollToDesktop();
    initPullToRefresh();
  }

  if (typeof MQ.addEventListener === "function") {
    MQ.addEventListener("change", function () {
      initShell();
      initPullToRefresh();
    });
  } else if (typeof MQ.addListener === "function") {
    MQ.addListener(function () {
      initShell();
      initPullToRefresh();
    });
  }

  window.addEventListener("resize", initShell);

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  window.INERTIA_MOBILE_APP = {
    refresh: initShell,
    showSkeletons: showSkeletons,
  };
})();
