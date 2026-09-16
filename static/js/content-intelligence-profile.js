/**
 * Content intelligence profile — collapsible, editable dropdowns + questionnaire + tags.
 */
(function () {
  var root = document.getElementById("ic-profile-root");
  if (!root) return;

  var personId = parseInt(root.getAttribute("data-person-id"), 10);
  if (!personId || isNaN(personId)) {
    root.innerHTML =
      '<div class="alert alert-warning py-2 small">Missing client id — reload the page or contact support.</div>';
    return;
  }

  var csrfMeta = document.querySelector('meta[name="csrf-token"]');
  var csrf = csrfMeta ? csrfMeta.content : "";
  var collapseEl = document.getElementById("icProfileCollapse");
  var chevron = document.querySelector("#ic-profile-card .ic-collapse-chevron");
  var summaryEl = document.getElementById("ic-profile-summary");

  if (collapseEl && chevron) {
    collapseEl.addEventListener("show.bs.collapse", function () {
      chevron.classList.remove("fa-chevron-right");
      chevron.classList.add("fa-chevron-down");
    });
    collapseEl.addEventListener("hide.bs.collapse", function () {
      chevron.classList.remove("fa-chevron-down");
      chevron.classList.add("fa-chevron-right");
    });
    collapseEl.addEventListener("shown.bs.collapse", function () {
      chevron.classList.remove("fa-chevron-right");
      chevron.classList.add("fa-chevron-down");
    });
    if (typeof $ !== "undefined" && collapseEl.classList.contains("show") === false) {
      $(collapseEl).on("shown.bs.collapse hidden.bs.collapse", function (e) {
        if (e.type === "shown") {
          chevron.classList.remove("fa-chevron-right");
          chevron.classList.add("fa-chevron-down");
        } else {
          chevron.classList.remove("fa-chevron-down");
          chevron.classList.add("fa-chevron-right");
        }
      });
    }
  }

  var QUESTIONS = [
    { key: "has_deferred_decision", label: "Deferred a financial decision 2+ times (past year)" },
    { key: "misses_review_calls", label: "Missed 2+ scheduled review calls" },
    { key: "reacted_to_market_event", label: "Reacted emotionally to a market event" },
    { key: "driven_by_market_news", label: "Often asks about news-driven market moves" },
    { key: "reads_financial_content", label: "Reads financial content regularly" },
    { key: "uses_diy_platforms", label: "Uses DIY platforms (Zerodha, Groww, etc.)" },
    { key: "ca_manages_investments", label: "CA / RM / MFD actively manages investments" },
    { key: "is_senior_professional", label: "Senior professional (CXO, partner, director)" },
  ];

  var ADVISER_TAGS = [
    { id: "behav_friction", label: "Behavioural Friction" },
    { id: "emotional_inv", label: "Emotional Investor" },
    { id: "high_awareness", label: "High Awareness" },
    { id: "underserved", label: "Underserved Setup" },
    { id: "life_transition", label: "Life Transition" },
    { id: "relship_drift", label: "Relationship Drift" },
    { id: "high_earner", label: "High Earner" },
  ];

  var HOLDINGS = [
    { key: "holds_mf", label: "Mutual funds" },
    { key: "holds_stock", label: "Stocks / equity" },
    { key: "holds_lic", label: "LIC" },
    { key: "holds_ulip", label: "ULIP" },
    { key: "holds_fd", label: "Fixed deposit" },
    { key: "holds_real_estate", label: "Real estate / REIT" },
    { key: "holds_pms_aif", label: "PMS / AIF" },
  ];

  var state = { profile: null, loading: false, saving: false, error: "", saveOk: false };
  var profileLoaded = false;

  function readEmbeddedSeed() {
    var node = document.getElementById("ic-profile-seed");
    if (!node || !node.textContent) return null;
    try {
      return JSON.parse(node.textContent);
    } catch (e) {
      console.warn("ic-profile-seed parse failed", e);
      return null;
    }
  }

  function mergePageHoldingsIntoProfile(profile) {
    var page = window.icClientPageData;
    if (!profile || !page || !page.holdingsSummary) return profile;
    var summary = page.holdingsSummary;
    var total = summary.total_value != null ? Number(summary.total_value) : NaN;
    if (!isNaN(total) && total > 0 && profile.questionnaire && !profile.questionnaire.investable_assets) {
      profile.system_snapshot = profile.system_snapshot || {};
      profile.system_snapshot.total_value_inr = total;
    }
    return profile;
  }

  function el(tag, cls, html) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html != null) n.innerHTML = html;
    return n;
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/"/g, "&quot;");
  }

  function fetchJson(url, opts) {
    var req = Object.assign({ credentials: "same-origin", headers: { Accept: "application/json" } }, opts || {});
    req.headers = Object.assign({}, req.headers || {});
    return fetch(url, req).then(function (r) {
      return r.text().then(function (text) {
        var data = {};
        var isHtml = /^\s*</.test(text || "");
        try {
          data = text && !isHtml ? JSON.parse(text) : {};
        } catch (e) {
          /* non-JSON body */
        }
        if (!r.ok) {
          var msg = data.error || r.statusText || "Request failed (" + r.status + ")";
          if (r.status === 404) {
            msg =
              "Profile API not found (404). Ensure Campaign Studio routes are deployed, then hard-refresh (Ctrl+Shift+R).";
          }
          if (r.status === 400 && String(text).indexOf("CSRF") !== -1) {
            msg = "Session expired — refresh the page and try again.";
          }
          throw new Error(msg);
        }
        if (isHtml) {
          throw new Error("Unexpected HTML response — log in again or refresh the page.");
        }
        if (data && data.error && !data.person_id) {
          throw new Error(data.error);
        }
        return data;
      });
    });
  }

  function updateSummary(p) {
    if (!summaryEl || !p) return;
    var n = 0;
    if (p.adviser_tags) {
      Object.keys(p.adviser_tags).forEach(function (k) { if (p.adviser_tags[k]) n++; });
    }
    summaryEl.textContent = n ? n + " tag" + (n === 1 ? "" : "s") + " on" : "Not configured";
    summaryEl.classList.remove("d-none");
  }

  function buildSelect(id, label, options, value, allowEmpty) {
    var col = el("div", "col-md-6 col-lg-4 mb-2");
    var html =
      '<label class="form-label small mb-1" for="' + id + '">' + esc(label) +
      '</label><select class="form-select form-select-sm" id="' + id + '">';
    if (allowEmpty) html += '<option value="">— Not set —</option>';
    (options || []).forEach(function (opt) {
      var v = typeof opt === "object" ? opt.value : opt;
      var t = typeof opt === "object" ? opt.label : opt;
      html += '<option value="' + esc(v) + '"' + (value === v ? " selected" : "") + ">" + esc(t) + "</option>";
    });
    html += "</select>";
    col.innerHTML = html;
    return col;
  }

  function renderSystemHint(snapshot) {
    if (!snapshot) return null;
    var box = el("div", "alert alert-light border small py-2 mb-3");
    box.innerHTML =
      "<strong class=\"text-muted\">System snapshot</strong> (portfolio / CRM) — " +
      "last contact: <span class=\"fw-semibold\">" + esc(snapshot.last_contacted || "—") + "</span>, " +
      "category: <span class=\"fw-semibold\">" + esc(snapshot.category || "—") + "</span>" +
      (snapshot.total_value_inr
        ? ", portfolio (page): <span class=\"fw-semibold\">₹" + esc(String(Math.round(snapshot.total_value_inr))) + "</span>"
        : "") +
      ". Use the fields below to override for matching.";
    return box;
  }

  function renderError(message, withRetry) {
    root.innerHTML = "";
    var box = el("div", "alert alert-danger py-2 small mb-2", esc(message || "Could not load profile."));
    root.appendChild(box);
    if (withRetry) {
      var btn = el("button", "btn btn-outline-danger btn-sm", "Retry");
      btn.type = "button";
      btn.addEventListener("click", function () {
        state.error = "";
        profileLoaded = false;
        loadProfileOnce();
      });
      root.appendChild(btn);
    }
  }

  function render() {
    try {
      renderInner();
    } catch (err) {
      console.error("Content intelligence profile render error:", err);
      renderError(err.message || "Failed to display profile.", true);
    }
  }

  function renderInner() {
    root.innerHTML = "";
    if (state.loading) {
      root.appendChild(el("div", "text-muted", '<span class="spinner-border spinner-border-sm me-2"></span>Loading…'));
      return;
    }
    if (!state.profile) {
      renderError(state.error || "Could not load profile.", true);
      return;
    }

    var p = state.profile;
    var q = p.questionnaire || {};
    var opts = p.options || {};

    var hint = renderSystemHint(p.system_snapshot);
    if (hint) root.appendChild(hint);

    var profileSec = el("div", "mb-4");
    profileSec.appendChild(el("h6", "text-secondary border-bottom pb-2 mb-3", "Profile (editable)"));

    var row1 = el("div", "row");
    row1.appendChild(buildSelect("ic_age_bracket", "Age bracket", opts.age_brackets || [], q.age_bracket, true));
    row1.appendChild(buildSelect("ic_investable_assets", "Investable assets", opts.asset_brackets || [], q.investable_assets, true));
    row1.appendChild(buildSelect("ic_monthly_surplus", "Monthly surplus / SIP", opts.surplus_brackets || [], q.monthly_surplus, true));
    profileSec.appendChild(row1);

    var row2 = el("div", "row");
    var engOpts = (opts.engagement_scores || [0, 1, 2, 3, 4, 5]).map(function (n) {
      return { value: String(n), label: n === 0 ? "— Not set —" : n + " / 5" };
    });
    row2.appendChild(buildSelect("ic_engagement_score", "Engagement score", engOpts, String(q.engagement_score || 0), false));

    var lifeCol = buildSelect("ic_recent_life_event", "Recent life event", p.life_events || [], q.recent_life_event || "None", false);
    var mileCol = buildSelect("ic_upcoming_milestone", "Upcoming milestone", p.milestones || [], q.upcoming_milestone || "None", false);
    row2.appendChild(lifeCol);
    row2.appendChild(mileCol);
    profileSec.appendChild(row2);

    var row3 = el("div", "row g-2 mb-2");
    row3.innerHTML =
      '<div class="col-md-6"><label class="form-label small">Profession</label><input type="text" class="form-control form-control-sm" id="ic_profession" value="' + esc(q.profession || "") + '"></div>' +
      '<div class="col-md-6"><label class="form-label small">City</label><input type="text" class="form-control form-control-sm" id="ic_city" value="' + esc(q.city || "") + '"></div>';
    profileSec.appendChild(row3);

    var flags = el("div", "d-flex flex-wrap gap-3 mb-2");
    [
      { id: "ic_is_nri", key: "is_nri", label: "NRI" },
      { id: "ic_is_business_owner", key: "is_business_owner", label: "Business owner" },
      { id: "ic_is_cross_border", key: "is_cross_border", label: "Cross-border" },
    ].forEach(function (f) {
      var wrap = el("div", "form-check");
      wrap.innerHTML =
        '<input class="form-check-input" type="checkbox" id="' + f.id + '"' + (q[f.key] ? " checked" : "") + ">" +
        '<label class="form-check-label small" for="' + f.id + '">' + f.label + "</label>";
      flags.appendChild(wrap);
    });
    profileSec.appendChild(flags);

    profileSec.appendChild(el("div", "small text-muted mb-1", "Products held (outside portfolio feed)"));
    var holdRow = el("div", "row g-1");
    HOLDINGS.forEach(function (h) {
      var c = el("div", "col-6 col-md-4 col-lg-3");
      c.innerHTML =
        '<div class="form-check">' +
        '<input class="form-check-input" type="checkbox" id="ic_' + h.key + '"' + (q[h.key] ? " checked" : "") + ">" +
        '<label class="form-check-label small" for="ic_' + h.key + '">' + h.label + "</label></div>";
      holdRow.appendChild(c);
    });
    profileSec.appendChild(holdRow);
    root.appendChild(profileSec);

    var qSec = el("div", "mb-4");
    qSec.appendChild(el("h6", "text-secondary border-bottom pb-2 mb-3", "Annual questionnaire"));
    QUESTIONS.forEach(function (item) {
      var row = el("div", "form-check mb-2");
      row.innerHTML =
        '<input class="form-check-input" type="checkbox" id="icq_' + item.key + '"' + (q[item.key] ? " checked" : "") + ">" +
        '<label class="form-check-label" for="icq_' + item.key + '">' + item.label + "</label>";
      qSec.appendChild(row);
    });
    root.appendChild(qSec);

    var tSec = el("div", "mb-3");
    tSec.appendChild(el("h6", "text-secondary border-bottom pb-2 mb-3", "Behavioural tags (confirm annually)"));
    var sugMap = {};
    (p.tag_suggestions || []).forEach(function (s) { sugMap[s.tag] = s; });
    ADVISER_TAGS.forEach(function (t) {
      var on = p.adviser_tags && p.adviser_tags[t.id];
      var sug = sugMap[t.id];
      var card = el("div", "border rounded p-2 mb-2 " + (on ? "border-primary bg-light" : ""));
      var hintBadge = sug && sug.suggested
        ? ' <span class="badge bg-warning text-dark">Suggested (' + esc(sug.confidence) + ")</span>"
        : "";
      card.innerHTML =
        '<div class="form-check">' +
        '<input class="form-check-input" type="checkbox" id="ictag_' + t.id + '"' + (on ? " checked" : "") + ">" +
        '<label class="form-check-label fw-semibold" for="ictag_' + t.id + '">' + esc(t.label) + hintBadge + "</label>" +
        (sug && sug.reason ? '<div class="small text-muted ms-4 mt-1">' + esc(sug.reason) + "</div>" : "") +
        "</div>";
      tSec.appendChild(card);
    });
    root.appendChild(tSec);

    if (p.is_lead) {
      root.appendChild(el("div", "alert alert-info small py-2", "Lead profile: questionnaire and behavioural tags are saved in lead notes and used for Campaign Studio matching."));
    }

    if (state.saveOk) {
      root.appendChild(el("div", "alert alert-success py-2 small mt-2", "Profile saved successfully."));
    }
    if (state.error) {
      root.appendChild(el("div", "alert alert-danger py-2 small mt-2", esc(state.error)));
    }

    var btnRow = el("div", "d-flex flex-wrap gap-2 sticky-bottom bg-white pt-2 border-top");
    var saveBtn = el("button", "btn btn-primary btn-sm", state.saving ? "Saving…" : "Save profile");
    saveBtn.type = "button";
    saveBtn.disabled = state.saving;
    saveBtn.addEventListener("click", saveProfile);
    btnRow.appendChild(saveBtn);
    var refreshBtn = el("button", "btn btn-outline-secondary btn-sm", "Reset form");
    refreshBtn.type = "button";
    refreshBtn.addEventListener("click", function () {
      profileLoaded = false;
      loadProfileFromNetwork();
    });
    btnRow.appendChild(refreshBtn);
    root.appendChild(btnRow);

    updateSummary(p);
  }
  /* end renderInner */

  function val(id) {
    var node = document.getElementById(id);
    return node ? node.value : "";
  }

  function checked(id) {
    var node = document.getElementById(id);
    return node ? node.checked : false;
  }

  function collectPayload() {
    var questionnaire = {};
    QUESTIONS.forEach(function (item) {
      questionnaire[item.key] = checked("icq_" + item.key);
    });
    questionnaire.age_bracket = val("ic_age_bracket");
    questionnaire.investable_assets = val("ic_investable_assets");
    questionnaire.monthly_surplus = val("ic_monthly_surplus");
    questionnaire.recent_life_event = val("ic_recent_life_event");
    questionnaire.upcoming_milestone = val("ic_upcoming_milestone");
    questionnaire.profession = val("ic_profession").trim();
    questionnaire.city = val("ic_city").trim();
    questionnaire.engagement_score = parseInt(val("ic_engagement_score"), 10) || 0;
    questionnaire.is_nri = checked("ic_is_nri");
    questionnaire.is_business_owner = checked("ic_is_business_owner");
    questionnaire.is_cross_border = checked("ic_is_cross_border");
    HOLDINGS.forEach(function (h) {
      questionnaire[h.key] = checked("ic_" + h.key);
    });

    var adviser_tags = {};
    ADVISER_TAGS.forEach(function (t) {
      adviser_tags["tag_" + t.id] = checked("ictag_" + t.id);
    });
    return { questionnaire: questionnaire, adviser_tags: adviser_tags };
  }

  function saveProfile() {
    var payload = collectPayload();
    state.saving = true;
    state.error = "";
    state.saveOk = false;
    render();
    fetchJson("/persons/" + personId + "/content-profile", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
      body: JSON.stringify(payload),
    })
      .then(function () {
        state.saving = false;
        profileLoaded = false;
        return loadProfileFromNetwork();
      })
      .then(function () {
        state.saveOk = true;
        render();
        setTimeout(function () {
          state.saveOk = false;
          render();
        }, 4000);
      })
      .catch(function (e) {
        state.saving = false;
        state.error = e.message || "Save failed";
        render();
      });
  }

  function applyProfileData(data) {
    state.profile = mergePageHoldingsIntoProfile(data);
    state.loading = false;
    state.error = "";
    render();
  }

  function loadProfileFromNetwork() {
    state.loading = true;
    state.error = "";
    render();
    return fetchJson("/persons/" + personId + "/content-profile")
      .then(function (data) {
        applyProfileData(data);
      })
      .catch(function (e) {
        state.loading = false;
        state.profile = null;
        state.error = e.message || "Failed to load profile";
        render();
      });
  }

  function loadProfileOnce() {
    if (profileLoaded) return Promise.resolve();
    profileLoaded = true;

    var seed = readEmbeddedSeed();
    if (seed && seed.person_id) {
      applyProfileData(seed);
      return Promise.resolve();
    }
    return loadProfileFromNetwork();
  }

  if (collapseEl) {
    collapseEl.addEventListener("shown.bs.collapse", function () {
      if (!profileLoaded) loadProfileOnce();
    });
  }

  /* Server embeds seed from holdings table + saved questionnaire — no forward calc API. */
  loadProfileOnce();
})();
