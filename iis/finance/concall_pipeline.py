"""Earnings-call Hive loop: relevance → deep-read → critic → evidence register."""
from __future__ import annotations

import logging
import re
from typing import Any, Callable

from iis.config import settings
from iis.finance.concall_guidelines import SECTION_GUIDELINES
from iis.models.session import ResearchSession

logger = logging.getLogger(__name__)

ProgressCb = Callable[[str], None]

_MODE_ORDER = ("prepared_remarks", "qa", "outlook_guidance", "other_keep")

_MODE_TITLES = {
    "prepared_remarks": "Prepared remarks (LLM)",
    "qa": "Q&A (LLM)",
    "outlook_guidance": "Outlook / guidance (LLM)",
    "other_keep": "Other thesis-relevant call passages (LLM)",
}


def _notify(cb: ProgressCb | None, msg: str) -> None:
    if cb:
        try:
            cb(msg)
        except Exception:
            pass


def _parse_bullets(raw: str) -> list[str]:
    lines: list[str] = []
    for ln in (raw or "").splitlines():
        s = ln.strip()
        if not s:
            continue
        s = re.sub(r"^[-*•]\s+", "", s)
        s = re.sub(r"^\d+[.)]\s+", "", s)
        if len(s) < 8:
            continue
        if s.startswith("#"):
            lines.append(s)
            continue
        lines.append(s)
    return lines[:40]


def _llm_bullets(
    *,
    mode: str,
    company: str,
    file: str,
    label: str,
    text: str,
    timeout: float,
) -> list[str]:
    from iis.context.manager import llm_agent
    from iis.llm.factory import get_llm_client
    from iis.llm.gate import LLMAbortError

    system = SECTION_GUIDELINES.get(mode) or SECTION_GUIDELINES["other_keep"]
    prompt = (
        f"Company: {company}\n"
        f"File: {file}\n"
        f"Unit: {label}\n\n"
        "Summarise the FULL call text below per the system rules. Bullets only.\n\n"
        "=== CALL TEXT ===\n"
        f"{text}\n"
    )
    llm = get_llm_client(finance=True)
    if not llm.is_available():
        raise RuntimeError("llm_unavailable")
    with llm_agent(f"concall_{mode}"):
        try:
            raw = llm.chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.15,
                timeout=timeout,
                max_tokens=int(getattr(settings, "concall_section_max_tokens", 2500) or 2500),
                allow_reasoning_fallback=False,
            )
        except TypeError:
            raw = llm.chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.15,
                timeout=timeout,
            )
        except LLMAbortError:
            raise
    return _parse_bullets(raw or "")


def build_evidence_register(
    *,
    units: list[dict[str, Any]],
    relevance: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for u in units:
        if not isinstance(u, dict):
            continue
        pages = u.get("pages") or []
        page_s = ",".join(str(p) for p in pages[:8])
        fname = str(u.get("file") or "")
        mode = str(u.get("mode") or "")
        for b in u.get("bullets") or []:
            if str(b).startswith("#"):
                continue
            rows.append(
                {
                    "claim": str(b)[:400],
                    "file": fname,
                    "pages": page_s,
                    "mode": mode,
                    "sections": str(u.get("label") or "")[:120],
                    "source": "concall",
                }
            )
    for row in (relevance or {}).get("units") or []:
        if not isinstance(row, dict) or row.get("decision") != "keep":
            continue
        why = (row.get("why") or "").strip()
        if why:
            pages = row.get("pages") or []
            rows.append(
                {
                    "claim": f"[relevance] {why}"[:400],
                    "file": str(row.get("file") or ""),
                    "pages": ",".join(str(p) for p in pages[:8]),
                    "mode": "relevance",
                    "sections": str(row.get("label") or ""),
                    "source": "concall",
                }
            )
    return rows[:200]


def run_concall_pipeline(
    session: ResearchSession,
    *,
    progress_cb: ProgressCb | None = None,
) -> dict[str, Any]:
    """Full concall Hive loop across all transcript units on the session."""
    if not bool(getattr(settings, "concall_structured_llm_enabled", False)):
        return {"ok": False, "skipped": True, "reason": "concall_structured_llm_enabled=false"}

    from iis.finance.concall_critic import run_concall_critic
    from iis.finance.concall_relevance import (
        run_concall_relevance_pass,
        units_to_deep_read,
    )
    from iis.llm.factory import get_llm_client
    from iis.llm.gate import LLMAbortError

    if not get_llm_client(finance=True).is_available():
        return {"ok": False, "error": "llm_unavailable"}

    max_chars = max(8000, int(getattr(settings, "concall_section_max_chars", 48000) or 48000))
    timeout = float(getattr(settings, "concall_section_timeout_sec", 120.0) or 120.0)
    company = session.company.name if session.company else (session.company_key or "Company")

    _notify(progress_cb, "Concall relevance (Hive)…")
    try:
        relevance = run_concall_relevance_pass(session, progress_cb=progress_cb)
    except LLMAbortError:
        raise
    except Exception as exc:
        logger.info("Concall relevance failed: %s", exc)
        relevance = {"ok": False, "error": str(exc)[:300], "units": []}

    to_read = units_to_deep_read(relevance if isinstance(relevance, dict) else {}, include_all_skims=False)
    max_units = max(1, int(getattr(settings, "concall_max_deep_units", 6) or 6))
    if len(to_read) > max_units:
        to_read = to_read[:max_units]
        _notify(progress_cb, f"Concall deep-read capped to top {max_units} units")
    if not to_read:
        return {
            "ok": False,
            "error": "no_concall_units_to_deep_read",
            "relevance": relevance,
            "units": [],
            "evidence_register": [],
        }

    deep_units: list[dict[str, Any]] = []
    critics: list[dict[str, Any]] = []

    for i, row in enumerate(to_read):
        unit_id = str(row.get("unit_id") or f"unit_{i}")
        mode = str(row.get("mode") or "other_keep")
        if mode not in SECTION_GUIDELINES:
            mode = "other_keep"
        label = str(row.get("label") or unit_id)
        fname = str(row.get("file") or "")
        text = (row.get("text") or "")[:max_chars]
        pages = list(row.get("pages") or [])
        if len(text) < 80:
            continue
        _notify(
            progress_cb,
            f"Concall deep-read {i + 1}/{len(to_read)} · {label[:28]} · {len(text):,} chars",
        )
        try:
            bullets = _llm_bullets(
                mode=mode,
                company=company,
                file=fname,
                label=label,
                text=text,
                timeout=timeout,
            )
        except LLMAbortError:
            raise
        except Exception as exc:
            logger.info("Concall deep-read failed %s: %s", unit_id, exc)
            deep_units.append(
                {
                    "unit_id": unit_id,
                    "file": fname,
                    "label": label,
                    "mode": mode,
                    "title": _MODE_TITLES.get(mode, mode),
                    "pages": pages,
                    "bullets": [],
                    "chars_sent": len(text),
                    "error": str(exc)[:240],
                    "relevance_decision": row.get("decision"),
                    "relevance_why": row.get("why"),
                }
            )
            continue

        entry: dict[str, Any] = {
            "unit_id": unit_id,
            "file": fname,
            "label": label,
            "mode": mode,
            "title": _MODE_TITLES.get(mode, mode),
            "pages": pages,
            "bullets": bullets,
            "chars_sent": len(text),
            "relevance_decision": row.get("decision"),
            "relevance_why": row.get("why"),
            "guidance_consistency": None,
        }

        # Critic per unit
        if bool(getattr(settings, "concall_critic_llm_enabled", True)) and bullets:
            try:
                draft = "\n".join(f"- {b}" for b in bullets)
                critic = run_concall_critic(
                    session,
                    company=company,
                    unit_id=unit_id,
                    label=label,
                    source_excerpt=text,
                    draft_bullets=draft,
                    progress_cb=progress_cb,
                )
                critics.append(critic)
                if critic.get("ok"):
                    entry["guidance_consistency"] = critic.get("guidance_consistency")
                    entry["critic_verdict"] = critic.get("verdict")
                    revised = (critic.get("revised_bullets") or "").strip()
                    if critic.get("verdict") == "revise" and revised:
                        entry["bullets"] = _parse_bullets(revised) or bullets
                        entry["critic_revised"] = True
                    elif critic.get("verdict") == "reject":
                        entry["bullets"] = []
                        entry["critic_rejected"] = True
                        entry["critic_issues"] = critic.get("issues") or []
            except LLMAbortError:
                raise
            except Exception as exc:
                logger.info("Concall critic failed %s: %s", unit_id, exc)
                critics.append({"ok": False, "unit_id": unit_id, "error": str(exc)[:240]})

        deep_units.append(entry)

    # Gap-fill: if critic flagged missing themes, run outlook pass on skipped keep-worthy units
    if (
        bool(getattr(settings, "concall_gap_fill_enabled", True))
        and critics
        and any(
            c.get("ok") and (c.get("missing_from_source") or c.get("verdict") == "revise")
            for c in critics
        )
    ):
        skipped = [
            u
            for u in (relevance.get("units") or [])
            if isinstance(u, dict) and u.get("decision") == "skip" and (u.get("text") or "")
        ]
        # Prefer outlook-looking skips for one gap-fill deep-read
        for row in skipped[:2]:
            text = (row.get("text") or "")[:max_chars]
            if len(text) < 120:
                continue
            mode = "outlook_guidance"
            unit_id = str(row.get("unit_id") or "gap")
            label = f"Gap-fill · {row.get('label') or unit_id}"
            _notify(progress_cb, f"Concall gap-fill · {label[:40]}")
            try:
                bullets = _llm_bullets(
                    mode=mode,
                    company=company,
                    file=str(row.get("file") or ""),
                    label=label,
                    text=text,
                    timeout=timeout,
                )
            except LLMAbortError:
                raise
            except Exception as exc:
                logger.info("Concall gap-fill failed: %s", exc)
                continue
            if bullets:
                deep_units.append(
                    {
                        "unit_id": f"{unit_id}::gap",
                        "file": row.get("file"),
                        "label": label,
                        "mode": mode,
                        "title": _MODE_TITLES[mode],
                        "pages": list(row.get("pages") or []),
                        "bullets": bullets,
                        "chars_sent": len(text),
                        "gap_fill": True,
                    }
                )

    evidence = build_evidence_register(units=deep_units, relevance=relevance if isinstance(relevance, dict) else None)

    # Aggregate outlook / themes for guidelines artifact compatibility
    outlook_bits: list[str] = []
    themes: list[str] = []
    key_quotes: list[str] = []
    reflections: list[dict[str, Any]] = []
    for u in deep_units:
        for b in u.get("bullets") or []:
            bs = str(b)
            if bs.startswith("#"):
                continue
            if u.get("mode") in ("outlook_guidance", "prepared_remarks", "qa"):
                if any(k in bs.lower() for k in ("outlook", "guid", "expect", "capex", "margin")):
                    outlook_bits.append(bs[:280])
            themes.append(bs[:120])
            key_quotes.append(bs[:220])
        cons = u.get("guidance_consistency")
        if cons and cons != "n_a":
            reflections.append(
                {
                    "unit_id": u.get("unit_id"),
                    "file": u.get("file"),
                    "assessment": cons,
                    "transcript_evidence": (u.get("bullets") or [""])[0][:240]
                    if u.get("bullets")
                    else "",
                }
            )

    counts = (relevance or {}).get("counts") if isinstance(relevance, dict) else {}
    return {
        "ok": True,
        "relevance": relevance,
        "units": deep_units,
        "critics": critics,
        "evidence_register": evidence,
        "counts": counts,
        "current_outlook": outlook_bits[0] if outlook_bits else "",
        "themes": themes[:12],
        "key_quotes": key_quotes[:10],
        "guidance_reflections": reflections[:10],
        "commentary": (
            f"Concall Hive loop: deep-read {len(deep_units)} units"
            + (
                f" (keep={counts.get('keep', 0)} skim={counts.get('skim', 0)} "
                f"skip={counts.get('skip', 0)})"
                if counts
                else ""
            )
            + "."
        ),
    }


def structured_concall_markdown(payload: dict[str, Any] | None) -> list[str]:
    if not payload or not payload.get("units"):
        return []
    lines: list[str] = ["## Earnings calls — structured Hive read", ""]
    rel = payload.get("relevance") if isinstance(payload.get("relevance"), dict) else None
    if rel and rel.get("counts"):
        c = rel["counts"]
        lines.append(
            f"_Relevance: keep={c.get('keep', 0)} · skim={c.get('skim', 0)} · "
            f"skip={c.get('skip', 0)}_"
        )
        lines.append("")
    # Group by file then mode
    by_file: dict[str, list[dict[str, Any]]] = {}
    for u in payload.get("units") or []:
        if isinstance(u, dict):
            by_file.setdefault(str(u.get("file") or "call"), []).append(u)
    for fname, units in by_file.items():
        lines.append(f"### `{fname}`")
        lines.append("")
        ordered = sorted(
            units,
            key=lambda x: (
                _MODE_ORDER.index(x.get("mode"))
                if x.get("mode") in _MODE_ORDER
                else 99
            ),
        )
        for u in ordered:
            title = u.get("title") or u.get("label") or u.get("mode")
            pages = u.get("pages") or []
            page_s = f" · p.{','.join(map(str, pages[:8]))}" if pages else ""
            lines.append(f"#### {title}{page_s}")
            lines.append("")
            if u.get("guidance_consistency"):
                lines.append(f"_vs CIQ guidance track: {u['guidance_consistency']}_")
                lines.append("")
            if u.get("error") and not u.get("bullets"):
                lines.append(f"_LLM summary failed: {u['error']}_")
                lines.append("")
                continue
            bullets = u.get("bullets") or []
            if not bullets:
                lines.append("_No bullets extracted._")
                lines.append("")
                continue
            for b in bullets:
                if str(b).startswith("#"):
                    lines.append(str(b))
                else:
                    lines.append(f"- {b}")
            lines.append("")
    # Critic rollup
    critics = [c for c in (payload.get("critics") or []) if isinstance(c, dict) and c.get("ok")]
    if critics:
        lines.append("### Independent reviewer (concalls)")
        lines.append("")
        for c in critics:
            lines.append(
                f"- `{c.get('unit_id')}`: **{c.get('verdict')}** "
                f"(guidance: {c.get('guidance_consistency', 'n_a')})"
            )
            for iss in (c.get("issues") or [])[:4]:
                if isinstance(iss, dict):
                    lines.append(
                        f"  - [{iss.get('severity', '?')}] {iss.get('issue', '')}"
                    )
        lines.append("")
    return lines
