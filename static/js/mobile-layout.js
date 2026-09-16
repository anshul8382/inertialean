/**
 * Mobile layout runtime fixes — auto-wrap wide tables, card layout, action stacks.
 * Works on narrow viewports and Capacitor shell (all pages using base.html).
 */
(function () {
  "use strict";

  var WRAP_CLASS = "inertia-auto-wrap";
  var MOBILE_MQ = window.matchMedia("(max-width: 991.98px)");
  var CARD_TABLE_MAX_COLS = 10;
  var CARD_TABLE_MAX_ROWS = 30;

  function isMobileLayout() {
    return (
      MOBILE_MQ.matches ||
      (document.body &&
        (document.body.classList.contains("mobile-app") ||
          document.body.classList.contains("mobile-viewport")))
    );
  }

  function updateViewportClass() {
    if (!document.body) return;
    document.body.classList.toggle("mobile-viewport", MOBILE_MQ.matches);
    if (!document.body.classList.contains("modern-app")) {
      document.body.classList.add("modern-app");
    }
  }

  function shouldWrapTable(table) {
    if (!table || table.tagName !== "TABLE") return false;
    if (
      table.classList.contains("rec-card-table") ||
      table.classList.contains("mobile-card-table")
    )
      return false;
    if (table.closest("." + WRAP_CLASS)) return false;
    if (table.closest(".table-responsive")) return false;
    if (table.closest(".dataTables_scrollBody")) return false;
    if (table.getAttribute("data-inertia-no-wrap") === "true") return false;
    return true;
  }

  function shouldUseCardTable(table) {
    if (!table || table.tagName !== "TABLE") return false;
    if (
      table.classList.contains("rec-card-table") ||
      table.classList.contains("mobile-card-table")
    )
      return false;
    if (table.getAttribute("data-mobile-table") === "scroll") return false;
    if (table.getAttribute("data-inertia-no-card") === "true") return false;
    if (table.classList.contains("dataTable")) return false;
    if (
      table.closest(
        ".dataTables_wrapper, #emailPreview, .dataTables_scroll, .modal, .dropdown-menu"
      )
    )
      return false;
    if (!table.classList.contains("table")) return false;

    var headers = table.querySelectorAll("thead th");
    var rows = table.querySelectorAll("tbody tr");
    if (!headers.length || headers.length > CARD_TABLE_MAX_COLS) return false;
    if (rows.length > CARD_TABLE_MAX_ROWS) return false;
    return true;
  }

  function headerLabel(th) {
    if (!th) return "";
    var text = (th.innerText || th.textContent || "").replace(/\s+/g, " ").trim();
    return text.split(" ")[0] === "Within" ? text : text.replace(/\s*Within\s.+/i, "").trim();
  }

  function annotateCardTable(table) {
    if (
      !table ||
      (!table.classList.contains("rec-card-table") &&
        !table.classList.contains("mobile-card-table"))
    )
      return;
    var headers = table.querySelectorAll("thead th");
    if (!headers.length) return;
    var labels = [];
    for (var h = 0; h < headers.length; h++) {
      labels.push(headerLabel(headers[h]));
    }
    var rows = table.querySelectorAll("tbody tr");
    for (var r = 0; r < rows.length; r++) {
      var cells = rows[r].querySelectorAll("td");
      for (var c = 0; c < cells.length; c++) {
        var td = cells[c];
        if (td.querySelector('input[type="checkbox"]')) {
          td.classList.add("rec-td-check", "mobile-td-check");
          td.setAttribute("data-label", "");
        } else if (labels[c]) {
          td.setAttribute("data-label", labels[c]);
        }
      }
    }
    var host = table.closest(".table-responsive, .inertia-auto-wrap");
    if (host) {
      host.classList.add("rec-card-table-host", "mobile-card-table-host");
    }
  }

  function promoteCardTables(root) {
    if (!isMobileLayout()) return;
    var scope = root || document;
    if (!scope.querySelectorAll) return;
    var tables = scope.querySelectorAll("table.table");
    for (var i = 0; i < tables.length; i++) {
      var table = tables[i];
      if (shouldUseCardTable(table)) {
        table.classList.add("mobile-card-table");
      }
    }
  }

  function applyCardTables(root) {
    if (!isMobileLayout()) return;
    var scope = root || document;
    if (!scope.querySelectorAll) return;
    promoteCardTables(scope);
    var tables = scope.querySelectorAll(
      ".rec-card-table, .mobile-card-table, .rec-workflow-mobile .rec-card-table"
    );
    for (var i = 0; i < tables.length; i++) {
      annotateCardTable(tables[i]);
    }
  }

  function stackActionRows(root) {
    if (!isMobileLayout()) return;
    var scope = root || document;
    if (!scope.querySelectorAll) return;

    var selectors = [
      ".text-center",
      ".mt-3.text-center",
      ".mt-4.text-center",
      ".card-body > .d-flex.gap-2",
      ".card-body > .btn-group",
    ];
    for (var s = 0; s < selectors.length; s++) {
      var nodes = scope.querySelectorAll(selectors[s]);
      for (var i = 0; i < nodes.length; i++) {
        var el = nodes[i];
        if (el.classList.contains("mobile-action-stack")) continue;
        if (el.closest(".dataTables_wrapper, .navbar, .modal-footer, .rec-sticky-actions"))
          continue;
        var buttons = el.querySelectorAll(":scope > .btn, :scope > a.btn, :scope > button.btn");
        if (buttons.length >= 2) {
          el.classList.add("mobile-action-stack");
        }
      }
    }
  }

  function wrapTable(table) {
    if (!shouldWrapTable(table)) return;
    var wrapper = document.createElement("div");
    wrapper.className = "table-responsive " + WRAP_CLASS;
    wrapper.setAttribute("role", "region");
    wrapper.setAttribute("aria-label", "Scrollable table");
    var parent = table.parentNode;
    if (!parent) return;
    parent.insertBefore(wrapper, table);
    wrapper.appendChild(table);
  }

  function wrapTables(root) {
    if (!isMobileLayout()) return;
    var scope = root || document;
    var tables = scope.querySelectorAll ? scope.querySelectorAll("table") : [];
    for (var i = 0; i < tables.length; i++) {
      var table = tables[i];
      if (
        table.classList.contains("mobile-card-table") ||
        table.classList.contains("rec-card-table")
      )
        continue;
      wrapTable(table);
    }
  }

  function fixInlineInputGroups(root) {
    if (!isMobileLayout()) return;
    var scope = root || document;
    var groups = scope.querySelectorAll
      ? scope.querySelectorAll('.input-group[style*="width"]')
      : [];
    for (var i = 0; i < groups.length; i++) {
      groups[i].style.maxWidth = "100%";
      groups[i].style.width = "100%";
    }
  }

  function applyLayoutFixes(root) {
    updateViewportClass();
    if (!isMobileLayout()) return;
    applyCardTables(root);
    stackActionRows(root);
    wrapTables(root);
    fixInlineInputGroups(root);
  }

  var observerTimer = null;
  function scheduleObserveFix() {
    if (observerTimer) clearTimeout(observerTimer);
    observerTimer = setTimeout(function () {
      applyLayoutFixes(document.body);
    }, 120);
  }

  function startMutationObserver() {
    if (!window.MutationObserver || !document.body) return;
    var observer = new MutationObserver(function (mutations) {
      if (!isMobileLayout()) return;
      for (var i = 0; i < mutations.length; i++) {
        var m = mutations[i];
        if (m.addedNodes && m.addedNodes.length) {
          scheduleObserveFix();
          return;
        }
      }
    });
    observer.observe(document.body, { childList: true, subtree: true });
  }

  function hookDataTables() {
    if (!window.jQuery || !window.jQuery.fn.dataTable) return;
    var $ = window.jQuery;
    if ($.fn.dataTable.ext && $.fn.dataTable.ext.type) {
      $(document).on("init.dt", function () {
        scheduleObserveFix();
      });
    }
  }

  function init() {
    updateViewportClass();
    applyLayoutFixes(document.body);
    startMutationObserver();
    if (document.readyState === "complete") {
      hookDataTables();
    } else {
      window.addEventListener("load", hookDataTables);
    }
  }

  if (typeof MOBILE_MQ.addEventListener === "function") {
    MOBILE_MQ.addEventListener("change", function () {
      updateViewportClass();
      applyLayoutFixes(document.body);
    });
  } else if (typeof MOBILE_MQ.addListener === "function") {
    MOBILE_MQ.addListener(function () {
      updateViewportClass();
      applyLayoutFixes(document.body);
    });
  }

  window.addEventListener("resize", updateViewportClass);

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  window.INERTIA_MOBILE_LAYOUT = {
    refresh: function () {
      applyLayoutFixes(document.body);
    },
  };
})();
