/* IIS Research UI — modern workspace (tabs + large chat, no approval gates) */
(() => {
  const $ = (id) => document.getElementById(id);

  let currentSession = null;
  let lastSession = null;
  let pendingFiles = [];
  let pendingSections = []; // parallel to pendingFiles
  let apiToken = localStorage.getItem('iis_token') || '';
  const IIS_BASE = String(window.IIS_BASE || '').replace(/\/$/, '');
  const IIS_INERTIA_AUTH_ONLY = !!window.IIS_INERTIA_AUTH_ONLY;
  function apiUrl(path) {
    if (!path) return IIS_BASE || '/';
    if (/^https?:\/\//i.test(path)) return path;
    return IIS_BASE + path;
  }
  let authRequired = false;
  let isProcessing = false;
  let selectedCompany = null;
  let workspaceTab = 'files';
  let overviewShownFor = null;
  let notesTimer = null;
  /** @type {{name:string,key?:string,metrics?:number,docs?:number,ticker?:string,source?:string}[]} */
  let companyCatalog = [];
  /** @type {any[]} light session cards from /api/sessions */
  let recentSessionCards = [];
  let suggestActive = -1;

  const WS_PANELS = {
    files: 'center-upload',
    library: 'library-workspace',
    review: 'review-workspace',
    flags: 'flags-workspace',
    guidance: 'guidance-workspace',
    peers: 'peers-workspace',
    valuation: 'valuation-workspace',
    progress: 'progress-workspace',
    report: 'report-workspace',
    direct: 'direct-workspace',
  };

  // ── API ──────────────────────────────────────────────────────────
  async function api(path, opts = {}) {
    const headers = {};
    if (opts.body && !(opts.body instanceof FormData)) headers['Content-Type'] = 'application/json';
    if (apiToken) headers['X-API-Token'] = apiToken;
    const res = await fetch(apiUrl(path), {
      headers,
      ...opts,
      body: opts.body instanceof FormData ? opts.body
        : opts.body ? JSON.stringify(opts.body) : undefined,
    });
    if (res.status === 401) {
      if (IIS_INERTIA_AUTH_ONLY) {
        throw new Error('Inertia session required — reopen IIS from the Inertia menu');
      }
      showLogin();
      throw new Error('Authentication required');
    }
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(typeof err.detail === 'string' ? err.detail : JSON.stringify(err.detail));
    }
    return res.json();
  }

  function showLogin() {
    if (IIS_INERTIA_AUTH_ONLY) {
      hideLogin();
      return;
    }
    $('login-overlay')?.classList.remove('hidden');
    if ($('login-error')) $('login-error').textContent = '';
    ($('login-email') || $('login-password'))?.focus?.();
  }
  function hideLogin() { $('login-overlay')?.classList.add('hidden'); }

  function setUserBadge(label) {
    const b = $('user-badge');
    if (b) b.textContent = label || (apiToken ? 'Signed in' : 'Account');
  }

  async function doLogin() {
    const email = ($('login-email')?.value || '').trim();
    const password = $('login-password')?.value || '';
    if (!email || !password) {
      if ($('login-error')) $('login-error').textContent = 'Email and password required.';
      return;
    }
    try {
      const r = await fetch(apiUrl('/api/auth/login'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
      });
      const u = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(u.detail || 'Login failed');
      apiToken = u.api_token || '';
      localStorage.setItem('iis_token', apiToken);
      setUserBadge(u.name || u.email || 'Signed in');
      hideLogin();
      loadSessions();
      loadCompanyCatalog().catch(() => {});
      pushStatus('Signed in as ' + (u.name || u.email), 'ok');
    } catch (e) {
      if ($('login-error')) $('login-error').textContent = e.message || String(e);
    }
  }

  async function doRegister() {
    const email = ($('login-email')?.value || '').trim();
    const password = $('login-password')?.value || '';
    const name = ($('login-name')?.value || '').trim() || 'Analyst';
    if (!email || !password) {
      if ($('login-error')) $('login-error').textContent = 'Email and password required.';
      return;
    }
    try {
      const r = await fetch(apiUrl('/api/auth/register'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password, name }),
      });
      const u = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof u.detail === 'string' ? u.detail : (u.detail || 'Registration failed'));
      apiToken = u.api_token || '';
      localStorage.setItem('iis_token', apiToken);
      setUserBadge(u.name || u.email || 'Signed in');
      hideLogin();
      loadSessions();
      loadCompanyCatalog().catch(() => {});
      pushStatus('Registered as ' + (u.name || u.email), 'ok');
    } catch (e) {
      if ($('login-error')) $('login-error').textContent = e.message || String(e);
    }
  }

  function doLogout() {
    apiToken = '';
    localStorage.removeItem('iis_token');
    setUserBadge('Account');
    hideLogin();
    pushStatus('Signed out');
  }

  // ── Status / chat helpers ────────────────────────────────────────
  function stripMdLight(s) {
    return String(s || '').replace(/\*\*/g, '').replace(/\s+/g, ' ').trim();
  }

  function renderReadinessPanel(payload) {
    const panel = $('readiness-panel');
    if (!panel) return;
    const ready = payload?.data_readiness || payload || {};
    const active = ready.active || payload?.asks || [];
    const later = ready.later || [];
    const laterCount = ready.later_count != null ? ready.later_count : later.length;
    const have = ready.have || [];
    if (!active.length && !laterCount) {
      panel.hidden = true;
      return;
    }
    panel.hidden = false;
    const haveEl = $('readiness-have');
    if (haveEl) {
      haveEl.textContent = have.length ? ('On file: ' + have.slice(0, 4).join(' · ')) : '';
    }
    const ol = $('readiness-active');
    if (ol) {
      ol.innerHTML = active.map((a) => {
        const prompt = stripMdLight(a.prompt || a);
        const section = a.section || '';
        const addBtn = section
          ? ` <button type="button" class="rd-add" data-add-section="${escapeHtml(section)}">+ Add</button>`
          : '';
        return `<li>${escapeHtml(prompt)}${addBtn}</li>`;
      }).join('');
      ol.querySelectorAll('[data-add-section]').forEach((btn) => {
        btn.addEventListener('click', () => {
          const sec = btn.getAttribute('data-add-section');
          const card = document.querySelector(`.need-card[data-section="${sec}"] .need-add`);
          if (card) card.click();
          else setWorkspaceTab('files');
        });
      });
    }
    const laterEl = $('readiness-later');
    if (laterEl) {
      laterEl.textContent = laterCount
        ? `+${laterCount} more helpful files after these`
        : (active.length ? 'These are the top priorities right now.' : '');
    }
    const btnAll = $('btn-readiness-all');
    const qEl = $('readiness-queue');
    if (btnAll && qEl) {
      if (laterCount > 0) {
        btnAll.hidden = false;
        btnAll.onclick = () => {
          const show = qEl.hidden;
          qEl.hidden = !show;
          btnAll.textContent = show ? 'Hide full list' : 'Show all gaps';
          if (show) {
            qEl.innerHTML = later.map((a) => `<li>${escapeHtml(stripMdLight(a.prompt || a))}</li>`).join('');
          }
        };
      } else {
        btnAll.hidden = true;
        qEl.hidden = true;
        qEl.innerHTML = '';
      }
    }
  }

  async function refreshReadinessAsks() {
    if (!currentSession) return null;
    try {
      const r = await api(`/api/sessions/${currentSession}/asks`);
      renderReadinessPanel(r);
      return r;
    } catch (_) {
      if (lastSession?.data_readiness || lastSession?.user_asks) {
        renderReadinessPanel({
          data_readiness: lastSession.data_readiness,
          asks: (lastSession.user_asks || {}).asks || [],
        });
      }
      return null;
    }
  }

  function pushStatus(text, kind) {
    const box = $('side-activity');
    if (!box) return;
    const row = document.createElement('div');
    row.className = 'side-activity-item' + (kind === 'ok' ? ' ok' : kind === 'err' ? ' err' : '');
    row.textContent = text;
    box.prepend(row);
    while (box.children.length > 40) box.removeChild(box.lastChild);
  }

  let lastSystemText = '';
  let reviewSubtab = 'brief';
  const READING_TABS = new Set([
    'report', 'direct', 'progress', 'review', 'flags', 'guidance', 'peers', 'valuation', 'library',
  ]);
  const REVIEW_SUBTAB_ORDER = ['brief', 'financials', 'assumptions', 'flags'];

  function isChatWide() {
    return document.body.classList.contains('chat-wide');
  }

  function setChatWide(on) {
    const want = !!on;
    document.body.classList.toggle('chat-wide', want);
    const btn = $('btn-chat-wide');
    if (btn) {
      btn.setAttribute('aria-pressed', want ? 'true' : 'false');
      btn.textContent = want ? 'Restore layout' : 'Widen chat';
      btn.title = want
        ? 'Back to sidebar + workspace'
        : 'Widen chat for long answers';
    }
    if (want) {
      const log = $('chat-log');
      if (log) log.scrollTop = log.scrollHeight;
      $('reading-answers')?.classList.add('hidden');
    } else if (READING_TABS.has(workspaceTab)) {
      $('reading-answers')?.classList.remove('hidden');
    }
  }

  function appendReadingAnswer(html, cls) {
    if (isChatWide()) return; // full chat log is visible
    const dock = $('reading-answers');
    const list = $('reading-answers-list');
    if (!dock || !list) return;
    dock.classList.remove('hidden');
    const el = document.createElement('div');
    el.className = 'msg ' + (cls || 'system');
    el.innerHTML = html;
    list.appendChild(el);
    while (list.children.length > 12) list.removeChild(list.firstChild);
    list.scrollTop = list.scrollHeight;
  }

  function addSystem(html) {
    const text = String(html || '');
    // Skip exact duplicate spam (e.g. double Continue clicks)
    if (text && text === lastSystemText) return;
    lastSystemText = text;
    const rendered = renderMarkdown(text);
    const log = $('chat-log');
    if (log) {
      const welcome = $('welcome');
      if (welcome) welcome.remove();
      const el = document.createElement('div');
      el.className = 'msg system';
      el.innerHTML = rendered;
      log.appendChild(el);
      log.scrollTop = log.scrollHeight;
    }
    if (READING_TABS.has(workspaceTab)) appendReadingAnswer(rendered, 'system');
  }

  /** Classify whether the active step is Python compute vs Hive/LLM. */
  function describeAnalyzeEngine(st) {
    const phase = String(st?.phase || '').toLowerCase();
    const msg = String(st?.message || '').toLowerCase();
    const mod = String(st?.llm_current_module || '').toLowerCase();
    const step = String(st?.current_step || '').toLowerCase();

    if (
      phase.includes('initiating_coverage')
      || mod.includes('initiating_coverage')
      || /initiating coverage|hive.*fundamental|fundamental analyst/i.test(msg)
    ) {
      return { engine: 'Hive', detail: 'Initiating coverage (LLM write-up)' };
    }
    if (phase.includes('llm_analyst') || mod.includes('llm_analyst')) {
      return { engine: 'Hive', detail: 'LLM analyst (tools + report)' };
    }
    if (phase.startsWith('llm:') || phase === 'llm' || mod) {
      const m = mod || phase.replace(/^llm:/, '') || 'LLM';
      return { engine: 'Hive', detail: `LLM · ${m.replace(/_/g, ' ')}` };
    }
    if (phase.includes('python_dossier') || /python.*(pack|dossier|overview)/i.test(msg)) {
      return { engine: 'Python', detail: 'Building research pack / financial tables' };
    }
    if (phase.includes('evidence_dossier')) {
      return { engine: 'Python', detail: 'Evidence dossier' };
    }
    if (phase.includes('research_packs') || phase.includes('unified_doc')) {
      return { engine: 'Python', detail: phase.includes('unified') ? 'Document digest' : 'Research packs' };
    }
    if (phase.includes('keyword') || phase.includes('enrich') || /overview|snippet.?review/i.test(msg)) {
      return { engine: 'Hive', detail: 'LLM enrichment / overview polish' };
    }
    if (step === 'report' || phase === 'report_running') {
      if (/hive|llm|initiating|analyst/i.test(msg + phase)) {
        return { engine: 'Hive', detail: rawMsgOr(st, 'Writing report') };
      }
      return { engine: 'Python', detail: rawMsgOr(st, 'Report stage') };
    }
    if (phase.startsWith('start:') || phase.startsWith('run:') || phase.startsWith('done:')) {
      return { engine: 'Python', detail: 'Pipeline module' };
    }
    if (Number(st?.llm_calls_total || 0) > 0 && (st?.status === 'running')) {
      // Tokens rising with no clear phase → likely LLM
      if (st?.llm_current_module) {
        return { engine: 'Hive', detail: `LLM · ${String(st.llm_current_module).replace(/_/g, ' ')}` };
      }
    }
    return { engine: 'Python', detail: 'Pipeline' };
  }

  function rawMsgOr(st, fallback) {
    const raw = String(st?.message || '').replace(/\s*·\s*\d+m\d{2}s\s*$/i, '').trim();
    return raw || fallback;
  }

  /** Human line for Recent answers / Progress while analyze runs. */
  function formatAnalyzeProgressLine(st) {
    const step = st?.current_step || '';
    const phase = (st?.phase || '').toLowerCase();
    const rawMsg = String(st?.message || '').replace(/\s*·\s*\d+m\d{2}s\s*$/i, '').trim();
    const labels = Object.fromEntries(ANALYZE_STAGES);
    const stepLabel = labels[step] || (step ? step.replace(/_/g, ' ') : 'analysis');
    const pct = typeof st?.progress === 'number' ? Math.round(st.progress * 100) : null;
    const eng = describeAnalyzeEngine(st);

    let action = '';
    if (phase.includes('initiating_coverage') || /initiating coverage/i.test(rawMsg)) {
      action = '**Hive** · initiating coverage write-up';
    } else if (phase.includes('llm_analyst') || phase === 'llm_analyst') {
      action = '**Hive** · LLM analyst (tools + final markdown)';
    } else if (phase.includes('python_dossier') || phase === 'python_dossier') {
      action = '**Python** · research pack (financials / scorecard / slots)';
    } else if (phase.includes('evidence_dossier') || phase === 'evidence_dossier') {
      action = '**Python** · evidence dossier';
    } else if (phase.includes('research_packs')) {
      action = '**Python** · AR / concall / broker packs';
    } else if (phase.includes('unified_doc')) {
      action = '**Python** · unified document digest';
    } else if (phase.includes('keyword') || phase.includes('enrich')) {
      action = '**Hive** · enriching extractors';
    } else if (phase.includes('overview') || /overview|snippet.?review/i.test(rawMsg)) {
      action = '**Hive** · overview snippet review';
    } else if (phase.startsWith('llm:')) {
      action = `**Hive** · ${phase.slice(4).replace(/_/g, ' ')}`;
    } else if (phase === 'llm' || /\bhive\b|\bllm\b|ollama|gpt/i.test(rawMsg)) {
      action = `**Hive** · ${stepLabel}`;
    } else if (phase.startsWith('start:')) {
      action = `**Python** · starting ${stepLabel}`;
    } else if (phase.startsWith('run:')) {
      action = `**Python** · running ${stepLabel}`;
    } else if (phase === 'report_running' || step === 'report') {
      action = rawMsg
        ? `${eng.engine === 'Hive' ? '**Hive**' : '**Python**'} · ${rawMsg}`
        : `**${eng.engine}** · writing report`;
    } else if (phase === 'done' || step === 'done') {
      action = 'Finishing up';
    } else if (rawMsg) {
      action = `**${eng.engine}** · ${rawMsg}`;
    } else {
      action = `**${eng.engine}** · ${stepLabel}`;
    }

    const calls = Number(st?.llm_calls_total || 0);
    const toks = Number(st?.llm_tokens_total || 0);
    const llmBit = (calls > 0 || toks > 0)
      ? ` · ${calls} LLM call${calls === 1 ? '' : 's'}`
        + (toks ? ` · ${(st?.llm_tokens_estimated ? '~' : '')}${toks.toLocaleString()} tok` : '')
      : '';
    const suffix = pct != null ? ` · ${pct}%` : '';
    return `⟳ ${action}${llmBit}${suffix}`;
  }

  function postAnalyzeProgress(st) {
    const line = formatAnalyzeProgressLine(st);
    pushStatus(line.replace(/\*\*/g, ''), 'ok');
    addSystem(line);
  }

  function renderMarkdown(s) {
    return String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\r?\n/g, '<br/>');
  }

  function mdToHtml(md) {
    const raw = String(md || '').replace(/\r\n/g, '\n');
    const escText = (s) => String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    const attr = (s) => encodeURIComponent(String(s || ''));
    const inline = (s) => escText(s)
      .replace(/\[\[src:([^|\]]+)\|([\s\S]*?)\]\]/g, (_, title, body) => {
        const t = String(title || 'Source').replace(/¦/g, '|');
        const b = String(body || '').replace(/¦/g, '|').replace(/〉/g, ']');
        return `<button type="button" class="src-ref-btn" data-src-title="${attr(t)}" data-src-body="${attr(b)}" title="Show source excerpt" aria-label="Show source">ref</button>`;
      })
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/\*(.+?)\*/g, '<em>$1</em>')
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');

    const lines = raw.split('\n');
    const out = [];
    let i = 0;
    let inUl = false;
    let inOl = false;
    const closeLists = () => {
      if (inUl) { out.push('</ul>'); inUl = false; }
      if (inOl) { out.push('</ol>'); inOl = false; }
    };

    const isTableSep = (line) => /^\s*\|?[\s:|-]+\|[\s:|-]*\|?\s*$/.test(line);
    const splitRow = (line) => line.replace(/^\s*\|/, '').replace(/\|\s*$/, '').split('|').map((c) => c.trim());

    while (i < lines.length) {
      const line = lines[i];
      // GFM table: header + separator + rows
      if (line.includes('|') && i + 1 < lines.length && isTableSep(lines[i + 1])) {
        closeLists();
        const headers = splitRow(line);
        i += 2;
        const rows = [];
        while (i < lines.length && lines[i].includes('|') && !isTableSep(lines[i])) {
          rows.push(splitRow(lines[i]));
          i += 1;
        }
        out.push('<div class="md-table-wrap"><table class="md-table"><thead><tr>');
        headers.forEach((h) => out.push(`<th>${inline(h)}</th>`));
        out.push('</tr></thead><tbody>');
        rows.forEach((cells) => {
          out.push('<tr>');
          headers.forEach((_, idx) => out.push(`<td>${inline(cells[idx] ?? '')}</td>`));
          out.push('</tr>');
        });
        out.push('</tbody></table></div>');
        continue;
      }
      if (/^### (.+)/.test(line)) {
        closeLists();
        out.push(`<h3>${inline(line.replace(/^### /, ''))}</h3>`);
        i += 1; continue;
      }
      if (/^## (.+)/.test(line)) {
        closeLists();
        out.push(`<h2>${inline(line.replace(/^## /, ''))}</h2>`);
        i += 1; continue;
      }
      if (/^# (.+)/.test(line)) {
        closeLists();
        out.push(`<h1>${inline(line.replace(/^# /, ''))}</h1>`);
        i += 1; continue;
      }
      if (/^---+$/.test(line.trim())) {
        closeLists();
        out.push('<hr/>');
        i += 1; continue;
      }
      if (/^\s*[-*] (.+)/.test(line)) {
        if (inOl) { out.push('</ol>'); inOl = false; }
        if (!inUl) { out.push('<ul>'); inUl = true; }
        out.push(`<li>${inline(line.replace(/^\s*[-*] /, ''))}</li>`);
        i += 1; continue;
      }
      if (/^\s*\d+\. (.+)/.test(line)) {
        if (inUl) { out.push('</ul>'); inUl = false; }
        if (!inOl) { out.push('<ol>'); inOl = true; }
        out.push(`<li>${inline(line.replace(/^\s*\d+\. /, ''))}</li>`);
        i += 1; continue;
      }
      if (!line.trim()) {
        closeLists();
        out.push('');
        i += 1; continue;
      }
      closeLists();
      out.push(`<p>${inline(line)}</p>`);
      i += 1;
    }
    closeLists();
    return out.join('\n');
  }

  // ── Tabs ─────────────────────────────────────────────────────────
  function setWorkspaceTab(tab) {
    workspaceTab = tab;
    // Opening a workspace panel exits wide chat so the panel is visible
    if (isChatWide()) setChatWide(false);
    const reading = READING_TABS.has(tab);
    document.body.classList.toggle('reading-mode', reading);
    document.body.classList.toggle('tab-progress', tab === 'progress');
    document.body.classList.toggle('tab-report', tab === 'report' || tab === 'direct');
    document.body.classList.toggle('tab-files', tab === 'files');
    const tabs = $('workspace-tabs');
    if (tabs) {
      tabs.querySelectorAll('[data-ws-tab]').forEach((b) => {
        const on = b.getAttribute('data-ws-tab') === tab;
        b.classList.toggle('active', on);
        if (on) b.classList.remove('has-new');
      });
    }
    Object.entries(WS_PANELS).forEach(([k, id]) => {
      const el = $(id);
      if (!el) return;
      if (k === 'files') {
        el.classList.toggle('hidden', tab !== 'files');
        // center-upload uses max-height layout; keep display via hidden
        el.style.display = tab === 'files' ? '' : 'none';
      } else {
        el.classList.toggle('visible', k === tab);
        el.classList.toggle('hidden', k !== tab);
      }
    });
    const dock = $('reading-answers');
    if (dock) dock.classList.toggle('hidden', !reading || isChatWide());
    // Chat composer stays available; enable when session exists
    const input = $('chat-input');
    if (input) {
      input.disabled = !currentSession;
      input.placeholder = reading
        ? 'Ask about this view…'
        : 'Ask about uploaded data…';
    }
    if (tab === 'library') loadLibraryPage();
    if (tab === 'review') setReviewSubtab(reviewSubtab);
  }

  function revealTab(tabId, { open = false, markNew = true } = {}) {
    const btn = $(`tab-${tabId}`) || document.querySelector(`[data-ws-tab="${tabId}"]`);
    if (btn) {
      btn.classList.remove('hidden');
      if (markNew && workspaceTab !== tabId) btn.classList.add('has-new');
    }
    if (open) setWorkspaceTab(tabId);
  }

  function fillPage(tabId, html, { open = false } = {}) {
    const body =
      $(`${tabId}-page-body`) ||
      (tabId === 'report' ? $('report-body') : null) ||
      (tabId === 'direct' ? $('direct-body') : null) ||
      (tabId === 'review' ? $('review-body') : null);
    if (body) body.innerHTML = html;
    revealTab(tabId, { open, markNew: true });
  }

  function showWorkspaceTabs(on) {
    const el = $('workspace-tabs');
    if (!el) return;
    el.classList.add('visible');
    el.style.display = 'flex';
  }

  // ── Analyze (no approval gates) ──────────────────────────────────
  function hasSessionData(s) {
    if (!s) return false;
    const inv = s.library || {};
    return (inv.total_line_items || 0) > 0
      || (inv.total_pdf_documents || 0) > 0
      || (s.line_items || 0) > 0
      || (s.pdf_chunks || 0) > 0
      || (s.pdf_documents?.length || 0) > 0
      || (s.financial_line_items?.length || 0) > 0
      || (s.uploaded_files?.length || 0) > 0
      || !!s.has_research_report;
  }

  function updateAnalyzeButton(s) {
    const ready = hasSessionData(s);
    const label = 'Run Analysis → Full Report';
    const title = ready
      ? 'One full pass — report + flags + valuation (no approval gates)'
      : 'Upload / open a session with Capital IQ data first';
    ['btn-analyze', 'btn-review-analyze'].forEach((id) => {
      const b = $(id);
      if (!b) return;
      b.disabled = !ready || isProcessing;
      b.textContent = isProcessing ? 'Analyzing…' : label;
      b.title = title;
    });
    // Report-only rewrite from current session data (no full Analyze job)
    const noteBtn = $('btn-note');
    if (noteBtn) {
      noteBtn.disabled = !ready || isProcessing || !currentSession;
      noteBtn.title = ready
        ? 'Rewrite report from current packs / assumptions — does not re-run full Analyze'
        : 'Open a session with data first';
    }
    const regenBtn = $('btn-report-regen');
    if (regenBtn) {
      regenBtn.disabled = !ready || isProcessing || !currentSession;
      regenBtn.title = noteBtn?.title || '';
    }
  }

  async function regenerateReportOnly() {
    if (!currentSession) {
      addSystem('Open a research session first (Recent sessions → click it, or Open research session).');
      return;
    }
    if (isProcessing) {
      addSystem('Analysis is running — wait for it to finish, or abort, before regenerating.');
      return;
    }
    try {
      pushStatus('Regenerating report (no full Analyze)…');
      addSystem('Rewriting research report from current session data — not re-running full Analyze.');
      const r = await api(`/api/sessions/${currentSession}/note`, { method: 'POST' });
      if (r.note || r.markdown) {
        fillPage('report', `<div class="report-md">${mdToHtml(r.note || r.markdown)}</div>`, { open: true });
      } else {
        await loadReportPage(true);
      }
      addSystem('Report regenerated. Open the **Report** tab to read it.');
    } catch (e) {
      addSystem('Regenerate failed: ' + e.message);
    }
  }

  const ANALYZE_STAGES = [
    ['normalizer', 'Normalize financials'],
    ['annual_report', 'Annual reports'],
    ['guidelines', 'Guidance & concalls'],
    ['porters', 'Competitive forces'],
    ['margin_of_safety', 'DCF / valuation'],
    ['brokerage_view', 'Broker vs CIQ'],
    ['competitors', 'Peers'],
    ['freshness', 'Data freshness'],
    ['reconcile', 'Reconcile sources'],
    ['critique', 'Investor checklists'],
    ['asks', 'Questions for you'],
    ['report', 'Write full report'],
  ];

  function setAnalyzeStrip(on, pct, label) {
    const strip = $('analyze-strip');
    if (!strip) return;
    strip.classList.toggle('hidden', !on);
    const fill = $('analyze-strip-fill');
    if (fill) fill.style.width = `${Math.max(0, Math.min(100, pct || 0))}%`;
    if ($('analyze-strip-label') && label) {
      $('analyze-strip-label').textContent = label;
    }
  }

  const STAGE_PCT_FLOOR = {
    normalizer: 0.05,
    annual_report: 0.15,
    guidelines: 0.28,
    porters: 0.40,
    margin_of_safety: 0.52,
    brokerage_view: 0.62,
    competitors: 0.66,
    freshness: 0.70,
    reconcile: 0.74,
    critique: 0.78,
    asks: 0.82,
    report: 0.86,
    done: 1.0,
  };

  let _progStepSince = 0;
  let _progStepKey = '';

  function _fmtElapsed(ms) {
    const s = Math.max(0, Math.floor(ms / 1000));
    const m = Math.floor(s / 60);
    const r = s % 60;
    return m > 0 ? `${m}m ${String(r).padStart(2, '0')}s` : `${r}s`;
  }

  /** Parse job timestamps as UTC (API sends naive UTC without Z → JS would treat as local IST). */
  function _parseJobUtcMs(iso) {
    if (!iso) return NaN;
    const s = String(iso).trim();
    if (!s) return NaN;
    if (/[zZ]|[+-]\d{2}:?\d{2}$/.test(s)) return Date.parse(s);
    return Date.parse(s.endsWith('Z') ? s : `${s}Z`);
  }

  function renderProgressUI(st) {
    // Prefer structured panel; never dump chat into Progress
    const panel = $('prog-panel');
    if (!panel) {
      // fallback for older HTML
      const pct = Math.round((st?.progress || 0) * 100);
      fillPage('progress', `<p><strong>${st?.status || 'running'}</strong> — ${st?.message || ''} (${pct}%)</p>`);
      return;
    }
    const status = st?.status || 'idle';
    const msg = st?.message || st?.current_step || 'Waiting…';
    const cur = st?.current_step || null;
    const phase = st?.phase || null;
    const labels = Object.fromEntries(ANALYZE_STAGES);
    const stepLabel = cur ? (labels[cur] || cur.replace(/_/g, ' ')) : '—';
    const rawPct = typeof st?.progress === 'number' ? st.progress : 0;
    // Never show a % behind the known floor for the active module
    const floor = cur && STAGE_PCT_FLOOR[cur] != null ? STAGE_PCT_FLOOR[cur] : 0;
    let pct = Math.round(Math.max(0, Math.min(1, Math.max(rawPct, floor))) * 100);
    if (status === 'complete' || status === 'done' || status === 'completed' || status === 'success') pct = 100;
    if (status === 'failed' || status === 'error') { /* keep */ }

    const stepKey = `${cur || ''}|${phase || ''}`;
    if (stepKey !== _progStepKey) {
      _progStepKey = stepKey;
      _progStepSince = Date.now();
    }
    // Prefer server-computed UTC elapsed; else client clock since step change.
    // Never Date.parse naive ISO as local (that added ~5.5h in IST → "337m").
    let elapsedMs = Date.now() - (_progStepSince || Date.now());
    if (typeof st?.step_elapsed_sec === 'number' && st.step_elapsed_sec >= 0) {
      elapsedMs = st.step_elapsed_sec * 1000;
    } else if (st?.step_started_at) {
      const t = _parseJobUtcMs(st.step_started_at);
      if (!Number.isNaN(t)) elapsedMs = Math.max(0, Date.now() - t);
    }

    const eng = describeAnalyzeEngine(st);
    if ($('prog-status')) {
      const label = status === 'idle' ? 'Idle'
        : (status === 'complete' || status === 'done' || status === 'completed' || status === 'success') ? 'Complete'
        : (status === 'failed' || status === 'error') ? 'Failed'
        : 'Running';
      const engineBit = (status === 'running' || status === 'pending' || status === 'queued')
        ? ` · ${eng.engine}`
        : '';
      $('prog-status').textContent = `${label}${engineBit} — ${stepLabel}`;
    }
    if ($('prog-sub')) {
      if (status === 'idle') {
        $('prog-sub').textContent = 'Upload on Files, then Run Analysis. You can add more docs anytime.';
      } else {
        const engineLine = `${eng.engine}: ${eng.detail}`;
        $('prog-sub').textContent = msg
          ? `${engineLine} — ${msg}`
          : engineLine;
      }
    }
    if ($('prog-pct')) $('prog-pct').textContent = `${pct}%`;
    const fill = $('prog-bar-fill');
    if (fill) {
      fill.style.width = `${pct}%`;
      fill.classList.toggle('done', pct >= 100 && status !== 'failed' && status !== 'error');
      fill.classList.toggle('err', status === 'failed' || status === 'error');
    }

    const mod = $('prog-module');
    if (mod) {
      const running = status === 'running' || status === 'pending' || status === 'queued';
      if (running && cur) {
        mod.style.display = '';
        const phaseTxt = phase ? phase.replace(/_/g, ' ') : 'in progress';
        const engCls = eng.engine === 'Hive' ? 'pm-hive' : 'pm-python';
        mod.innerHTML =
          `<div><strong>Current module:</strong> <span class="pm-phase">${stepLabel}</span>`
          + ` · <span class="${engCls}">${eng.engine}</span>`
          + (phase ? ` · <code>${phaseTxt}</code>` : '')
          + `</div>`
          + `<div class="pm-elapsed">${eng.detail} · on this step ${_fmtElapsed(elapsedMs)}`
          + (msg ? ` — ${msg}` : '')
          + `</div>`;
      } else if (status === 'failed' || status === 'error' || status === 'aborted') {
        mod.style.display = '';
        mod.innerHTML =
          `<div><strong>${status === 'aborted' ? 'Aborted on' : 'Stopped on'}:</strong> <span class="pm-phase">${stepLabel}</span>`
          + (phase ? ` · <code>${String(phase).replace(/_/g, ' ')}</code>` : '')
          + `</div>`
          + `<div class="pm-elapsed">${msg}</div>`;
      } else {
        mod.style.display = 'none';
        mod.innerHTML = '';
      }
    }

    // Token / call cloud + Abort
    const llmBox = $('prog-llm');
    const llmStats = $('prog-llm-stats');
    const llmCloud = $('prog-llm-cloud');
    const abortBtn = $('btn-abort-analyze');
    const calls = Number(st?.llm_calls_total || 0);
    const toks = Number(st?.llm_tokens_total || 0);
    const byMod = st?.llm_by_module || {};
    const runningJob = status === 'running' || status === 'pending' || status === 'queued';
    if (llmBox) {
      if (calls > 0 || st?.llm_current_module || runningJob) {
        llmBox.style.display = '';
        const est = st?.llm_tokens_estimated ? '~' : '';
        const curLlm = st?.llm_current_module
          ? ` · now <code>${String(st.llm_current_module).replace(/_/g, ' ')}</code>`
            + (st.llm_current_elapsed_sec != null ? ` (${st.llm_current_elapsed_sec}s)` : '')
          : '';
        if (llmStats) {
          llmStats.innerHTML =
            `<strong>LLM</strong> · ${calls} call${calls === 1 ? '' : 's'}`
            + (st?.llm_calls_fail ? ` · ${st.llm_calls_fail} fail` : '')
            + ` · ${est}${toks.toLocaleString()} tokens`
            + curLlm;
        }
        if (llmCloud) {
          const chips = Object.entries(byMod)
            .sort((a, b) => b[1] - a[1])
            .map(([name, n]) => {
              const active = name === st?.llm_current_module ? ' active' : '';
              return `<span class="chip${active}">${String(name).replace(/_/g, ' ')} ×${n}</span>`;
            });
          llmCloud.innerHTML = chips.length
            ? chips.join('')
            : '<span class="muted">Waiting for first LLM call…</span>';
        }
      } else {
        llmBox.style.display = 'none';
      }
    }
    if (abortBtn) {
      abortBtn.style.display = runningJob && window._analyzeJobId ? '' : 'none';
      abortBtn.disabled = !!window._analyzeAborting;
    }

    const steps = (st?.steps && st.steps.length) ? st.steps : ANALYZE_STAGES.map((x) => x[0]);
    const curIdx = cur ? steps.indexOf(cur) : -1;
    const list = $('prog-stages');
    if (list) {
      list.innerHTML = steps.map((sid, i) => {
        let cls = 'pending';
        if (status === 'complete' || status === 'done' || status === 'completed' || status === 'success') cls = 'done';
        else if (curIdx >= 0) {
          if (i < curIdx) cls = 'done';
          else if (i === curIdx) cls = 'active';
        } else if (sid === cur) cls = 'active';
        const name = labels[sid] || sid.replace(/_/g, ' ');
        const detail = (i === curIdx && (status === 'running' || status === 'pending' || status === 'queued'))
          ? `<span class="stage-detail">${phase ? phase.replace(/_/g, ' ') + ' · ' : ''}${msg}</span>`
          : '';
        return `<li class="${cls}"><span class="dot"></span><span class="stage-text"><span>${name}</span>${detail}</span></li>`;
      }).join('');
    }

    const ex = $('prog-excerpt');
    if (ex) {
      const text = st?.excerpt || '';
      if (text) {
        ex.style.display = '';
        ex.textContent = text;
      } else {
        ex.style.display = 'none';
      }
    }

    renderProgressPrompts();
    const running = status === 'running' || status === 'pending' || status === 'queued';
    const stripLabel = running || isProcessing
      ? `${eng.engine} · ${stepLabel}`
        + (eng.detail && eng.detail !== stepLabel ? ` · ${eng.detail}` : '')
        + (phase && !String(phase).includes(eng.engine.toLowerCase()) ? ` · ${String(phase).replace(/_/g, ' ')}` : '')
      : `${stepLabel}${phase ? ' · ' + phase : ''}`;
    setAnalyzeStrip(running || isProcessing, pct, stripLabel);
  }

  function renderProgressPrompts() {
    const row = $('prog-prompt-row');
    if (!row || row.dataset.wired === '1') {
      // still refresh missing-doc buttons if empty first paint
      if (row && row.dataset.wired === '1') return;
    }
    if (!row) return;
    row.dataset.wired = '1';
    const actions = [
      { label: 'Add Annual Report…', section: 'annual_report' },
      { label: 'Add Estimates…', section: 'ciq_estimates' },
      { label: 'Add Concall…', section: 'concall' },
      { label: 'Add Broker note…', section: 'brokerage' },
      { label: 'Open Files tab', go: 'files' },
      { label: 'Ignore for now', skip: true },
    ];
    row.innerHTML = actions.map((a, i) =>
      `<button type="button" data-pi="${i}">${a.label}</button>`
    ).join('');
    row.querySelectorAll('button').forEach((btn) => {
      const a = actions[Number(btn.getAttribute('data-pi'))];
      btn.onclick = () => {
        if (a.skip) {
          pushStatus('Optional docs skipped', 'ok');
          return;
        }
        if (a.go) {
          setWorkspaceTab(a.go);
          return;
        }
        if (a.section) {
          setWorkspaceTab('files');
          const input = $('file-input-section');
          if (!input) return;
          input.onchange = () => { queueFiles(input.files, a.section); input.value = ''; };
          input.click();
        }
      };
    });
  }

  async function runAnalyze() {
    if (!currentSession) {
      addSystem('Open a research session first.');
      return;
    }
    if (!hasSessionData(lastSession)) {
      addSystem('Need Capital IQ Financials (or library data) before analysis.');
      return;
    }
    isProcessing = true;
    updateAnalyzeButton(lastSession);
    revealTab('progress', { open: true, markNew: false });
    renderProgressUI({
      status: 'running',
      progress: 0.02,
      message: 'Starting full analysis pass…',
      current_step: 'normalizer',
      steps: ANALYZE_STAGES.map((x) => x[0]),
    });
    pushStatus('Analysis started');
    addSystem('Analysis started — live steps will appear here (Python modules + LLM calls). **Progress** tab has the full checklist.');
    try {
      // force=true keeps compat; orchestrator already skips gates
      const s = await api(`/api/sessions/${currentSession}/analyze?force=true`, { method: 'POST' });
      // async job?
      if (s.job_id || s.status === 'queued' || s.status === 'running' || s.status === 'pending') {
        if (s.progress != null || s.message) renderProgressUI(s);
        if (s.data_readiness || s.user_asks) {
          renderReadinessPanel({
            data_readiness: s.data_readiness,
            asks: (s.user_asks || {}).asks || [],
          });
        }
        postAnalyzeProgress(s);
        await pollAnalyzeJob(s.job_id || s.id);
      } else {
        await applySession(s);
        await afterAnalyze();
      }
    } catch (e) {
      addSystem('Analyze error: ' + e.message);
      pushStatus('Analyze failed: ' + e.message, 'err');
      renderProgressUI({ status: 'failed', progress: 0, message: e.message, steps: ANALYZE_STAGES.map((x) => x[0]) });
      setAnalyzeStrip(false);
    } finally {
      isProcessing = false;
      updateAnalyzeButton(lastSession);
    }
  }

  async function abortAnalyzeJob() {
    const id = window._analyzeJobId;
    if (!id || window._analyzeAborting) return;
    if (!confirm('Force abort analysis? Python draft (if already written) is kept; in-flight LLM call is stopped.')) {
      return;
    }
    window._analyzeAborting = true;
    const btn = $('btn-abort-analyze');
    if (btn) btn.disabled = true;
    try {
      await api(`/api/jobs/${id}/abort`, { method: 'POST' });
      addSystem('Abort requested — waiting for current LLM call to stop…');
      pushStatus('Aborting analysis…');
    } catch (e) {
      addSystem('Abort failed: ' + e.message);
      window._analyzeAborting = false;
      if (btn) btn.disabled = false;
    }
  }

  async function pollAnalyzeJob(jobId) {
    const id = jobId;
    window._analyzeJobId = id;
    window._analyzeAborting = false;
    let miss = 0;
    // Fat AR Hive loops routinely exceed 90 min — only fail on true stall.
    const maxIdleMs = 60 * 60 * 1000; // 60 min with no progress/message change
    const hardCapMs = 6 * 60 * 60 * 1000; // 6h absolute (warn earlier, don't kill healthy jobs)
    const warnCapMs = 90 * 60 * 1000;
    const started = Date.now();
    let lastSig = '';
    let lastChange = Date.now();
    let warnedLong = false;
    for (;;) {
      await new Promise((r) => setTimeout(r, 2000));
      let st;
      try {
        st = await api(`/api/jobs/${id}`);
        miss = 0;
      } catch (e) {
        const msg = String(e?.message || e || '');
        // Server restart drops in-memory jobs → 404 "Job not found" or network "Failed to fetch"
        const lost =
          /job not found/i.test(msg)
          || /failed to fetch/i.test(msg)
          || /networkerror|load failed|fetch/i.test(msg);
        try {
          const s = await api(`/api/sessions/${currentSession}`);
          if (s?.has_research_report || (s?.assumptions || []).length) {
            await applySession(s);
            await afterAnalyze();
            return;
          }
          if (lost && (s?.status === 'complete' || s?.status === 'ready')) {
            await applySession(s);
            throw new Error('Analysis job was lost (server restarted). Click Run Analysis again.');
          }
        } catch (inner) {
          if (/Run Analysis again/i.test(String(inner?.message || ''))) throw inner;
        }
        miss += 1;
        if (lost && miss >= 3) {
          throw new Error('Lost connection to analysis job (server restart?). Click Run Analysis again.');
        }
        continue;
      }
      renderProgressUI(st);
      const msg = st.message || st.stage || st.status || 'running';
      // Announce module / LLM phase changes in Recent answers (not every heartbeat tick)
      const announceSig = `${st.status}|${st.current_step}|${st.phase || ''}`;
      const aliveSig = `${announceSig}|${String(msg).replace(/\s*·\s*\d+m\d{2}s\s*$/i, '')}|${Number(st.progress || 0).toFixed(3)}`;
      if (announceSig !== lastSig) {
        lastSig = announceSig;
        lastChange = Date.now();
        if (st.status === 'running' || st.status === 'pending' || st.status === 'queued') {
          postAnalyzeProgress(st);
        }
        refreshReadinessAsks().catch(() => {});
      } else if (aliveSig !== (pollAnalyzeJob._lastAlive || '')) {
        pollAnalyzeJob._lastAlive = aliveSig;
        lastChange = Date.now();
        // Keep strip fresh without flooding Recent answers
        pushStatus(formatAnalyzeProgressLine(st).replace(/\*\*/g, '').slice(0, 140));
      }
      // Periodic readiness refresh (~every 12s) so late uploads shrink the ask list
      pollAnalyzeJob._rdTicks = (pollAnalyzeJob._rdTicks || 0) + 1;
      if (pollAnalyzeJob._rdTicks % 6 === 0) {
        refreshReadinessAsks().catch(() => {});
      }
      if (st.status === 'done' || st.status === 'completed' || st.status === 'success' || st.status === 'complete') {
        window._analyzeJobId = null;
        window._analyzeAborting = false;
        const s = await api(`/api/sessions/${currentSession}`);
        await applySession(s);
        await afterAnalyze();
        return;
      }
      if (st.status === 'aborted') {
        window._analyzeJobId = null;
        window._analyzeAborting = false;
        setAnalyzeStrip(false);
        const tok = Number(st.llm_tokens_total || 0);
        const est = st.llm_tokens_estimated ? '~' : '';
        pushStatus('Analysis aborted', 'err');
        renderProgressUI(st);
        addSystem(
          `Analysis **aborted** · ${st.llm_calls_total || 0} LLM calls · ${est}${tok.toLocaleString()} tokens. `
          + 'Check **Report** for any Python draft already saved.'
        );
        try {
          const s = await api(`/api/sessions/${currentSession}`);
          await applySession(s);
          if (s?.has_research_report) await loadReportPage(true);
        } catch (_) { /* ignore */ }
        return;
      }
      if (st.status === 'error' || st.status === 'failed') {
        window._analyzeJobId = null;
        window._analyzeAborting = false;
        throw new Error(st.error || st.message || 'Analysis failed');
      }
      const idle = Date.now() - lastChange;
      const elapsed = Date.now() - started;
      if (idle > maxIdleMs) {
        throw new Error(
          `Analysis stalled on “${st.current_step || 'unknown'}” — no progress for ${Math.round(idle / 60000)} min. `
          + `${msg}. Refresh and check Report / Progress (job may still be running on the server).`
        );
      }
      if (!warnedLong && elapsed > warnCapMs) {
        warnedLong = true;
        addSystem(
          `Still running after ${Math.round(elapsed / 60000)} min (step: **${st.current_step || 'unknown'}**). `
          + 'This is normal for large annual reports — leaving it alone.'
        );
      }
      if (elapsed > hardCapMs) {
        throw new Error(
          `Analysis still running after ${Math.round(elapsed / 60000)} min (step: ${st.current_step || 'unknown'}). `
          + 'Refresh and check Report tab; re-run Analyze to resume from checkpoint if needed.'
        );
      }
    }
  }

  async function afterAnalyze() {
    setAnalyzeStrip(false);
    const s = lastSession || await api(`/api/sessions/${currentSession}`).catch(() => null);
    if (s) await applySession(s);
    const hasReport = !!(lastSession?.has_research_report);
    if (hasReport) {
      pushStatus('Analysis complete', 'ok');
      renderProgressUI({
        status: 'complete',
        progress: 1,
        message: 'Full analysis + report ready',
        current_step: 'done',
        phase: 'done',
        steps: ANALYZE_STAGES.map((x) => x[0]),
      });
      await loadReportPage(true);
      await loadOverviewIntoTabs(true);
      revealTab('report', { open: true, markNew: true });
      if (lastSession?.has_direct_llm_report) {
        revealTab('direct', { open: false, markNew: true });
        await loadDirectReportPage(false);
        const cmp = lastSession?.report_compare;
        if (cmp?.direct_llm_report) {
          const d = cmp.direct_llm_report;
          const p = cmp.pipeline_report || {};
          addSystem(
            `✓ Full pass done — **Report** (retrieval+LLM) and **Compare** (vs Direct LLM) ready. `
            + `Tokens: pipeline ${_fmtTok(p.tokens_total, p.tokens_estimated)} · `
            + `direct ${_fmtTok(d.tokens_total, d.tokens_estimated)} (${d.calls || 0} calls, ${d.documents_read || 0} docs).`
          );
        } else {
          addSystem('✓ Full pass done — open **Report** (path A) and **Compare** (path A vs Direct LLM B).');
        }
      } else {
        addSystem('✓ Full pass done — open **Report**. Direct LLM (path B) may still be off or skipped.');
      }
    } else {
      pushStatus('Analysis finished but report missing', 'err');
      renderProgressUI({
        status: 'failed',
        progress: 0.9,
        message: 'Stages ran but Report is empty — click Run Analysis again',
        current_step: 'report',
        phase: 'report_missing',
        steps: ANALYZE_STAGES.map((x) => x[0]),
      });
      fillPage('report', '<p class="muted">Report missing after analysis. Click <strong>Run Analysis</strong> again.</p>', { open: true });
      addSystem('⚠ Analysis stages finished but **Report** was not saved. Re-run analysis.');
    }
  }

  // ── Overview → stage tabs ────────────────────────────────────────
  function renderFlagsHtml(data) {
    const flags = data.flags || [];
    const items = flags.map((f) => {
      const sev = f.severity === 'review' ? '⚠' : '·';
      return `<div class="flag-item" style="margin:0.55rem 0;padding:0.45rem 0;border-bottom:1px solid var(--border)">
        <div><strong>${sev} ${f.headline || 'Flag'}</strong></div>
        <div style="font-size:0.88em;color:var(--muted);margin-top:0.2rem">${f.explanation || ''}</div>
      </div>`;
    }).join('');
    return `<div class="fin-card">
      <div class="fin-card-head">
        <div class="fin-card-title">Data flags (${data.review_count || flags.length || 0} to review)</div>
        <div class="fin-card-sub">Verifiable anomalies — check figures in Capital IQ</div>
      </div>
      ${items || '<p class="muted">No flags triggered.</p>'}
    </div>`;
  }

  function renderGuidanceHtml(data) {
    const items = (data.items || []).map((i) =>
      `<div style="padding:0.45rem 0;border-bottom:1px solid var(--border);font-size:0.88rem">${esc(i.text || itemLine(i))}</div>`
    ).join('');
    return `<div class="fin-card">
      <div class="fin-card-head">
        <div class="fin-card-title">${esc(data.title || 'Management guidance')}</div>
        <div class="fin-card-sub">${esc(data.subtitle || '')}</div>
      </div>
      ${items || '<p class="muted">No guidance rows yet.</p>'}
    </div>`;
  }

  function esc(s) {
    return String(s ?? '')
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function fmtNum(v, unit) {
    if (v == null || v === '') return '—';
    const n = Number(v);
    if (!Number.isFinite(n)) return esc(v);
    if (unit === 'percent' || unit === '%') return `${n.toFixed(1)}%`;
    if (Math.abs(n) >= 100) return n.toLocaleString('en-IN', { maximumFractionDigits: 0 });
    return n.toLocaleString('en-IN', { maximumFractionDigits: 2 });
  }

  function fmtYoY(v) {
    if (v == null) return '<span class="yoy-val muted">—</span>';
    const n = Number(v);
    if (!Number.isFinite(n)) return '<span class="yoy-val muted">—</span>';
    const cls = n >= 0 ? 'yoy-val' : 'yoy-val muted';
    const sign = n > 0 ? '+' : '';
    return `<span class="${cls}">${sign}${n.toFixed(1)}%</span>`;
  }

  function itemLine(i) {
    if (!i || typeof i !== 'object') return String(i ?? '');
    if (i.text) return i.text;
    if (i.label && i.value != null) {
      return `${i.label}: ${i.value}${i.detail ? ` · ${i.detail}` : ''}`;
    }
    if (i.metric != null) return `${i.metric}: ${fmtNum(i.latest_value, i.unit)}`;
    return Object.keys(i).slice(0, 4).map((k) => `${k}: ${i[k]}`).join(' · ');
  }

  function renderKpis(kpis) {
    if (!kpis?.length) return '';
    return `<div class="fin-kpis">${kpis.map((k) =>
      `<div class="fin-kpi${k.highlight ? ' highlight' : ''}"><div class="fin-kpi-label">${esc(k.label)}</div><div class="fin-kpi-value">${esc(k.value)}</div></div>`
    ).join('')}</div>`;
  }

  function renderRowsTable(rows) {
    if (!rows?.length) return '<p class="muted">No rows yet.</p>';
    const head = rows[0];
    // Financial movement rows
    if (head.metric != null || head.latest_value != null) {
      return `<table class="fin-table"><thead><tr>
        <th>Metric</th><th>Latest</th><th>Prior</th><th>YoY</th>
      </tr></thead><tbody>${rows.map((r) => {
        const unit = r.unit || '';
        const period = r.latest_period ? `<div class="muted" style="font-size:0.7rem">${esc(String(r.latest_period).slice(0, 10))}</div>` : '';
        return `<tr>
          <td>${esc(r.metric || r.label || '—')}${period}</td>
          <td>${fmtNum(r.latest_value, unit)}${unit && unit !== 'percent' ? ` <span class="muted">${esc(unit)}</span>` : ''}</td>
          <td>${fmtNum(r.prior_value, unit)}</td>
          <td>${fmtYoY(r.yoy_pct)}</td>
        </tr>`;
      }).join('')}</tbody></table>`;
    }
    // Generic object rows
    const keys = Object.keys(head).filter((k) => !['id', 'key'].includes(k)).slice(0, 5);
    return `<table class="fin-table"><thead><tr>${keys.map((k) => `<th>${esc(k)}</th>`).join('')}</tr></thead>
      <tbody>${rows.map((r) => `<tr>${keys.map((k) => `<td>${esc(r[k] ?? '—')}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
  }

  function renderSeriesTable(rows, label) {
    if (!rows?.length) return '';
    return `<div style="margin-top:0.55rem"><div class="muted" style="font-size:0.75rem;margin-bottom:0.25rem">${esc(label)}</div>
      <table class="fin-table"><thead><tr><th>Period</th><th>Value (mn)</th><th>YoY</th></tr></thead>
      <tbody>${rows.map((r) => `<tr>
        <td>${esc(r.period || '—')}</td>
        <td>${fmtNum(r.value_mn)}</td>
        <td>${fmtYoY(r.yoy_pct)}</td>
      </tr>`).join('')}</tbody></table></div>`;
  }

  function cardShell(title, subtitle, body) {
    return `<div class="fin-card">
      <div class="fin-card-head">
        <div class="fin-card-title">${esc(title || '')}</div>
        <div class="fin-card-sub">${esc(subtitle || '')}</div>
      </div>
      ${body}
    </div>`;
  }

  function sectionToHtml(sec) {
    if (!sec) return '';
    const id = sec.id || '';
    const type = sec.type || '';

    if (type === 'data_flags' || id === 'flags') return renderFlagsHtml(sec);
    if (type === 'guidance' || id === 'guidance') return renderGuidanceHtml(sec);
    if (sec.html) return sec.html;

    // Financial movement / multiples-style row tables
    if ((id === 'financials' || sec.rows) && Array.isArray(sec.rows) && sec.rows[0]?.metric != null) {
      return cardShell(sec.title || 'Financials', sec.subtitle || '', renderRowsTable(sec.rows));
    }

    // Revenue growth
    if (type === 'revenue_growth' || id === 'revenue') {
      return cardShell(
        sec.title || 'Revenue & growth',
        sec.subtitle || '',
        renderKpis(sec.kpis)
          + renderSeriesTable(sec.historical, 'Historical')
          + renderSeriesTable(sec.estimates, 'Consensus estimates')
      );
    }

    // Porter's
    if (type === 'porters' || id === 'porters') {
      if (sec.pending) {
        return cardShell(sec.title || "Porter's Five Forces", sec.subtitle || '',
          `<p class="muted">${esc(sec.note || 'Run analysis after uploading AR / concalls.')}</p>`);
      }
      const forces = (sec.forces || []).map((f) =>
        `<div class="force-row"><strong>${esc(f.label || f.key)}</strong>
          <div class="muted" style="font-size:0.85rem;margin-top:0.2rem">${esc(f.text || '—')}</div></div>`
      ).join('');
      return cardShell(sec.title || "Porter's Five Forces", sec.subtitle || '', forces || '<p class="muted">—</p>');
    }

    // Valuation / MoS
    if (type === 'valuation_mos' || id === 'mos' || type === 'valuation_multiples' || id === 'multiples') {
      if (sec.pending) {
        return cardShell(sec.title || 'Valuation', sec.subtitle || '',
          `<p class="muted">${esc(sec.note || 'Run analysis to build DCF.')}</p>`);
      }
      let body = renderKpis(sec.kpis);
      if (sec.rows?.length) body += renderRowsTable(sec.rows);
      if (sec.assumptions?.length) {
        body += `<ul class="meta-list">${sec.assumptions.map((a) =>
          `<li><strong>${esc(a.label || a.key)}</strong> — ${esc(a.display || a.value)}</li>`
        ).join('')}</ul>`;
      }
      if (sec.note) body += `<p class="muted" style="margin-top:0.5rem">${esc(sec.note)}</p>`;
      return cardShell(sec.title || 'Valuation', sec.subtitle || '', body || '<p class="muted">—</p>');
    }

    // Consensus / generic items (label + value)
    if (sec.items?.length) {
      const body = sec.items.map((i) => {
        if (i.label != null && i.value != null) {
          return `<div class="kv-row"><span>${esc(i.label)}</span><strong>${esc(i.value)}</strong>
            ${i.detail ? `<span class="muted" style="font-size:0.75rem">${esc(i.detail)}</span>` : ''}</div>`;
        }
        return `<div style="padding:0.35rem 0;border-bottom:1px solid var(--border);font-size:0.85rem">${esc(itemLine(i))}</div>`;
      }).join('');
      return cardShell(sec.title || '', sec.subtitle || '', body);
    }

    if (sec.kpis?.length) {
      return cardShell(sec.title || '', sec.subtitle || '', renderKpis(sec.kpis) + (sec.rows ? renderRowsTable(sec.rows) : ''));
    }

    if (sec.rows?.length) {
      return cardShell(sec.title || '', sec.subtitle || '', renderRowsTable(sec.rows));
    }

    if (sec.note || sec.text || sec.summary) {
      return cardShell(sec.title || '', sec.subtitle || '', `<p>${esc(sec.note || sec.text || sec.summary)}</p>`);
    }

    // Last resort — short readable dump, never full JSON wall
    return cardShell(sec.title || id || 'Section', sec.subtitle || '',
      `<p class="muted">No displayable fields for this card yet.</p>`);
  }

  function renderPointBlock(block) {
    if (!block) return '';
    const title = block.title || '';
    const points = block.points || block.items || [];
    if (!title && !points.length) return '';
    return `<div style="margin-top:0.75rem">
      ${title ? `<div class="muted" style="font-size:0.75rem;margin-bottom:0.25rem">${esc(title)}</div>` : ''}
      ${points.length ? `<ul>${points.slice(0, 10).map((p) =>
        `<li>${esc(typeof p === 'string' ? p : (p.text || p.label || itemLine(p)))}</li>`
      ).join('')}</ul>` : ''}
    </div>`;
  }

  function renderBriefingHtml(ov) {
    const b = ov.briefing || {};
    const kpis = ov.headline_kpis || [];
    const co = b.company || {};
    const sources = b.sources || [];
    const findings = b.findings || {};
    const findingPoints = Array.isArray(findings)
      ? findings
      : (findings.points || findings.items || []);
    let body = '';
    if (b.headline) body += `<p class="lead"><strong>${esc(b.headline)}</strong></p>`;
    if (b.narrative || ov.summary_text) {
      body += `<p>${renderMarkdown(b.narrative || ov.summary_text)}</p>`;
    }
    if (co.identity) {
      body += `<p class="muted" style="font-size:0.85rem">${esc(co.identity)}</p>`;
    }
    body += renderKpis(kpis);
    body += renderPointBlock(b.basis);
    if (sources.length) {
      body += `<div class="muted" style="font-size:0.78rem;margin:0.65rem 0 0.25rem">Sources on file</div>
        <ul class="meta-list">${sources.slice(0, 8).map((s) =>
          `<li>${esc(s.kind || s.type || 'Doc')} — ${esc(s.name || s.filename || s)}</li>`
        ).join('')}</ul>`;
    }
    body += renderPointBlock(b.analysis_done);
    if (findingPoints.length || findings.title) {
      body += renderPointBlock({
        title: findings.title || 'Findings so far',
        points: findingPoints,
      });
    }
    if (b.next_step?.title) {
      body += `<div style="margin-top:0.85rem;padding:0.65rem 0.75rem;border:1px solid var(--border);border-radius:8px">
        <div style="font-weight:650;font-size:0.85rem">${esc(b.next_step.title)}</div>
        <div class="muted" style="font-size:0.8rem;margin-top:0.2rem">${esc(b.next_step.detail || '')}</div>
      </div>`;
    }
    return cardShell(
      `${ov.company || co.name || 'Company'} — kickoff brief`,
      co.identity || 'From uploaded Capital IQ / PDFs',
      body || '<p class="muted">Open a session with financials to build the brief.</p>'
    );
  }

  let lastOverview = null;

  function overviewSection(id, type) {
    const sections = lastOverview?.sections || [];
    return sections.find((s) => {
      if (id && s.id === id) return true;
      if (type && s.type === type) return true;
      return false;
    }) || null;
  }

  function setReviewSubtab(tab) {
    reviewSubtab = tab || 'brief';
    const rail = $('review-subtabs');
    if (rail) {
      rail.querySelectorAll('[data-review-tab]').forEach((b) => {
        b.classList.toggle('active', b.getAttribute('data-review-tab') === reviewSubtab);
      });
    }
    const body = $('review-body');
    if (!body) return;
    if (!lastOverview) {
      body.innerHTML = '<p class="muted">No overview yet — upload Capital IQ Financials, then Refresh.</p>';
      return;
    }
    const ov = lastOverview;
    const sections = ov.sections || [];
    if (reviewSubtab === 'brief') {
      body.innerHTML = renderBriefingHtml(ov)
        || '<p class="muted">Brief not ready yet.</p>';
      return;
    }
    if (reviewSubtab === 'financials') {
      const bits = ['financials', 'revenue', 'consensus', 'multiples']
        .map((id) => overviewSection(id) || sections.find((s) => s.id === id))
        .filter(Boolean)
        .map(sectionToHtml);
      body.innerHTML = bits.join('') || '<p class="muted">No financial tables yet — upload Capital IQ Financials.</p>';
      return;
    }
    if (reviewSubtab === 'assumptions') {
      const mos = overviewSection('mos', 'valuation_mos');
      body.innerHTML = mos
        ? sectionToHtml(mos)
        : `<div class="fin-card"><div class="fin-card-head"><div class="fin-card-title">Assumptions &amp; valuation</div>
            <div class="fin-card-sub">Editable via chat after analysis</div></div>
            <p class="muted">Run <strong>Analysis → Full Report</strong>, then ask e.g. “Set WACC to 11%”.</p></div>`;
      return;
    }
    if (reviewSubtab === 'flags') {
      const flags = overviewSection('flags', 'data_flags');
      body.innerHTML = flags
        ? renderFlagsHtml(flags)
        : '<p class="muted">No data flags yet.</p>';
    }
  }

  function advanceReviewSubtab() {
    const i = REVIEW_SUBTAB_ORDER.indexOf(reviewSubtab);
    if (i < 0 || i >= REVIEW_SUBTAB_ORDER.length - 1) {
      // After flags → open Report if ready, else Flags top tab
      if (lastSession?.has_research_report) setWorkspaceTab('report');
      else setWorkspaceTab('flags');
      return;
    }
    setWorkspaceTab('review');
    setReviewSubtab(REVIEW_SUBTAB_ORDER[i + 1]);
  }

  async function loadOverviewIntoTabs(force) {
    if (!currentSession) return;
    if (!force && overviewShownFor === currentSession) return;
    try {
      const ov = await api(`/api/sessions/${currentSession}/overview`);
      overviewShownFor = currentSession;
      lastOverview = ov;

      setReviewSubtab(reviewSubtab);

      const flags = overviewSection('flags', 'data_flags');
      if (flags) {
        fillPage('flags', renderFlagsHtml(flags));
        revealTab('flags', { open: false, markNew: true });
      }

      const guidance = overviewSection('guidance', 'guidance');
      if (guidance) {
        fillPage('guidance', renderGuidanceHtml(guidance));
        revealTab('guidance', { open: false, markNew: true });
      }

      const mos = overviewSection('mos', 'valuation_mos');
      if (mos) fillPage('valuation', sectionToHtml(mos));

      if (force) {
        addSystem(
          `**${ov.company || 'Session'}** brief ready — use Brief subtabs (Brief → Financials → Assumptions → Flags).`
        );
      }
    } catch (e) {
      pushStatus('Overview: ' + e.message, 'err');
    }
  }

  function inventoryCards(inv, emptyHint) {
    const blocks = [
      ['Capital IQ Financials', inv.capital_iq_financials],
      ['Capital IQ Estimates', inv.capital_iq_estimates],
      ['Annual reports', inv.annual_report_files || inv.annual_reports],
      ['Concalls', inv.concall_files || inv.concalls],
      ['Presentations', inv.presentations],
      ['Brokerage', inv.brokerage_reports],
    ];
    return blocks.map(([title, rows]) => {
      const list = Array.isArray(rows) ? rows : [];
      if (!list.length) {
        return `<div class="fin-card"><div class="fin-card-head"><div class="fin-card-title">${esc(title)}</div>
          <div class="fin-card-sub">None on file</div></div><p class="muted">${esc(emptyHint || 'Add from the Files tab anytime.')}</p></div>`;
      }
      const items = list.map((r) => {
        if (typeof r === 'string' || typeof r === 'number') return `<li>${esc(r)}</li>`;
        return `<li>${esc(r.filename || r.name || r.stored_path || JSON.stringify(r))}</li>`;
      }).join('');
      return `<div class="fin-card"><div class="fin-card-head"><div class="fin-card-title">${esc(title)}</div>
        <div class="fin-card-sub">${list.length} on file</div></div><ul class="meta-list">${items}</ul></div>`;
    }).join('');
  }

  async function loadLibraryPage() {
    const body = $('library-page-body');
    if (!body) return;
    const inv = selectedCompany?.lookup?.inventory
      || lastSession?.library
      || {};
    const metrics = lastSession?.library?.total_line_items
      || selectedCompany?.lookup?.total_line_items
      || lastSession?.line_items
      || 0;
    const label = companyName()
      || lastSession?.company?.name
      || selectedCompany?.name
      || 'Company';
    let peerHtml = '';
    if (currentSession) {
      try {
        const r = await api(`/api/sessions/${currentSession}/peers`);
        const libs = r.libraries || [];
        if (libs.length) {
          peerHtml = `<h3 style="margin:1.25rem 0 0.5rem">Peer libraries</h3>
            <p class="muted" style="margin-bottom:0.65rem;font-size:0.82rem">Files uploaded for confirmed peers (stored under each peer’s company library).</p>`
            + libs.map((lib) => {
              const name = lib.peer_name || lib.company || lib.company_key || 'Peer';
              const sym = lib.symbol ? ` (${esc(lib.symbol)})` : '';
              const flags = lib.file_flags || {};
              const ready = flags.ciq_financials
                ? '<span class="muted">CIQ Financials ready</span>'
                : '<span class="muted">Needs CIQ Financials for comparison</span>';
              const fileList = (lib.files || []).length
                ? `<ul class="meta-list">${(lib.files || []).map((f) =>
                    `<li><strong>${esc(f.kind)}</strong> — ${esc(f.filename)}</li>`).join('')}</ul>`
                : '<p class="muted">No files listed yet.</p>';
              return `<div class="fin-card"><div class="fin-card-head">
                <div class="fin-card-title">${esc(name)}${sym}</div>
                <div class="fin-card-sub">${lib.document_count || 0} docs · ${lib.total_line_items || 0} metrics</div>
              </div>${ready}${fileList}</div>`;
            }).join('');
        } else {
          peerHtml = `<h3 style="margin:1.25rem 0 0.5rem">Peer libraries</h3>
            <p class="muted">No peer files yet — upload from the <strong>Peers</strong> tab (peer name + CIQ Financials).</p>`;
        }
      } catch (_) {
        peerHtml = '';
      }
    }
    body.innerHTML = `<p class="muted" style="margin-bottom:0.75rem">${esc(label)} · ${metrics} metrics</p>`
      + (inventoryCards(inv) || '<p class="muted">No library inventory yet.</p>')
      + peerHtml;
  }

  async function loadReportPage(open) {
    if (!currentSession) return;
    try {
      const r = await api(`/api/sessions/${currentSession}/report`);
      const md = r.markdown || r.report || r.content || '';
      if (!md) {
        fillPage('report', '<p class="muted">No report yet — run analysis.</p>', { open: !!open });
        return;
      }
      fillPage('report', `<div class="report-md">${mdToHtml(md)}</div>`, { open: !!open });
    } catch {
      fillPage('report', '<p class="muted">Report not available yet.</p>');
    }
  }

  function _fmtTok(n, est) {
    const v = Number(n || 0);
    return `${est ? '~' : ''}${v.toLocaleString()}`;
  }

  function renderReportCompare(compare) {
    const box = $('direct-compare');
    if (!box) return;
    if (!compare || (!compare.pipeline_report && !compare.direct_llm_report)) {
      box.style.display = 'none';
      box.innerHTML = '';
      return;
    }
    const a = compare.pipeline_report || {};
    const b = compare.direct_llm_report || {};
    box.style.display = '';
    box.innerHTML =
      `<div class="prog-llm-head"><div class="prog-llm-stats"><strong>A/B tokens</strong> — two separate reports</div></div>`
      + `<div class="prog-llm-cloud">`
      + `<span class="chip">A Retrieval+LLM · ${a.calls || 0} calls · ${_fmtTok(a.tokens_total, a.tokens_estimated)} tok · ${(a.report_chars || 0).toLocaleString()} chars</span>`
      + `<span class="chip active">B Direct LLM · ${b.calls || 0} calls · ${_fmtTok(b.tokens_total, b.tokens_estimated)} tok · ${(b.report_chars || 0).toLocaleString()} chars · ${b.documents_read || 0} PDFs · ${b.pages_sent || 0} pages · ${b.read_batches || '—'} batches</span>`
      + `</div>`
      + `<p class="muted" style="font-size:0.75rem;margin:0.35rem 0 0">${esc(compare.note || '')}</p>`;
  }

  async function loadDirectReportPage(open) {
    if (!currentSession) return;
    try {
      const [pipe, direct] = await Promise.all([
        api(`/api/sessions/${currentSession}/report`).catch(() => ({})),
        api(`/api/sessions/${currentSession}/direct-report`).catch(() => ({})),
      ]);
      renderReportCompare(direct.compare || lastSession?.report_compare);
      const pipeMd = pipe.markdown || pipe.report || '';
      const directMd = direct.markdown || '';
      const left = $('compare-pipeline-body');
      if (left) {
        left.innerHTML = pipeMd
          ? `<div class="report-md">${mdToHtml(pipeMd)}</div>`
          : '<p class="muted">No pipeline report yet — run analysis.</p>';
      }
      if (!directMd) {
        fillPage('direct', '<p class="muted">No Direct LLM report yet — finishes after the pipeline Report on Analyze.</p>', { open: !!open });
        return;
      }
      fillPage('direct', `<div class="report-md">${mdToHtml(directMd)}</div>`, { open: !!open });
    } catch {
      fillPage('direct', '<p class="muted">Direct LLM report not available yet.</p>');
    }
  }

  async function rerunDirectReport() {
    if (!currentSession) return;
    const btn = $('btn-direct-regen');
    if (btn) btn.disabled = true;
    fillPage('direct', '<p class="muted">Re-running Direct LLM (raw PDF pages + guidelines)…</p>', { open: true });
    try {
      const r = await api(`/api/sessions/${currentSession}/direct-report`, { method: 'POST' });
      if (!r.ok) throw new Error(r.error || 'Direct LLM failed');
      addSystem('Direct LLM (path B) refreshed — side-by-side with retrieval Report on Compare.');
      const s = await api(`/api/sessions/${currentSession}`);
      await applySession(s);
      await loadDirectReportPage(true);
    } catch (e) {
      fillPage('direct', `<p class="muted">Direct LLM error: ${esc(e.message)}</p>`, { open: true });
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async function loadPeersPage() {
    if (!currentSession) return;
    try {
      const r = await api(`/api/sessions/${currentSession}/peers`);
      const confirmed = r.confirmed || [];
      const proposed = r.proposed || r.suggestions || [];
      const fileAsks = r.file_asks || [];
      const onFile = r.on_file || [];
      const libraries = r.libraries || [];
      const listBlock = (title, rows, empty) => {
        if (!rows?.length) return `<p class="muted">${empty}</p>`;
        return `<div class="fin-card"><div class="fin-card-head"><div class="fin-card-title">${esc(title)}</div></div>
          <ul class="meta-list">${rows.map((p) => {
            const name = typeof p === 'string' ? p : (p.name || p.company || '');
            const sym = p.symbol ? ` (${esc(p.symbol)})` : '';
            const src = (p.sources || []).length ? ` — ${esc((p.sources || []).join(', '))}` : '';
            return `<li><strong>${esc(name)}</strong>${sym}${src}</li>`;
          }).join('')}</ul></div>`;
      };
      const uploadBox = `<div class="peer-upload-box">
        <strong>Upload peer documents</strong>
        <p class="muted" style="font-size:0.78rem;margin:0.25rem 0">Capital IQ <strong>Financials</strong> (required for comps), Estimates, AR, concalls — saved to that peer’s library (also shown under Library → Peer libraries).</p>
        <input type="text" id="peer-upload-name" placeholder="Peer company name (e.g. Bharat Bijlee)" />
        <div style="display:flex;gap:0.4rem;flex-wrap:wrap">
          <button type="button" id="btn-peer-pick-files">Choose files…</button>
          <button type="button" id="btn-peer-upload-go" disabled>Upload to peer library</button>
        </div>
        <input type="file" id="peer-file-input" multiple accept=".xls,.xlsx,.pdf" class="hidden" />
        <div class="muted" id="peer-file-label" style="font-size:0.75rem;margin-top:0.35rem">No files selected</div>
      </div>`;
      const uploadedBlock = libraries.length
        ? `<div class="fin-card"><div class="fin-card-head"><div class="fin-card-title">Uploaded peer files</div>
            <div class="fin-card-sub">${libraries.length} peer librar${libraries.length === 1 ? 'y' : 'ies'}</div></div>
            ${libraries.map((lib) => {
              const name = lib.peer_name || lib.company || lib.company_key;
              const ready = (lib.file_flags || {}).ciq_financials;
              const badge = ready
                ? '<span class="muted"> · ready for comps</span>'
                : '<span class="muted"> · still needs CIQ Financials</span>';
              const files = (lib.files || []).map((f) =>
                `<li><strong>${esc(f.kind)}</strong> — ${esc(f.filename)}</li>`).join('')
                || '<li class="muted">No files</li>';
              return `<div style="margin:0.55rem 0 0.35rem"><strong>${esc(name)}</strong>${badge}
                <ul class="meta-list">${files}</ul></div>`;
            }).join('')}</div>`
        : '<p class="muted">No peer files uploaded yet.</p>';
      const onFileBlock = onFile.length
        ? `<div class="fin-card"><div class="fin-card-head"><div class="fin-card-title">On file (status)</div></div>
            <ul class="meta-list">${onFile.map((p) => {
              const bits = [];
              const f = p.files || {};
              if (f.ciq_financials) bits.push('Financials');
              if (f.ciq_estimates) bits.push('Estimates');
              if (f.annual_report) bits.push('AR');
              if (f.transcript) bits.push('Concalls');
              const status = p.ready ? 'ready for comps' : 'partial';
              return `<li><strong>${esc(p.name)}</strong> — ${esc(bits.join(', ') || 'files')} (${status})</li>`;
            }).join('')}</ul></div>`
        : '';
      const asks = fileAsks.length
        ? `<div class="fin-card"><div class="fin-card-head"><div class="fin-card-title">Still need files</div></div>
            <ul>${fileAsks.map((a) => `<li><strong>${esc(a.name)}</strong> — ${esc((a.needs || []).join('; '))}</li>`).join('')}</ul>
            <p class="muted" style="font-size:0.78rem">After upload, click <strong>Update report</strong> or say “rerun analysis”.</p></div>`
        : '';
      const html = uploadBox
        + listBlock('Confirmed', confirmed, 'None confirmed yet — add peers in the sidebar or chat.')
        + listBlock('Proposed', proposed, 'No proposals — run analysis or add peers manually.')
        + uploadedBlock
        + onFileBlock
        + asks;
      fillPage('peers', html);
      wirePeerUploadControls();
    } catch {
      fillPage('peers', '<p class="muted">Peers unavailable.</p>');
    }
  }

  let peerUploadFiles = [];
  function wirePeerUploadControls() {
    const pick = $('btn-peer-pick-files');
    const go = $('btn-peer-upload-go');
    const input = $('peer-file-input');
    const label = $('peer-file-label');
    if (!pick || !go || !input) return;
    pick.onclick = () => input.click();
    input.onchange = () => {
      peerUploadFiles = [...(input.files || [])];
      if (label) label.textContent = peerUploadFiles.length
        ? peerUploadFiles.map((f) => f.name).join(', ')
        : 'No files selected';
      go.disabled = !peerUploadFiles.length;
    };
    go.onclick = () => uploadPeerFiles().catch((e) => addSystem(e.message));
  }

  async function uploadPeerFiles() {
    if (!currentSession) return;
    const name = ($('peer-upload-name')?.value || '').trim();
    if (!name) {
      addSystem('Enter the peer company name first.');
      return;
    }
    if (!peerUploadFiles.length) {
      addSystem('Choose peer files first.');
      return;
    }
    const fd = new FormData();
    fd.append('peer_name', name);
    peerUploadFiles.forEach((f) => fd.append('files', f));
    // Guess sections
    const sections = peerUploadFiles.map((f) => guessSection(f.name));
    fd.append('sections', JSON.stringify(sections));
    pushStatus(`Uploading peer files for ${name}…`);
    try {
      const r = await api(`/api/sessions/${currentSession}/peers/upload`, { method: 'POST', body: fd });
      peerUploadFiles = [];
      if ($('peer-file-input')) $('peer-file-input').value = '';
      if ($('peer-file-label')) $('peer-file-label').textContent = 'No files selected';
      const n = (r.files || []).length;
      addSystem(r.hint || `✓ ${n} peer file(s) saved for **${name}** (key \`${r.peer_key || '?'}\`).`);
      // Refresh session summary so Library can pick up peer inventories
      try {
        lastSession = await api(`/api/sessions/${currentSession}`);
      } catch (_) {}
      await loadPeersPage();
      if ($('library-page-body')) loadLibraryPage();
      showWorkspaceTabs(true);
      const peersBtn = document.querySelector('[data-ws-tab="peers"]');
      if (peersBtn) peersBtn.classList.add('has-new');
      const libBtn = document.querySelector('[data-ws-tab="library"]');
      if (libBtn) libBtn.classList.add('has-new');
    } catch (e) {
      addSystem('Peer upload failed: ' + e.message);
    }
  }

  async function updateReportFromNewFiles() {
    if (!currentSession) {
      addSystem('Open a session first.');
      return;
    }
    addSystem('Updating report — reingest → full analysis → regenerate…');
    setWorkspaceTab('progress');
    isProcessing = true;
    updateAnalyzeButton(lastSession);
    try {
      try {
        await api(`/api/sessions/${currentSession}/reingest`, { method: 'POST' });
      } catch (_) { /* optional */ }
      const s = await api(`/api/sessions/${currentSession}/analyze?force=true`, { method: 'POST' });
      if (s.job_id || s.status === 'queued' || s.status === 'running' || s.status === 'pending') {
        await pollAnalyzeJob(s.job_id || s.id);
      } else {
        await applySession(s);
        await afterAnalyze();
      }
      // afterAnalyze already loads report; also force note regen if needed
      try {
        const r = await api(`/api/sessions/${currentSession}/note`, { method: 'POST' });
        if (r.note || r.markdown) {
          fillPage('report', `<div class="report-md">${mdToHtml(r.note || r.markdown)}</div>`, { open: true });
        }
      } catch (_) { /* analyze may have written report */ }
    } catch (e) {
      addSystem('Update report failed: ' + e.message);
    } finally {
      isProcessing = false;
      updateAnalyzeButton(lastSession);
    }
  }

  function renderProposalChips(proposals) {
    if (!proposals?.length) return;
    const row = document.createElement('div');
    row.className = 'proposal-chip-row';
    proposals.forEach((p) => {
      const chip = document.createElement('div');
      chip.className = 'proposal-chip';
      chip.innerHTML = `<div class="pt">${esc(p.title || p.kind)}</div>
        <div class="muted" style="font-size:0.72rem">${esc((p.text || '').slice(0, 140))}</div>
        <div class="pa">
          <button type="button" data-accept="1">Yes, apply</button>
          <button type="button" class="ghost" data-accept="0">Ignore</button>
        </div>`;
      chip.querySelectorAll('button').forEach((b) => {
        b.onclick = async () => {
          const accept = b.getAttribute('data-accept') === '1';
          try {
            const r = await api(`/api/sessions/${currentSession}/chat/proposals`, {
              method: 'POST',
              body: { proposal: p, accept },
            });
            chip.remove();
            if (!accept) {
              pushStatus('Proposal ignored', 'ok');
              return;
            }
            addSystem(`Applied: **${(p.kind || '').replace(/_/g, ' ')}**`);
            const action = r.handoff?.action || r.action;
            if (action === 'rerun_analysis' || action === 'update_report') {
              if (r.job_id) await pollAnalyzeJob(r.job_id);
              else await updateReportFromNewFiles();
            } else if (action === 'show_report' || action === 'regenerate_report') {
              await loadReportPage(true);
            }
          } catch (e) {
            addSystem('Could not apply: ' + e.message);
          }
        };
      });
      row.appendChild(chip);
    });
    const host = READING_TABS.has(workspaceTab)
      ? ($('reading-answers-list') || $('chat-log'))
      : $('chat-log');
    if (host) {
      host.appendChild(row);
      if (READING_TABS.has(workspaceTab)) {
        $('reading-answers')?.classList.remove('hidden');
      }
    }
  }

  async function handleChatHandoff(r) {
    const action = r.handoff?.action;
    if (!action) return;
    if (action === 'rerun_analysis' || action === 'update_report') {
      const jobId = r.handoff?.job_id || r.job_id;
      setWorkspaceTab('progress');
      if (jobId) await pollAnalyzeJob(jobId);
      else await updateReportFromNewFiles();
    } else if (action === 'show_report' || action === 'regenerate_report') {
      await loadReportPage(true);
      setWorkspaceTab('report');
    }
  }

  function paintValuation(s) {
    const v = s?.valuation;
    if (!v) return;
    const fv = v.intrinsic_value_per_share;
    const mos = v.margin_of_safety_pct;
    const html = `<div class="fin-card">
      <div class="fin-card-head">
        <div class="fin-card-title">DCF preview</div>
        <div class="fin-card-sub">Edit assumptions in chat — no approval step</div>
      </div>
      <p style="font-size:1.1rem;margin:0.5rem 0"><strong>Fair value:</strong> ${fv != null ? '₹' + Number(fv).toLocaleString('en-IN', { maximumFractionDigits: 0 }) + '/share' : '—'}</p>
      <p class="muted">Margin of safety: ${mos != null ? (Number(mos) * (Math.abs(mos) <= 1.5 ? 100 : 1)).toFixed(1) + '%' : '—'}</p>
      <p class="muted" style="font-size:0.8rem">Ask in chat e.g. “Set WACC to 11% and show updated fair value”.</p>
    </div>`;
    fillPage('valuation', html);
  }

  // ── Session ──────────────────────────────────────────────────────
  function markActiveSession(sessionId) {
    document.querySelectorAll('.session-item').forEach((el) => {
      el.classList.toggle('active', el.dataset.id === sessionId);
    });
  }

  /** Full hydrate on click: GET session → tabs/report/chat. Cards never apply alone. */
  async function openSessionById(sessionId, { preferReport = true } = {}) {
    if (!sessionId) throw new Error('No session id');
    pushStatus('Loading full session…');
    addSystem(`Loading session \`${sessionId.slice(0, 8)}…\` — report, packs, and chat.`);
    const s = await api(`/api/sessions/${sessionId}`);
    await applySession(s, { openedFromList: true, preferReport });
    markActiveSession(s.id || sessionId);
    setChatEnabled(true);
    const input = $('chat-input');
    if (input) {
      input.disabled = false;
      input.placeholder = 'Ask about uploaded data…';
    }
    if ($('btn-send')) $('btn-send').disabled = false;
    pushStatus('Session loaded', 'ok');
    addSystem(
      `**${s.company?.name || 'Session'}** loaded.`
      + (s.has_research_report ? ' Report is on the **Report** tab.' : '')
      + ' Chat is ready.'
    );
    return s;
  }

  async function applySession(s, opts = {}) {
    lastSession = s;
    currentSession = s.id || currentSession;
    const name = s.company?.name || 'Research';
    if ($('chat-title')) $('chat-title').textContent = name;
    const metrics = s.library?.total_line_items || s.line_items || s.financial_line_items?.length || 0;
    const pdfs = s.library?.total_pdf_documents || s.pdf_documents?.length || 0;
    if ($('chat-meta')) $('chat-meta').textContent = `${metrics} metrics · ${pdfs} PDF docs`;
    if ($('side-company-label')) $('side-company-label').textContent = name;

    const sb = $('sidebar');
    if (sb) sb.classList.add('session-active');
    showWorkspaceTabs(true);

    updateAnalyzeButton(s);
    setChatEnabled(hasSessionData(s) || !!s.id);
    paintValuation(s);
    markActiveSession(s.id);
    if (s.data_readiness || s.user_asks) {
      renderReadinessPanel({
        data_readiness: s.data_readiness,
        asks: (s.user_asks || {}).asks || [],
      });
    }
    refreshReadinessAsks().catch(() => {});

    if (hasSessionData(s)) {
      revealTab('library', { markNew: false });
      revealTab('review', { markNew: false });
      revealTab('flags', { markNew: false });
      if (s.has_research_report) revealTab('report', { markNew: false });
      await loadOverviewIntoTabs(false);
      await loadReportPage(!!opts.openedFromList && !!opts.preferReport);
      if (s.has_direct_llm_report) {
        revealTab('direct', { markNew: false });
        await loadDirectReportPage(false);
      }
      await loadPeersPage();
      if (opts.openedFromList) {
        if (opts.preferReport && s.has_research_report) setWorkspaceTab('report');
        else setWorkspaceTab('review');
      } else if (!READING_TABS.has(workspaceTab) && workspaceTab !== 'files') {
        setWorkspaceTab('files');
      }
    } else {
      setWorkspaceTab('files');
    }

    await loadNotes();
    await loadAsks();
  }

  function setChatEnabled(on) {
    if ($('chat-input')) $('chat-input').disabled = !on;
    if ($('btn-send')) $('btn-send').disabled = !on;
  }

  function resetResearchSession() {
    currentSession = null;
    lastSession = null;
    selectedCompany = null;
    overviewShownFor = null;
    pendingFiles = [];
    pendingSections = [];
    $('sidebar')?.classList.remove('session-active');
    showWorkspaceTabs(true);
    setWorkspaceTab('files');
    if ($('chat-title')) $('chat-title').textContent = 'New Research';
    if ($('chat-meta')) $('chat-meta').textContent = 'Name the company, drop files, run one full analysis pass';
    if ($('side-company-label')) $('side-company-label').textContent = '—';
    setChatEnabled(false);
    updateAnalyzeButton(null);
  }

  // ── Company catalog / autocomplete ───────────────────────────────
  function _normName(s) {
    return String(s || '').trim().toLowerCase().replace(/\s+/g, ' ');
  }

  function mergeCatalogEntry(map, row) {
    const name = (row.name || row.company_key || '').trim();
    if (!name) return;
    const key = _normName(name);
    const prev = map.get(key) || { name, key: row.company_key || '', metrics: 0, docs: 0, ticker: '', source: '' };
    map.set(key, {
      name: prev.name.length >= name.length ? prev.name : name,
      key: row.company_key || prev.key || '',
      metrics: Math.max(prev.metrics || 0, row.metrics || row.total_line_items || 0),
      docs: Math.max(prev.docs || 0, row.docs || row.total_pdf_documents || row.document_count || 0),
      ticker: row.nse_ticker || row.ticker || prev.ticker || '',
      source: row.source || prev.source || 'library',
    });
  }

  async function loadCompanyCatalog() {
    const map = new Map();
    try {
      const data = await api('/api/library');
      for (const c of data.companies || []) {
        mergeCatalogEntry(map, { ...c, source: 'library' });
      }
    } catch (_) { /* optional */ }
    try {
      const sessions = await api('/api/sessions');
      for (const s of sessions || []) {
        const name = s.company?.name || s.company_key;
        if (!name) continue;
        const pdfCount = Array.isArray(s.pdf_documents)
          ? s.pdf_documents.length
          : (s.pdf_chunks || s.library?.total_pdf_documents || 0);
        mergeCatalogEntry(map, {
          name,
          company_key: s.company_key || s.company?.company_key,
          nse_ticker: s.company?.nse_ticker,
          source: 'recent',
          metrics: s.line_items || s.library?.total_line_items || 0,
          docs: pdfCount,
        });
      }
    } catch (_) { /* optional */ }
    companyCatalog = Array.from(map.values()).sort((a, b) => {
      const ad = (b.metrics || 0) + (b.docs || 0) - ((a.metrics || 0) + (a.docs || 0));
      if (ad) return ad;
      return a.name.localeCompare(b.name);
    });
    renderSavedCompaniesList();
  }

  function filterCompanyCatalog(q, limit = 12) {
    const needle = _normName(q);
    if (!needle) return companyCatalog.slice(0, limit);
    const scored = [];
    for (const c of companyCatalog) {
      const n = _normName(c.name);
      const t = _normName(c.ticker);
      const k = _normName(c.key);
      let score = 0;
      if (n === needle || t === needle || k === needle) score = 100;
      else if (n.startsWith(needle) || t.startsWith(needle)) score = 80;
      else if (n.includes(needle) || t.includes(needle) || k.includes(needle)) score = 50;
      else {
        const parts = needle.split(' ').filter(Boolean);
        if (parts.length && parts.every((p) => n.includes(p))) score = 40;
      }
      if (score) scored.push({ c, score: score + Math.min(10, (c.metrics || 0) / 500) });
    }
    scored.sort((a, b) => b.score - a.score || a.c.name.localeCompare(b.c.name));
    return scored.slice(0, limit).map((x) => x.c);
  }

  function renderSavedCompaniesList() {
    const section = $('saved-companies-section');
    const list = $('saved-companies-list');
    if (!list) return;
    if (!companyCatalog.length) {
      list.innerHTML = '<div class="muted" style="font-size:0.75rem">No saved profiles yet — type a name in the center.</div>';
      if (section) section.classList.remove('hidden');
      return;
    }
    if (section) section.classList.remove('hidden');
    list.innerHTML = '';
    companyCatalog.slice(0, 24).forEach((c) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'saved-co';
      const bits = [];
      if (c.ticker) bits.push(c.ticker);
      bits.push(`${c.metrics || 0} metrics`);
      bits.push(`${c.docs || 0} docs`);
      if (c.source === 'recent') bits.push('recent');
      btn.innerHTML = `<div>${escapeHtml(c.name)}</div><div class="sub">${escapeHtml(bits.join(' · '))}</div>`;
      btn.addEventListener('click', () => {
        if ($('company-input-center')) $('company-input-center').value = c.name;
        if ($('company-input')) $('company-input').value = c.name;
        closeCompanySuggest();
        // If a recent session exists for this company, load it fully
        const needle = _normName(c.name);
        const key = (c.company_key || c.key || '').toLowerCase();
        const match = recentSessionCards.find((s) => {
          const n = _normName(s.company?.name || s.company_key || '');
          const k = (s.company_key || '').toLowerCase();
          return (key && k && key === k) || (needle && n && (n === needle || n.includes(needle) || needle.includes(n)));
        });
        if (match?.id) {
          openSessionById(match.id, { preferReport: true }).catch((err) => addSystem(err.message));
          return;
        }
        selectCompany().catch((err) => addSystem(err.message));
      });
      list.appendChild(btn);
    });
  }

  function escapeHtml(s) {
    return String(s || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function closeCompanySuggest() {
    const box = $('company-suggest');
    if (box) {
      box.classList.remove('open');
      box.innerHTML = '';
    }
    suggestActive = -1;
  }

  function openCompanySuggest(q) {
    const box = $('company-suggest');
    const input = $('company-input-center');
    if (!box || !input) return;
    const rows = filterCompanyCatalog(q, 12);
    box.innerHTML = '';
    if (!rows.length) {
      if ((q || '').trim().length >= 2) {
        box.innerHTML = '<div class="sg-empty">No saved match — Continue to look up a new name</div>';
        box.classList.add('open');
      } else {
        closeCompanySuggest();
      }
      return;
    }
    rows.forEach((c, i) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.setAttribute('role', 'option');
      btn.dataset.idx = String(i);
      const bits = [];
      if (c.ticker) bits.push(c.ticker);
      bits.push(`${c.metrics || 0} metrics · ${c.docs || 0} docs`);
      if (c.source === 'recent') bits.push('recent session');
      else bits.push('saved profile');
      btn.innerHTML = `<div class="sg-name">${escapeHtml(c.name)}</div><div class="sg-sub">${escapeHtml(bits.join(' · '))}</div>`;
      btn.addEventListener('mousedown', (e) => {
        e.preventDefault(); // keep focus; avoid blur-before-click
        pickSuggestedCompany(c.name);
      });
      box.appendChild(btn);
    });
    suggestActive = -1;
    box.classList.add('open');
  }

  function pickSuggestedCompany(name) {
    if ($('company-input-center')) $('company-input-center').value = name;
    if ($('company-input')) $('company-input').value = name;
    closeCompanySuggest();
    selectCompany().catch((err) => addSystem(err.message));
  }

  function moveSuggest(delta) {
    const box = $('company-suggest');
    if (!box?.classList.contains('open')) return;
    const opts = [...box.querySelectorAll('button[role="option"]')];
    if (!opts.length) return;
    suggestActive = (suggestActive + delta + opts.length) % opts.length;
    opts.forEach((el, i) => el.classList.toggle('active', i === suggestActive));
    opts[suggestActive]?.scrollIntoView({ block: 'nearest' });
  }

  function wireCompanyAutocomplete() {
    const input = $('company-input-center');
    if (!input || input.dataset.acWired) return;
    input.dataset.acWired = '1';
    input.addEventListener('input', () => {
      if ($('company-input')) $('company-input').value = input.value;
      openCompanySuggest(input.value);
    });
    input.addEventListener('focus', () => openCompanySuggest(input.value));
    input.addEventListener('blur', () => {
      setTimeout(closeCompanySuggest, 150);
    });
    input.addEventListener('keydown', (e) => {
      const box = $('company-suggest');
      const open = box?.classList.contains('open');
      if (e.key === 'ArrowDown' && open) {
        e.preventDefault();
        moveSuggest(1);
      } else if (e.key === 'ArrowUp' && open) {
        e.preventDefault();
        moveSuggest(-1);
      } else if (e.key === 'Escape' && open) {
        e.preventDefault();
        closeCompanySuggest();
      } else if (e.key === 'Enter' && open && suggestActive >= 0) {
        e.preventDefault();
        const opts = box.querySelectorAll('button[role="option"]');
        const name = opts[suggestActive]?.querySelector('.sg-name')?.textContent;
        if (name) pickSuggestedCompany(name);
      }
    });
  }

  function renderCompanyPreview(lookup) {
    const el = $('company-preview');
    if (!el || !lookup) return;
    const inv = lookup.inventory || {};
    const name = lookup.company?.name || lookup.hint || '';
    const periods = (inv.financial_periods || []).slice(0, 6).join(' · ');
    const missing = lookup.missing || [];
    const metrics = lookup.total_line_items || inv.total_line_items || 0;
    const pdfs = lookup.total_pdf_documents ?? inv.total_pdf_documents ?? 0;
    el.innerHTML = `
      <div class="cp-title">${escapeHtml(name)}</div>
      ${lookup.matched
        ? `<div class="cp-row"><strong>${metrics}</strong> metrics · <strong>${pdfs}</strong> PDF docs</div>`
        : `<div class="cp-empty">No saved data yet for this company.</div>`}
      ${periods ? `<div class="cp-row">Periods: <strong>${escapeHtml(periods)}</strong></div>` : ''}
      ${missing.length
        ? `<div class="cp-missing">Still useful: ${escapeHtml(missing.join(' · '))}</div>`
        : (lookup.matched ? `<div class="cp-row" style="color:var(--green)">✓ Core documents on file</div>` : '')}`;
  }

  // ── Company / upload ─────────────────────────────────────────────
  function setContinueBusy(busy) {
    ['btn-company-continue', 'btn-company-continue-center'].forEach((id) => {
      const b = $(id);
      if (!b) return;
      b.disabled = !!busy;
      if (id === 'btn-company-continue') b.textContent = busy ? 'Looking up…' : 'Next';
      if (id === 'btn-company-continue-center') b.textContent = busy ? 'Looking up…' : 'Continue';
    });
  }

  async function selectCompany() {
    const name = ($('company-input-center')?.value || $('company-input')?.value || '').trim();
    if (!name) {
      addSystem('Enter a company name first.');
      ($('company-input-center') || $('company-input'))?.focus();
      return;
    }
    closeCompanySuggest();
    if ($('company-input')) $('company-input').value = name;
    if ($('company-input-center')) $('company-input-center').value = name;
    pushStatus('Looking up ' + name);
    setContinueBusy(true);
    try {
      const lookup = await api('/api/library/lookup?company=' + encodeURIComponent(name));
      const display = lookup.company?.name || name;
      selectedCompany = { name: display, lookup };
      if ($('company-input')) $('company-input').value = display;
      if ($('company-input-center')) $('company-input-center').value = display;

      const cta = $('center-session-cta');
      if (cta) cta.classList.remove('hidden');
      if ($('center-session-cta-title')) {
        $('center-session-cta-title').textContent = lookup.matched
          ? `${display} — library ready`
          : `${display} — no saved data yet`;
      }
      const inv = lookup.inventory || {};
      const metrics = lookup.total_line_items || inv.total_line_items || 0;
      const pdfs = lookup.total_pdf_documents ?? inv.total_pdf_documents ?? 0;
      const meta = lookup.matched
        ? `${metrics} metrics · ${pdfs} PDFs · core docs look ${lookup.missing?.length ? 'incomplete' : 'complete'}`
        : 'Upload Capital IQ Financials / Estimates to begin';
      if ($('center-session-cta-meta')) $('center-session-cta-meta').textContent = meta;
      if ($('company-preview-section')) $('company-preview-section').classList.remove('hidden');
      if ($('upload-workflow')) $('upload-workflow').classList.remove('hidden');
      renderCompanyPreview(lookup);
      if ($('chat-title')) $('chat-title').textContent = display;
      if ($('chat-meta')) $('chat-meta').textContent = meta;

      const missing = lookup.missing || [];
      const summary = lookup.matched
        ? `**${display}** — found saved data:\n- ${metrics} financial metrics\n- ${pdfs} PDF docs\n`
          + (missing.length ? `\nStill useful to add: ${missing.join(', ')}\n` : '\nCore documents look complete.\n')
          + `\nDrop any new files together below (or open the session and analyze with what you have).`
        : `**${display}** — no saved data yet.\n\n`
          + `Drop files together below — Capital IQ **Financials** (required), **Estimates** (recommended), AR / concalls optional.\n`
          + `Then click **Upload & Process**. You can add more docs anytime.`;
      addSystem(summary);
      showDocChecklist(lookup);
      pushStatus('Lookup ok', 'ok');
      // Refresh catalog so a newly matched profile appears in sidebar/suggest
      loadCompanyCatalog().catch(() => {});
      if (!lookup.matched || missing.length) setWorkspaceTab('files');
    } catch (e) {
      addSystem('Lookup error: ' + e.message);
      pushStatus('Lookup failed: ' + e.message, 'err');
    } finally {
      setContinueBusy(false);
    }
  }

  const DOC_CHECKLIST = [
    { key: 'ciq_financials', label: 'Capital IQ — Financials (Annual + Quarterly)', required: true },
    { key: 'ciq_estimates', label: 'Capital IQ — Estimates', required: false },
    { key: 'annual_report', label: 'Annual report PDF(s)', required: false },
    { key: 'concall', label: 'Concalls / transcripts', required: false },
    { key: 'presentation', label: 'Investor presentations', required: false },
    { key: 'brokerage', label: 'Brokerage reports', required: false },
  ];

  function showDocChecklist(lookup) {
    const grid = $('need-grid');
    if (!grid) return;
    const inv = lookup?.inventory || {};
    const has = {
      ciq_financials: !!(inv.capital_iq_financials || []).length || (lookup?.total_line_items || 0) > 0,
      ciq_estimates: !!(inv.capital_iq_estimates || []).length,
      annual_report: !!(inv.annual_report_files || inv.annual_reports || []).length,
      concall: !!(inv.concalls || inv.transcripts || []).length,
      presentation: !!(inv.presentations || []).length,
      brokerage: !!(inv.brokerage_reports || []).length,
    };
    grid.querySelectorAll('.need-card').forEach((card) => {
      const sec = card.getAttribute('data-section');
      const onFile = !!has[sec];
      card.classList.toggle('has-files', onFile);
      const empty = card.querySelector('[data-empty]');
      if (empty) empty.textContent = onFile ? 'On file ✓ — add more anytime' : 'None yet — add one or more';
    });
  }

  function companyName() {
    return (selectedCompany?.name
      || $('company-input-center')?.value
      || $('company-input')?.value
      || '').trim();
  }

  async function ensureSession() {
    if (currentSession) return currentSession;
    const name = companyName();
    if (!name) throw new Error('Enter a company name and click Continue first.');
    const fd = new FormData();
    fd.append('company', name);
    const s = await api('/api/research/start', { method: 'POST', body: fd });
    await applySession(s);
    return currentSession;
  }

  async function openResearchSession() {
    const name = companyName();
    if (!name) {
      addSystem('Enter a company name and click Continue first.');
      return;
    }
    pushStatus('Opening session…');
    const openBtns = ['btn-open-research', 'btn-open-research-center'];
    openBtns.forEach((id) => {
      const b = $(id);
      if (b) { b.disabled = true; b.textContent = 'Opening…'; }
    });
    try {
      // If files are already queued, upload them in the same start call
      if (pendingFiles.length) {
        await uploadPending();
      } else {
        await ensureSession();
        try {
          const re = await api(`/api/sessions/${currentSession}/reingest`, { method: 'POST' });
          await applySession(re);
        } catch (_) { /* optional */ }
        pushStatus('Session ready', 'ok');
        addSystem(
          hasSessionData(lastSession)
            ? 'Session open. Run **Analysis → Full Report**, or drop more files anytime on the Files tab.'
            : 'Session open — still need Capital IQ Financials. Drop files on the Files tab, then **Upload & Process**.'
        );
        showWorkspaceTabs(true);
        setWorkspaceTab(hasSessionData(lastSession) ? 'review' : 'files');
        await promptMissingDocs();
      }
    } catch (e) {
      addSystem('Could not open session: ' + e.message);
      pushStatus('Open failed: ' + e.message, 'err');
    } finally {
      openBtns.forEach((id) => {
        const b = $(id);
        if (b) { b.disabled = false; b.textContent = 'Open research session'; }
      });
    }
  }

  function guessSection(filename) {
    const n = (filename || '').toLowerCase();
    if (/financials?|ciq.?fin|cap.?iq.?fin|income.?statement|balance.?sheet/.test(n) && /\.(xls|xlsx)$/.test(n)) {
      return 'ciq_financials';
    }
    if (/estimates?|consensus|ciq.?est/.test(n) && /\.(xls|xlsx)$/.test(n)) return 'ciq_estimates';
    if (/annual.?report|\bar\b|20\d{2}.*report/.test(n) && n.endsWith('.pdf')) return 'annual_report';
    if (/concall|transcript|earnings.?call|q[1-4]/.test(n) && n.endsWith('.pdf')) return 'concall';
    if (/presentation|investor.?deck|ppt/.test(n)) return 'presentation';
    if (/initiat|broker|securit|motilal|nuvama|icici|kotak|jm.?financial/.test(n)) return 'brokerage';
    if (/\.(png|jpe?g|webp|gif)$/.test(n)) return 'screenshot';
    if (/\.(xls|xlsx)$/.test(n)) return 'ciq_financials'; // default spreadsheet → financials
    return null;
  }

  function queueFiles(fileList, section) {
    let added = 0;
    [...fileList].forEach((f) => {
      // de-dupe by name+size
      if (pendingFiles.some((p) => p.name === f.name && p.size === f.size)) return;
      pendingFiles.push(f);
      pendingSections.push(section || guessSection(f.name));
      added += 1;
    });
    renderPending();
    if (added) {
      pushStatus(`Queued ${added} file(s) (${pendingFiles.length} total)`);
      lastSystemText = ''; // allow upload status to show even if similar
      addSystem(
        `Queued **${pendingFiles.length}** file(s). Click **Upload & Process** `
        + `(opens a session automatically if needed). You can add more later.`
      );
    }
  }

  function sectionLabel(sec) {
    return DOC_CHECKLIST.find((d) => d.key === sec)?.label || sec || 'auto-detect';
  }

  function renderPending() {
    const n = pendingFiles.length;
    const label = $('center-pending-label');
    const box = $('center-pending');
    const list = $('center-pending-files') || $('pending-files');
    const canUpload = n > 0 && !!companyName();
    if (label) {
      label.textContent = n
        ? `${n} file(s) queued${canUpload ? ' — ready to save' : ' — Continue with company name first'}`
        : '';
    }
    if (box) {
      box.classList.toggle('has-files', n > 0);
      box.classList.toggle('visible', n > 0);
    }
    if (list) {
      list.innerHTML = pendingFiles.map((f, i) => {
        const sec = pendingSections[i];
        return `<div class="file-row" style="display:flex;justify-content:space-between;gap:0.5rem;align-items:center;padding:0.25rem 0">
          <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${f.name}</span>
          <span class="section-tag">${sectionLabel(sec)}</span>
        </div>`;
      }).join('');
    }
    const btn = $('btn-upload-center') || $('btn-upload');
    if (btn) {
      btn.classList.remove('hidden');
      btn.style.display = n ? '' : 'none';
      btn.disabled = !canUpload || isProcessing;
      btn.textContent = isProcessing
        ? 'Uploading…'
        : (currentSession ? 'Upload & Process' : 'Upload & open session');
      btn.title = canUpload
        ? 'Save these files to the company library and ingest'
        : 'Enter company name + Continue first';
    }
    // Highlight need-cards that match queued sections
    document.querySelectorAll('#need-grid .need-card').forEach((card) => {
      const sec = card.getAttribute('data-section');
      const queued = pendingSections.includes(sec);
      card.classList.toggle('has-queued', queued);
    });
  }

  async function uploadPending() {
    if (!pendingFiles.length) {
      addSystem('Drop files first, then click Upload & Process.');
      return;
    }
    if (!companyName()) {
      addSystem('Enter the company name and click **Continue**, then Upload & Process.');
      ($('company-input-center') || $('company-input'))?.focus();
      return;
    }
    // Fill any remaining null sections with guesses
    pendingSections = pendingSections.map((s, i) => s || guessSection(pendingFiles[i].name));

    isProcessing = true;
    renderPending();
    const n = pendingFiles.length;
    pushStatus(`Uploading ${n} file(s)…`);
    addSystem(`Uploading **${n}** file(s) for **${companyName()}**…`);

    const filesSnapshot = pendingFiles.slice();
    const sectionsSnapshot = pendingSections.slice();

    try {
      let s;
      if (!currentSession) {
        // One-shot: create session + upload + ingest
        const fd = new FormData();
        fd.append('company', companyName());
        filesSnapshot.forEach((f) => fd.append('files', f));
        fd.append('sections', JSON.stringify(sectionsSnapshot));
        s = await api('/api/research/start', { method: 'POST', body: fd });
      } else {
        const fd = new FormData();
        filesSnapshot.forEach((f) => fd.append('files', f));
        fd.append('sections', JSON.stringify(sectionsSnapshot));
        try {
          s = await api(`/api/sessions/${currentSession}/process`, { method: 'POST', body: fd });
        } catch (e) {
          const fd2 = new FormData();
          filesSnapshot.forEach((f) => fd2.append('files', f));
          fd2.append('sections', JSON.stringify(sectionsSnapshot));
          await api(`/api/sessions/${currentSession}/upload`, { method: 'POST', body: fd2 });
          s = await api(`/api/sessions/${currentSession}/ingest`, { method: 'POST' });
        }
      }
      pendingFiles = [];
      pendingSections = [];
      renderPending();
      await applySession(s);
      setWorkspaceTab('files'); // docs-first: stay so user can add more
      pushStatus('Upload + ingest done', 'ok');
      const kinds = sectionsSnapshot.filter(Boolean);
      addSystem(
        `✓ Saved **${n}** file(s)`
        + (kinds.length ? ` (${[...new Set(kinds.map(sectionLabel))].join(', ')})` : '')
        + '. You can drop more anytime on the **Files** tab.'
      );
      const readiness = await refreshReadinessAsks();
      if (readiness?.summary) {
        addSystem(`**Next uploads:** ${stripMdLight(readiness.summary)}`);
      }
      await promptMissingDocs();
      if (hasSessionData(lastSession)) {
        addSystem('Ready when you are — click **Run Analysis → Full Report**, or keep adding docs.');
      }
    } catch (e) {
      addSystem('Upload failed: ' + e.message);
      pushStatus('Upload failed: ' + e.message, 'err');
    } finally {
      isProcessing = false;
      renderPending();
      updateAnalyzeButton(lastSession);
    }
  }

  async function promptMissingDocs() {
    // Ask about gaps — user can ignore (separate from API asks)
    const lookup = selectedCompany?.lookup;
    let missingKeys = [];
    try {
      const fresh = await api('/api/library/lookup?company=' + encodeURIComponent(companyName()) + '&pull_drive=false&pull_telegram=false');
      selectedCompany = { ...(selectedCompany || {}), name: companyName(), lookup: fresh };
      showDocChecklist(fresh);
      const inv = fresh.inventory || {};
      const haveFin = !!(inv.capital_iq_financials || []).length || (fresh.total_line_items || 0) > 0;
      const haveEst = !!(inv.capital_iq_estimates || []).length;
      const haveAr = !!(inv.annual_report_files || inv.annual_reports || []).length;
      if (!haveFin) missingKeys.push('ciq_financials');
      if (!haveEst) missingKeys.push('ciq_estimates');
      if (!haveAr) missingKeys.push('annual_report');
    } catch (_) {
      // lookup.missing is human labels — map known ones
      const raw = lookup?.missing || [];
      if (raw.some((x) => /financial/i.test(String(x)))) missingKeys.push('ciq_financials');
      if (raw.some((x) => /estimate/i.test(String(x)))) missingKeys.push('ciq_estimates');
      if (raw.some((x) => /annual/i.test(String(x)))) missingKeys.push('annual_report');
    }

    const box = $('side-missing-docs');
    const empty = $('side-missing-empty');
    if (!missingKeys.length) {
      if (box) { box.classList.add('hidden'); box.innerHTML = ''; }
      if (empty) empty.style.display = '';
      return;
    }

    const lines = missingKeys.map((k) => {
      const d = DOC_CHECKLIST.find((x) => x.key === k);
      return `- **${d?.label || k}**${d?.required ? ' _(required for analysis)_' : ' _(optional — can skip)'}`;
    });
    addSystem(
      'Still useful to add:\n' + lines.join('\n')
      + '\n\nDrop them on **Files** whenever you want, or ignore for now and continue.'
    );
    if (!box) return;
    box.classList.remove('hidden');
    if (empty) empty.style.display = 'none';
    box.innerHTML = missingKeys.map((k) => {
      const d = DOC_CHECKLIST.find((x) => x.key === k);
      const label = d?.label || k;
      return `<div class="side-ask" style="margin:0.35rem 0;padding:0.45rem;border:1px solid var(--border);border-radius:8px">
        <div style="font-size:0.8rem;margin-bottom:0.35rem">Add ${label}?</div>
        <button type="button" class="ghost" data-ask-upload="${k}" style="font-size:0.75rem;margin-right:0.35rem">Upload…</button>
        <button type="button" class="ghost" data-ask-skip="${k}" style="font-size:0.75rem">Ignore for now</button>
      </div>`;
    }).join('');
    box.querySelectorAll('[data-ask-upload]').forEach((b) => {
      b.onclick = () => {
        const sec = b.getAttribute('data-ask-upload');
        const input = $('file-input-section');
        if (!input) return;
        setWorkspaceTab('files');
        input.onchange = () => { queueFiles(input.files, sec); input.value = ''; };
        input.click();
      };
    });
    box.querySelectorAll('[data-ask-skip]').forEach((b) => {
      b.onclick = () => {
        b.closest('.side-ask')?.remove();
        if (!box.querySelector('.side-ask')) {
          box.classList.add('hidden');
          if (empty) empty.style.display = '';
        }
        pushStatus('Skipped ' + (b.getAttribute('data-ask-skip') || ''), 'ok');
      };
    });
  }

  // ── Notes / asks ─────────────────────────────────────────────────
  async function loadNotes() {
    if (!currentSession) return;
    try {
      const r = await api(`/api/notes?session_id=${currentSession}`);
      if ($('analyst-notes') && typeof r.text === 'string') $('analyst-notes').value = r.text;
      if ($('notes-save-state')) $('notes-save-state').textContent = 'loaded';
    } catch (_) {}
  }

  function scheduleSaveNotes() {
    if (notesTimer) clearTimeout(notesTimer);
    notesTimer = setTimeout(saveNotes, 600);
    if ($('notes-save-state')) $('notes-save-state').textContent = 'saving…';
  }

  async function saveNotes() {
    if (!currentSession) return;
    try {
      await api('/api/notes', {
        method: 'PUT',
        body: { session_id: currentSession, text: $('analyst-notes')?.value || '' },
      });
      if ($('notes-save-state')) $('notes-save-state').textContent = 'saved';
    } catch {
      if ($('notes-save-state')) $('notes-save-state').textContent = 'error';
    }
  }

  async function loadAsks() {
    if (!currentSession) return;
    try {
      const r = await api(`/api/sessions/${currentSession}/asks`);
      const asks = r.asks || r.items || [];
      // Do not touch #side-missing-docs — that belongs to promptMissingDocs
      const box = $('side-asks');
      const empty = $('side-asks-empty');
      if (!box) return;
      if (!asks.length) {
        box.classList.add('hidden');
        box.innerHTML = '';
        if (empty) empty.style.display = '';
        return;
      }
      if (empty) empty.style.display = 'none';
      box.classList.remove('hidden');
      box.innerHTML = asks.map((a) =>
        `<div class="side-ask">${esc(a.prompt || a.text || a.question || String(a))}</div>`
      ).join('');
    } catch (_) {}
  }

  // ── Chat ─────────────────────────────────────────────────────────
  async function sendChat(text) {
    if (!text.trim()) return;
    if (!currentSession) {
      addSystem('Open a research session (or upload files) before chatting.');
      return;
    }
    const renderedUser = esc(text);
    const log = $('chat-log');
    const welcome = $('welcome');
    if (welcome) welcome.remove();
    if (log) {
      const user = document.createElement('div');
      user.className = 'msg user';
      user.textContent = text;
      log.appendChild(user);
    }
    if (READING_TABS.has(workspaceTab)) {
      appendReadingAnswer(`<div class="msg-bubble">${renderedUser}</div>`, 'user');
    }
    try {
      const r = await api(`/api/sessions/${currentSession}/chat`, {
        method: 'POST',
        body: { question: text },
      });
      const reply = renderMarkdown(r.reply || r.answer || r.message || r.response || JSON.stringify(r));
      if (log) {
        const bot = document.createElement('div');
        bot.className = 'msg bot';
        bot.innerHTML = reply;
        log.appendChild(bot);
        log.scrollTop = log.scrollHeight;
      }
      if (READING_TABS.has(workspaceTab)) appendReadingAnswer(reply, 'bot');
      if (r.proposals?.length) renderProposalChips(r.proposals);
      await handleChatHandoff(r);
      if (r.session) await applySession(r.session);
    } catch (e) {
      addSystem('Chat error: ' + e.message);
    }
  }

  // ── Boot ─────────────────────────────────────────────────────────
  async function initAuth() {
    try {
      const h = await api('/api/health');
      authRequired = !!h.auth_required;
      const ob = $('ollama-badge');
      if (ob) {
        // Prefer Hive status if present
        if (h.hive || h.llm) {
          ob.textContent = (h.hive || h.llm) ? 'LLM ✓' : 'LLM offline';
          ob.className = 'badge ' + ((h.hive || h.llm) ? 'ok' : 'warn');
        } else {
          ob.textContent = h.ollama ? 'Ollama ✓' : 'Ollama offline';
          ob.className = 'badge ' + (h.ollama ? 'ok' : 'warn');
        }
      }
      if (authRequired && !apiToken && !IIS_INERTIA_AUTH_ONLY) {
        setUserBadge('Sign in');
        showLogin();
        return;
      }
      try {
        const me = await api('/api/auth/me');
        setUserBadge(me.name || me.email || (apiToken ? 'Signed in' : 'Guest'));
        hideLogin();
      } catch {
        setUserBadge(authRequired ? 'Sign in' : 'Account');
        if (authRequired && !IIS_INERTIA_AUTH_ONLY) showLogin();
        else hideLogin();
      }
      loadSessions();
    } catch (e) {
      console.warn(e);
    }
  }

  async function loadSessions() {
    const list = $('session-list');
    if (list) {
      list.innerHTML = '<div class="muted" style="font-size:0.75rem;padding:0.35rem">Loading recent…</div>';
    }
    try {
      const data = await api('/api/sessions?limit=16');
      if (!list) return;
      const sessions = data.sessions || data || [];
      recentSessionCards = sessions;
      if (!sessions.length) {
        list.innerHTML = '<div class="muted" style="font-size:0.75rem;padding:0.35rem">No sessions yet</div>';
        return;
      }
      list.innerHTML = sessions.slice(0, 16).map((s) => {
        const name = s.company?.name || s.company_key || (s.id || '').slice(0, 8);
        const bits = [];
        if (s.has_research_report) bits.push('report');
        if (s.status) bits.push(String(s.status).replace(/_/g, ' '));
        const sub = bits.length ? `<div class="sub">${bits.join(' · ')}</div>` : '';
        return `<div class="session-item" data-id="${s.id}" title="Click to load full session"><div>${escapeHtml(name)}</div>${sub}</div>`;
      }).join('');
      list.querySelectorAll('.session-item').forEach((el) => {
        el.onclick = async () => {
          if (!el.dataset.id) return;
          el.classList.add('loading');
          try {
            await openSessionById(el.dataset.id, { preferReport: true });
          } catch (e) {
            addSystem('Could not load session: ' + (e.message || e));
            pushStatus('Load failed', 'err');
          } finally {
            el.classList.remove('loading');
          }
        };
      });
      if (currentSession) markActiveSession(currentSession);
    } catch (e) {
      if (list) {
        list.innerHTML = `<div class="muted" style="font-size:0.75rem;padding:0.35rem">Could not list sessions${e?.message ? ': ' + escapeHtml(e.message) : ''}</div>`;
      }
    }
  }

  function wireUi() {
    $('user-badge')?.addEventListener('click', () => showLogin());
    $('btn-login')?.addEventListener('click', (e) => { e.preventDefault(); doLogin(); });
    $('btn-register')?.addEventListener('click', (e) => { e.preventDefault(); doRegister(); });
    $('btn-logout')?.addEventListener('click', (e) => { e.preventDefault(); doLogout(); });
    $('btn-login-dismiss')?.addEventListener('click', (e) => { e.preventDefault(); hideLogin(); });
    $('login-password')?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); doLogin(); }
    });
    $('login-overlay')?.addEventListener('click', (e) => {
      if (e.target === $('login-overlay') && !authRequired) hideLogin();
    });

    $('btn-chat-wide')?.addEventListener('click', () => {
      setChatWide(!isChatWide());
    });
    $('workspace-tabs')?.querySelectorAll('[data-ws-tab]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const tab = btn.getAttribute('data-ws-tab');
        setWorkspaceTab(tab);
        if (tab === 'report') loadReportPage(false);
        if (tab === 'direct') loadDirectReportPage(false);
        if (tab === 'peers') loadPeersPage();
        if (tab === 'flags' || tab === 'guidance' || tab === 'review') {
          loadOverviewIntoTabs(false);
        }
      });
    });
    $('review-subtabs')?.querySelectorAll('[data-review-tab]').forEach((btn) => {
      btn.addEventListener('click', () => {
        setWorkspaceTab('review');
        setReviewSubtab(btn.getAttribute('data-review-tab'));
      });
    });

    const onContinue = (e) => {
      e?.preventDefault?.();
      selectCompany().catch((err) => addSystem(err.message));
    };
    const onOpen = (e) => {
      e?.preventDefault?.();
      openResearchSession().catch((err) => addSystem(err.message));
    };
    // Prefer onclick only — survives listener clears without double-firing
    ['btn-company-continue', 'btn-company-continue-center'].forEach((id) => {
      const b = $(id);
      if (b) b.onclick = onContinue;
    });
    ['btn-open-research', 'btn-open-research-center'].forEach((id) => {
      const b = $(id);
      if (b) b.onclick = onOpen;
    });
    ['company-input', 'company-input-center'].forEach((id) => {
      $(id)?.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          selectCompany().catch((err) => addSystem(err.message));
        }
      });
    });
    $('btn-analyze')?.addEventListener('click', runAnalyze);
    $('btn-review-analyze')?.addEventListener('click', runAnalyze);
    $('btn-abort-analyze')?.addEventListener('click', abortAnalyzeJob);
    $('btn-approve-center')?.addEventListener('click', () => {
      if (!hasSessionData(lastSession)) {
        setWorkspaceTab('files');
        return;
      }
      advanceReviewSubtab();
    });
    $('btn-review-refresh')?.addEventListener('click', () => {
      overviewShownFor = null;
      loadOverviewIntoTabs(true);
    });
    ['btn-upload-center', 'btn-upload'].forEach((id) => {
      const b = $(id);
      if (b) b.onclick = (e) => { e?.preventDefault?.(); uploadPending(); };
    });
    $('btn-analyze-strip-progress')?.addEventListener('click', () => setWorkspaceTab('progress'));
    $('btn-analyze-strip-files')?.addEventListener('click', () => setWorkspaceTab('files'));
    $('btn-update-report')?.addEventListener('click', () => {
      updateReportFromNewFiles().catch((e) => addSystem(e.message));
    });
    // Initial progress stage list + prompts
    renderProgressUI({ status: 'idle', progress: 0, message: 'Idle — start Run Analysis when ready', steps: ANALYZE_STAGES.map((x) => x[0]) });
    $('btn-new-research')?.addEventListener('click', resetResearchSession);
    $('btn-note')?.addEventListener('click', () => {
      regenerateReportOnly().catch((e) => addSystem(e.message));
    });
    $('btn-report-regen')?.addEventListener('click', () => {
      regenerateReportOnly().catch((e) => addSystem(e.message));
    });
    $('btn-direct-regen')?.addEventListener('click', rerunDirectReport);
    $('btn-report-finalize')?.addEventListener('click', async () => {
      if (!currentSession) return;
      try {
        const r = await api(`/api/sessions/${currentSession}/report/finalize-docx`, { method: 'POST' });
        addSystem(r.path ? `Word saved: ${r.path}` : 'Finalize requested.');
      } catch (e) { addSystem(e.message); }
    });
    $('btn-send')?.addEventListener('click', () => {
      const t = $('chat-input');
      if (!t?.value.trim()) return;
      const v = t.value; t.value = '';
      sendChat(v);
    });
    $('chat-input')?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        $('btn-send')?.click();
      }
    });
    $('analyst-notes')?.addEventListener('input', scheduleSaveNotes);
    $('btn-peer-names')?.addEventListener('click', async () => {
      if (!currentSession) return;
      const raw = $('peer-names-input')?.value || '';
      const names = raw.split(/[,;]/).map((x) => x.trim()).filter(Boolean);
      if (!names.length) {
        addSystem('Enter peer names separated by commas.');
        return;
      }
      try {
        await api(`/api/sessions/${currentSession}/peers`, { method: 'POST', body: { add: names } });
        pushStatus('Peers saved', 'ok');
        if ($('peer-names-input')) $('peer-names-input').value = '';
        loadPeersPage();
      } catch (e) { addSystem(e.message); }
    });

    // Dropzone
    const dz = $('dropzone');
    const fi = $('file-input');
    if (dz && fi) {
      dz.addEventListener('click', () => fi.click());
      dz.addEventListener('dragover', (e) => { e.preventDefault(); dz.classList.add('dragover'); });
      dz.addEventListener('dragleave', () => dz.classList.remove('dragover'));
      dz.addEventListener('drop', (e) => {
        e.preventDefault();
        dz.classList.remove('dragover');
        queueFiles(e.dataTransfer.files, null);
      });
      fi.addEventListener('change', () => { queueFiles(fi.files, null); fi.value = ''; });
    }
    document.querySelectorAll('[data-add-section]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const sec = btn.getAttribute('data-add-section');
        const input = $('file-input-section');
        if (!input) return;
        input.onchange = () => { queueFiles(input.files, sec); input.value = ''; };
        input.click();
      });
    });

    document.querySelectorAll('.chip[data-prompt]').forEach((c) => {
      c.addEventListener('click', () => {
        if (!currentSession) {
          addSystem('Upload files or open a session first, then use these prompts.');
          return;
        }
        const prompt = c.getAttribute('data-prompt');
        if ($('chat-input')) {
          $('chat-input').value = prompt;
          $('chat-input').disabled = false;
        }
        if ($('btn-send')) $('btn-send').disabled = false;
        sendChat(prompt);
      });
    });

    // Sync company inputs
    const a = $('company-input');
    const b = $('company-input-center');
    if (a && b) {
      a.addEventListener('input', () => { b.value = a.value; });
      b.addEventListener('input', () => { a.value = b.value; });
    }

    wireSrcRefPopup();
    setWorkspaceTab('files');
  }

  function wireSrcRefPopup() {
    const pop = $('src-ref-pop');
    const titleEl = $('src-ref-title');
    const bodyEl = $('src-ref-body');
    const closeBtn = $('src-ref-close');
    if (!pop || !titleEl || !bodyEl) return;
    let activeBtn = null;

    const close = () => {
      pop.classList.remove('open');
      pop.hidden = true;
      if (activeBtn) {
        activeBtn.setAttribute('aria-expanded', 'false');
        activeBtn = null;
      }
    };

    const openAt = (btn) => {
      let title = '';
      let body = '';
      try {
        title = decodeURIComponent(btn.getAttribute('data-src-title') || '');
        body = decodeURIComponent(btn.getAttribute('data-src-body') || '');
      } catch (_) {
        title = btn.getAttribute('data-src-title') || 'Source';
        body = btn.getAttribute('data-src-body') || '';
      }
      titleEl.textContent = title || 'Source';
      bodyEl.textContent = body || '';
      pop.hidden = false;
      pop.classList.add('open');
      if (activeBtn && activeBtn !== btn) activeBtn.setAttribute('aria-expanded', 'false');
      activeBtn = btn;
      btn.setAttribute('aria-expanded', 'true');

      const r = btn.getBoundingClientRect();
      const pad = 10;
      const w = Math.min(360, window.innerWidth - pad * 2);
      let left = Math.min(Math.max(pad, r.left), window.innerWidth - w - pad);
      let top = r.bottom + 8;
      pop.style.width = w + 'px';
      pop.style.left = left + 'px';
      pop.style.top = top + 'px';
      // Flip above if it would cover too much below
      requestAnimationFrame(() => {
        const h = pop.offsetHeight || 160;
        if (top + h > window.innerHeight - pad && r.top > h + 16) {
          pop.style.top = Math.max(pad, r.top - h - 8) + 'px';
        }
      });
    };

    document.addEventListener('click', (e) => {
      const btn = e.target?.closest?.('.src-ref-btn');
      if (btn) {
        e.preventDefault();
        e.stopPropagation();
        if (activeBtn === btn && pop.classList.contains('open')) {
          close();
        } else {
          openAt(btn);
        }
        return;
      }
      if (pop.classList.contains('open') && !pop.contains(e.target)) close();
    });
    closeBtn?.addEventListener('click', (e) => {
      e.preventDefault();
      close();
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && pop.classList.contains('open')) close();
    });
  }

  function boot() {
    try {
      wireUi();
      wireCompanyAutocomplete();
      showWorkspaceTabs(true);
      setWorkspaceTab('files');
      setChatWide(false);
      initAuth();
      loadCompanyCatalog().catch(() => {});
    } catch (e) {
      console.error('IIS UI boot failed', e);
      const log = document.getElementById('chat-log');
      if (log) {
        const el = document.createElement('div');
        el.className = 'msg system';
        el.textContent = 'UI boot error: ' + (e && e.message ? e.message : e);
        log.appendChild(el);
      }
    }
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
