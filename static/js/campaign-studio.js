const { useState, useEffect, useRef, useMemo } = React;

// CSRF token from meta tag (set by Flask/Jinja2 in the HTML)
const CSRF = document.querySelector('meta[name="csrf-token"]')?.content || "";

// Parse JSON from fetch response; if server returned HTML (e.g. login page) throw a clear error
async function safeJson(r) {
  var ct = (r.headers.get("content-type") || "").toLowerCase();
  if (r.status === 302 || r.status === 401 || ct.indexOf("text/html") !== -1) {
    throw new Error("Session expired. Please refresh the page and log in again.");
  }
  var text = await r.text();
  if (!text || !text.trim()) return {};
  if (text.trim().substring(0, 15).toLowerCase().indexOf("<!doctype") !== -1 || text.trim().startsWith("<")) {
    throw new Error("Session expired. Please refresh the page and log in again.");
  }
  try {
    return JSON.parse(text);
  } catch (e) {
    throw new Error("Session expired. Please refresh the page and log in again.");
  }
}

// ══════════════════════════════════════════════════════════════════════
// STORAGE — localStorage
// ══════════════════════════════════════════════════════════════════════
function stor(op, key, val) {
  try {
    if (op === "get") {
      const raw = localStorage.getItem(key);
      return raw ? JSON.parse(raw) : null;
    }
    if (op === "set") {
      localStorage.setItem(key, JSON.stringify(val));
    }
  } catch(e) { return null; }
}

// ══════════════════════════════════════════════════════════════════════
// DESIGN TOKENS
// ══════════════════════════════════════════════════════════════════════
const T = {
  bg: "#060810", s1: "#0B0E1A", s2: "#0F1220",
  border: "rgba(255,255,255,0.07)", text: "#E4E7F2", muted: "#424960",
  gold: "#E9B96E", blue: "#6C8EF5", green: "#3ECFA0", red: "#FC6076",
};

// ══════════════════════════════════════════════════════════════════════
// PLATFORM CONFIG
// ══════════════════════════════════════════════════════════════════════
const PLATFORMS = {
  linkedin: {
    label: "LinkedIn", icon: "in", color: "#0A66C2", bg: "#0A66C214",
    postGuide: "Professional tone. 150-220 words. End with a thought-provoking question. Include: 'I wrote a detailed research article on this - link in comments.' No hashtag spam.",
    teaserStyle: "insight-driven hook + 3 key findings from article + 'Full research with sources in comments'",
  },
  whatsapp: {
    label: "WhatsApp", icon: "W", color: "#25D366", bg: "#25D36614",
    postGuide: "Conversational. Short paragraphs. Mobile-friendly. 80-120 words. End with: 'I published a full breakdown with data - reply if you want the link.'",
    teaserStyle: "casual friendly opener + 1 surprising stat from article + 'Full article available - just reply'",
  },
  twitter: {
    label: "X / Twitter", icon: "X", color: "#e0e0e0", bg: "#e0e0e010",
    postGuide: "Thread of 3-4 tweets. Hook tweet must stop the scroll. Each tweet max 280 chars. Final tweet: 'Full research article (link in bio / replies)'",
    teaserStyle: "viral hook tweet + 2-3 finding tweets + link tweet pointing to full article",
  },
};

const ARTICLE_TYPES = [
  { id: "deepdive", icon: "Research", label: "Concept Deep-Dive" },
  { id: "guide",    icon: "Guide",    label: "Personal Finance Guide" },
  { id: "myth",     icon: "Myth",     label: "Myth-Busting Long Read" },
  { id: "current",  icon: "Current",  label: "Current Topic Analysis" },
];

const TONES   = ["Authoritative & analytical", "Conversational & warm", "Journalistic & neutral", "Educator-to-student"];
const LENGTHS = ["Short (600-800 words)", "Standard (900-1200 words)", "Long-form (1300-1600 words)"];

// Content intelligence — adviser behavioural tags (must match inertia_content.models.Tag.ADVISER)
const IC_CHANNELS = ["WhatsApp", "Email", "LinkedIn", "Phone", "In-person"];
const IC_DECISIONS = ["Send", "Skip", "Hold"];
const IC_ENGAGEMENTS = ["None", "Opened", "Responded"];

const ADVISER_TAGS = [
  { id: "behav_friction", label: "Behavioural Friction" },
  { id: "emotional_inv", label: "Emotional Investor" },
  { id: "high_awareness", label: "High Awareness" },
  { id: "underserved", label: "Underserved Setup" },
  { id: "life_transition", label: "Life Transition" },
  { id: "relship_drift", label: "Relationship Drift" },
  { id: "high_earner", label: "High Earner" },
];

// ══════════════════════════════════════════════════════════════════════
// UI PRIMITIVES
// ══════════════════════════════════════════════════════════════════════
const SL = ({ c, children }) => (
  <div style={{ fontSize: 10, color: c || T.gold, letterSpacing: 2.5, fontWeight: 700, fontFamily: "monospace", marginBottom: 10 }}>{children}</div>
);

const Card = ({ children, sx, glow, onClick }) => (
  <div onClick={onClick} style={{ background: T.s1, border: "1px solid " + (glow ? glow + "44" : T.border), borderRadius: 14, padding: 18, cursor: onClick ? "pointer" : "default", transition: "all .2s", ...sx }}>
    {children}
  </div>
);

const Tag = ({ children, color, active, onClick }) => {
  const c = color || T.gold;
  return (
    <span onClick={onClick} style={{
      background: active ? c + "18" : "rgba(255,255,255,0.03)",
      color: active ? c : T.muted, border: "1px solid " + (active ? c + "44" : T.border),
      borderRadius: 20, padding: "3px 10px", fontSize: 11, fontWeight: 700,
      fontFamily: "monospace", cursor: onClick ? "pointer" : "default", transition: "all .15s",
    }}>{children}</span>
  );
};

function Btn({ children, onClick, disabled, v, sx, sm }) {
  const variant = v || "gold";
  const vs = {
    gold:  { background: "linear-gradient(135deg,#E9B96E,#C8881E)", color: "#060810" },
    blue:  { background: "linear-gradient(135deg,#6C8EF5,#4A6CD4)", color: "#fff" },
    green: { background: "linear-gradient(135deg,#3ECFA0,#28A87E)", color: "#060810" },
    ghost: { background: "rgba(255,255,255,.04)", color: T.muted, border: "1px solid " + T.border },
    red:   { background: "rgba(252,96,118,.1)", color: T.red, border: "1px solid rgba(252,96,118,.2)" },
  };
  return (
    <button onClick={onClick} disabled={disabled} style={{
      ...vs[variant], border: "none", borderRadius: 8, fontWeight: 700, fontFamily: "inherit",
      cursor: disabled ? "not-allowed" : "pointer", opacity: disabled ? .4 : 1, transition: "all .15s",
      padding: sm ? "5px 12px" : "10px 20px", fontSize: sm ? 11 : 13, ...sx,
    }}>{children}</button>
  );
}

const Inp = ({ value, onChange, placeholder, multi, rows, style: s }) => {
  const r = rows || 3;
  const base = { width: "100%", boxSizing: "border-box", background: "rgba(255,255,255,.04)", border: "1px solid " + T.border, borderRadius: 9, padding: "10px 13px", color: T.text, fontSize: 13, outline: "none", fontFamily: "inherit", resize: "vertical", ...s };
  return multi
    ? <textarea rows={r} value={value} onChange={function(e) { onChange(e.target.value); }} placeholder={placeholder} style={base} />
    : <input value={value} onChange={function(e) { onChange(e.target.value); }} placeholder={placeholder} style={base} />;
};

const Spin = ({ color }) => (
  <div style={{ width: 14, height: 14, border: "2px solid " + (color || T.gold), borderTopColor: "transparent", borderRadius: "50%", animation: "spin .7s linear infinite", display: "inline-block" }} />
);

// ── A/B Comparison Panel ─────────────────────────────────────────
const LLM_META = {
  claude: { label: "Claude (Anthropic)", color: "#E9B96E", short: "CLAUDE" },
  gpt4o:  { label: "GPT-4o (OpenAI)",    color: "#74AA9C", short: "GPT-4o" },
};

function ABCompare({ contentType, results, prefs, onPick, onRerun, copied, onCopy }) {
  const p = prefs[contentType] || {};
  const total = Object.values(p).reduce(function(a, b) { return a + b; }, 0);
  const sides = [
    { key: "claude", text: results.claude },
    { key: "gpt4o",  text: results.gpt4o  },
  ];
  return (
    <div>
      {/* Stats bar */}
      {total > 0 && (
        <div style={{ display: "flex", gap: 6, alignItems: "center", marginBottom: 14, flexWrap: "wrap" }}>
          <span style={{ fontSize: 11, color: T.muted, fontFamily: "monospace" }}>YOUR HISTORY ({total} comparisons):</span>
          {sides.map(function(s) {
            const wins = p[s.key] || 0;
            const pct = total ? Math.round(wins / total * 100) : 0;
            const m = LLM_META[s.key];
            return (
              <span key={s.key} style={{ background: m.color + "18", color: m.color, border: "1px solid " + m.color + "44", borderRadius: 20, padding: "2px 10px", fontSize: 11, fontWeight: 700, fontFamily: "monospace" }}>
                {m.short}: {wins} wins ({pct}%)
              </span>
            );
          })}
          {total >= 3 && (
            <span style={{ fontSize: 11, color: T.green }}>
              {(p.claude || 0) > (p.gpt4o || 0) ? "Claude is your preferred writer" : (p.gpt4o || 0) > (p.claude || 0) ? "GPT-4o is your preferred writer" : "Tied so far"}
            </span>
          )}
        </div>
      )}

      {/* Side-by-side panels */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        {sides.map(function(s) {
          const m = LLM_META[s.key];
          const isError = s.text && s.text.startsWith(s.key + " error:");
          return (
            <div key={s.key} style={{ background: T.s1, border: "1px solid " + m.color + "44", borderRadius: 14, padding: 16, display: "flex", flexDirection: "column" }}>
              {/* LLM badge */}
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
                <span style={{ background: m.color + "18", color: m.color, border: "1px solid " + m.color + "44", borderRadius: 6, fontSize: 10, fontWeight: 800, padding: "3px 8px", fontFamily: "monospace" }}>{m.short}</span>
                <span style={{ fontSize: 11, color: T.muted }}>{m.label}</span>
              </div>

              {/* Content preview */}
              <div style={{ flex: 1, background: "rgba(0,0,0,.25)", borderRadius: 8, padding: "10px 12px", fontSize: 12.5, lineHeight: 1.75, color: isError ? T.red : "#ccc", whiteSpace: "pre-wrap", maxHeight: 320, overflowY: "auto", marginBottom: 12, fontFamily: contentType === "article" ? "Georgia,serif" : "inherit" }}>
                {s.text || "No output"}
              </div>

              {/* Actions */}
              {!isError && (
                <div style={{ display: "flex", gap: 6 }}>
                  <button onClick={function() { onPick(s.key, s.text); }}
                    style={{ flex: 1, background: "linear-gradient(135deg," + m.color + ",#888)", color: "#060810", border: "none", borderRadius: 8, padding: "9px 0", fontWeight: 800, fontSize: 12, cursor: "pointer" }}>
                    Use This Version
                  </button>
                  <button onClick={function() { onCopy(s.text, s.key + "_ab"); }}
                    style={{ background: "rgba(255,255,255,.06)", color: T.muted, border: "1px solid " + T.border, borderRadius: 8, padding: "9px 12px", fontSize: 11, cursor: "pointer" }}>
                    {copied === s.key + "_ab" ? "Copied" : "Copy"}
                  </button>
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div style={{ textAlign: "center", marginTop: 14 }}>
        <Btn sm v="ghost" onClick={onRerun}>Run Again (both)</Btn>
      </div>
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════════
// PERPLEXITY — used for research (live web search, no rate limit issues)
// ══════════════════════════════════════════════════════════════════════
async function callPerplexity(systemPrompt, userMsg, _retries) {
  const retries = _retries === undefined ? 3 : _retries;
  const body = {
    model: "sonar-pro",
    messages: [
      { role: "system", content: systemPrompt },
      { role: "user",   content: userMsg },
    ],
    max_tokens: 4000,
    temperature: 0.1,
    search_recency_filter: "year",
    return_citations: true,
  };

  const r = await fetch("/api/v1/perplexity-proxy", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRFToken": CSRF },
    body: JSON.stringify(body),
  });
  const data = await safeJson(r);

  if (data.error) {
    if ((data.error.type === "rate_limit_error" || (typeof data.error === "string" && data.error.includes("rate"))) && retries > 0) {
      await new Promise(function(res) { setTimeout(res, 15000); });
      return callPerplexity(systemPrompt, userMsg, retries - 1);
    }
    throw new Error(typeof data.error === "string" ? data.error : (data.error.message || JSON.stringify(data.error)));
  }

  // OpenAI-compatible response: choices[0].message.content
  const text = data.choices && data.choices[0] && data.choices[0].message
    ? data.choices[0].message.content
    : "";

  // Extract citations if present
  const citations = data.citations || [];
  return { text, citations };
}

// ══════════════════════════════════════════════════════════════════════
// OPENAI — GPT-4o, OpenAI-compatible format
// ══════════════════════════════════════════════════════════════════════
async function callOpenAI(system, userMsg, maxTokens) {
  const mt = maxTokens || 2000;
  const body = {
    model: "gpt-4o",
    max_tokens: mt,
    messages: [
      { role: "system", content: system },
      { role: "user",   content: userMsg },
    ],
    temperature: 0.7,
  };
  const r = await fetch("/api/v1/openai-proxy", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRFToken": CSRF },
    body: JSON.stringify(body),
  });
  const data = await safeJson(r);
  if (data.error) throw new Error(typeof data.error === "object" ? (data.error.message || JSON.stringify(data.error)) : data.error);
  return data.choices && data.choices[0] ? data.choices[0].message.content : "";
}

// ══════════════════════════════════════════════════════════════════════
// CLAUDE — used for writing (outline, article, social posts)
// ══════════════════════════════════════════════════════════════════════
async function callClaude(system, userMsg, tools, maxTokens, _retries) {
  const mt = maxTokens || (tools ? 4000 : 1500);
  const retries = _retries === undefined ? 3 : _retries;
  const body = {
    model: "claude-sonnet-4-20250514",
    max_tokens: mt,
    system: system,
    messages: [{ role: "user", content: userMsg }],
  };
  if (tools) body.tools = tools;

  const r = await fetch("/api/v1/claude-proxy", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRFToken": CSRF },
    body: JSON.stringify(body),
  });
  const data = await safeJson(r);

  // Rate-limit: wait and retry automatically
  if (data.error && data.error.type === "rate_limit_error" && retries > 0) {
    const wait = (4 - retries) * 20000; // 20s, 40s, 60s
    await new Promise(function(res) { setTimeout(res, wait); });
    return callClaude(system, userMsg, tools, maxTokens, retries - 1);
  }

  if (data.error) throw new Error(data.error.message || JSON.stringify(data.error));
  if (data._llm_provider === "ollama") {
    console.info("[Campaign Studio] Using local Ollama (cloud API unavailable).");
  }
  return data;
}

// ══════════════════════════════════════════════════════════════════════
// MAIN APP
// ══════════════════════════════════════════════════════════════════════
function App() {
  var draftInit = useMemo(function() {
    try { return stor("get", "ria_draft_v1") || {}; } catch (e) { return {}; }
  }, []);
  var hasDraft = draftInit.artTopic || draftInit.artAngle || (draftInit.dataPoints && draftInit.dataPoints.length) || draftInit.article;

  const [nav, setNav] = useState(hasDraft ? "flow" : "campaigns");
  const [profile, setProfile] = useState(function() { try { return stor("get", "ria_profile_v3") || {}; } catch (e) { return {}; } });
  const [campaigns, setCampaigns] = useState([]);
  const [profileSaved, setProfileSaved] = useState(false);
  const [copied, setCopied] = useState("");

  const [flowPath, setFlowPath] = useState(draftInit.flowPath || "research");
  const [pastePhase, setPastePhase] = useState(draftInit.pastePhase || 1);
  const [artPhase, setArtPhase] = useState(draftInit.artPhase || 1);
  const [artTopic, setArtTopic] = useState(draftInit.artTopic || "");
  const [artAngle, setArtAngle] = useState(draftInit.artAngle || "");
  const [artType, setArtType] = useState(draftInit.artType || "deepdive");
  const [artTone, setArtTone] = useState(draftInit.artTone || TONES[0]);
  const [artLength, setArtLength] = useState(draftInit.artLength || LENGTHS[1]);
  const [resLog, setResLog] = useState([]);
  const [dataPoints, setDataPoints] = useState(Array.isArray(draftInit.dataPoints) ? draftInit.dataPoints : []);
  const [researching, setResearching] = useState(false);
  const [extraQ, setExtraQ] = useState("");
  const [hypothesis, setHypothesis] = useState(draftInit.hypothesis || "");
  const [outlinePref, setOutlinePref] = useState(draftInit.outlinePref || "");
  const [outline, setOutline] = useState(Array.isArray(draftInit.outline) ? draftInit.outline : []);
  const [outlineLoad, setOutlineLoad] = useState(false);
  const [outlineError, setOutlineError] = useState("");
  const [article, setArticle] = useState(draftInit.article || "");
  const [artLoad, setArtLoad] = useState(false);
  const [artError, setArtError] = useState("");

  const [activeCampaign, setActiveCampaign] = useState(null);
  const [genPosts, setGenPosts] = useState({});
  const [articleUrl, setArticleUrl] = useState("");
  const [designLoad, setDesignLoad] = useState(false);
  const [imageLoad, setImageLoad] = useState(false);
  const [designError, setDesignError] = useState("");

  // ── A/B Test state ───────────────────────────────────────────────
  const [abMode, setAbMode] = useState(function() { return stor("get", "ria_ab_mode") || false; });
  const [abResults, setAbResults] = useState({}); // {contentType: {llmA: text, llmB: text, loading: bool}}
  const [abPrefs, setAbPrefs] = useState(function() { return stor("get", "ria_ab_prefs_v1") || {}; });
  // abPrefs shape: { article: { claude: 3, gpt4o: 5 }, linkedin: { claude: 2, gpt4o: 1 }, ... }
  const [writingLLM, setWritingLLM] = useState(function() { return stor("get", "ria_writing_llm") || "claude"; });

  const [icTagLoading, setIcTagLoading] = useState(false);
  const [icTagError, setIcTagError] = useState("");
  const [icTagReasons, setIcTagReasons] = useState({});
  const [icConfirmedTags, setIcConfirmedTags] = useState(null);
  const [icTagsReady, setIcTagsReady] = useState(false);
  const [icSaveLoading, setIcSaveLoading] = useState(false);
  const [icMatches, setIcMatches] = useState(null);
  const [icMatchLoading, setIcMatchLoading] = useState(false);
  const [icMatchError, setIcMatchError] = useState("");
  const [icSendLog, setIcSendLog] = useState([]);
  const [icSendBusy, setIcSendBusy] = useState(null);
  const [icEngageEdit, setIcEngageEdit] = useState(null);

  const logRef = useRef(null);
  const draftRef = useRef(null);

  // Keep draft ref in sync so we can save on page unload (refresh/close)
  useEffect(function() {
    var hasContent = artTopic || artAngle || hypothesis || outlinePref || (dataPoints && dataPoints.length > 0) || (outline && outline.length > 0) || article;
    if (!hasContent) return;
    var payload = { artTopic, artAngle, artType, artTone, artLength, hypothesis, outlinePref, dataPoints: dataPoints || [], outline: outline || [], article: article || "", artPhase, flowPath, pastePhase };
    draftRef.current = payload;
    try { localStorage.setItem("ria_draft_v1", JSON.stringify(payload)); } catch (e) {}
  }, [artTopic, artAngle, artType, artTone, artLength, hypothesis, outlinePref, dataPoints, outline, article, artPhase, flowPath, pastePhase]);

  useEffect(function() {
    function onBeforeUnload() {
      if (draftRef.current) try { localStorage.setItem("ria_draft_v1", JSON.stringify(draftRef.current)); } catch (e) {}
    }
    window.addEventListener("beforeunload", onBeforeUnload);
    return function() { window.removeEventListener("beforeunload", onBeforeUnload); };
  }, []);

  // Load campaigns from API (database), fallback to localStorage
  useEffect(function() {
    fetch("/api/v1/campaign-studio/campaigns", { headers: { "X-CSRFToken": CSRF } })
      .then(function(r) { return safeJson(r).then(function(data) { return r.ok ? data : Promise.reject(data); }); })
      .then(function(data) {
        if (Array.isArray(data)) {
          var list = data.map(function(c) { return { ...c, id: c.serverId != null ? c.serverId : c.id, serverId: c.serverId != null ? c.serverId : c.id }; });
          setCampaigns(list);
          stor("set", "ria_campaigns_v1", list);
        }
      })
      .catch(function() {
        var c = stor("get", "ria_campaigns_v1");
        if (c) setCampaigns(c);
      });
  }, []);

  useEffect(function() {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [resLog]);

  useEffect(function() {
    if (!activeCampaign) return;
    hydrateIcFromCampaign(activeCampaign);
    setIcMatches(null);
    setIcMatchError("");
    var ci = activeCampaign.contentIntelligence;
    if (ci && ci.send_log) setIcSendLog(ci.send_log);
    else if (activeCampaign.serverId != null || typeof activeCampaign.id === "number") {
      fetchSendLog(activeCampaign.serverId || activeCampaign.id);
    } else {
      setIcSendLog([]);
    }
  }, [activeCampaign ? activeCampaign.id : null]);

  function saveProfile(p) { setProfile(p); stor("set", "ria_profile_v3", p); setProfileSaved(true); setTimeout(function() { setProfileSaved(false); }, 2000); }
  function saveCampaigns(c) { setCampaigns(c); stor("set", "ria_campaigns_v1", c); }

  function updateCampaignOnServer(campaignId, patch, onDone) {
    var sid = typeof campaignId === "number" ? campaignId : (campaignId && campaignId.serverId);
    if (sid == null) { if (onDone) onDone(); return; }
    fetch("/api/v1/campaign-studio/campaigns/" + sid, {
      method: "PUT",
      headers: { "Content-Type": "application/json", "X-CSRFToken": CSRF },
      body: JSON.stringify(patch),
    })
      .then(function(r) { return safeJson(r); })
      .then(function(data) {
        setCampaigns(function(prev) {
          return prev.map(function(c) { return c.id === sid || c.serverId === sid ? { ...c, ...data, id: data.serverId != null ? data.serverId : data.id, serverId: data.serverId != null ? data.serverId : data.id } : c; });
        });
        setActiveCampaign(function(p) { return p && (p.id === sid || p.serverId === sid) ? { ...p, ...data, id: data.serverId != null ? data.serverId : data.id, serverId: data.serverId != null ? data.serverId : data.id } : p; });
        if (onDone) onDone();
      })
      .catch(function() { if (onDone) onDone(); });
  }
  function cp(text, key) { if (navigator.clipboard) navigator.clipboard.writeText(text); setCopied(key); setTimeout(function() { setCopied(""); }, 2000); }

  function topicCategoryFromArtType(typeId) {
    var tid = typeId || artType;
    var at = ARTICLE_TYPES.find(function(a) { return a.id === tid; });
    return at ? at.label : "";
  }

  function hydrateIcFromCampaign(c) {
    var ci = c && c.contentIntelligence;
    if (!ci || !ci.confirmed_tags) return;
    setIcConfirmedTags(ci.confirmed_tags);
    setIcTagsReady(true);
    setIcTagReasons({});
    if (ci.send_log) setIcSendLog(ci.send_log);
  }

  async function fetchSendLog(campaignId) {
    try {
      var r = await fetch("/campaign-studio/send-log/" + campaignId, { headers: { "X-CSRFToken": CSRF } });
      var data = await safeJson(r);
      if (r.ok && Array.isArray(data)) setIcSendLog(data);
    } catch (e) { /* optional */ }
  }

  async function logSendDecision(personId, personName, decision, channel) {
    var cid = activeCampaign && (activeCampaign.serverId || activeCampaign.id);
    if (!cid) return;
    setIcSendBusy(personId + ":" + decision);
    try {
      var r = await fetch("/campaign-studio/log-decision", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": CSRF },
        body: JSON.stringify({
          content_id: cid,
          person_id: personId,
          decision: decision,
          channel: channel || "WhatsApp",
        }),
      });
      var data = await safeJson(r);
      if (!r.ok) throw new Error(data.error || "Failed to log");
      await fetchSendLog(cid);
      setActiveCampaign(function(p) {
        if (!p) return p;
        var ci = { ...(p.contentIntelligence || {}), send_log: icSendLog };
        return { ...p, contentIntelligence: ci };
      });
    } catch (e) {
      setIcMatchError(e.message || String(e));
    }
    setIcSendBusy(null);
  }

  async function saveEngagement(logId, engagement, followupNeeded, notes) {
    var cid = activeCampaign && (activeCampaign.serverId || activeCampaign.id);
    if (!cid) return;
    setIcSendBusy("log:" + logId);
    try {
      var r = await fetch("/campaign-studio/update-engagement/" + logId, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": CSRF },
        body: JSON.stringify({
          campaign_id: cid,
          engagement: engagement,
          followup_needed: !!followupNeeded,
          notes: notes || "",
        }),
      });
      var data = await safeJson(r);
      if (!r.ok) throw new Error(data.error || "Failed to update");
      await fetchSendLog(cid);
      setIcEngageEdit(null);
    } catch (e) {
      setIcMatchError(e.message || String(e));
    }
    setIcSendBusy(null);
  }

  function toggleIcTag(tagId) {
    setIcConfirmedTags(function(prev) {
      var base = prev ? { ...prev } : {};
      base[tagId] = !base[tagId];
      return base;
    });
  }

  async function generateContentTags() {
    var content = (article || "").trim();
    if (!content && activeCampaign && activeCampaign.article) content = String(activeCampaign.article).trim();
    if (!content) {
      setIcTagError("Write or paste article text before generating tags.");
      return;
    }
    setIcTagLoading(true);
    setIcTagError("");
    try {
      var r = await fetch("/campaign-studio/generate-tags", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": CSRF },
        body: JSON.stringify({ content: content }),
      });
      var data = await safeJson(r);
      if (!r.ok) throw new Error(data.error || "Could not generate tags");
      setIcConfirmedTags(data.tags || {});
      setIcTagReasons(data.reasons || {});
      setIcTagsReady(true);
      if (data._llm_provider === "ollama") {
        console.info("[Campaign Studio] Tags from local Ollama — review before saving.");
      }
    } catch (e) {
      setIcTagError(e.message || String(e));
    }
    setIcTagLoading(false);
  }

  async function saveContentIntelligence(serverId, meta) {
    var r = await fetch("/campaign-studio/save-content", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": CSRF },
      body: JSON.stringify({
        campaign_studio_campaign_id: serverId,
        title: (meta && meta.title) || artTopic,
        format: "article",
        topic_category: (meta && meta.topic_category) || topicCategoryFromArtType(meta && meta.type),
        content_text: (meta && meta.article) || article,
        confirmed_tags: icConfirmedTags || {},
      }),
    });
    var data = await safeJson(r);
    if (!r.ok) throw new Error(data.error || "Could not save content intelligence");
    return data;
  }

  async function fetchMatchPersons(campaignId) {
    setIcMatchLoading(true);
    setIcMatchError("");
    setIcMatches(null);
    try {
      var r = await fetch("/campaign-studio/match-persons/" + campaignId, {
        headers: { "X-CSRFToken": CSRF },
      });
      var data = await safeJson(r);
      if (!r.ok) throw new Error(data.error || "Could not load matches");
      setIcMatches(Array.isArray(data) ? data : []);
    } catch (e) {
      setIcMatchError(e.message || String(e));
    }
    setIcMatchLoading(false);
  }

  async function saveTagsToExistingCampaign() {
    var c = activeCampaign;
    var sid = c && (c.serverId != null ? c.serverId : c.id);
    if (!sid || !icTagsReady || !icConfirmedTags) {
      setIcTagError("Generate and confirm audience tags first.");
      return;
    }
    setIcSaveLoading(true);
    try {
      await saveContentIntelligence(sid, {
        title: c.title,
        type: c.type,
        article: c.article,
        topic_category: topicCategoryFromArtType(c.type),
      });
      var patch = {
        contentIntelligence: {
          confirmed_tags: icConfirmedTags,
          topic_category: topicCategoryFromArtType(c.type),
          send_log: icSendLog || [],
        },
      };
      var updated = campaigns.map(function(row) {
        return row.id === c.id || row.serverId === sid ? { ...row, ...patch } : row;
      });
      saveCampaigns(updated);
      setActiveCampaign(function(p) { return p ? { ...p, ...patch } : p; });
      setIcTagError("");
    } catch (e) {
      setIcTagError(e.message || String(e));
    }
    setIcSaveLoading(false);
  }

  function renderAudienceTagsPanel(opts) {
    var inFlow = opts && opts.inFlow;
    var onDetail = opts && opts.onDetail;
    var tags = icConfirmedTags;
    var showGenerate = !icTagsReady || inFlow || onDetail;
    return (
      <Card sx={{ marginTop: inFlow ? 0 : 0, marginBottom: inFlow ? 0 : 20, background: "rgba(108,142,245,.08)", borderColor: T.blue, glow: T.blue }}>
        <SL c={T.blue}>STEP: AUDIENCE TAGS (CONTENT INTELLIGENCE)</SL>
        <div style={{ fontSize: 13, color: T.text, marginBottom: 12, lineHeight: 1.5 }}>
          <strong style={{ color: T.blue }}>Required before client matching.</strong> AI reads your article only (no client PII). Generate tags, toggle as needed, then save.
        </div>
        {icTagError && (
          <div style={{ background: "rgba(252,96,118,.1)", border: "1px solid rgba(252,96,118,.3)", borderRadius: 8, padding: "8px 12px", marginBottom: 12, fontSize: 12, color: T.red }}>{icTagError}</div>
        )}
        {showGenerate && (
          <Btn sm v="blue" onClick={generateContentTags} disabled={icTagLoading || !((article || "").trim() || (activeCampaign && activeCampaign.article))} sx={{ marginBottom: 12 }}>
            {icTagLoading ? <span style={{ display: "flex", alignItems: "center", gap: 6 }}><Spin color="#fff" />Analysing article...</span> : (icTagsReady ? "Regenerate tags" : "Generate audience tags")}
          </Btn>
        )}
        {onDetail && icTagsReady && activeCampaign && (activeCampaign.serverId != null || typeof activeCampaign.id === "number") && (
          <Btn v="green" onClick={saveTagsToExistingCampaign} disabled={icSaveLoading} sx={{ marginBottom: 12 }}>
            {icSaveLoading ? "Saving tags…" : "Save tags to this campaign"}
          </Btn>
        )}
        {icTagsReady && tags && (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {ADVISER_TAGS.map(function(t) {
              var on = !!tags[t.id];
              var reason = icTagReasons[t.id] || "";
              return (
                <div key={t.id} onClick={function() { toggleIcTag(t.id); }}
                  style={{ background: on ? "rgba(108,142,245,.12)" : "rgba(0,0,0,.2)", border: "1px solid " + (on ? T.blue + "66" : T.border), borderRadius: 10, padding: "10px 12px", cursor: "pointer" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: reason ? 6 : 0 }}>
                    <div style={{ width: 16, height: 16, borderRadius: 3, background: on ? T.blue : "transparent", border: "2px solid " + (on ? T.blue : T.muted), display: "flex", alignItems: "center", justifyContent: "center", fontSize: 10, color: "#fff", flexShrink: 0 }}>{on ? "v" : ""}</div>
                    <span style={{ fontSize: 13, fontWeight: 700, color: on ? T.blue : T.text }}>{t.label}</span>
                    {on && <span style={{ fontSize: 9, color: T.green, fontFamily: "monospace", marginLeft: "auto" }}>ON</span>}
                  </div>
                  {reason ? <div style={{ fontSize: 11, color: T.muted, lineHeight: 1.5, paddingLeft: 24 }}>{reason}</div> : null}
                </div>
              );
            })}
          </div>
        )}
      </Card>
    );
  }

  const selectedDP = dataPoints.filter(function(d) { return d.selected; });
  function toggleDP(id) { setDataPoints(function(p) { return p.map(function(d) { return d.id === id ? { ...d, selected: !d.selected } : d; }); }); }
  function noteDP(id, note) { setDataPoints(function(p) { return p.map(function(d) { return d.id === id ? { ...d, note: note } : d; }); }); }

  // ── Research (Perplexity — live web search) ───────────────────────
  function parseDataPoints(raw, prefix) {
    if (!raw || typeof raw !== "string") return [];
    var s = raw.trim();
    s = s.replace(/^```(?:json)?\s*/i, "").replace(/\s*```\s*$/, "").trim();
    var tried = [];
    var greedy = s.match(/\[[\s\S]*\]/);
    if (greedy) tried.push(greedy[0]);
    var nonGreedy = s.match(/\[[\s\S]*?\]/g);
    if (nonGreedy) {
      nonGreedy.forEach(function(m) { if (m.length > 20 && tried.indexOf(m) === -1) tried.push(m); });
    }
    for (var i = 0; i < tried.length; i++) {
      try {
        var parsed = JSON.parse(tried[i]);
        if (!Array.isArray(parsed) || parsed.length === 0) continue;
        return parsed.map(function(dp, i) {
          var stat = dp.stat || dp.label || dp.title || "";
          var value = dp.value || dp.figure || dp.data || "";
          return { ...dp, id: (prefix || "dp") + i + "_" + Date.now(), selected: false, note: "", stat: stat, value: value, text: dp.text || (stat + (value ? ": " + value : "")) };
        });
      } catch (e) { continue; }
    }
    return [];
  }

  const RESEARCH_SYSTEM = "You are a quantitative research analyst for a SEBI Registered Investment Advisor.\n\nYour job: find HARD DATA — actual numbers, percentages, rupee values, growth rates, survey figures — NOT article summaries.\n\nReturn ONLY a valid JSON array (no markdown fences, no prose before or after):\n[\n  {\n    \"id\": \"dp1\",\n    \"stat\": \"short label (e.g. SIP monthly inflow)\",\n    \"value\": \"the actual figure (e.g. Rs 26,459 crore)\",\n    \"context\": \"one sentence: what this means and why it matters for Indian investors\",\n    \"source\": \"exact source (e.g. AMFI, SEBI Annual Report 2024, RBI MPC)\",\n    \"sourceUrl\": \"direct URL to original data if available\",\n    \"year\": \"2024\",\n    \"tags\": [\"sip\", \"retail\"],\n    \"quality\": \"high\"\n  }\n]\n\nQuality:\n- high = SEBI / RBI / AMFI / NSE / BSE / MoF / MOSPI / government press release\n- medium = reputable media (ET, Business Standard, Mint) citing primary data with number\n- low = estimate, opinion, secondary reference\n\nFind 12-16 data points from 2022-2025. Prioritise primary regulator data.";

  function isPasteFlow() {
    return flowPath === "paste";
  }

  function flowSteps() {
    return isPasteFlow() ? PASTE_FLOW_PHASES : PHASES;
  }

  function flowStepIndex() {
    if (isPasteFlow()) {
      if (artPhase >= 6) return 3;
      return pastePhase;
    }
    return artPhase;
  }

  function startPasteFlow() {
    setFlowPath("paste");
    setPastePhase(1);
    setArtPhase(1);
    setResLog([]);
    setDataPoints([]);
    setOutline([]);
    setAbResults({});
    setIcTagError("");
  }

  function openPasteContentStep() {
    if (!artTopic.trim()) {
      setIcTagError("Add a campaign title or topic label first.");
      return;
    }
    setIcTagError("");
    setPastePhase(2);
  }

  function goToAudienceFromPaste() {
    var content = (article || "").trim();
    if (!content) {
      setIcTagError("Paste your article, email, or message content first.");
      return;
    }
    if (content.length < 80) {
      setIcTagError("Content looks too short — paste at least a few sentences for meaningful tag suggestions.");
      return;
    }
    setIcTagError("");
    setIcConfirmedTags(null);
    setIcTagsReady(false);
    setIcTagReasons({});
    setArtPhase(6);
  }

  async function startResearch() {
    if (!artTopic.trim()) return;
    setFlowPath("research");
    setResearching(true); setArtPhase(2);
    setResLog(["Planning research queries for: " + artTopic + "..."]);
    setDataPoints([]);
    const at = ARTICLE_TYPES.find(function(a) { return a.id === artType; });

    // Try Perplexity first, fall back to Claude web search
    var usedEngine = "Perplexity";
    try {
      setResLog(function(p) { return [...p, "[Perplexity] Searching live web for Indian financial data..."]; });
      const result = await callPerplexity(
        RESEARCH_SYSTEM,
        "Topic: \"" + artTopic + "\" (" + at.label + ")\nAngle: " + (artAngle || "general overview") + "\n\nSearch for: specific statistics, rupee values, percentages, growth rates related to this topic in India. Prioritise SEBI, RBI, AMFI, NSE, MoF data from 2022-2025."
      );
      var dps = parseDataPoints(result.text, "dp");
      if (dps.length === 0 && result.text && result.text.length > 150) {
        setResLog(function(p) { return [...p, "[Perplexity] Retrying with simpler prompt..."]; });
        var result2 = await callPerplexity(
          "Return ONLY a JSON array. Each item: {\"stat\":\"label\",\"value\":\"figure\",\"source\":\"source name\",\"year\":\"2024\"}. No other text.",
          "India " + artTopic + " statistics 2022-2025. JSON array of 6-10 data points only."
        );
        dps = parseDataPoints(result2.text, "dp");
        if (dps.length) { result.citations = result2.citations; result.text = result2.text; }
      }
      if (dps.length) {
        var cited = dps.map(function(dp, i) {
          if (!dp.sourceUrl && result.citations && result.citations[i]) dp.sourceUrl = result.citations[i];
          return dp;
        });
        setDataPoints(cited);
        setResLog(function(p) { return [...p, "[Perplexity] Found " + cited.length + " data points. Select what matters for your story."]; });
        setResearching(false);
        return;
      }
      setResLog(function(p) { return [...p, "[Perplexity] No structured data — falling back to Claude web search..."]; });
    } catch(e) {
      setResLog(function(p) { return [...p, "[Perplexity] Unavailable (" + e.message.slice(0, 60) + ") — switching to Claude web search..."]; });
    }

    // Fallback: Claude with web search
    usedEngine = "Claude";
    try {
      setResLog(function(p) { return [...p, "[Claude] Searching web for: " + artTopic + "..."]; });
      const data = await callClaude(
        RESEARCH_SYSTEM,
        "Topic: \"" + artTopic + "\" (" + at.label + ")\nAngle: " + (artAngle || "general overview") + "\n\nSearch for specific statistics, rupee values, percentages, growth rates related to this topic in India. Prioritise SEBI, RBI, AMFI, NSE, MoF data from 2022-2025.\n\nReturn ONLY the JSON array.",
        [{ type: "web_search_20250305", name: "web_search" }]
      );
      let raw = "";
      for (const b of (data.content || [])) {
        if (b.type === "text") raw += b.text;
        if (b.type === "tool_use" || b.type === "server_tool_use") setResLog(function(p) { return [...p, "[Claude] Searching: " + (b.input && b.input.query ? b.input.query : "...")]; });
      }
      var dps = parseDataPoints(raw, "dp");
      if (dps.length === 0 && raw.length > 100) {
        setResLog(function(p) { return [...p, "[Claude] Retrying with a simpler format..."]; });
        var data2 = await callClaude(
          "You are a data researcher. Return ONLY a JSON array of objects. Each object has: stat (short label), value (number or figure), source (e.g. AMFI, RBI), year. No markdown, no explanation.",
          "Topic: " + artTopic + ". India, 2022-2025. Give 6-10 data points as a JSON array only.",
          null,
          2000
        );
        var raw2 = (data2.content || []).filter(function(b) { return b.type === "text"; }).map(function(b) { return b.text; }).join("");
        dps = parseDataPoints(raw2, "dp");
      }
      if (dps.length) {
        setDataPoints(dps);
        setResLog(function(p) { return [...p, "[Claude] Found " + dps.length + " data points. Select what matters for your story."]; });
      } else {
        setResLog(function(p) { return [...p, "No structured data returned. Try a more specific topic (e.g. \"SIP inflows India 2024\") or use \"Add search\" below with a different query."]; });
      }
    } catch(e) { setResLog(function(p) { return [...p, "Error: " + e.message]; }); }
    setResearching(false);
  }

  async function addSearch() {
    if (!extraQ.trim()) return;
    setResearching(true);
    setResLog(function(p) { return [...p, "[Perplexity] Searching: " + extraQ + "..."]; });
    var added = false;
    try {
      const result = await callPerplexity(RESEARCH_SYSTEM, extraQ + "\n\nReturn ONLY the JSON array of data points.");
      const newDPs = parseDataPoints(result.text, "ex");
      if (newDPs.length) {
        const cited = newDPs.map(function(dp, i) { if (!dp.sourceUrl && result.citations && result.citations[i]) dp.sourceUrl = result.citations[i]; return dp; });
        setDataPoints(function(p) { return [...p, ...cited]; });
        setResLog(function(p) { return [...p, "[Perplexity] Added " + cited.length + " more data points"]; });
        added = true;
      }
    } catch(e) {
      setResLog(function(p) { return [...p, "[Perplexity] Unavailable — trying Claude..."]; });
    }
    if (!added) {
      try {
        const data = await callClaude(RESEARCH_SYSTEM, extraQ + "\n\nReturn ONLY the JSON array.", [{ type: "web_search_20250305", name: "web_search" }]);
        const raw = (data.content || []).filter(function(b) { return b.type === "text"; }).map(function(b) { return b.text; }).join("");
        const newDPs = parseDataPoints(raw, "ex");
        if (newDPs.length) {
          setDataPoints(function(p) { return [...p, ...newDPs]; });
          setResLog(function(p) { return [...p, "[Claude] Added " + newDPs.length + " more data points"]; });
        }
      } catch(e) { setResLog(function(p) { return [...p, "Error: " + e.message]; }); }
    }
    setExtraQ(""); setResearching(false);
  }

  // ── Outline ───────────────────────────────────────────────────────
  async function genOutline() {
    setOutlineLoad(true); setOutlineError(""); setArtPhase(4);
    const at = ARTICLE_TYPES.find(function(a) { return a.id === artType; });
    const ctx = selectedDP.map(function(d) {
      const val = d.value ? " = " + d.value : "";
      const note = d.note ? " [Angle: " + d.note + "]" : "";
      const label = d.stat || d.text || "";
      return "- " + label + val + " (" + (d.source || "") + ", " + (d.year || "") + ")" + note;
    }).join("\n");
    const systemPrompt = "You are an editorial planner for a SEBI RIA. Return ONLY a valid JSON array, no markdown fences, no explanation. Each element: {\"section\":\"Section Title\",\"purpose\":\"what it achieves\",\"keyPoints\":[\"point1\",\"point2\"]}. 5-7 sections for Indian retail investors.";
    const userPrompt = "Article: \"" + artTopic + "\"\nType: " + (at ? at.label : "article") + "\nHypothesis: " + (hypothesis || "let data decide") + "\nTone: " + artTone + "\nLength: " + artLength + "\n" + (outlinePref ? "Structure pref: " + outlinePref + "\n" : "") + "Data:\n" + ctx + "\nReturn only the JSON array.";

    function parseOutlineResponse(raw) {
      if (!raw || typeof raw !== "string") return null;
      var s = raw.trim().replace(/^```(?:json)?\s*/i, "").replace(/\s*```\s*$/, "").trim();
      var m = s.match(/\[[\s\S]*\]/);
      if (!m) return null;
      try {
        var arr = JSON.parse(m[0]);
        if (!Array.isArray(arr) || arr.length === 0) return null;
        return arr.map(function(item) {
          return {
            section: item.section || "Section",
            purpose: item.purpose || "",
            keyPoints: Array.isArray(item.keyPoints) ? item.keyPoints : [],
          };
        });
      } catch (e) { return null; }
    }

    function tryWithClaude() {
      return callClaude(systemPrompt, userPrompt, null, 2000).then(function(data) {
        return (data.content || []).filter(function(b) { return b.type === "text"; }).map(function(b) { return b.text; }).join("");
      });
    }
    function tryWithOpenAI() {
      return callOpenAI(systemPrompt, userPrompt, 2000);
    }

    var raw = "";
    var lastError = "";
    try {
      if (writingLLM === "gpt4o") {
        raw = await tryWithOpenAI();
      } else {
        try {
          raw = await tryWithClaude();
        } catch (e1) {
          lastError = e1 && e1.message ? e1.message : String(e1);
          raw = await tryWithOpenAI();
        }
      }
      if (typeof raw !== "string") raw = "";
      var parsed = parseOutlineResponse(raw);
      if (parsed && parsed.length > 0) {
        setOutline(parsed);
      } else {
        setOutlineError("Could not parse outline (no valid JSON array). Try again.");
      }
    } catch (e) {
      var msg = e && e.message ? e.message : String(e);
      if (writingLLM !== "gpt4o" && lastError) setOutlineError("First try failed: " + (lastError.slice(0, 60)) + "... Then GPT-4o also failed: " + msg.slice(0, 80));
      else setOutlineError(msg.indexOf("Session expired") !== -1 ? msg : (msg.indexOf("rate_limit") !== -1 || msg.indexOf("rate limit") !== -1 ? "Rate limit — wait 1 minute and retry." : "Error: " + msg));
    }
    setOutlineLoad(false);
  }

  // ── Write article ─────────────────────────────────────────────────
  function buildArticlePrompts() {
    const at = ARTICLE_TYPES.find(function(a) { return a.id === artType; });
    const ctx = selectedDP.map(function(d) {
      const val = d.value ? " = " + d.value : "";
      const note = d.note ? " [Advisor angle: " + d.note + "]" : "";
      const label = d.stat || d.text || "";
      const c2 = d.context ? " Context: " + d.context : "";
      return "- " + label + val + c2 + " -- Source: " + (d.source || "") + (d.year ? " (" + d.year + ")" : "") + note;
    }).join("\n");
    const outCtx = outline.map(function(s) { return "## " + s.section + "\nPurpose: " + s.purpose + "\nKey points: " + (s.keyPoints || []).join(", "); }).join("\n\n");
    const system = "You are a senior financial writer for " + (profile.firmName || "a SEBI Registered Investment Advisor") + (profile.sebiReg ? " (SEBI Reg: " + profile.sebiReg + ")" : "") + ".\nWrite for: " + (profile.audience || "Indian salaried professionals, 30-50, urban") + ".\nTone: " + (profile.tone || "professional yet warm") + ". Never guarantee returns. Cite data inline. Add disclaimer at end.";
    const user = "Write a " + (at ? at.label : "article") + ": \"" + artTopic + "\"\nHypothesis: " + (hypothesis || "let data tell the story") + "\nLength: " + artLength + "\nTone: " + artTone + "\n\nOUTLINE:\n" + outCtx + "\n\nDATA (cite inline):\n" + ctx + "\n\nWrite the complete publication-ready article in markdown. End with a Sources section.";
    return { system, user };
  }

  async function writeArticle() {
    setArtLoad(true); setArtError(""); setArtPhase(5);
    const { system, user } = buildArticlePrompts();

    if (abMode) {
      try {
        await generateAB("article", system, user, 2000);
      } catch(e) { setArtError("Error: " + e.message); }
      setArtLoad(false);
      return;
    }

    try {
      let text = "";
      if (writingLLM === "gpt4o") {
        text = await callOpenAI(system, user, 2000);
      } else {
        const data = await callClaude(system, user, null, 2000);
        text = (data.content || []).filter(function(b) { return b.type === "text"; }).map(function(b) { return b.text; }).join("");
      }
      setArticle(text || "");
      if (text) setArtPhase(6);
    } catch(e) {
      setArtError(e.message.includes("rate_limit") || e.message.includes("rate limit")
        ? "Rate limit hit — please wait 1 minute and click Rewrite."
        : "Error: " + e.message);
    }
    setArtLoad(false);
  }

  // ── Save campaign (to DB via API, then content intelligence tags) ───
  function saveCampaign() {
    if (!icTagsReady || !icConfirmedTags) {
      setIcTagError("Generate and review audience tags before saving the campaign.");
      return;
    }
    const auditLog = selectedDP.map(function(d) {
      return { stat: d.stat || d.text, value: d.value, source: d.source, sourceUrl: d.sourceUrl, year: d.year, quality: d.quality };
    });
    const payload = {
      title: artTopic, type: artType, article: article, articleUrl: "",
      posts: {}, dpCount: selectedDP.length, auditLog: auditLog, ts: Date.now(), status: "article_ready",
    };
    setIcSaveLoading(true);
    fetch("/api/v1/campaign-studio/campaigns", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": CSRF },
      body: JSON.stringify(payload),
    })
      .then(function(r) { return safeJson(r).then(function(data) { return r.ok ? data : Promise.reject(data); }); })
      .then(function(data) {
        var sid = data.serverId != null ? data.serverId : data.id;
        return saveContentIntelligence(sid, { title: artTopic, type: artType, article: article }).then(function() {
          data.contentIntelligence = { confirmed_tags: icConfirmedTags, topic_category: topicCategoryFromArtType() };
          return data;
        });
      })
      .then(function(data) {
        var c = { ...data, id: data.serverId != null ? data.serverId : data.id, serverId: data.serverId != null ? data.serverId : data.id };
        var updated = [c, ...campaigns].slice(0, 100);
        saveCampaigns(updated);
        resetFlow();
        setNav("campaigns");
        setActiveCampaign(c);
        setIcMatches(null);
        hydrateIcFromCampaign(c);
      })
      .catch(function(err) {
        var msg = err && err.error ? err.error : (err && err.message ? err.message : null);
        if (msg) setIcTagError(msg);
        var c = { id: Date.now(), serverId: null, title: artTopic, type: artType, article: article, articleUrl: "", posts: {}, dpCount: selectedDP.length, auditLog: auditLog, ts: Date.now(), status: "article_ready" };
        var updated = [c, ...campaigns].slice(0, 20);
        saveCampaigns(updated);
        resetFlow();
        setNav("campaigns");
        setActiveCampaign(c);
      })
      .finally(function() { setIcSaveLoading(false); });
  }

  // ── Generate social posts ─────────────────────────────────────────
  function buildPostPrompts(campaign, platId) {
    const plat = PLATFORMS[platId];
    const url = campaign.articleUrl || articleUrl || "[Article URL]";
    const system = "You are a social media writer for " + (profile.firmName || "a SEBI Registered Investment Advisor") + ".\nYou are creating a TEASER post for an existing long-form research article.\nPlatform rules: " + plat.postGuide + "\nStyle: " + plat.teaserStyle + "\nCRITICAL: End with a clear reference to the full article. Never guarantee returns. SEBI compliant.";
    const user = "ARTICLE TITLE: \"" + campaign.title + "\"\nARTICLE LINK: " + url + "\nARTICLE EXCERPT:\n" + (campaign.article || "").slice(0, 800) + "\n\nWrite the " + plat.label + " teaser post now.";
    return { system, user, url };
  }

  function savePostToCampaign(campaign, platId, postText, url) {
    const posts = { ...(campaign.posts || {}), [platId]: postText };
    const updated = campaigns.map(function(c) {
      if (c.id === campaign.id) return { ...c, posts: posts, status: "posts_ready", articleUrl: url };
      return c;
    });
    saveCampaigns(updated);
    setActiveCampaign(function(prev) {
      return prev && prev.id === campaign.id ? { ...prev, posts: posts, articleUrl: url } : prev;
    });
    updateCampaignOnServer(campaign.id, { posts: posts, articleUrl: url });
  }

  // ── Designer: LLM suggestions + DALL·E cover image ──────────────────
  var DESIGN_SYSTEM = "You are a creative director for a SEBI RIA's content. Return ONLY valid JSON (no markdown, no prose) with: heroImagePrompt (one detailed prompt for an AI image: professional, finance, India, no text in image, 1 sentence), headlines (array of 3 short punchy headline variants for the article), colorMood (2-3 words, e.g. trustworthy blue, warm gold), cta (one call-to-action line for the article).";
  function designUserMsg(title, excerpt) {
    return "Article title: \"" + (title || "") + "\"\nExcerpt: " + (excerpt || "").slice(0, 300) + "\n\nReturn JSON: { \"heroImagePrompt\": \"...\", \"headlines\": [\"...\", \"...\", \"...\"], \"colorMood\": \"...\", \"cta\": \"...\" }";
  }

  async function fetchDesignSuggestions(campaign) {
    setDesignLoad(true); setDesignError("");
    var title = campaign.title; var excerpt = (campaign.article || "").slice(0, 400);
    var system = DESIGN_SYSTEM; var user = designUserMsg(title, excerpt);
    try {
      var text = "";
      if (writingLLM === "gpt4o") {
        text = await callOpenAI(system, user, 600);
      } else {
        var data = await callClaude(system, user, null, 600);
        text = (data.content || []).filter(function(b) { return b.type === "text"; }).map(function(b) { return b.text; }).join("");
      }
      var json = text.replace(/[\s\S]*?(\{[\s\S]*\})[\s\S]*/, "$1");
      var suggestions = JSON.parse(json);
      setActiveCampaign(function(p) {
        if (!p || p.id !== campaign.id) return p;
        var next = { ...p, designSuggestions: suggestions };
        updateCampaignOnServer(p.id, { designSuggestions: suggestions });
        return next;
      });
      setCampaigns(function(prev) {
        return prev.map(function(c) { return c.id === campaign.id ? { ...c, designSuggestions: suggestions } : c; });
      });
    } catch (e) {
      setDesignError(e.message || "Could not generate design suggestions.");
    }
    setDesignLoad(false);
  }

  async function generateCoverImage(campaign, promptOverride) {
    setImageLoad(true); setDesignError("");
    var prompt = promptOverride || (campaign.designSuggestions && campaign.designSuggestions.heroImagePrompt) || ("Professional, trustworthy image for Indian finance article: " + (campaign.title || "").slice(0, 80) + ", no text in image, clean and modern.");
    try {
      var r = await fetch("/api/v1/openai-images-proxy", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": CSRF },
        body: JSON.stringify({ prompt: prompt, model: "dall-e-3", size: "1792x1024", quality: "standard", n: 1 }),
      });
      var data = await safeJson(r);
      if (data.error) throw new Error(data.error.message || data.error);
      var url = data.data && data.data[0] && data.data[0].url;
      if (!url) throw new Error("No image URL in response");
      setActiveCampaign(function(p) {
        if (!p || p.id !== campaign.id) return p;
        var next = { ...p, coverImageUrl: url };
        updateCampaignOnServer(p.id, { coverImageUrl: url });
        return next;
      });
      setCampaigns(function(prev) {
        return prev.map(function(c) { return c.id === campaign.id ? { ...c, coverImageUrl: url } : c; });
      });
    } catch (e) {
      setDesignError(e.message || "Image generation failed.");
    }
    setImageLoad(false);
  }

  async function generateLinkedPost(campaign, platId) {
    const plat = PLATFORMS[platId];
    setGenPosts(function(p) { return { ...p, [platId]: true }; });
    const { system, user, url } = buildPostPrompts(campaign, platId);
    const abKey = "post_" + platId;

    if (abMode) {
      try { await generateAB(abKey, system, user, 600); } catch(e) { console.error(e); }
      setGenPosts(function(p) { return { ...p, [platId]: false }; });
      return;
    }

    try {
      let postText = "";
      if (writingLLM === "gpt4o") {
        postText = await callOpenAI(system, user, 600);
      } else {
        const data = await callClaude(system, user);
        const t = (data.content || []).find(function(b) { return b.type === "text"; });
        postText = t ? t.text : "";
      }
      savePostToCampaign(campaign, platId, postText, url);
    } catch(e) { console.error(e); }
    setGenPosts(function(p) { return { ...p, [platId]: false }; });
  }

  async function generateAllPosts(campaign) {
    await generateLinkedPost(campaign, "linkedin");
    await generateLinkedPost(campaign, "whatsapp");
    await generateLinkedPost(campaign, "twitter");
  }

  function resetFlow() {
    setFlowPath("research");
    setPastePhase(1);
    setArtPhase(1); setArtTopic(""); setArtAngle(""); setHypothesis("");
    setOutlinePref(""); setDataPoints([]); setOutline([]); setArticle(""); setResLog([]);
    setAbResults({});
    setIcTagLoading(false); setIcTagError(""); setIcTagReasons({});
    setIcConfirmedTags(null); setIcTagsReady(false); setIcSaveLoading(false);
    setIcMatches(null); setIcMatchLoading(false); setIcMatchError("");
    setIcSendLog([]); setIcSendBusy(null); setIcEngageEdit(null);
    localStorage.removeItem("ria_draft_v1");
  }

  // ── A/B helpers ──────────────────────────────────────────────────
  function toggleAbMode() {
    const next = !abMode;
    setAbMode(next);
    stor("set", "ria_ab_mode", next);
  }

  function setWritingLLMPersist(llm) {
    setWritingLLM(llm);
    stor("set", "ria_writing_llm", llm);
  }

  function recordPref(contentType, winner) {
    setAbPrefs(function(prev) {
      const updated = { ...prev };
      if (!updated[contentType]) updated[contentType] = {};
      updated[contentType][winner] = (updated[contentType][winner] || 0) + 1;
      stor("set", "ria_ab_prefs_v1", updated);
      return updated;
    });
  }

  function getBestLLM(contentType) {
    const p = abPrefs[contentType];
    if (!p) return null;
    const total = Object.values(p).reduce(function(a, b) { return a + b; }, 0);
    if (total < 3) return null; // not enough data
    return Object.keys(p).reduce(function(a, b) { return (p[a] || 0) >= (p[b] || 0) ? a : b; });
  }

  function getPrefStats(contentType) {
    const p = abPrefs[contentType];
    if (!p) return null;
    const total = Object.values(p).reduce(function(a, b) { return a + b; }, 0);
    if (total === 0) return null;
    return { total, breakdown: p };
  }

  // Generate from both LLMs in parallel for A/B
  async function generateAB(contentType, systemPrompt, userMsg, maxTokens) {
    setAbResults(function(prev) { return { ...prev, [contentType]: { loading: true, claude: null, gpt4o: null } }; });
    const [claudeResult, gptResult] = await Promise.allSettled([
      (async function() {
        const data = await callClaude(systemPrompt, userMsg, null, maxTokens || 1500);
        return (data.content || []).filter(function(b) { return b.type === "text"; }).map(function(b) { return b.text; }).join("");
      })(),
      callOpenAI(systemPrompt, userMsg, maxTokens || 1500),
    ]);
    const claudeText = claudeResult.status === "fulfilled" ? claudeResult.value : ("Claude error: " + claudeResult.reason);
    const gptText = gptResult.status === "fulfilled" ? gptResult.value : ("GPT-4o error: " + gptResult.reason);
    setAbResults(function(prev) { return { ...prev, [contentType]: { loading: false, claude: claudeText, gpt4o: gptText } }; });
    return { claude: claudeText, gpt4o: gptText };
  }

  // ════════════════════════════════════════════════════════════════
  // RENDER
  // ════════════════════════════════════════════════════════════════
  const NAV_ITEMS = [
    { id: "campaigns", label: "Campaigns" },
    { id: "setup",     label: "Setup" },
  ];
  const PHASES = ["Setup", "Research", "Curate", "Outline", "Article", "Audience"];
  const PASTE_FLOW_PHASES = ["Topic", "Paste content", "Audience"];

  return (
    <div style={{ minHeight: "100vh", background: T.bg, color: T.text, fontFamily: "'DM Sans', system-ui, sans-serif" }}>

      {/* TOP BAR */}
      <div style={{ background: T.s1, borderBottom: "1px solid " + T.border, padding: "13px 20px", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <a href="/" style={{ textDecoration: "none", color: T.muted, fontSize: 12, marginRight: 4 }}>Back to App</a>
          <div style={{ width: 36, height: 36, borderRadius: 9, background: "linear-gradient(135deg,#E9B96E,#6C8EF5)", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 17 }}>+</div>
          <div>
            <div style={{ fontSize: 9, color: T.gold, letterSpacing: 2.5, fontWeight: 700, fontFamily: "monospace" }}>ARTICLE-FIRST · CONTENT INTELLIGENCE</div>
            <div style={{ fontSize: 15, fontWeight: 700 }}>{profile.firmName || "Your Firm"} - Weekly Campaign Studio</div>
          </div>
        </div>
        <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
          {profile.firmName && <Tag active color={T.green}>Memory On</Tag>}
          <Btn v="blue" onClick={function() { resetFlow(); startPasteFlow(); setNav("flow"); }} sx={{ padding: "8px 16px" }}>Paste &amp; match</Btn>
          <Btn onClick={function() { resetFlow(); setNav("flow"); }} sx={{ padding: "8px 16px" }}>+ New Campaign</Btn>
        </div>
      </div>

      {/* NAV */}
      {nav !== "flow" && (
        <div style={{ display: "flex", background: T.s2, borderBottom: "1px solid " + T.border }}>
          {NAV_ITEMS.map(function(n) {
            return (
              <button key={n.id} onClick={function() { setNav(n.id); }} style={{
                flex: 1, padding: "11px 8px", background: "transparent", border: "none",
                borderBottom: nav === n.id ? "2px solid " + T.gold : "2px solid transparent",
                color: nav === n.id ? T.gold : T.muted, cursor: "pointer", fontSize: 13, fontWeight: 600,
                fontFamily: "inherit", transition: "all .15s",
              }}>{n.label}</button>
            );
          })}
        </div>
      )}

      <div style={{ maxWidth: 800, margin: "0 auto", padding: "24px 16px 80px" }}>

        {/* CAMPAIGNS LIST */}
        {nav === "campaigns" && !activeCampaign && (
          <div>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 20 }}>
              <div>
                <div style={{ fontSize: 24, fontWeight: 700, marginBottom: 4 }}>Weekly Campaigns</div>
                <div style={{ color: T.muted, fontSize: 13 }}>One article per week generates LinkedIn, WhatsApp and X teasers</div>
              </div>
            </div>

                {/* Resume draft banner */}
            {stor("get", "ria_draft_v1") && (stor("get", "ria_draft_v1").artTopic || stor("get", "ria_draft_v1").article) && nav === "campaigns" && !activeCampaign && (
              <div style={{ background: "rgba(233,185,110,.08)", border: "1px solid " + T.gold + "44", borderRadius: 12, padding: "12px 16px", marginBottom: 16, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div>
                  <div style={{ fontSize: 12, fontWeight: 700, color: T.gold }}>Draft in progress</div>
                  <div style={{ fontSize: 12, color: T.muted, marginTop: 2 }}>{stor("get", "ria_draft_v1").artTopic}</div>
                </div>
                <div style={{ display: "flex", gap: 8 }}>
                  <Btn sm onClick={function() { setNav("flow"); }} sx={{ background: "rgba(233,185,110,.15)", color: T.gold }}>Resume</Btn>
                  <Btn sm v="ghost" onClick={function() { localStorage.removeItem("ria_draft_v1"); setFlowPath("research"); setPastePhase(1); setArtTopic(""); setArtAngle(""); setArtPhase(1); setDataPoints([]); setOutline([]); setArticle(""); }}>Discard</Btn>
                </div>
              </div>
            )}

        {campaigns.length === 0 && (
              <Card sx={{ marginBottom: 24, background: "rgba(108,142,245,.06)", borderColor: "rgba(108,142,245,.2)" }}>
                <SL c={T.blue}>HOW THE ARTICLE-FIRST WORKFLOW WORKS</SL>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 12 }}>
                  {[
                    { icon: "1", title: "Write Article", desc: "Research-backed deep dive. Your credibility anchor." },
                    { icon: "2", title: "Generate Teasers", desc: "3 platform posts auto-written from the article." },
                    { icon: "3", title: "Add Your Link", desc: "Paste your published article URL into each post." },
                    { icon: "4", title: "Post and Grow", desc: "Readers get the hook. Serious ones read the full piece." },
                  ].map(function(s) {
                    return (
                      <div key={s.title} style={{ textAlign: "center", padding: "12px 8px" }}>
                        <div style={{ width: 32, height: 32, borderRadius: "50%", background: "rgba(108,142,245,.2)", color: T.blue, display: "flex", alignItems: "center", justifyContent: "center", fontWeight: 800, margin: "0 auto 8px" }}>{s.icon}</div>
                        <div style={{ fontSize: 12, fontWeight: 700, color: T.blue, marginBottom: 3 }}>{s.title}</div>
                        <div style={{ fontSize: 11, color: T.muted, lineHeight: 1.5 }}>{s.desc}</div>
                      </div>
                    );
                  })}
                </div>
                <div style={{ textAlign: "center", marginTop: 16, display: "flex", gap: 10, justifyContent: "center", flexWrap: "wrap" }}>
                  <Btn onClick={function() { resetFlow(); setNav("flow"); }} sx={{ padding: "12px 24px" }}>Create with AI research</Btn>
                  <Btn v="blue" onClick={function() { resetFlow(); startPasteFlow(); setNav("flow"); }} sx={{ padding: "12px 24px" }}>Paste content &amp; match</Btn>
                </div>
              </Card>
            )}

            {campaigns.map(function(c) {
              const at = ARTICLE_TYPES.find(function(a) { return a.id === c.type; });
              return (
                <Card key={c.id} sx={{ marginBottom: 12 }} glow={c.status === "posts_ready" ? T.green : T.gold}
                  onClick={function() { setActiveCampaign(c); }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
                    <div style={{ flex: 1 }}>
                      <div style={{ fontSize: 17, fontWeight: 700, marginBottom: 6 }}>{c.title}</div>
                      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                        <Tag active color={T.gold}>{at ? at.label : c.type}</Tag>
                        <Tag active color={c.status === "posts_ready" ? T.green : T.blue}>
                          {c.status === "posts_ready" ? "Full Campaign Ready" : "Article Ready - Posts Pending"}
                        </Tag>
                        {c.article && (
                          c.contentIntelligence && c.contentIntelligence.confirmed_tags ? (
                            <Tag color={T.green}>CI tags</Tag>
                          ) : (
                            <Tag color={T.blue}>CI pending</Tag>
                          )
                        )}
                        {c.dpCount && <Tag>{c.dpCount} data points</Tag>}
                      </div>
                    </div>
                    <div style={{ textAlign: "right", flexShrink: 0, marginLeft: 12 }}>
                      <div style={{ fontSize: 11, color: T.muted }}>{new Date(c.ts).toLocaleDateString()}</div>
                      <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
                        {["linkedin", "whatsapp", "twitter"].map(function(pl) {
                          return (
                            <div key={pl} style={{
                              width: 24, height: 24, borderRadius: 6, display: "flex", alignItems: "center", justifyContent: "center",
                              background: (c.posts && c.posts[pl]) ? PLATFORMS[pl].bg : "rgba(255,255,255,.04)",
                              border: "1px solid " + ((c.posts && c.posts[pl]) ? PLATFORMS[pl].color + "55" : T.border),
                              fontSize: 10, color: (c.posts && c.posts[pl]) ? PLATFORMS[pl].color : T.muted, fontWeight: 800,
                            }}>{PLATFORMS[pl].icon}</div>
                          );
                        })}
                      </div>
                    </div>
                  </div>
                </Card>
              );
            })}

            {campaigns.length > 0 && (
              <div style={{ textAlign: "center", marginTop: 24 }}>
                <Btn v="ghost" onClick={function() { resetFlow(); setNav("flow"); }}>+ New Campaign</Btn>
              </div>
            )}
          </div>
        )}

        {/* CAMPAIGN DETAIL */}
        {nav === "campaigns" && activeCampaign && (
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 20 }}>
              <Btn sm v="ghost" onClick={function() { setActiveCampaign(null); }}>Back</Btn>
              <div style={{ fontSize: 20, fontWeight: 700 }}>{activeCampaign.title}</div>
            </div>

            {(activeCampaign.article || "").trim() && (
              <Card sx={{ marginBottom: 16, borderColor: T.blue + "66", background: "rgba(108,142,245,.06)" }}>
                <div style={{ fontSize: 13, color: T.text, lineHeight: 1.55 }}>
                  <strong style={{ color: T.blue }}>Content intelligence</strong> — tag this article, match clients, log sends.
                  {!activeCampaign.contentIntelligence && !icTagsReady ? " Start by generating audience tags below." : ""}
                </div>
              </Card>
            )}

            {(activeCampaign.article || "").trim() && renderAudienceTagsPanel({ inFlow: false, onDetail: true })}

            {(activeCampaign.contentIntelligence || icTagsReady) && (activeCampaign.article || "").trim() && (
              <Card sx={{ marginBottom: 20, background: "rgba(62,207,160,.05)", borderColor: "rgba(62,207,160,.25)" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 10, marginBottom: 12 }}>
                  <div>
                    <SL c={T.green}>CLIENT MATCHING</SL>
                    <div style={{ fontSize: 12, color: T.muted }}>Rank active clients and leads whose profile tags overlap this content.</div>
                  </div>
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                    {icTagsReady && activeCampaign.serverId != null && (
                      <Btn sm v="ghost" onClick={function() {
                        setIcSaveLoading(true);
                        saveContentIntelligence(activeCampaign.serverId || activeCampaign.id, {
                          title: activeCampaign.title,
                          type: activeCampaign.type,
                          article: activeCampaign.article,
                          topic_category: topicCategoryFromArtType(activeCampaign.type),
                        }).then(function() {
                          var patch = { contentIntelligence: { confirmed_tags: icConfirmedTags, topic_category: topicCategoryFromArtType(activeCampaign.type) } };
                          var updated = campaigns.map(function(c) {
                            return c.id === activeCampaign.id ? { ...c, ...patch } : c;
                          });
                          saveCampaigns(updated);
                          setActiveCampaign(function(p) { return { ...p, ...patch }; });
                        }).catch(function(e) { setIcTagError(e.message); }).finally(function() { setIcSaveLoading(false); });
                      }} disabled={icSaveLoading}>
                        {icSaveLoading ? "Saving..." : "Save tag changes"}
                      </Btn>
                    )}
                    <Btn sm v="green" onClick={function() { fetchMatchPersons(activeCampaign.serverId || activeCampaign.id); }} disabled={icMatchLoading || !(activeCampaign.contentIntelligence || icTagsReady)}>
                      {icMatchLoading ? <span style={{ display: "flex", alignItems: "center", gap: 6 }}><Spin color={T.green} />Matching...</span> : "Find matching clients"}
                    </Btn>
                  </div>
                </div>
                {icMatchError && <div style={{ fontSize: 12, color: T.red, marginBottom: 10 }}>{icMatchError}</div>}
                {icMatches && icMatches.length === 0 && <div style={{ fontSize: 12, color: T.muted }}>No matches with at least one overlapping tag. Try adjusting tags or broaden content.</div>}
                {icMatches && icMatches.length > 0 && (
                  <div style={{ overflowX: "auto", border: "1px solid " + T.border, borderRadius: 10 }}>
                    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                      <thead>
                        <tr style={{ background: T.s2 }}>
                          <th style={{ padding: "8px 10px", textAlign: "left", color: T.muted, fontSize: 10, fontFamily: "monospace" }}>NAME</th>
                          <th style={{ padding: "8px 10px", textAlign: "left", color: T.muted, fontSize: 10, fontFamily: "monospace" }}>MATCH</th>
                          <th style={{ padding: "8px 10px", textAlign: "left", color: T.muted, fontSize: 10, fontFamily: "monospace" }}>CONTACT</th>
                          <th style={{ padding: "8px 10px", textAlign: "left", color: T.muted, fontSize: 10, fontFamily: "monospace" }}>ACTIONS</th>
                        </tr>
                      </thead>
                      <tbody>
                        {icMatches.slice(0, 25).map(function(m) {
                          var busy = icSendBusy === (m.person_id + ":Send") || icSendBusy === (m.person_id + ":Skip");
                          var profileHref = m.client_id ? ("/clients/" + m.client_id) : (m.lead_id ? ("/leads/" + m.lead_id) : null);
                          return (
                            <tr key={m.person_id} style={{ borderBottom: "1px solid " + T.border }}>
                              <td style={{ padding: "8px 10px", verticalAlign: "top" }}>
                                <div style={{ fontWeight: 600, color: T.text }}>{m.name}</div>
                                <div style={{ fontSize: 10, color: T.muted }}>{m.category}</div>
                                <div style={{ fontSize: 10, color: T.muted, marginTop: 4 }}>{(m.matched_tags || []).join(" · ")}</div>
                                {profileHref && (
                                  <a href={profileHref} target="_blank" rel="noopener noreferrer" style={{ fontSize: 10, color: T.blue, display: "inline-block", marginTop: 4 }}>Open profile →</a>
                                )}
                              </td>
                              <td style={{ padding: "8px 10px", color: T.gold, fontFamily: "monospace", verticalAlign: "top" }}>{m.match_count}</td>
                              <td style={{ padding: "8px 10px", fontSize: 11, color: m.ok_to_contact ? T.green : T.red, verticalAlign: "top", maxWidth: 140 }}>{m.contact_reason || (m.ok_to_contact ? "OK" : "Hold")}</td>
                              <td style={{ padding: "8px 10px", verticalAlign: "top" }}>
                                <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                                  <Btn sm v="green" disabled={busy || !m.ok_to_contact} onClick={function() { logSendDecision(m.person_id, m.name, "Send", "WhatsApp"); }}>Send</Btn>
                                  <Btn sm v="ghost" disabled={busy} onClick={function() { logSendDecision(m.person_id, m.name, "Skip", ""); }}>Skip</Btn>
                                </div>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}

                {icSendLog.length > 0 && (
                  <div style={{ marginTop: 20, borderTop: "1px solid " + T.border, paddingTop: 16 }}>
                    <SL c={T.muted}>SEND LOG</SL>
                    <div style={{ fontSize: 11, color: T.muted, marginBottom: 10 }}>Record outcomes after you message clients (stored on this campaign).</div>
                    {icSendLog.map(function(entry) {
                      var editing = icEngageEdit === entry.log_id;
                      return (
                        <div key={entry.log_id} style={{ background: "rgba(0,0,0,.2)", borderRadius: 8, padding: "10px 12px", marginBottom: 8, fontSize: 12 }}>
                          <div style={{ display: "flex", justifyContent: "space-between", flexWrap: "wrap", gap: 6 }}>
                            <span style={{ fontWeight: 700, color: T.text }}>{entry.person_name}</span>
                            <span style={{ color: T.muted }}>{entry.date_sent} · {entry.decision}{entry.channel ? " · " + entry.channel : ""}</span>
                          </div>
                          <div style={{ marginTop: 6, color: T.muted }}>Engagement: <span style={{ color: T.gold }}>{entry.engagement || "None"}</span>{entry.followup_needed ? " · Follow-up needed" : ""}</div>
                          {entry.notes ? <div style={{ marginTop: 4, color: T.muted }}>{entry.notes}</div> : null}
                          {!editing ? (
                            <Btn sm v="ghost" sx={{ marginTop: 8 }} onClick={function() { setIcEngageEdit(entry.log_id); }}>Update engagement</Btn>
                          ) : (
                            <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 8 }}>
                              <select id={"ic_eng_" + entry.log_id} defaultValue={entry.engagement || "None"} style={{ background: T.s2, border: "1px solid " + T.border, borderRadius: 6, padding: 6, color: T.text, fontSize: 12 }}>
                                {IC_ENGAGEMENTS.map(function(e) { return <option key={e} value={e}>{e}</option>; })}
                              </select>
                              <label style={{ fontSize: 11, color: T.muted, display: "flex", alignItems: "center", gap: 6 }}>
                                <input type="checkbox" id={"ic_fu_" + entry.log_id} defaultChecked={!!entry.followup_needed} /> Follow-up needed
                              </label>
                              <input id={"ic_notes_" + entry.log_id} defaultValue={entry.notes || ""} placeholder="Notes" style={{ width: "100%", boxSizing: "border-box", background: T.s2, border: "1px solid " + T.border, borderRadius: 6, padding: 8, color: T.text, fontSize: 12 }} />
                              <div style={{ display: "flex", gap: 6 }}>
                                <Btn sm v="green" onClick={function() {
                                  var sel = document.getElementById("ic_eng_" + entry.log_id);
                                  var fu = document.getElementById("ic_fu_" + entry.log_id);
                                  var notesEl = document.getElementById("ic_notes_" + entry.log_id);
                                  saveEngagement(entry.log_id, sel ? sel.value : "None", fu && fu.checked, notesEl ? notesEl.value : "");
                                }}>Save</Btn>
                                <Btn sm v="ghost" onClick={function() { setIcEngageEdit(null); }}>Cancel</Btn>
                              </div>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </Card>
            )}

            <Card sx={{ marginBottom: 16, background: "rgba(62,207,160,.06)", borderColor: "rgba(62,207,160,.25)" }}>
              <SL c={T.green}>YOUR ARTICLE LINK</SL>
              <div style={{ display: "flex", gap: 8 }}>
                <Inp value={articleUrl || activeCampaign.articleUrl || ""}
                  onChange={function(v) { setArticleUrl(v); setActiveCampaign(function(p) { return { ...p, articleUrl: v }; }); }}
                  placeholder="Paste your published article URL here (LinkedIn, website, Substack...)"
                  style={{ fontSize: 12 }} />
                <Btn sm v="green" onClick={function() {
                  const updated = campaigns.map(function(c) { return c.id === activeCampaign.id ? { ...c, articleUrl: articleUrl } : c; });
                  saveCampaigns(updated);
                  updateCampaignOnServer(activeCampaign.id, { articleUrl: articleUrl });
                }} sx={{ flexShrink: 0 }}>Save</Btn>
              </div>
              <div style={{ fontSize: 11, color: T.muted, marginTop: 6 }}>Once published, add the URL here and it will be woven into social posts.</div>
            </Card>

            {/* Designer: suggestions + cover image */}
            <Card sx={{ marginBottom: 20, background: "rgba(233,185,110,.04)", borderColor: T.gold + "44" }}>
              <SL c={T.gold}>DESIGNER</SL>
              <div style={{ fontSize: 12, color: T.muted, marginBottom: 14 }}>Use the LLM to get headline and image ideas, then generate a cover image with DALL·E 3 (requires OpenAI API key).</div>
              {designError && <div style={{ background: "rgba(252,96,118,.1)", border: "1px solid rgba(252,96,118,.3)", borderRadius: 8, padding: "8px 12px", marginBottom: 12, fontSize: 12, color: T.red }}>{designError}</div>}
              <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginBottom: 14 }}>
                <Btn sm onClick={function() { fetchDesignSuggestions(activeCampaign); }} disabled={designLoad}>
                  {designLoad ? <span style={{ display: "flex", alignItems: "center", gap: 6 }}><Spin color={T.gold} />Thinking...</span> : "Get design suggestions"}
                </Btn>
                <Btn sm v="gold" onClick={function() { generateCoverImage(activeCampaign); }} disabled={imageLoad}>
                  {imageLoad ? <span style={{ display: "flex", alignItems: "center", gap: 6 }}><Spin color={T.gold} />Generating image...</span> : "Generate cover image"}
                </Btn>
              </div>
              {activeCampaign.designSuggestions && (
                <div style={{ borderTop: "1px solid " + T.border, paddingTop: 14, marginTop: 8 }}>
                  <div style={{ fontSize: 11, color: T.muted, fontWeight: 700, marginBottom: 8 }}>SUGGESTIONS</div>
                  <div style={{ display: "grid", gap: 10, marginBottom: 12 }}>
                    {(activeCampaign.designSuggestions.headlines || []).map(function(h, i) {
                      return <div key={i} style={{ background: "rgba(0,0,0,.2)", borderRadius: 8, padding: "10px 12px", fontSize: 13, color: T.text }}>{h}</div>;
                    })}
                  </div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 8 }}>
                    <span style={{ background: T.gold + "18", color: T.gold, border: "1px solid " + T.gold + "44", borderRadius: 6, padding: "4px 10px", fontSize: 11 }}>{activeCampaign.designSuggestions.colorMood || ""}</span>
                    <span style={{ color: T.muted, fontSize: 12 }}>CTA: {activeCampaign.designSuggestions.cta || ""}</span>
                  </div>
                  <div style={{ fontSize: 11, color: T.muted }}>Image prompt: {activeCampaign.designSuggestions.heroImagePrompt || ""}</div>
                </div>
              )}
              {activeCampaign.coverImageUrl && (
                <div style={{ borderTop: "1px solid " + T.border, paddingTop: 14, marginTop: 14 }}>
                  <div style={{ fontSize: 11, color: T.muted, fontWeight: 700, marginBottom: 8 }}>COVER IMAGE</div>
                  <img src={activeCampaign.coverImageUrl} alt="Cover" style={{ maxWidth: "100%", borderRadius: 12, border: "1px solid " + T.border, display: "block" }} />
                  <Btn sm v="ghost" sx={{ marginTop: 8 }} onClick={function() { cp(activeCampaign.coverImageUrl, "cover"); }}>{copied === "cover" ? "Copied URL" : "Copy image URL"}</Btn>
                </div>
              )}
            </Card>

            {!(activeCampaign.posts && activeCampaign.posts.linkedin) && !(activeCampaign.posts && activeCampaign.posts.whatsapp) && !(activeCampaign.posts && activeCampaign.posts.twitter) && (
              <Card sx={{ marginBottom: 20, textAlign: "center", background: "rgba(108,142,245,.06)", borderColor: "rgba(108,142,245,.25)" }}>
                <div style={{ fontSize: 28, marginBottom: 8 }}>Generate Posts</div>
                <div style={{ color: T.muted, fontSize: 13, marginBottom: 16 }}>3 platform-specific teaser posts, each pointing back to your full research article.</div>
                <Btn onClick={function() { generateAllPosts(activeCampaign); }} disabled={Object.values(genPosts).some(Boolean)} sx={{ padding: "12px 32px" }}>
                  {Object.values(genPosts).some(Boolean)
                    ? <span style={{ display: "flex", alignItems: "center", gap: 8, justifyContent: "center" }}><Spin />Generating...</span>
                    : "Generate All 3 Posts"}
                </Btn>
              </Card>
            )}

            {["linkedin", "whatsapp", "twitter"].map(function(platId) {
              const pl = PLATFORMS[platId];
              const post = activeCampaign.posts && activeCampaign.posts[platId];
              const loading = genPosts[platId];
              return (
                <Card key={platId} sx={{ marginBottom: 14 }} glow={post ? pl.color : undefined}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <div style={{ width: 32, height: 32, borderRadius: 8, background: pl.bg, border: "1px solid " + pl.color + "33", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 14, color: pl.color, fontWeight: 800 }}>{pl.icon}</div>
                      <div>
                        <div style={{ fontWeight: 700, fontSize: 14, color: post ? T.text : T.muted }}>{pl.label}</div>
                        <div style={{ fontSize: 11, color: T.muted }}>{pl.teaserStyle.slice(0, 55)}...</div>
                      </div>
                    </div>
                    <Btn sm v={post ? "ghost" : "blue"}
                      onClick={function() { generateLinkedPost(activeCampaign, platId); }}
                      disabled={loading}>
                      {loading ? <span style={{ display: "flex", alignItems: "center", gap: 6 }}><Spin color={pl.color} />Writing...</span> : post ? "Regenerate" : "Generate"}
                    </Btn>
                  </div>

                  {abMode && abResults["post_" + platId] && !abResults["post_" + platId].loading ? (
                    <ABCompare
                      contentType={"post_" + platId}
                      results={abResults["post_" + platId]}
                      prefs={abPrefs}
                      onPick={function(winner, text) {
                        recordPref("post_" + platId, winner);
                        savePostToCampaign(activeCampaign, platId, text, activeCampaign.articleUrl || articleUrl || "");
                        setAbResults(function(p) { return { ...p, ["post_" + platId]: null }; });
                      }}
                      onRerun={function() { generateLinkedPost(activeCampaign, platId); }}
                      copied={copied} onCopy={cp}
                    />
                  ) : post ? (
                    <div>
                      <div style={{ background: "rgba(0,0,0,.25)", borderRadius: 10, padding: "13px 15px", fontSize: 13.5, lineHeight: 1.75, whiteSpace: "pre-wrap", color: "#ccc", maxHeight: 220, overflowY: "auto", marginBottom: 10 }}>
                        {post}
                      </div>
                      <div style={{ display: "flex", gap: 8 }}>
                        <Btn sm onClick={function() { cp(post, platId); }}>{copied === platId ? "Copied!" : "Copy and Post"}</Btn>
                        {activeCampaign.articleUrl && (
                          <span style={{ fontSize: 11, color: T.green, display: "flex", alignItems: "center", gap: 4 }}>Article URL included</span>
                        )}
                      </div>
                    </div>
                  ) : !loading && (
                    <div style={{ background: "rgba(255,255,255,.02)", borderRadius: 10, padding: "20px", textAlign: "center", color: T.muted, fontSize: 12, border: "1px dashed " + T.border }}>
                      Click Generate to create the {pl.label} teaser post
                    </div>
                  )}
                </Card>
              );
            })}

            <div style={{ marginTop: 24 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
                <SL>FULL ARTICLE</SL>
                <Btn sm v="ghost" onClick={function() { cp(activeCampaign.article, "fullarticle"); }}>{copied === "fullarticle" ? "Copied" : "Copy Article"}</Btn>
              </div>
              <div style={{ background: T.s2, borderRadius: 12, padding: "16px", fontSize: 13, lineHeight: 1.8, color: "#bbb", whiteSpace: "pre-wrap", maxHeight: 300, overflowY: "auto", border: "1px solid " + T.border }}>
                {(activeCampaign.article || "").slice(0, 1200)}
                {activeCampaign.article && activeCampaign.article.length > 1200 && (
                  <span style={{ color: T.muted }}>... <button onClick={function() { cp(activeCampaign.article, "fullarticle"); }} style={{ background: "none", border: "none", color: T.gold, cursor: "pointer", fontSize: 12 }}>Copy full article</button></span>
                )}
              </div>
            </div>

            {activeCampaign.auditLog && activeCampaign.auditLog.length > 0 && (
              <Card sx={{ marginTop: 24, borderColor: T.blue + "44", background: "rgba(108,142,245,.04)" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14, flexWrap: "wrap", gap: 8 }}>
                  <div>
                    <SL c={T.blue}>AUDIT REFERENCE LOG</SL>
                    <div style={{ fontSize: 12, color: T.muted }}>Verify each fact against the original source.</div>
                  </div>
                  <Btn sm v="ghost" onClick={function() {
                    const rows = activeCampaign.auditLog.map(function(d, i) {
                      const stat = (d.stat || "").replace(/\|/g, " ");
                      const value = (d.value || "").replace(/\|/g, " ");
                      const src = (d.source || "").replace(/\|/g, " ");
                      const url = d.sourceUrl || "";
                      const year = d.year || "";
                      return "| " + (i + 1) + " | " + stat + " | " + value + " | " + src + " | " + (url ? "<" + url + ">" : "") + " | " + year + " |";
                    });
                    cp("| # | Fact / Stat | Value | Source | URL | Year |\n|--|--|--|--|--|--|\n" + rows.join("\n"), "auditlog");
                  }}>{copied === "auditlog" ? "Copied" : "Copy as markdown table"}</Btn>
                </div>
                <div style={{ overflowX: "auto", border: "1px solid " + T.border, borderRadius: 10, overflow: "hidden" }}>
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                    <thead>
                      <tr style={{ background: T.s2 }}>
                        <th style={{ padding: "10px 12px", textAlign: "left", color: T.blue, fontWeight: 700, fontFamily: "monospace", fontSize: 10, letterSpacing: 1.5, width: 36 }}>REF</th>
                        <th style={{ padding: "10px 12px", textAlign: "left", color: T.muted, fontWeight: 700, fontFamily: "monospace", fontSize: 10, letterSpacing: 1.5 }}>FACT / STAT</th>
                        <th style={{ padding: "10px 12px", textAlign: "left", color: T.muted, fontWeight: 700, fontFamily: "monospace", fontSize: 10, letterSpacing: 1.5 }}>VALUE</th>
                        <th style={{ padding: "10px 12px", textAlign: "left", color: T.muted, fontWeight: 700, fontFamily: "monospace", fontSize: 10, letterSpacing: 1.5 }}>SOURCE</th>
                        <th style={{ padding: "10px 12px", textAlign: "left", color: T.muted, fontWeight: 700, fontFamily: "monospace", fontSize: 10, letterSpacing: 1.5 }}>YEAR</th>
                        <th style={{ padding: "10px 12px", textAlign: "left", color: T.muted, fontWeight: 700, fontFamily: "monospace", fontSize: 10, letterSpacing: 1.5, width: 60 }}>VERIFY</th>
                      </tr>
                    </thead>
                    <tbody>
                      {activeCampaign.auditLog.map(function(d, i) {
                        const qColor = d.quality === "high" ? T.green : d.quality === "medium" ? T.gold : T.red;
                        return (
                          <tr key={i} style={{ borderBottom: "1px solid " + T.border }}>
                            <td style={{ padding: "10px 12px", verticalAlign: "top", color: T.blue, fontWeight: 800, fontFamily: "monospace" }}>{i + 1}</td>
                            <td style={{ padding: "10px 12px", verticalAlign: "top", color: T.text, maxWidth: 180 }}>{d.stat}</td>
                            <td style={{ padding: "10px 12px", verticalAlign: "top", color: T.gold, fontWeight: 600, fontFamily: "monospace", whiteSpace: "nowrap" }}>{d.value}</td>
                            <td style={{ padding: "10px 12px", verticalAlign: "top", color: T.muted, maxWidth: 160 }}>{d.source}</td>
                            <td style={{ padding: "10px 12px", verticalAlign: "top", color: T.muted }}>{d.year}</td>
                            <td style={{ padding: "10px 12px", verticalAlign: "top" }}>
                              {d.sourceUrl ? <a href={d.sourceUrl} target="_blank" rel="noopener noreferrer" style={{ color: T.blue, fontWeight: 600, fontSize: 11 }}>Open source →</a> : <span style={{ color: T.muted, fontSize: 11 }}>No URL</span>}
                              {d.quality && <span style={{ marginLeft: 6, background: qColor + "18", color: qColor, border: "1px solid " + qColor + "44", borderRadius: 4, padding: "1px 5px", fontSize: 9, fontWeight: 700, fontFamily: "monospace" }}>{(d.quality || "").toUpperCase()}</span>}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </Card>
            )}
          </div>
        )}

        {/* ARTICLE CREATION FLOW */}
        {nav === "flow" && (
          <div>
            {/* Phase stepper */}
            <div style={{ display: "flex", alignItems: "center", marginBottom: 28, gap: 0, flexWrap: "wrap" }}>
              {flowSteps().map(function(label, i) {
                const n = i + 1;
                const cur = flowStepIndex();
                const done = cur > n;
                const active = cur === n;
                return (
                  <div key={n} style={{ display: "flex", alignItems: "center" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
                      <div style={{ width: 22, height: 22, borderRadius: "50%", display: "flex", alignItems: "center", justifyContent: "center", background: (done || active) ? T.gold : "rgba(255,255,255,.06)", color: (done || active) ? "#060810" : T.muted, fontSize: 10, fontWeight: 800 }}>{done ? "v" : n}</div>
                      <span style={{ fontSize: 11, color: active ? T.gold : done ? "#888" : T.muted, fontWeight: active ? 700 : 400 }}>{label}</span>
                    </div>
                    {i < flowSteps().length - 1 && <div style={{ width: 18, height: 1, background: done ? T.gold + "55" : T.border, margin: "0 6px" }} />}
                  </div>
                );
              })}
              {isPasteFlow() && (
                <span style={{ marginLeft: 8, fontSize: 10, color: T.blue, fontFamily: "monospace", fontWeight: 700 }}>PASTE MODE</span>
              )}
              <Btn sm v="ghost" sx={{ marginLeft: "auto" }} onClick={function() { resetFlow(); setNav("campaigns"); }}>Cancel</Btn>
            </div>
            <div style={{ fontSize: 11, color: T.muted, marginBottom: 16 }}>
              {isPasteFlow()
                ? "Paste existing content, evaluate audience tags, then match clients and leads — no AI article writing required."
                : "Progress is saved automatically. You can leave and come back to continue."}
            </div>

            {/* Paste flow — step 1: topic */}
            {isPasteFlow() && pastePhase === 1 && (
              <div>
                <div style={{ fontSize: 26, fontWeight: 700, marginBottom: 4 }}>Paste &amp; match</div>
                <div style={{ color: T.muted, fontSize: 13, marginBottom: 20 }}>Skip research and article writing. Use content you already have (article, note, email draft).</div>
                <Card sx={{ marginBottom: 14 }}>
                  <SL>CAMPAIGN LABEL</SL>
                  <Inp value={artTopic} onChange={setArtTopic} placeholder="e.g. March newsletter — retirement inflation gap" />
                  <div style={{ marginTop: 10 }}>
                    <label style={{ fontSize: 11, color: T.muted, display: "block", marginBottom: 5 }}>Content category (optional)</label>
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                      {ARTICLE_TYPES.map(function(at) {
                        return (
                          <div key={at.id} onClick={function() { setArtType(at.id); }} style={{ background: artType === at.id ? "rgba(108,142,245,.12)" : "rgba(255,255,255,.02)", border: "1px solid " + (artType === at.id ? T.blue + "55" : T.border), borderRadius: 10, padding: 10, cursor: "pointer" }}>
                            <div style={{ fontSize: 12, fontWeight: 700, color: artType === at.id ? T.blue : T.text }}>{at.label}</div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                </Card>
                {icTagError && (
                  <div style={{ background: "rgba(252,96,118,.1)", border: "1px solid rgba(252,96,118,.3)", borderRadius: 8, padding: "10px 14px", marginBottom: 14, fontSize: 12, color: T.red }}>{icTagError}</div>
                )}
                <Btn onClick={openPasteContentStep} disabled={!artTopic.trim()} sx={{ width: "100%", padding: 14, fontSize: 14 }}>Next: paste your content</Btn>
                <div style={{ textAlign: "center", marginTop: 14 }}>
                  <Btn sm v="ghost" onClick={function() { setFlowPath("research"); setPastePhase(1); setArtPhase(1); }}>Use full AI article workflow instead</Btn>
                </div>
              </div>
            )}

            {/* Paste flow — step 2: content */}
            {isPasteFlow() && pastePhase === 2 && artPhase < 6 && (
              <div>
                <div style={{ fontSize: 22, fontWeight: 700, marginBottom: 4 }}>Your content</div>
                <div style={{ color: T.muted, fontSize: 13, marginBottom: 16 }}>Paste the full text. We will analyse it for audience tags and match clients/leads — no client PII is sent to the tag model.</div>
                <Card glow={T.blue} sx={{ marginBottom: 14 }}>
                  <SL c={T.blue}>CONTENT TO EVALUATE</SL>
                  <textarea value={article} onChange={function(e) { setArticle(e.target.value); }} rows={22}
                    placeholder="Paste your article, LinkedIn post, email draft, or WhatsApp message here..."
                    style={{ width: "100%", boxSizing: "border-box", background: "rgba(0,0,0,.2)", border: "1px solid " + T.border, borderRadius: 10, padding: 16, color: "#ddd", fontSize: 13.5, lineHeight: 1.75, outline: "none", fontFamily: "Georgia,serif", resize: "vertical" }} />
                  <div style={{ fontSize: 11, color: T.muted, marginTop: 8 }}>
                    {(article || "").trim().split(/\s+/).filter(Boolean).length} words
                    {(article || "").trim().length < 80 ? " — add more text for better tag suggestions" : ""}
                  </div>
                </Card>
                {icTagError && (
                  <div style={{ background: "rgba(252,96,118,.1)", border: "1px solid rgba(252,96,118,.3)", borderRadius: 8, padding: "10px 14px", marginBottom: 14, fontSize: 12, color: T.red }}>{icTagError}</div>
                )}
                <div style={{ display: "flex", gap: 8 }}>
                  <Btn v="ghost" onClick={function() { setPastePhase(1); setIcTagError(""); }}>Back</Btn>
                  <Btn v="blue" onClick={goToAudienceFromPaste} disabled={!(article || "").trim()} sx={{ flex: 1, padding: 14 }}>Continue to audience tags</Btn>
                </div>
              </div>
            )}

            {/* Phase 1: Setup (full research flow) */}
            {!isPasteFlow() && artPhase === 1 && (
              <div>
                <div style={{ fontSize: 26, fontWeight: 700, marginBottom: 4 }}>What is this week's article?</div>
                <div style={{ color: T.muted, fontSize: 13, marginBottom: 20 }}>One strong article anchors your entire week's content across all platforms.</div>
                <Card sx={{ marginBottom: 14 }}>
                  <SL>TOPIC AND ANGLE</SL>
                  <Inp value={artTopic} onChange={setArtTopic} placeholder="e.g. Why most Indians are dangerously under-insured for retirement" />
                  <div style={{ marginTop: 10 }}>
                    <label style={{ fontSize: 11, color: T.muted, display: "block", marginBottom: 5 }}>Your hypothesis / angle</label>
                    <Inp value={artAngle} onChange={setArtAngle} placeholder="e.g. Indians save for retirement but ignore inflation - creating a 60% gap in actual needs" />
                  </div>
                </Card>
                <Card sx={{ marginBottom: 14 }}>
                  <SL>ARTICLE TYPE</SL>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                    {ARTICLE_TYPES.map(function(at) {
                      return (
                        <div key={at.id} onClick={function() { setArtType(at.id); }} style={{ background: artType === at.id ? "rgba(233,185,110,.1)" : "rgba(255,255,255,.02)", border: "1px solid " + (artType === at.id ? T.gold + "55" : T.border), borderRadius: 10, padding: 12, cursor: "pointer" }}>
                          <div style={{ fontSize: 12, fontWeight: 700, color: artType === at.id ? T.gold : T.text }}>{at.label}</div>
                        </div>
                      );
                    })}
                  </div>
                </Card>
                <Card sx={{ marginBottom: 20 }}>
                  <SL>OUTPUT SETTINGS</SL>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
                    <div>
                      <label style={{ fontSize: 11, color: T.muted, display: "block", marginBottom: 5 }}>Tone</label>
                      <select value={artTone} onChange={function(e) { setArtTone(e.target.value); }} style={{ width: "100%", background: T.s2, border: "1px solid " + T.border, borderRadius: 8, padding: "8px 9px", color: T.text, fontSize: 12, outline: "none", fontFamily: "inherit" }}>
                        {TONES.map(function(o) { return <option key={o}>{o}</option>; })}
                      </select>
                    </div>
                    <div>
                      <label style={{ fontSize: 11, color: T.muted, display: "block", marginBottom: 5 }}>Length</label>
                      <select value={artLength} onChange={function(e) { setArtLength(e.target.value); }} style={{ width: "100%", background: T.s2, border: "1px solid " + T.border, borderRadius: 8, padding: "8px 9px", color: T.text, fontSize: 12, outline: "none", fontFamily: "inherit" }}>
                        {LENGTHS.map(function(o) { return <option key={o}>{o}</option>; })}
                      </select>
                    </div>
                  </div>
                </Card>
                <Btn onClick={startResearch} disabled={!artTopic.trim()} sx={{ width: "100%", padding: 14, fontSize: 14 }}>Start Live Research</Btn>
                <div style={{ marginTop: 20, paddingTop: 18, borderTop: "1px solid " + T.border }}>
                  <div style={{ fontSize: 12, color: T.muted, marginBottom: 10, lineHeight: 1.5 }}>Already have the content? Skip research, outline, and AI writing.</div>
                  <Btn v="blue" onClick={startPasteFlow} sx={{ width: "100%", padding: 12 }}>Paste content and match audience</Btn>
                </div>
              </div>
            )}

            {/* Phase 2: Research */}
            {!isPasteFlow() && artPhase === 2 && (
              <div>
                <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 4 }}>
                  <div style={{ fontSize: 22, fontWeight: 700 }}>Phase 1 - Live Research</div>
                  <span style={{ background: "rgba(108,142,245,.15)", color: T.blue, border: "1px solid rgba(108,142,245,.3)", borderRadius: 6, fontSize: 10, fontWeight: 800, padding: "3px 8px", fontFamily: "monospace" }}>PERPLEXITY</span>
                  <span style={{ background: "rgba(233,185,110,.1)", color: T.gold, border: "1px solid rgba(233,185,110,.3)", borderRadius: 6, fontSize: 10, fontWeight: 800, padding: "3px 8px", fontFamily: "monospace" }}>CLAUDE</span>
                </div>
                <div style={{ color: T.muted, fontSize: 13, marginBottom: 16 }}>Perplexity searches the live web for hard data. Claude writes from your selected data points.</div>
                <Card sx={{ marginBottom: 14 }}>
                  <SL>RESEARCH LOG</SL>
                  <div ref={logRef} style={{ background: "rgba(0,0,0,.3)", borderRadius: 8, padding: "10px 13px", maxHeight: 160, overflowY: "auto", fontFamily: "monospace", fontSize: 12 }}>
                    {resLog.map(function(l, i) {
                      const isPerp = l.startsWith("[Perplexity]");
                      const color = l.startsWith("Error") ? T.red : (l.includes("Found") || l.includes("Added")) ? T.green : isPerp ? T.blue : "#666";
                      return (
                        <div key={i} style={{ color: color, marginBottom: 4, display: "flex", gap: 6, alignItems: "flex-start" }}>
                          {isPerp && <span style={{ background: "rgba(108,142,245,.2)", color: T.blue, fontSize: 9, fontWeight: 800, padding: "1px 5px", borderRadius: 3, flexShrink: 0, marginTop: 1 }}>PERPLEXITY</span>}
                          <span>{isPerp ? l.replace("[Perplexity] ", "") : l}</span>
                        </div>
                      );
                    })}
                    {researching && <div style={{ display: "flex", alignItems: "center", gap: 8, color: T.gold }}><Spin />Searching with Perplexity...</div>}
                  </div>
                </Card>
                {!researching && dataPoints.length > 0 && (
                  <Card sx={{ marginBottom: 14 }}>
                    <SL>ADD MORE DATA</SL>
                    <div style={{ display: "flex", gap: 8 }}>
                      <Inp value={extraQ} onChange={setExtraQ} placeholder="e.g. AMFI SIP data 2024, RBI inflation 2025..." />
                      <Btn v="blue" onClick={addSearch} disabled={researching || !extraQ.trim()} sx={{ flexShrink: 0 }}>Search</Btn>
                    </div>
                  </Card>
                )}
                {!researching && dataPoints.length > 0 && (
                  <Btn onClick={function() { setArtPhase(3); }} sx={{ width: "100%", padding: 13 }}>Curate {dataPoints.length} Data Points</Btn>
                )}
              </div>
            )}

            {/* Phase 3: Curate */}
            {!isPasteFlow() && artPhase === 3 && (
              <div>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", marginBottom: 16 }}>
                  <div>
                    <div style={{ fontSize: 22, fontWeight: 700 }}>Phase 2 - Curate</div>
                    <div style={{ color: T.muted, fontSize: 13 }}>Select what matters. Add your interpretation.</div>
                  </div>
                  <div style={{ textAlign: "right" }}>
                    <div style={{ fontSize: 26, fontWeight: 800, color: T.gold }}>{selectedDP.length}</div>
                    <div style={{ fontSize: 9, color: T.muted, fontFamily: "monospace" }}>SELECTED</div>
                  </div>
                </div>
                {/* Structured data table */}
                <div style={{ overflowX: "auto", marginBottom: 16 }}>
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                    <thead>
                      <tr style={{ borderBottom: "1px solid " + T.border }}>
                        <th style={{ padding: "8px 10px", textAlign: "left", color: T.muted, fontWeight: 700, fontFamily: "monospace", fontSize: 10, letterSpacing: 1.5, width: 28 }}></th>
                        <th style={{ padding: "8px 10px", textAlign: "left", color: T.muted, fontWeight: 700, fontFamily: "monospace", fontSize: 10, letterSpacing: 1.5 }}>STAT</th>
                        <th style={{ padding: "8px 10px", textAlign: "left", color: T.gold, fontWeight: 700, fontFamily: "monospace", fontSize: 10, letterSpacing: 1.5 }}>VALUE</th>
                        <th style={{ padding: "8px 10px", textAlign: "left", color: T.muted, fontWeight: 700, fontFamily: "monospace", fontSize: 10, letterSpacing: 1.5 }}>CONTEXT</th>
                        <th style={{ padding: "8px 10px", textAlign: "left", color: T.muted, fontWeight: 700, fontFamily: "monospace", fontSize: 10, letterSpacing: 1.5 }}>SOURCE</th>
                        <th style={{ padding: "8px 10px", textAlign: "left", color: T.muted, fontWeight: 700, fontFamily: "monospace", fontSize: 10, letterSpacing: 1.5 }}>YR</th>
                        <th style={{ padding: "8px 10px", textAlign: "left", color: T.muted, fontWeight: 700, fontFamily: "monospace", fontSize: 10, letterSpacing: 1.5 }}>Q</th>
                      </tr>
                    </thead>
                    <tbody>
                      {dataPoints.map(function(dp) {
                        const qColor = dp.quality === "high" ? T.green : dp.quality === "medium" ? T.gold : T.red;
                        const stat = dp.stat || dp.text || "";
                        const value = dp.value || "";
                        const context = dp.context || "";
                        const source = dp.source || "";
                        const year = dp.year || "";
                        return [
                          <tr key={dp.id} onClick={function() { toggleDP(dp.id); }}
                            style={{ background: dp.selected ? "rgba(233,185,110,.06)" : "transparent", borderBottom: "1px solid " + T.border, cursor: "pointer", transition: "background .15s" }}>
                            <td style={{ padding: "10px 10px", verticalAlign: "top" }}>
                              <div style={{ width: 16, height: 16, borderRadius: 3, background: dp.selected ? T.gold : "transparent", border: "2px solid " + (dp.selected ? T.gold : T.muted), display: "flex", alignItems: "center", justifyContent: "center", fontSize: 10, color: "#060810", flexShrink: 0 }}>{dp.selected ? "v" : ""}</div>
                            </td>
                            <td style={{ padding: "10px 10px", verticalAlign: "top", color: dp.selected ? T.text : "#888", fontWeight: 600, maxWidth: 160 }}>{stat}</td>
                            <td style={{ padding: "10px 10px", verticalAlign: "top", color: T.gold, fontWeight: 700, fontFamily: "monospace", whiteSpace: "nowrap" }}>{value}</td>
                            <td style={{ padding: "10px 10px", verticalAlign: "top", color: T.muted, lineHeight: 1.5, maxWidth: 220 }}>{context}</td>
                            <td style={{ padding: "10px 10px", verticalAlign: "top", maxWidth: 140 }}>
                              {dp.sourceUrl
                                ? <a href={dp.sourceUrl} target="_blank" rel="noopener noreferrer" style={{ color: T.blue, textDecoration: "none", fontSize: 11 }} onClick={function(e) { e.stopPropagation(); }}>{source}</a>
                                : <span style={{ color: T.muted, fontSize: 11 }}>{source}</span>}
                            </td>
                            <td style={{ padding: "10px 10px", verticalAlign: "top", color: T.muted, whiteSpace: "nowrap" }}>{year}</td>
                            <td style={{ padding: "10px 10px", verticalAlign: "top" }}>
                              <span style={{ background: qColor + "18", color: qColor, border: "1px solid " + qColor + "44", borderRadius: 4, padding: "2px 6px", fontSize: 9, fontWeight: 700, fontFamily: "monospace", whiteSpace: "nowrap" }}>{(dp.quality || "").toUpperCase()}</span>
                            </td>
                          </tr>,
                          dp.selected && (
                            <tr key={dp.id + "_note"} style={{ background: "rgba(233,185,110,.04)", borderBottom: "1px solid " + T.border }}>
                              <td colSpan={7} style={{ padding: "4px 10px 10px 38px" }}>
                                <input value={dp.note || ""} onChange={function(e) { noteDP(dp.id, e.target.value); }} placeholder="Your angle / interpretation for this data point..."
                                  style={{ width: "100%", boxSizing: "border-box", background: "rgba(255,255,255,.04)", border: "1px solid " + T.gold + "44", borderRadius: 6, padding: "6px 10px", color: T.text, fontSize: 12, outline: "none", fontFamily: "inherit" }} />
                              </td>
                            </tr>
                          )
                        ];
                      })}
                    </tbody>
                  </table>
                </div>
                <Card sx={{ marginBottom: 14, marginTop: 16 }} glow={T.gold}>
                  <SL>YOUR HYPOTHESIS</SL>
                  <Inp value={hypothesis} onChange={setHypothesis} multi rows={3} placeholder="What is the core argument this article makes? This shapes the entire story." />
                </Card>
                <Card sx={{ marginBottom: 20 }}>
                  <SL>STRUCTURAL PREFERENCES</SL>
                  <Inp value={outlinePref} onChange={setOutlinePref} placeholder="e.g. Open with a shocking stat, use a real case in section 3, end with 3 action steps..." />
                </Card>
                <Btn onClick={genOutline} disabled={selectedDP.length < 2 || outlineLoad} sx={{ width: "100%", padding: 13 }}>
                  {outlineLoad ? <span style={{ display: "flex", alignItems: "center", gap: 8, justifyContent: "center" }}><Spin />Building outline...</span> : "Generate Outline (" + selectedDP.length + " data points)"}
                </Btn>
              </div>
            )}

            {/* Phase 4: Outline */}
            {!isPasteFlow() && artPhase === 4 && (
              <div>
                <div style={{ fontSize: 22, fontWeight: 700, marginBottom: 4 }}>Phase 3 - Outline</div>
                <div style={{ color: T.muted, fontSize: 13, marginBottom: 16 }}>Review the structure. Approve to write the full article.</div>
                {outlineError && (
                  <div style={{ background: "rgba(252,96,118,.12)", border: "1px solid rgba(252,96,118,.4)", borderRadius: 10, padding: "14px 18px", marginBottom: 20, fontSize: 13, color: T.red, display: "flex", alignItems: "center", flexWrap: "wrap", gap: 10 }}>
                    <span style={{ flex: "1 1 200px" }}>{outlineError}</span>
                    <Btn sm v="ghost" onClick={genOutline}>Retry</Btn>
                  </div>
                )}
                {outlineLoad
                  ? <Card sx={{ textAlign: "center", padding: 50 }}>
                      <Spin />
                      <div style={{ color: T.muted, fontSize: 13, marginTop: 10 }}>Building outline...</div>
                      <div style={{ color: T.muted, fontSize: 11, marginTop: 6 }}>If rate-limited, retrying automatically (up to 60s)</div>
                    </Card>
                  : outline.length > 0 ? (
                    <div>
                      {outline.map(function(s, i) {
                        return (
                          <Card key={i} sx={{ marginBottom: 10 }} glow={T.gold}>
                            <div style={{ display: "flex", gap: 12 }}>
                              <div style={{ width: 26, height: 26, borderRadius: "50%", background: "rgba(233,185,110,.15)", color: T.gold, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 12, fontWeight: 800, flexShrink: 0 }}>{i + 1}</div>
                              <div>
                                <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 3 }}>{s.section}</div>
                                <div style={{ fontSize: 12, color: T.muted, marginBottom: 5 }}>{s.purpose}</div>
                                <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>{(s.keyPoints || []).map(function(p, j) { return <Tag key={j} color={T.blue}>{p}</Tag>; })}</div>
                              </div>
                            </div>
                          </Card>
                        );
                      })}
                      <div style={{ display: "flex", gap: 8, marginTop: 20 }}>
                        <Btn onClick={writeArticle} sx={{ flex: 1, padding: 13 }}>Write Full Article</Btn>
                        <Btn v="ghost" onClick={function() { setArtPhase(3); }}>Adjust</Btn>
                      </div>
                    </div>
                  ) : (
                    <Card sx={{ textAlign: "center", padding: 40 }}>
                      {outlineError && (
                        <div style={{ background: "rgba(252,96,118,.1)", border: "1px solid rgba(252,96,118,.3)", borderRadius: 8, padding: "10px 14px", marginBottom: 16, fontSize: 12, color: T.red, textAlign: "left" }}>
                          {outlineError}
                        </div>
                      )}
                      <Btn onClick={genOutline}>Generate Outline</Btn>
                    </Card>
                  )
                }
              </div>
            )}

            {/* Phase 5: Article */}
            {!isPasteFlow() && artPhase === 5 && (
              <div>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
                  <div>
                    <div style={{ fontSize: 22, fontWeight: 700 }}>Your Article</div>
                    <div style={{ color: T.muted, fontSize: 13 }}>{abMode ? "A/B mode: both AIs write in parallel — you pick the better one." : "Edit, then continue to audience tags (step 6)."}</div>
                  </div>
                  {article && !abMode && <div style={{ display: "flex", gap: 8 }}>
                    <Btn sm v="ghost" onClick={function() { cp(article, "art"); }}>{copied === "art" ? "Copied" : "Copy"}</Btn>
                    <Btn sm v="blue" onClick={function() { setArtPhase(6); }}>Audience tags →</Btn>
                  </div>}
                </div>

                {artError && (
                  <div style={{ background: "rgba(252,96,118,.1)", border: "1px solid rgba(252,96,118,.3)", borderRadius: 10, padding: "12px 16px", marginBottom: 16, fontSize: 13, color: T.red }}>
                    {artError} <Btn sm v="ghost" onClick={writeArticle} sx={{ marginLeft: 12 }}>Retry</Btn>
                  </div>
                )}

                {artLoad ? (
                  <Card sx={{ textAlign: "center", padding: 70 }}>
                    <Spin />
                    <div style={{ color: T.muted, fontSize: 13, marginTop: 12 }}>
                      {abMode ? "Both Claude and GPT-4o are writing in parallel..." : "Writing your article... 20-30 seconds."}
                    </div>
                  </Card>
                ) : abMode && abResults["article"] && !abResults["article"].loading ? (
                  <ABCompare
                    contentType="article"
                    results={abResults["article"]}
                    prefs={abPrefs}
                    onPick={function(winner, text) {
                      recordPref("article", winner);
                      setArticle(text);
                      if (text) setArtPhase(6);
                      setAbResults(function(p) { return { ...p, article: null }; });
                    }}
                    onRerun={writeArticle}
                    copied={copied} onCopy={cp}
                  />
                ) : article ? (
                  <div>
                    <Card glow={T.gold}>
                      <textarea value={article} onChange={function(e) { setArticle(e.target.value); }} rows={28}
                        style={{ width: "100%", boxSizing: "border-box", background: "rgba(0,0,0,.2)", border: "1px solid " + T.border, borderRadius: 10, padding: 16, color: "#ddd", fontSize: 13.5, lineHeight: 1.8, outline: "none", fontFamily: "Georgia,serif", resize: "vertical" }} />
                      <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
                        <Btn onClick={function() { setArtPhase(6); }} sx={{ flex: 1, padding: 13 }} v="blue">Continue to audience tags →</Btn>
                        <Btn v="ghost" onClick={writeArticle}>Rewrite</Btn>
                      </div>
                    </Card>

                    {/* Audit reference log — verify each fact against original source */}
                    {selectedDP.length > 0 && (
                      <Card sx={{ marginTop: 24, borderColor: T.blue + "44", background: "rgba(108,142,245,.04)" }}>
                        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14, flexWrap: "wrap", gap: 8 }}>
                          <div>
                            <SL c={T.blue}>AUDIT REFERENCE LOG</SL>
                            <div style={{ fontSize: 12, color: T.muted }}>Verify each fact against the original source before publishing.</div>
                          </div>
                          <Btn sm v="ghost" onClick={function() {
                            const rows = selectedDP.map(function(d, i) {
                              const stat = (d.stat || d.text || "").replace(/\|/g, " ");
                              const value = (d.value || "").replace(/\|/g, " ");
                              const src = (d.source || "").replace(/\|/g, " ");
                              const url = d.sourceUrl || "";
                              const year = d.year || "";
                              const ref = i + 1;
                              return "| " + ref + " | " + stat + " | " + value + " | " + src + " | " + (url ? "<" + url + ">" : "") + " | " + year + " |";
                            });
                            const header = "| # | Fact / Stat | Value | Source | URL | Year |\n|--|--|--|--|--|--|\n";
                            cp(header + rows.join("\n"), "auditlog");
                          }}>{copied === "auditlog" ? "Copied" : "Copy as markdown table"}</Btn>
                        </div>
                        <div style={{ overflowX: "auto", border: "1px solid " + T.border, borderRadius: 10, overflow: "hidden" }}>
                          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                            <thead>
                              <tr style={{ background: T.s2 }}>
                                <th style={{ padding: "10px 12px", textAlign: "left", color: T.blue, fontWeight: 700, fontFamily: "monospace", fontSize: 10, letterSpacing: 1.5, width: 36 }}>REF</th>
                                <th style={{ padding: "10px 12px", textAlign: "left", color: T.muted, fontWeight: 700, fontFamily: "monospace", fontSize: 10, letterSpacing: 1.5 }}>FACT / STAT</th>
                                <th style={{ padding: "10px 12px", textAlign: "left", color: T.muted, fontWeight: 700, fontFamily: "monospace", fontSize: 10, letterSpacing: 1.5 }}>VALUE</th>
                                <th style={{ padding: "10px 12px", textAlign: "left", color: T.muted, fontWeight: 700, fontFamily: "monospace", fontSize: 10, letterSpacing: 1.5 }}>SOURCE</th>
                                <th style={{ padding: "10px 12px", textAlign: "left", color: T.muted, fontWeight: 700, fontFamily: "monospace", fontSize: 10, letterSpacing: 1.5 }}>YEAR</th>
                                <th style={{ padding: "10px 12px", textAlign: "left", color: T.muted, fontWeight: 700, fontFamily: "monospace", fontSize: 10, letterSpacing: 1.5, width: 60 }}>VERIFY</th>
                              </tr>
                            </thead>
                            <tbody>
                              {selectedDP.map(function(d, i) {
                                const stat = d.stat || d.text || "";
                                const value = d.value || "";
                                const source = d.source || "";
                                const year = d.year || "";
                                const qColor = d.quality === "high" ? T.green : d.quality === "medium" ? T.gold : T.red;
                                return (
                                  <tr key={d.id} style={{ borderBottom: "1px solid " + T.border }}>
                                    <td style={{ padding: "10px 12px", verticalAlign: "top", color: T.blue, fontWeight: 800, fontFamily: "monospace" }}>{i + 1}</td>
                                    <td style={{ padding: "10px 12px", verticalAlign: "top", color: T.text, maxWidth: 180 }}>{stat}</td>
                                    <td style={{ padding: "10px 12px", verticalAlign: "top", color: T.gold, fontWeight: 600, fontFamily: "monospace", whiteSpace: "nowrap" }}>{value}</td>
                                    <td style={{ padding: "10px 12px", verticalAlign: "top", color: T.muted, maxWidth: 160 }}>{source}</td>
                                    <td style={{ padding: "10px 12px", verticalAlign: "top", color: T.muted }}>{year}</td>
                                    <td style={{ padding: "10px 12px", verticalAlign: "top" }}>
                                      {d.sourceUrl ? (
                                        <a href={d.sourceUrl} target="_blank" rel="noopener noreferrer" style={{ color: T.blue, fontWeight: 600, fontSize: 11 }} title="Open original source">Open source →</a>
                                      ) : (
                                        <span style={{ color: T.muted, fontSize: 11 }}>No URL</span>
                                      )}
                                      <span style={{ marginLeft: 6, background: qColor + "18", color: qColor, border: "1px solid " + qColor + "44", borderRadius: 4, padding: "1px 5px", fontSize: 9, fontWeight: 700, fontFamily: "monospace" }}>{(d.quality || "").toUpperCase()}</span>
                                    </td>
                                  </tr>
                                );
                              })}
                            </tbody>
                          </table>
                        </div>
                        <div style={{ fontSize: 11, color: T.muted, marginTop: 10 }}>Quality: high = primary (SEBI/RBI/AMFI etc.), medium = reputable media citing data, low = secondary. Use REF # to cross-check facts in your article.</div>
                      </Card>
                    )}
                  </div>
                ) : (
                  <Card sx={{ textAlign: "center", padding: 40 }}>
                    <Btn onClick={writeArticle} sx={{ padding: "13px 32px" }}>Write Article Now</Btn>
                  </Card>
                )}
              </div>
            )}

            {/* Phase 6: Audience tags + save */}
            {artPhase === 6 && (
              <div>
                <div style={{ marginBottom: 16 }}>
                  <div style={{ fontSize: 22, fontWeight: 700 }}>Audience tags</div>
                  <div style={{ color: T.muted, fontSize: 13 }}>
                    {isPasteFlow()
                      ? "Generate tags from your pasted content, confirm them, then save. After save, open the campaign to find matching clients and leads."
                      : "Generate tags from your article, confirm, then save the campaign. After save you can match clients from the campaign list."}
                  </div>
                </div>
                {isPasteFlow() && (article || "").trim() && (
                  <Card sx={{ marginBottom: 14, background: "rgba(0,0,0,.2)" }}>
                    <SL>CONTENT PREVIEW</SL>
                    <div style={{ fontSize: 12, color: "#aaa", lineHeight: 1.6, maxHeight: 120, overflow: "hidden", whiteSpace: "pre-wrap" }}>
                      {(article || "").trim().slice(0, 600)}{(article || "").length > 600 ? "…" : ""}
                    </div>
                  </Card>
                )}
                {renderAudienceTagsPanel({ inFlow: true })}
                <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
                  <Btn onClick={saveCampaign} disabled={icSaveLoading || !icTagsReady} sx={{ flex: 1, padding: 14 }}>
                    {icSaveLoading ? "Saving campaign…" : (icTagsReady ? "Save campaign" : "Generate tags above to enable save")}
                  </Btn>
                  <Btn v="ghost" onClick={function() {
                    if (isPasteFlow()) { setArtPhase(1); setPastePhase(2); setIcTagError(""); }
                    else { setArtPhase(5); }
                  }}>{isPasteFlow() ? "← Back to content" : "← Back to article"}</Btn>
                </div>
              </div>
            )}
          </div>
        )}

        {/* SETUP */}
        {nav === "setup" && (
          <div>
            <div style={{ fontSize: 24, fontWeight: 700, marginBottom: 4 }}>Business Profile</div>
            <div style={{ color: T.muted, fontSize: 13, marginBottom: 24 }}>Saved permanently. Powers every article and social post.</div>
            {[
              { label: "Firm Name", key: "firmName", ph: "e.g. WealthWise Advisory" },
              { label: "SEBI Registration Number", key: "sebiReg", ph: "e.g. INA000012345" },
              { label: "Target Audience", key: "audience", ph: "e.g. Salaried professionals 30-50, urban, 15L+ income, seeking fee-only guidance", multi: true },
              { label: "Brand Tone and Voice", key: "tone", ph: "e.g. Warm but authoritative, no jargon, real examples, never fear-based", multi: true },
            ].map(function(f) {
              return (
                <div key={f.key} style={{ marginBottom: 14 }}>
                  <label style={{ fontSize: 11, color: T.muted, display: "block", marginBottom: 5, fontWeight: 600 }}>{f.label}</label>
                  <Inp value={profile[f.key] || ""} onChange={function(v) { setProfile(function(p) { const np = { ...p }; np[f.key] = v; return np; }); }} placeholder={f.ph} multi={f.multi} />
                </div>
              );
            })}
            <Btn onClick={function() { saveProfile(profile); }} sx={{ width: "100%", padding: 13 }}>
              {profileSaved ? "Saved!" : "Save Profile"}
            </Btn>

            {/* ── AI Engine Settings ─────────────────────────────────── */}
            <div style={{ marginTop: 32 }}>
              <div style={{ fontSize: 18, fontWeight: 700, marginBottom: 4 }}>AI Engine Settings</div>
              <div style={{ color: T.muted, fontSize: 13, marginBottom: 20 }}>Choose your writing model, or run A/B tests to find your favourite.</div>

              {/* Writing engine selector */}
              <Card sx={{ marginBottom: 14 }}>
                <SL>WRITING ENGINE (ARTICLE + POSTS)</SL>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginBottom: 12 }}>
                  {[
                    { key: "claude", label: "Claude (Anthropic)", sublabel: "Nuanced, editorial depth", color: LLM_META.claude.color },
                    { key: "gpt4o",  label: "GPT-4o (OpenAI)",    sublabel: "Crisp, clear, direct",    color: LLM_META.gpt4o.color },
                  ].map(function(opt) {
                    const selected = writingLLM === opt.key;
                    const stats = getPrefStats(opt.key.replace("gpt4o", "article")) || getPrefStats("article");
                    const wins = stats && stats.breakdown && (stats.breakdown[opt.key] || 0);
                    const bestFor = getBestLLM("article");
                    return (
                      <div key={opt.key} onClick={function() { setWritingLLMPersist(opt.key); }}
                        style={{ background: selected ? opt.color + "18" : "rgba(255,255,255,.02)", border: "2px solid " + (selected ? opt.color : T.border), borderRadius: 12, padding: "14px 16px", cursor: "pointer", position: "relative" }}>
                        {bestFor === opt.key && (
                          <div style={{ position: "absolute", top: 8, right: 8, background: T.green + "22", color: T.green, border: "1px solid " + T.green + "44", borderRadius: 10, fontSize: 9, fontWeight: 700, padding: "2px 6px", fontFamily: "monospace" }}>YOUR PICK</div>
                        )}
                        <div style={{ fontWeight: 700, fontSize: 13, color: selected ? opt.color : T.text, marginBottom: 3 }}>{opt.label}</div>
                        <div style={{ fontSize: 11, color: T.muted }}>{opt.sublabel}</div>
                        {wins ? <div style={{ fontSize: 10, color: opt.color, marginTop: 6, fontFamily: "monospace" }}>{wins} A/B wins</div> : null}
                      </div>
                    );
                  })}
                </div>
                <div style={{ fontSize: 11, color: T.muted }}>Research always uses Perplexity → Claude fallback regardless of this setting.</div>
              </Card>

              {/* A/B test toggle */}
              <Card>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
                  <div>
                    <div style={{ fontWeight: 700, fontSize: 13 }}>A/B Test Mode</div>
                    <div style={{ fontSize: 12, color: T.muted, marginTop: 4, maxWidth: 380 }}>When ON, article and social post generation runs both Claude and GPT-4o in parallel. You pick the better output — and we track your preferences automatically.</div>
                  </div>
                  <div onClick={toggleAbMode} style={{ width: 44, height: 24, borderRadius: 12, background: abMode ? T.gold : "rgba(255,255,255,.1)", cursor: "pointer", position: "relative", transition: "background .2s", flexShrink: 0, marginLeft: 20 }}>
                    <div style={{ position: "absolute", top: 3, left: abMode ? 23 : 3, width: 18, height: 18, borderRadius: "50%", background: abMode ? "#060810" : "#555", transition: "left .2s" }} />
                  </div>
                </div>

                {/* Stats */}
                {Object.keys(abPrefs).length > 0 && (
                  <div style={{ marginTop: 16, borderTop: "1px solid " + T.border, paddingTop: 14 }}>
                    <SL>PREFERENCE HISTORY</SL>
                    {["article", "post_linkedin", "post_whatsapp", "post_twitter"].map(function(ct) {
                      const stats = getPrefStats(ct);
                      if (!stats) return null;
                      const label = ct === "article" ? "Article" : ct.replace("post_", "").charAt(0).toUpperCase() + ct.replace("post_", "").slice(1) + " Post";
                      const best = getBestLLM(ct);
                      return (
                        <div key={ct} style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
                          <div style={{ fontSize: 11, color: T.muted, width: 100 }}>{label}</div>
                          <div style={{ flex: 1, height: 4, background: "rgba(255,255,255,.07)", borderRadius: 2, overflow: "hidden", display: "flex" }}>
                            {Object.entries(stats.breakdown).map(function(entry) {
                              const k = entry[0]; const v = entry[1];
                              const pct = Math.round(v / stats.total * 100);
                              return <div key={k} style={{ width: pct + "%", background: LLM_META[k] ? LLM_META[k].color : "#888", transition: "width .3s" }} />;
                            })}
                          </div>
                          <div style={{ fontSize: 10, color: T.muted, fontFamily: "monospace", minWidth: 80 }}>
                            {Object.entries(stats.breakdown).map(function(e) { return (LLM_META[e[0]] ? LLM_META[e[0]].short : e[0]) + ":" + e[1]; }).join(" | ")}
                          </div>
                          {best && <span style={{ fontSize: 10, color: T.green, fontFamily: "monospace" }}>{LLM_META[best] ? LLM_META[best].short : best} wins</span>}
                        </div>
                      );
                    })}
                    <Btn sm v="ghost" sx={{ marginTop: 8 }} onClick={function() {
                      setAbPrefs({}); stor("set", "ria_ab_prefs_v1", {});
                    }}>Clear All Data</Btn>
                  </div>
                )}
              </Card>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(<App />);
