"""Context Manager — what every agent / LLM call is allowed to know.

Three scopes, all plain JSON the user can read and edit:
  global   ~/.iis/research_context.json                 rules for ALL research
  company  ~/.iis/companies/<key>/research_context.json  rules + confirmed / rejected peers
  session  session artifact "session_context"            current discussion: directives + recent turns

Directives are only stored when the user states them explicitly (chat or API); nothing is inferred.
Also owns the LLM call log (~/.iis/logs/llm_calls.jsonl) and optional full
transcripts under ~/.iis/logs/llm_transcripts/ (messages + response).
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import re
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from iis.config import settings
from iis.models.artifacts import AgentArtifact
from iis.models.enums import SourceTier
from iis.models.session import ResearchSession

SCOPES = ("global", "company", "session")

DEFAULT_GLOBAL_RULES = [
    "Write reports and answers from provided information only; no assumptions, forecasts or invented data.",
    "Live price only from the Inertia price sheet or the NSE/BSE feed; otherwise state 'price unavailable'.",
    "Peers must be confirmed by the user; peer comparisons use only peer CIQ / AR / concall files on record.",
    "Web or LLM content is a proposal for the user to approve, never a fact in the report.",
]

_lock = threading.Lock()
_agent_var: contextvars.ContextVar[str] = contextvars.ContextVar("iis_llm_agent", default="unlabelled")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── storage ──────────────────────────────────────────────────────────────


def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return json.loads(json.dumps(default))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else json.loads(json.dumps(default))
    except Exception:
        return json.loads(json.dumps(default))


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = _now()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _global_path() -> Path:
    return Path(settings.research_context_path)


def _company_path(company_key: str) -> Path:
    return settings.data_dir / "companies" / company_key.replace("/", "_") / "research_context.json"


def _rule(text: str, *, source: str = "chat") -> dict[str, Any]:
    return {"id": uuid4().hex[:8], "text": text.strip(), "source": source, "created_at": _now()}


def load_global() -> dict[str, Any]:
    path = _global_path()
    if not path.exists():
        data = {"rules": [_rule(t, source="default") for t in DEFAULT_GLOBAL_RULES]}
        with _lock:
            _write_json(path, data)
        return data
    data = _read_json(path, {"rules": []})
    data.setdefault("rules", [])
    return data


def load_company(company_key: str | None) -> dict[str, Any]:
    if not company_key:
        return {"rules": [], "peers": {}, "analyst_notes": ""}
    data = _read_json(_company_path(company_key), {"rules": [], "peers": {}, "analyst_notes": ""})
    data.setdefault("rules", [])
    data.setdefault("peers", {})
    data.setdefault("analyst_notes", "")
    return data


def get_analyst_notes(company_key: str | None) -> str:
    return str(load_company(company_key).get("analyst_notes") or "")


def set_analyst_notes(company_key: str, text: str) -> str:
    """Persist free-form analyst notes for this company (injected into LLM context)."""
    if not company_key:
        raise ValueError("company_key required")
    data = load_company(company_key)
    data["analyst_notes"] = (text or "").strip()
    save_company(company_key, data)
    return data["analyst_notes"]


def save_company(company_key: str, data: dict[str, Any]) -> None:
    with _lock:
        _write_json(_company_path(company_key), data)


def _session_payload(session: ResearchSession) -> dict[str, Any]:
    art = next((a for a in session.agent_artifacts if a.artifact_type == "session_context"), None)
    payload = dict(art.payload) if art else {}
    payload.setdefault("directives", [])
    payload.setdefault("turns", [])
    return payload


def _save_session_payload(session: ResearchSession, payload: dict[str, Any]) -> None:
    session.agent_artifacts = [a for a in session.agent_artifacts if a.artifact_type != "session_context"]
    session.agent_artifacts.append(
        AgentArtifact(
            agent_id="context_manager",
            artifact_type="session_context",
            payload=payload,
            source_tier=SourceTier.SESSION,
        )
    )


# ── directives ───────────────────────────────────────────────────────────


def add_directive(session: ResearchSession | None, text: str, *, scope: str, source: str = "chat") -> dict[str, Any]:
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {SCOPES}")
    rule = _rule(text, source=source)
    rule["scope"] = scope
    if scope == "global":
        data = load_global()
        data["rules"].append(rule)
        with _lock:
            _write_json(_global_path(), data)
    elif scope == "company":
        if not session or not session.company_key:
            raise ValueError("company scope needs a session with a company")
        data = load_company(session.company_key)
        data["rules"].append(rule)
        save_company(session.company_key, data)
    else:
        if not session:
            raise ValueError("session scope needs a session")
        payload = _session_payload(session)
        payload["directives"].append(rule)
        _save_session_payload(session, payload)
    return rule


def remove_directive(session: ResearchSession | None, rule_id_or_index: str) -> dict[str, Any] | None:
    """Remove by id, or by the 1-based number shown in `list_directives` output."""
    listing = list_directives(session)
    target = None
    key = str(rule_id_or_index).strip()
    if key.isdigit():
        idx = int(key) - 1
        if 0 <= idx < len(listing):
            target = listing[idx]
    else:
        target = next((r for r in listing if r["id"] == key), None)
    if not target:
        return None
    scope = target["scope"]
    if scope == "global":
        data = load_global()
        data["rules"] = [r for r in data["rules"] if r["id"] != target["id"]]
        with _lock:
            _write_json(_global_path(), data)
    elif scope == "company" and session and session.company_key:
        data = load_company(session.company_key)
        data["rules"] = [r for r in data["rules"] if r["id"] != target["id"]]
        save_company(session.company_key, data)
    elif scope == "session" and session:
        payload = _session_payload(session)
        payload["directives"] = [r for r in payload["directives"] if r["id"] != target["id"]]
        _save_session_payload(session, payload)
    return target


def list_directives(session: ResearchSession | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in load_global()["rules"]:
        out.append({**r, "scope": "global"})
    if session and session.company_key:
        for r in load_company(session.company_key)["rules"]:
            out.append({**r, "scope": "company"})
    if session:
        for r in _session_payload(session)["directives"]:
            out.append({**r, "scope": "session"})
    return out


_GLOBAL_CUES = re.compile(
    r"\b(for all (?:research|companies|reports|stocks)|across all|always|from now on|never|house rule|global rule)\b",
    re.I,
)
_COMPANY_CUES = re.compile(r"\b(for this company|for this stock|for this name|company rule)\b", re.I)
_SESSION_CUES = re.compile(
    r"\b(in this report|for this report|for this analysis|for now|in this discussion|focus on|"
    r"use (?:standalone|consolidated)|ignore|exclude|don'?t use|do not use)\b",
    re.I,
)
_EXPLICIT = re.compile(r"^\s*(?:remember|rule|note for (?:analyst|research)|context)\s*[:\-]\s*(.+)$", re.I | re.S)


def parse_directive(text: str) -> tuple[str, str] | None:
    """Return (scope, directive_text) if the message is an explicit instruction to remember."""
    raw = (text or "").strip()
    if not raw or raw.endswith("?"):
        return None
    m = _EXPLICIT.match(raw)
    body = m.group(1).strip() if m else None
    if body is None:
        if not (_GLOBAL_CUES.search(raw) or _COMPANY_CUES.search(raw) or _SESSION_CUES.search(raw)):
            return None
        # Only imperative-looking sentences; questions and long pastes are not rules.
        if len(raw) > 400:
            return None
        body = raw
    if _GLOBAL_CUES.search(body):
        return "global", body
    if _COMPANY_CUES.search(body):
        return "company", body
    return "session", body


# ── discussion turns ─────────────────────────────────────────────────────


def record_turn(session: ResearchSession, question: str, answer: str, *, max_turns: int = 8) -> None:
    payload = _session_payload(session)
    payload["turns"].append(
        {"q": (question or "")[:400], "a": (answer or "")[:600], "at": _now()}
    )
    payload["turns"] = payload["turns"][-max_turns:]
    _save_session_payload(session, payload)


# ── context block for agents / LLM ───────────────────────────────────────


def build_context_block(session: ResearchSession | None, *, budget_chars: int = 2500) -> str:
    parts: list[str] = []
    rules = load_global()["rules"]
    if rules:
        parts.append("=== RESEARCH RULES (apply to all research) ===")
        parts.extend(f"- {r['text']}" for r in rules)
    if session and session.company_key:
        comp = load_company(session.company_key)
        name = session.company.name if session.company else session.company_key
        lines: list[str] = []
        confirmed = [p["name"] for p in comp.get("peers", {}).values() if p.get("status") == "confirmed"]
        rejected = [p["name"] for p in comp.get("peers", {}).values() if p.get("status") == "rejected"]
        if confirmed:
            lines.append("- User-confirmed peers: " + ", ".join(confirmed))
        if rejected:
            lines.append("- Not peers (user rejected): " + ", ".join(rejected))
        lines.extend(f"- {r['text']}" for r in comp["rules"])
        notes = (comp.get("analyst_notes") or "").strip()
        if notes:
            lines.append("- Analyst notes (user):\n" + "\n".join(f"  {ln}" for ln in notes.splitlines()[:40]))
        if lines:
            parts.append(f"=== COMPANY CONTEXT ({name}) ===")
            parts.extend(lines)
    if session:
        payload = _session_payload(session)
        if payload["directives"]:
            parts.append("=== CURRENT DISCUSSION — USER DIRECTIVES ===")
            parts.extend(f"- {r['text']}" for r in payload["directives"])
        if payload["turns"]:
            parts.append("=== CURRENT DISCUSSION — RECENT TURNS ===")
            for t in payload["turns"][-4:]:
                parts.append(f"Q: {t['q'][:200]}")
                parts.append(f"A: {t['a'][:300]}")
    block = "\n".join(parts)
    if len(block) > budget_chars:
        block = block[: budget_chars - 20].rsplit("\n", 1)[0] + "\n[context trimmed]"
    return block


def context_snapshot(session: ResearchSession | None) -> dict[str, Any]:
    comp = load_company(session.company_key) if session and session.company_key else {"rules": [], "peers": {}}
    sess = _session_payload(session) if session else {"directives": [], "turns": []}
    return {
        "global_rules": load_global()["rules"],
        "company_rules": comp["rules"],
        "peers": comp.get("peers", {}),
        "analyst_notes": comp.get("analyst_notes") or "",
        "session_directives": sess["directives"],
        "recent_turns": sess["turns"],
        "numbered": list_directives(session),
    }


def format_directives_markdown(session: ResearchSession | None) -> str:
    listing = list_directives(session)
    if not listing:
        return "No research rules stored yet."
    lines = ["**Research context in force** (say `forget rule N` to remove one):", ""]
    label = {"global": "all research", "company": "this company", "session": "this discussion"}
    for i, r in enumerate(listing, 1):
        lines.append(f"{i}. _{label[r['scope']]}_ — {r['text']}")
    return "\n".join(lines)


# ── LLM call log ─────────────────────────────────────────────────────────


@contextmanager
def llm_agent(name: str):
    """Label LLM calls made inside this block (for the call log)."""
    token = _agent_var.set(name)
    try:
        yield
    finally:
        _agent_var.reset(token)


def current_agent() -> str:
    return _agent_var.get()


def _sanitize_messages_for_log(messages: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Drop huge base64 image payloads from transcripts; keep text + image metadata."""
    out: list[dict[str, Any]] = []
    for m in messages or []:
        role = m.get("role") or "user"
        content = m.get("content")
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        if isinstance(content, list):
            parts: list[Any] = []
            for p in content:
                if not isinstance(p, dict):
                    parts.append(p)
                    continue
                if p.get("type") == "image_url":
                    url = ""
                    iu = p.get("image_url")
                    if isinstance(iu, dict):
                        url = iu.get("url") or ""
                    parts.append(
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"[omitted data URL · {len(url)} chars]",
                            },
                        }
                    )
                else:
                    parts.append(p)
            out.append({"role": role, "content": parts})
            continue
        out.append({"role": role, "content": content})
    return out


def _message_plain(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        bits: list[str] = []
        for p in content:
            if isinstance(p, dict) and p.get("type") == "text":
                bits.append(p.get("text") or "")
            elif isinstance(p, dict) and p.get("type") == "image_url":
                bits.append("[image]")
            else:
                bits.append(str(p)[:80])
        return "\n".join(bits)
    return str(content or "")


def _write_llm_transcript(
    *,
    at: str,
    agent: str,
    provider: str,
    model: str,
    digest: str,
    messages: list[dict[str, Any]],
    response: str | None,
    latency_sec: float,
    ok: bool,
    error: str | None,
    attempts: int,
) -> str | None:
    """Save full messages + response as JSON and readable MD. Returns relative path."""
    if not bool(getattr(settings, "llm_log_full_transcripts", True)):
        return None
    root = Path(getattr(settings, "llm_transcript_dir", None) or (Path.home() / ".iis" / "logs" / "llm_transcripts"))
    # at is like 2026-11-06T06:30:00Z
    day = (at or "")[:10].replace("-", "") or datetime.now(timezone.utc).strftime("%Y%m%d")
    stamp = (at or "").replace(":", "").replace("-", "")[:15] or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    safe_agent = re.sub(r"[^\w\-]+", "_", agent or "llm")[:40]
    folder = root / day
    base = f"{stamp}_{safe_agent}_{digest}"
    safe_messages = _sanitize_messages_for_log(messages)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        payload = {
            "at": at,
            "agent": agent,
            "provider": provider,
            "model": model,
            "prompt_sha": digest,
            "latency_sec": round(latency_sec, 2),
            "ok": ok,
            "attempts": attempts,
            "error": error,
            "messages": safe_messages,
            "response": response,
        }
        jp = folder / f"{base}.json"
        mp = folder / f"{base}.md"
        jp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        md_lines = [
            f"# LLM call — {agent}",
            "",
            f"- **at**: {at}",
            f"- **provider/model**: {provider} / {model}",
            f"- **ok**: {ok} · **latency**: {latency_sec:.2f}s · **attempts**: {attempts}",
            f"- **prompt_sha**: `{digest}`",
        ]
        if error:
            md_lines += ["", f"**error**: {error}"]
        md_lines += ["", "---", "", "## Request messages", ""]
        for i, m in enumerate(safe_messages or [], 1):
            role = m.get("role") or "user"
            content = _message_plain(m.get("content"))
            md_lines += [f"### [{i}] {role}", "", "```", content, "```", ""]
        md_lines += ["---", "", "## Response", "", "```", response or "(empty)", "```", ""]
        mp.write_text("\n".join(md_lines), encoding="utf-8")
        # Store path relative to transcript root for the index
        return str(Path(day) / f"{base}.md")
    except Exception:
        return None


def log_llm_call(
    *,
    provider: str,
    model: str,
    messages: list[dict[str, Any]],
    response: str | None,
    latency_sec: float,
    ok: bool,
    error: str | None = None,
    attempts: int = 1,
) -> None:
    plain = "\n".join(_message_plain(m.get("content")) for m in messages)
    prompt_chars = len(plain)
    digest = hashlib.sha256(plain.encode("utf-8", "ignore")).hexdigest()[:16]
    at = _now()
    agent = current_agent()
    transcript_rel = _write_llm_transcript(
        at=at,
        agent=agent,
        provider=provider,
        model=model,
        digest=digest,
        messages=messages,
        response=response,
        latency_sec=latency_sec,
        ok=ok,
        error=error,
        attempts=attempts,
    )
    row = {
        "at": at,
        "agent": agent,
        "provider": provider,
        "model": model,
        "prompt_chars": prompt_chars,
        "response_chars": len(response or ""),
        "latency_sec": round(latency_sec, 2),
        "ok": ok,
        "attempts": attempts,
        "prompt_sha": digest,
        "error": (error or "")[:200] or None,
        "transcript": transcript_rel,
    }
    path = Path(settings.llm_call_log_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _lock, path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except Exception:
        pass


def recent_llm_calls(limit: int = 50) -> list[dict[str, Any]]:
    path = Path(settings.llm_call_log_path)
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()[-limit:]
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except Exception:
            continue
    return out
