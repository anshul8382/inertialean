"""Verifier — every number in LLM output must trace to data Python supplied.

Allowed numbers = numbers in the Python draft + tool results the LLM received + fact store values,
with rounding tolerance and common unit rescaling (mn↔bn↔cr, ratio↔%). Sentences / table rows
containing a number that cannot be traced are removed and listed for audit.
"""
from __future__ import annotations

import bisect
import re
from typing import Any

_NUM_RE = re.compile(r"(?<![A-Za-z0-9_\[])(?:₹\s?)?[-−]?\d[\d,]*(?:\.\d+)?")
_SKIP_BEFORE = re.compile(r"(?:FY|CY|Q|H|p\.|pp\.|page|pages|F|#|Rule|rule|Section|section|Note|note)\s?$")
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z*_(\[“\"])")
_CITE_RE = re.compile(r"\s?\[F(\d+)(?:\s*,\s*F?(\d+))*\]")


def _to_float(raw: str) -> float | None:
    t = raw.replace("₹", "").replace(",", "").replace("−", "-").strip()
    try:
        return float(t)
    except ValueError:
        return None


def extract_numbers(text: str) -> list[tuple[float, str, int]]:
    out: list[tuple[float, str, int]] = []
    for m in _NUM_RE.finditer(text or ""):
        raw = m.group(0)
        start = m.start()
        before = text[max(0, start - 8) : start]
        after = text[m.end() : m.end() + 3]
        if _SKIP_BEFORE.search(before):
            continue
        if re.match(r"\s?(?:FY|Q[1-4])", after):  # 1QFY27
            continue
        v = _to_float(raw)
        if v is None:
            continue
        plain_int = re.fullmatch(r"-?\d+", raw.replace("₹", "").strip()) is not None
        if plain_int and 1990 <= abs(v) <= 2040:
            continue  # years
        if plain_int and abs(v) <= 12:
            continue  # counts, list numbers, months, quarters
        if re.match(r"\d{1,2}[-/]\d{1,2}[-/]\d{2,4}", text[start : start + 10]):
            continue
        out.append((abs(v), raw, start))
    return out


class NumberPool:
    def __init__(self) -> None:
        self._vals: list[float] = []

    def add_value(self, v: Any) -> None:
        try:
            x = abs(float(v))
        except (TypeError, ValueError):
            return
        for s in (x, x / 1000, x / 10, x * 100, x / 100, x * 1000, x * 10, x / 1e5):
            if s > 0:
                self._vals.append(s)

    def add_text(self, text: str) -> None:
        for v, _raw, _pos in extract_numbers(text or ""):
            self.add_value(v)
        for m in re.finditer(r"\d[\d,]*(?:\.\d+)?", text or ""):
            v = _to_float(m.group(0))
            if v is not None:
                self._vals.append(abs(v))

    def freeze(self) -> None:
        self._vals = sorted(set(round(v, 6) for v in self._vals))

    def supports(self, v: float) -> bool:
        if v == 0:
            return True
        tol = max(0.006 * v, 0.051)
        i = bisect.bisect_left(self._vals, v - tol)
        return i < len(self._vals) and self._vals[i] <= v + tol


def build_pool(*, texts: list[str], facts: list[dict[str, Any]] | None = None) -> NumberPool:
    pool = NumberPool()
    for t in texts:
        pool.add_text(t)
    for f in facts or []:
        pool.add_value(f.get("value"))
        pool.add_text(str(f.get("value")))
    pool.freeze()
    return pool


def _unsupported(text: str, pool: NumberPool) -> list[str]:
    return [raw for v, raw, _ in extract_numbers(text) if not pool.supports(v)]


def verify_markdown(
    text: str,
    pool: NumberPool,
    facts: list[dict[str, Any]] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Return (clean markdown, audit dict)."""
    fact_ids = {f["id"]: f for f in facts or []}
    cited: list[str] = []
    bad_cites: list[str] = []

    def _collect_cites(s: str) -> str:
        def repl(m: re.Match) -> str:
            ids = re.findall(r"F?(\d+)", m.group(0))
            for i in ids:
                fid = f"F{i}"
                (cited if fid in fact_ids else bad_cites).append(fid)
            return ""
        return _CITE_RE.sub(repl, s)

    removed: list[dict[str, str]] = []
    kept_lines: list[str] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or re.fullmatch(r"\|?[\s:|-]+\|?", stripped):
            kept_lines.append(line)
            continue
        if stripped.startswith("|"):
            bad = _unsupported(_CITE_RE.sub("", line), pool)
            if bad:
                removed.append({"text": stripped[:300], "numbers": ", ".join(bad[:5])})
                continue
            kept_lines.append(_collect_cites(line))
            continue
        m = re.match(r"^(\s*(?:[-*•]|\d+[.)])\s+)?(.*)$", line)
        prefix, body = (m.group(1) or ""), m.group(2)
        parts = _SENT_SPLIT.split(body)
        keep: list[str] = []
        for sent in parts:
            bad = _unsupported(_CITE_RE.sub("", sent), pool)
            if bad:
                removed.append({"text": sent.strip()[:300], "numbers": ", ".join(bad[:5])})
            else:
                keep.append(_collect_cites(sent))
        if keep:
            kept_lines.append(prefix + " ".join(keep).strip())
    clean = "\n".join(kept_lines)
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    seen: list[str] = []
    for c in cited:
        if c not in seen:
            seen.append(c)
    audit = {
        "removed": removed,
        "removed_count": len(removed),
        "cited_facts": [fact_ids[c] for c in seen],
        "unknown_citations": sorted(set(bad_cites)),
    }
    return clean, audit


def verify_llm_output(
    session: Any,
    text: str,
    evidence: list[str],
    *,
    agent: str,
    facts: list[dict[str, Any]] | None = None,
    footer: bool = True,
) -> str | None:
    """Verify LLM text against evidence + fact store; record audit artifact. None if nothing survives."""
    from iis.config import settings

    if not text or not getattr(settings, "llm_verify_numbers", True):
        return text
    if facts is None:
        try:
            from iis.facts.store import build_fact_store

            facts = build_fact_store(session)
        except Exception:
            facts = []
    pool = build_pool(texts=evidence, facts=facts)
    clean, audit = verify_markdown(text, pool, facts)
    try:
        from iis.models.artifacts import AgentArtifact

        session.agent_artifacts = [
            a for a in session.agent_artifacts
            if not (a.artifact_type == "llm_verification" and a.agent_id == agent)
        ]
        session.agent_artifacts.append(
            AgentArtifact(
                agent_id=agent,
                artifact_type="llm_verification",
                payload={
                    "removed": audit["removed"][:40],
                    "removed_count": audit["removed_count"],
                    "cited": [f["id"] for f in audit["cited_facts"]],
                    "unknown_citations": audit["unknown_citations"],
                },
            )
        )
    except Exception:
        pass
    if len(clean) < 40:
        return None
    tail = audit_footer(audit) if footer else ""
    return clean + ("\n\n" + tail if tail else "")


def audit_footer(audit: dict[str, Any], *, max_sources: int = 25) -> str:
    lines: list[str] = []
    if audit.get("cited_facts"):
        lines.append("**Facts cited** (from Python-collected data):")
        for f in audit["cited_facts"][:max_sources]:
            lines.append(f"- {f['id']}: {f['label']} {f.get('period') or ''} = {f['value']} {f['unit']} — {f['source']}")
    n = audit.get("removed_count") or 0
    if n:
        lines.append(
            f"_Verifier removed {n} statement(s) whose numbers were not found in the collected data._"
        )
    return "\n".join(lines)
