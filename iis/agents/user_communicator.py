"""User Communicator — one place that turns data gaps into concrete asks, and user replies into
peer decisions / context directives. Deterministic (no LLM): commands are parsed with regexes.
"""
from __future__ import annotations

import re
from typing import Any

from iis.models.artifacts import AgentArtifact
from iis.models.session import ResearchSession


def _payload(session: ResearchSession, kind: str) -> dict[str, Any]:
    return next((a.payload for a in session.agent_artifacts if a.artifact_type == kind), {}) or {}


def _ask_key(kind: str, prompt: str, peer: str | None = None) -> str:
    if peer:
        return f"{kind}:{peer.strip().lower()}"
    compact = re.sub(r"\s+", " ", prompt).strip().lower()
    return f"{kind}:{compact}"


def open_asks(session: ResearchSession) -> list[dict[str, Any]]:
    """Progressive asks: data_readiness active window first, then extras (deduped)."""
    asks: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(item: dict[str, Any]) -> None:
        key = _ask_key(item.get("kind") or "other", item.get("prompt") or "", item.get("peer"))
        if not item.get("prompt") or key in seen:
            return
        seen.add(key)
        asks.append(item)

    # Ensure readiness is fresh
    ready = _payload(session, "data_readiness")
    if not ready.get("active") and not ready.get("queue"):
        try:
            from iis.finance.data_readiness import apply_data_readiness

            apply_data_readiness(session)
            ready = _payload(session, "data_readiness")
        except Exception:
            ready = {}

    # Primary: top 3–4 from readiness queue
    for a in ready.get("active") or []:
        _add(
            {
                "kind": a.get("kind") or "readiness",
                "priority": a.get("priority") or 1,
                "prompt": a.get("prompt"),
                "peer": a.get("peer"),
                "section": a.get("section"),
                "id": a.get("id"),
                "optional": a.get("optional"),
                "source": "data_readiness",
            }
        )

    # Price is urgent UX — keep even if outside window
    try:
        from iis.finance.market_data import is_reliable_quote

        if not is_reliable_quote(session.market_quote):
            _add(
                {
                    "kind": "price",
                    "priority": 0,
                    "prompt": (
                        "Market price unavailable (price sheet and NSE/BSE feed returned nothing). "
                        "Check the symbol in the price sheet, then click **Refresh price**."
                    ),
                }
            )
    except Exception:
        pass

    # Reconciliation gaps not already covered
    peer_seen = {
        (a.get("peer") or "").lower()
        for a in asks
        if a.get("peer")
    }
    for g in _payload(session, "reconciliation").get("gaps") or []:
        ask = g.get("ask") or ""
        if any(p and p in ask.lower() for p in peer_seen) and "peer" in (g.get("kind") or "").lower():
            continue
        _add({"kind": g.get("kind") or "gap", "priority": 12, "prompt": ask})

    asks.sort(key=lambda x: x.get("priority") or 99)
    # Cap UI list at active window + price (+1); rest live in data_readiness.later
    capped = []
    for a in asks:
        if a.get("kind") == "price" or len([x for x in capped if x.get("kind") != "price"]) < 4:
            capped.append(a)
    return capped


def apply_user_asks(session: ResearchSession) -> ResearchSession:
    try:
        from iis.finance.data_readiness import apply_data_readiness

        apply_data_readiness(session)
    except Exception:
        pass
    asks = open_asks(session)
    ready = _payload(session, "data_readiness")
    session.agent_artifacts = [a for a in session.agent_artifacts if a.artifact_type != "user_asks"]
    session.agent_artifacts.append(
        AgentArtifact(
            agent_id="user_communicator",
            artifact_type="user_asks",
            payload={
                "asks": asks,
                "later_count": ready.get("later_count") or 0,
                "later": ready.get("later") or [],
                "have": ready.get("have") or [],
                "summary": ready.get("summary") or "",
            },
        )
    )
    return session


def asks_markdown(asks: list[dict[str, Any]], session: ResearchSession | None = None) -> str:
    if session is not None:
        try:
            from iis.finance.data_readiness import readiness_markdown

            return readiness_markdown(session=session)
        except Exception:
            pass
    if not asks:
        return ""
    lines = ["**Next uploads (most useful now)**", ""]
    lines.extend(f"{i}. {a['prompt']}" for i, a in enumerate(asks, 1))
    return "\n".join(lines)


# ── Chat commands ────────────────────────────────────────────────────────

_PEER_CMD = re.compile(
    r"^\s*(?P<verb>confirm|approve|accept|reject|remove|drop|add)\s+(?:the\s+)?"
    r"(?P<all>all\s+)?(?:peers?|competitors?)\s*(?P<rest>.*)$",
    re.I,
)
_PEERS_ARE = re.compile(r"^\s*(?:my\s+)?(?:peers|competitors)\s+(?:are|=|:)\s*(?P<rest>.+)$", re.I)


def _split_items(rest: str) -> list[str]:
    rest = rest.strip(" :.-")
    if not rest:
        return []
    return [x.strip(" .") for x in re.split(r"[,;\n]", rest) if x.strip(" .")]


def parse_peer_command(text: str) -> dict[str, Any] | None:
    m = _PEERS_ARE.match(text)
    if m:
        return {"add": _split_items(m.group("rest")), "confirm_all": False, "replace": True}
    m = _PEER_CMD.match(text)
    if not m:
        return None
    verb = m.group("verb").lower()
    items = _split_items(m.group("rest"))
    if verb in ("confirm", "approve", "accept"):
        return {"confirm": items, "confirm_all": bool(m.group("all")) or not items}
    if verb in ("reject", "remove", "drop"):
        return {"reject": items} if items else None
    return {"add": items} if items else None


def handle_peer_command(session: ResearchSession, cmd: dict[str, Any]) -> str:
    from iis.finance.competitor_discovery import apply_competitor_discovery, update_peers

    result = update_peers(
        session,
        confirm=cmd.get("confirm") or [],
        reject=cmd.get("reject") or [],
        add=cmd.get("add") or [],
        confirm_all=bool(cmd.get("confirm_all")),
        replace=bool(cmd.get("replace")),
    )
    apply_competitor_discovery(session)
    apply_user_asks(session)
    p = _payload(session, "competitor_suggestions")
    lines = []
    conf = p.get("confirmed") or []
    lines.append("**Confirmed peers:** " + (", ".join(x["name"] for x in conf) if conf else "none yet"))
    if p.get("proposed"):
        lines.append("**Still proposed:** " + ", ".join(f"{i}. {x['name']}" for i, x in enumerate(p["proposed"], 1)))
    if p.get("rejected"):
        lines.append("**Rejected:** " + ", ".join(x if isinstance(x, str) else x["name"] for x in p["rejected"]))
    if p.get("file_asks"):
        lines.append("")
        lines.append("To compare from reliable sources, upload:")
        for a in p["file_asks"]:
            lines.append(f"- **{a['name']}**: " + "; ".join(a["needs"]))
    if p.get("on_file"):
        lines.append("")
        lines.append("Peers with CIQ on file (used in comparison): " + ", ".join(x["name"] for x in p["on_file"]))
        lines.append("Say **rerun analysis** to refresh the comparison.")
    if result.get("unlisted"):
        lines.append("")
        lines.append("Not found in the listed-company universe (kept as you typed — check spelling, or it may be unlisted): "
                     + ", ".join(result["unlisted"]))
    lines.append("")
    lines.append("_Decisions are saved for this company and reused in future research._")
    return "\n".join(lines)


_SHOW_CTX = re.compile(r"^\s*(show|list|what are)\s+(?:my\s+|the\s+)?(context|rules|directives|research rules)\??\s*$", re.I)
_FORGET = re.compile(r"^\s*(?:forget|remove|delete|drop)\s+(?:rule|directive)\s+#?(?P<id>[\w-]+)\s*$", re.I)
_ASKS = re.compile(r"^\s*(what do you need|open asks|pending asks|what('s| is) missing|data gaps|what should i upload)\??\s*$", re.I)


_SCOPE_LABEL = {"global": "all research", "company": "this company", "session": "this discussion"}


def capture_directive(session: ResearchSession, text: str) -> tuple[str, bool] | None:
    """Store an instruction found in a chat message. Returns (note, explicit_only).

    Only *explicit* Remember:/Rule: lines are auto-saved. Cue-only phrases return
    (note, False) without persisting — UI/chat_router asks the user to confirm.
    """
    from iis.context.manager import _EXPLICIT, add_directive, parse_directive

    d = parse_directive(text)
    if not d:
        return None
    scope, body = d
    if scope == "company" and not session.company_key:
        scope = "session"
    explicit = bool(_EXPLICIT.match(text.strip()))
    if not explicit:
        note = (
            f"I can save this for **{_SCOPE_LABEL[scope]}** — confirm below if you want it "
            f"applied every time (or for this analysis)."
        )
        return note, False
    add_directive(session, body, scope=scope)
    note = f"Noted for **{_SCOPE_LABEL[scope]}**: “{body}”. Say `show rules` to review or `forget rule N` to remove."
    return note, True


def handle_context_command(session: ResearchSession, text: str) -> str | None:
    from iis.context.manager import format_directives_markdown, remove_directive

    if _SHOW_CTX.match(text):
        return format_directives_markdown(session)
    m = _FORGET.match(text)
    if m:
        ok = remove_directive(session, m.group("id"))
        return ("Removed. " if ok else "No rule with that number. ") + "\n\n" + format_directives_markdown(session)
    if _ASKS.match(text):
        apply_user_asks(session)
        md = asks_markdown(open_asks(session), session=session)
        return md or "Nothing pending — all requested data is on file."
    return None
