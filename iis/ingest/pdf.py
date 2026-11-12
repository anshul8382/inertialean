from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

from iis.config import settings
from iis.models.artifacts import PdfChunk, PdfDocument, TranscriptQA
from iis.models.enums import PdfDocType

CLASSIFICATION_KEYWORDS: dict[PdfDocType, list[str]] = {
    PdfDocType.TRANSCRIPT: [
        "concall", "conference call", "earnings call", "transcript",
        "question and answer", "q&a session", "analyst meet",
    ],
    PdfDocType.EARNINGS_PRESENTATION: [
        "investor presentation", "earnings presentation", "results presentation",
        "quarterly presentation", "analyst presentation",
    ],
    PdfDocType.ANNUAL_REPORT: [
        "annual report", "directors report", "directors' report",
        "financial statements", "balance sheet", "standalone", "consolidated",
        "management discussion", "corporate governance",
    ],
}

# Section headers commonly found in Indian annual reports
ANNUAL_REPORT_SECTION_PATTERNS: list[tuple[str, str]] = [
    (r"management discussion and analysis", "MD&A"),
    (r"directors[\']? report|board[\']?s report", "Directors Report"),
    (r"chairman[\']?s message|chairperson[\']?s message|message from the chairman", "Chairman's Message"),
    (
        r"ceo[\']?s message|letter from the managing director|md[\']?s message|"
        r"letter to shareholders|from the desk of",
        "CEO Message",
    ),
    (r"business overview|about the company|company overview", "Business Overview"),
    (r"risk management|risk factors|principal risks", "Risk Factors"),
    (r"corporate governance|report on corporate governance", "Corporate Governance"),
    (r"financial capital|capital allocation", "Financial Capital"),
    (r"segment|business segment|operating segment", "Segments"),
    (r"competition|competitive|industry structure", "Competition"),
    (
        r"future outlook|outlook|future prospects|way forward|looking ahead",
        "Outlook",
    ),
    (r"\bswot\b|strengths?\s*(?:and|&|/)\s*weaknesses|strengths?\s*[,:]?\s*weaknesses?\s*[,:]?\s*opportunit", "SWOT"),
    (r"human resources|employees", "HR"),
    (r"financial statements|standalone financial|consolidated financial", "Financial Statements"),
    (r"statement of profit and loss|income statement|profit & loss", "Income Statement"),
    (r"balance sheet|statement of financial position", "Balance Sheet"),
    (r"cash flow statement|statement of cash flows", "Cash Flow"),
    (r"notes to (?:the )?financial|notes to accounts", "Notes to Accounts"),
    (r"related party", "Related Party"),
]

SECTION_HINTS = [
    ("management discussion", "MD&A"),
    ("md&a", "MD&A"),
    ("directors report", "Directors Report"),
    ("chairman", "Chairman's Message"),
    ("chief executive", "CEO Message"),
    ("question and answer", "Q&A"),
    ("q&a", "Q&A"),
    ("concall", "Concall"),
    ("guidance", "Guidance"),
    ("outlook", "Outlook"),
    ("competition", "Competition"),
    ("competitive", "Competition"),
    ("industry overview", "Industry"),
    ("risk factor", "Risk Factors"),
    ("store expansion", "Operations"),
    ("capex", "Capex"),
    ("business overview", "Business Overview"),
    ("segment information", "Segments"),
    ("swot", "SWOT"),
    ("strengths and weaknesses", "SWOT"),
    ("strengths, weaknesses", "SWOT"),
]


@dataclass
class PdfIngestResult:
    document: PdfDocument
    chunks: list[PdfChunk]


def ingest_pdf(path: str | Path) -> PdfIngestResult:
    path = Path(path)
    reader = PdfReader(str(path))
    page_texts: list[tuple[int, str]] = []

    for page_num, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        page_texts.append((page_num, text))

    full_sample = "\n".join(t for _, t in page_texts[:5])
    step = max(1, len(page_texts) // 25)
    spread = "\n".join(t[:1500] for _, t in page_texts[5::step])
    doc_type = classify_pdf(path.name, full_sample, page_count=len(page_texts), spread_sample=spread)

    if doc_type == PdfDocType.ANNUAL_REPORT:
        chunks, sections_found = _chunk_annual_report(path.name, page_texts)
    else:
        chunks, sections_found = _chunk_standard(path.name, page_texts, doc_type)

    qa_pairs: list[TranscriptQA] = []
    if doc_type in (PdfDocType.TRANSCRIPT, PdfDocType.EARNINGS_PRESENTATION):
        qa_pairs = extract_qa_pairs(path.name, page_texts)

    document = PdfDocument(
        file=path.name,
        doc_type=doc_type.value,
        page_count=len(reader.pages),
        chunk_count=len(chunks),
        qa_pairs=qa_pairs,
        sections_found=sorted(sections_found),
    )
    return PdfIngestResult(document=document, chunks=chunks)


def _chunk_standard(
    filename: str,
    page_texts: list[tuple[int, str]],
    doc_type: PdfDocType,
) -> tuple[list[PdfChunk], set[str]]:
    sections_found: set[str] = set()
    chunks: list[PdfChunk] = []
    idx = 0

    for page_num, text in page_texts:
        if not text:
            continue
        section = _guess_section(text[:800])
        if section:
            sections_found.add(section)
        size = settings.max_pdf_chunk_chars
        for i in range(0, len(text), size):
            chunks.append(
                PdfChunk(
                    file=filename,
                    page=page_num,
                    text=text[i : i + size],
                    section_hint=section if i == 0 else None,
                    doc_type=doc_type.value,
                    chunk_index=idx,
                )
            )
            idx += 1

    return chunks, sections_found


def _chunk_annual_report(
    filename: str,
    page_texts: list[tuple[int, str]],
) -> tuple[list[PdfChunk], set[str]]:
    """Section-aware chunking with overlap for thorough annual report reading."""
    sections_found: set[str] = set()
    chunks: list[PdfChunk] = []
    idx = 0

    chunk_size = settings.annual_report_chunk_chars
    overlap = settings.annual_report_chunk_overlap

    current_section = "Preamble"
    current_section_title = "Preamble"

    for page_num, text in page_texts:
        if not text:
            continue

        # Detect section breaks within page
        detected = _detect_section_title(text[:1200])
        if detected:
            current_section = detected
            current_section_title = detected
            sections_found.add(detected)

        page_section = _guess_section(text[:800]) or current_section
        if page_section:
            sections_found.add(page_section)

        # Split page into overlapping chunks — do not break mid-sentence where possible
        start = 0
        while start < len(text):
            end = min(start + chunk_size, len(text))
            if end < len(text):
                # Try to break at sentence boundary
                boundary = text.rfind(". ", start, end)
                if boundary > start + chunk_size // 2:
                    end = boundary + 1

            chunk_text = text[start:end].strip()
            if chunk_text:
                chunks.append(
                    PdfChunk(
                        file=filename,
                        page=page_num,
                        text=chunk_text,
                        section_hint=page_section,
                        section_title=current_section_title,
                        doc_type=PdfDocType.ANNUAL_REPORT.value,
                        chunk_index=idx,
                    )
                )
                idx += 1

            if end >= len(text):
                break
            start = max(start + 1, end - overlap)

    return chunks, sections_found


def _detect_section_title(text: str) -> str | None:
    lower = text.lower()
    for pattern, label in ANNUAL_REPORT_SECTION_PATTERNS:
        if re.search(pattern, lower[:800]):
            return label
    # ALL CAPS header lines (common in Indian ARs)
    for line in text.split("\n")[:15]:
        line = line.strip()
        if 5 < len(line) < 80 and line.isupper() and " " in line:
            return line.title()[:60]
    return None


_BROKER_FILE_RE = re.compile(r"^tg_\d+_", re.I)


_CALL_CUES = re.compile(r"(?i)\bmoderator\b|next question|comes? from the line of|please go ahead")
_AR_CUES = re.compile(
    r"(?i)board'?s report|directors'? report|independent auditor|notes to (?:the )?(?:standalone|consolidated)|"
    r"annual report|corporate governance report|management discussion and analysis|business responsibility"
)


def classify_pdf(
    filename: str,
    text_sample: str,
    *,
    page_count: int | None = None,
    spread_sample: str = "",
) -> PdfDocType:
    if _BROKER_FILE_RE.match(Path(filename).name):
        return PdfDocType.BROKERAGE_REPORT
    combined = (filename + " " + text_sample[:8000]).lower()
    scores: dict[PdfDocType, int] = {t: 0 for t in PdfDocType if t != PdfDocType.UNKNOWN}
    for doc_type, keywords in CLASSIFICATION_KEYWORDS.items():
        for kw in keywords:
            if kw in combined:
                scores[doc_type] += combined.count(kw) + 1
    best = max(scores, key=scores.get) if scores and max(scores.values()) > 0 else PdfDocType.UNKNOWN
    # Long documents: concalls run 15–60 pages; a 100+ page filing without call cues is an AR.
    if page_count and page_count > 80 and best in (PdfDocType.TRANSCRIPT, PdfDocType.UNKNOWN):
        body = text_sample + "\n" + spread_sample
        if len(_CALL_CUES.findall(body)) < 3:
            return PdfDocType.ANNUAL_REPORT if len(_AR_CUES.findall(body)) >= 2 or page_count > 150 else PdfDocType.UNKNOWN
    return best


_MODERATOR_NAMES = re.compile(
    r"(?i)^(moderator|operator|host|chorus\s*call|lipsy|lizy|margaret|caroline)\b"
)
_MGMT_HINT = re.compile(
    r"(?i)\b("
    r"managing\s+director|executive\s+director|whole[\s-]?time|"
    r"chief\s+executive|chief\s+financial|ceo|cfo|cto|coo|"
    r"chairman|promoter|management|investor\s+relations|\bir\b"
    r")\b"
)
_ANALYST_FIRM = re.compile(
    r"(?i)\b("
    r"anand\s+rathi|motilal|icici|hdfc|kotak|axis|edelweiss|emkay|"
    r"jm\s+financial|iifl|nomura|goldman|morgan\s+stanley|jp\s*morgan|"
    r"clsa|macquarie|bernstein|dam\s+capital|nuvama|systematix|"
    r"securities|capital|research|institutional"
    r")\b"
)
_HANDOFF = re.compile(
    r"(?i)(?:next\s+question|question\s+is|comes?\s+from\s+the\s+line\s+of|"
    r"we\s+have\s+(?:the\s+)?(?:next\s+)?question|"
    r"please\s+go\s+ahead|over\s+to\s+you)"
)
_SPEAKER_TURN = re.compile(
    r"(?m)^[\s\u2022]*([A-Z][A-Za-z0-9][A-Za-z0-9 .'\-]{0,55})\s*[:\-–]\s*(.+?)(?=^[\s\u2022]*[A-Z][A-Za-z0-9][A-Za-z0-9 .'\-]{0,55}\s*[:\-–]\s*|\Z)",
    re.DOTALL,
)


def _strip_moderator_boilerplate(text: str) -> str:
    """Remove handoff / 'please go ahead' / firm intros from analyst lines."""
    t = text or ""
    # Drop leading handoff fragments
    t = re.sub(
        r"(?is)^(?:.*?(?:comes?\s+from\s+the\s+line\s+of|next\s+question\s+is\s+from|"
        r"question\s+from\s+the\s+line\s+of)\s+[^.]{0,80}\.\s*)+"
        r"(?:please\s+go\s+ahead[.\s]*)*",
        "",
        t,
        count=1,
    )
    t = re.sub(r"(?i)^please\s+go\s+ahead[.\s]*", "", t)
    # "Name: actual question" after handoff left in same blob
    m = re.search(
        r"(?i)\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3})\s*:\s*(sir[,.]?\s+|hi[,.]?\s+|hello[,.]?\s+|my\s+question|firstly|first\b|thanks?\b)",
        t,
    )
    if m:
        t = t[m.start() :]
        # Prefer text after "Name:"
        colon = t.find(":")
        if 0 < colon < 60:
            t = t[colon + 1 :]
    return _clean(t)


def _overlap_ratio(a: str, b: str) -> float:
    """How much of the shorter string appears inside the longer (token Jaccard-ish)."""
    ta = {w for w in re.findall(r"[a-z0-9]{3,}", (a or "").lower())}
    tb = {w for w in re.findall(r"[a-z0-9]{3,}", (b or "").lower())}
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    return inter / max(1, min(len(ta), len(tb)))


def _is_usable_qa(question: str, answer: str) -> bool:
    q, a = _clean(question), _clean(answer)
    if len(q) < 20 or len(a) < 25:
        return False
    # Moderator debris
    if re.match(r"(?i)^(comes?\s+from\s+the\s+line|please\s+go\s+ahead|the\s+next\s+question)\b", q):
        return False
    if re.match(r"(?i)^(comes?\s+from\s+the\s+line|please\s+go\s+ahead|with\s+\w+\s+\w+\.\s*please)\b", a):
        return False
    # Answer is mostly a repeat of the question (the bug you hit)
    if _overlap_ratio(q, a) >= 0.72 and len(a) < len(q) * 1.35:
        return False
    # Answer starts by repeating the first 40+ chars of the question
    q0, a0 = q[:50].lower(), a[:50].lower()
    if len(q0) > 25 and (q0 in a.lower()[:120] or a0 in q.lower()[:120]):
        # allow if answer continues with substantial unique content
        if _overlap_ratio(q, a) > 0.55:
            return False
    return True


def _classify_speaker(name: str, text: str, pending_analyst: str | None) -> str:
    n = (name or "").strip()
    if _MODERATOR_NAMES.search(n):
        return "moderator"
    if _MGMT_HINT.search(n) or _MGMT_HINT.search(text[:120]):
        return "management"
    if pending_analyst and _clean(n).lower() == _clean(pending_analyst).lower():
        return "analyst"
    if _ANALYST_FIRM.search(text[:180]) or _ANALYST_FIRM.search(n):
        return "analyst"
    # Short thank-you / follow-up after mgmt often still analyst
    if re.match(r"(?i)^(sir|thanks?|thank\s+you|okay|ok|got\s+it|understood|my\s+(?:first|second|next)\s+question)\b", text.strip()):
        return "analyst"
    return "unknown"


def _extract_qa_speaker_turns(filename: str, page_texts: list[tuple[int, str]]) -> list[TranscriptQA]:
    """Indian IR / Chorus Call style: Moderator → Analyst Name: Q → Management: A."""
    pairs: list[TranscriptQA] = []
    # Keep page map by character offset
    parts: list[str] = []
    page_at: list[tuple[int, int]] = []  # (start_offset, page)
    offset = 0
    for page_num, text in page_texts:
        page_at.append((offset, page_num))
        chunk = (text or "").replace("\r\n", "\n") + "\n"
        parts.append(chunk)
        offset += len(chunk)
    full = "".join(parts)

    def page_for(pos: int) -> int:
        pg = page_at[0][1] if page_at else 1
        for off, p in page_at:
            if off <= pos:
                pg = p
            else:
                break
        return pg

    turns: list[tuple[int, str, str]] = []  # pos, speaker, text
    for m in _SPEAKER_TURN.finditer(full):
        speaker, body = m.group(1).strip(), _clean(m.group(2))
        if len(body) < 8:
            continue
        turns.append((m.start(), speaker, body))

    pending_analyst: str | None = None
    i = 0
    while i < len(turns):
        pos, speaker, body = turns[i]
        role = _classify_speaker(speaker, body, pending_analyst)

        if role == "moderator" or (role == "unknown" and _HANDOFF.search(body)):
            # "from the line of Name with Firm. Please go ahead."
            mname = re.search(
                r"(?i)(?:line\s+of|question\s+from(?:\s+the\s+line\s+of)?)\s+"
                r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3})"
                r"(?:\s+(?:with|from|of)\s+)?",
                body,
            )
            if mname:
                pending_analyst = mname.group(1).strip()
            i += 1
            continue

        if role in ("analyst", "unknown") and (
            pending_analyst
            or role == "analyst"
            or re.match(r"(?i)^(sir|hi|hello|thanks?|my\s+question|firstly|first\b)", body)
        ):
            # Collect consecutive analyst turns as one question block
            q_parts = [_strip_moderator_boilerplate(body)]
            q_page = page_for(pos)
            if pending_analyst and _clean(speaker).lower() == _clean(pending_analyst).lower():
                pass
            elif role == "unknown" and not pending_analyst:
                # skip unknown without handoff — likely management opener
                if not re.match(r"(?i)^(sir|hi|my\s+question|firstly|first\b|thanks?)", body):
                    i += 1
                    continue
            j = i + 1
            while j < len(turns):
                p2, s2, b2 = turns[j]
                r2 = _classify_speaker(s2, b2, pending_analyst or speaker)
                if r2 == "moderator":
                    break
                if r2 == "management":
                    break
                if r2 == "analyst" or (
                    pending_analyst and _clean(s2).lower() == _clean(pending_analyst).lower()
                ):
                    q_parts.append(_strip_moderator_boilerplate(b2))
                    j += 1
                    continue
                break
            # Management answer = following management / unknown substantive turns
            a_parts: list[str] = []
            k = j
            while k < len(turns):
                p3, s3, b3 = turns[k]
                r3 = _classify_speaker(s3, b3, None)
                if r3 == "moderator" or _HANDOFF.search(b3):
                    break
                if r3 == "analyst":
                    break
                if r3 == "management" or (
                    r3 == "unknown"
                    and len(b3) > 40
                    and not re.match(r"(?i)^(sir|thanks?|okay|my\s+)", b3)
                ):
                    a_parts.append(b3)
                    k += 1
                    # allow multi-part mgmt answer until analyst returns
                    continue
                break

            q = _clean(" ".join(x for x in q_parts if x))
            a = _clean(" ".join(a_parts))
            if _is_usable_qa(q, a):
                pairs.append(
                    # Keep full Q / full management answer — LLM can trim later
                    TranscriptQA(file=filename, page=q_page, question=q[:4000], answer=a[:16000])
                )
            pending_analyst = None
            i = max(k, j, i + 1)
            continue

        i += 1

    return pairs


def _extract_qa_labeled(filename: str, page_texts: list[tuple[int, str]]) -> list[TranscriptQA]:
    """Classic 'Q:' / 'A:' labels — only when both sides look like real Q&A."""
    pairs: list[TranscriptQA] = []
    for page_num, text in page_texts:
        # Split only on line-start Q: / Question: (avoid mid-line noise)
        blocks = re.split(r"(?m)(?=^\s*(?:Q[\.\:]|Question\s*[:\.]))", text or "")
        for block in blocks:
            q_match = re.match(
                r"(?is)^\s*(?:Q[\.\:]\s*|Question\s*[:\.]\s*)(.+?)"
                r"(?=^\s*(?:A[\.\:]|Answer\s*[:\.])|\Z)",
                block,
                re.MULTILINE,
            )
            a_match = re.search(
                r"(?im)^\s*(?:A[\.\:]\s*|Answer\s*[:\.]\s*)(.+)",
                block,
            )
            if not (q_match and a_match):
                continue
            q = _strip_moderator_boilerplate(q_match.group(1))
            a = _clean(a_match.group(1))
            # Cut answer at next Q if present
            a = re.split(r"(?im)^\s*(?:Q[\.\:]|Question\s*[:\.])", a)[0]
            a = _clean(a)
            if _is_usable_qa(q, a):
                pairs.append(
                    TranscriptQA(file=filename, page=page_num, question=q[:4000], answer=a[:16000])
                )
    return pairs


def sanitize_qa_pairs(pairs: list[TranscriptQA]) -> list[TranscriptQA]:
    """Drop / repair bad pairs (safe to run on already-stored session data)."""
    out: list[TranscriptQA] = []
    seen: set[str] = set()
    for p in pairs or []:
        q = _strip_moderator_boilerplate(p.question or "")
        a = _clean(p.answer or "")
        # If answer embeds the question handoff, try to peel mgmt reply after last repeat
        if q and a and _overlap_ratio(q, a) > 0.5:
            # Prefer the longer unique suffix of answer
            for needle in (q[:80], "please go ahead"):
                idx = a.lower().find(needle.lower())
                if idx >= 0:
                    tail = _clean(a[idx + len(needle) :])
                    if len(tail) > len(a) * 0.4 and len(tail) >= 40:
                        a = tail
        if not _is_usable_qa(q, a):
            continue
        key = re.sub(r"\s+", " ", q[:100].lower())
        # Also dedupe near-identical answers
        akey = re.sub(r"\s+", " ", a[:100].lower())
        if key in seen or akey in seen:
            continue
        seen.add(key)
        seen.add(akey)
        out.append(
            TranscriptQA(file=p.file, page=p.page, question=q[:4000], answer=a[:16000])
        )
    return out[:100]


def extract_qa_pairs(filename: str, page_texts: list[tuple[int, str]]) -> list[TranscriptQA]:
    """Extract analyst Q → management A from earnings call transcripts.

    Prefers speaker-turn parsing (Indian Chorus Call / IR style). Falls back to
    labeled Q:/A:. Always strips moderator handoffs and rejects repeated Q-as-A.
    """
    speaker = _extract_qa_speaker_turns(filename, page_texts)
    labeled = _extract_qa_labeled(filename, page_texts)
    # Prefer speaker turns when they found anything usable
    merged = speaker + labeled if speaker else labeled
    if not merged and labeled:
        merged = labeled
    return sanitize_qa_pairs(merged)


def _guess_section(text: str) -> str | None:
    lower = text.lower()
    for needle, label in SECTION_HINTS:
        if needle in lower:
            return label
    return None


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def get_annual_report_sections(chunks: list[PdfChunk], filename: str) -> dict[str, list[PdfChunk]]:
    """Group annual report chunks by section for careful reading."""
    ar_chunks = [c for c in chunks if c.file == filename and c.doc_type == "annual_report"]
    by_section: dict[str, list[PdfChunk]] = {}
    for c in ar_chunks:
        key = c.section_title or c.section_hint or "Other"
        by_section.setdefault(key, []).append(c)
    for key in by_section:
        by_section[key].sort(key=lambda x: (x.page, x.chunk_index))
    return by_section


def search_pdf_chunks(
    chunks: list[PdfChunk],
    query: str,
    limit: int = 5,
    *,
    doc_types: list[str] | None = None,
    section_hints: list[str] | None = None,
) -> list[PdfChunk]:
    pool = chunks
    if doc_types:
        pool = [c for c in pool if c.doc_type in doc_types]
    if section_hints:
        typed = [
            c for c in pool
            if c.section_hint in section_hints or c.section_title in section_hints
        ]
        if typed:
            pool = typed

    query_terms = [t.lower() for t in query.split() if len(t) > 2]
    if not query_terms:
        return pool[:limit]

    scored: list[tuple[int, PdfChunk]] = []
    for chunk in pool:
        text_lower = chunk.text.lower()
        score = sum(text_lower.count(term) for term in query_terms)
        if score > 0:
            scored.append((score, chunk))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in scored[:limit]]


def search_transcript_qa(
    documents: list[PdfDocument],
    query: str,
    limit: int = 5,
) -> list[TranscriptQA]:
    terms = [t.lower() for t in query.split() if len(t) > 2]
    results: list[tuple[int, TranscriptQA]] = []
    for doc in documents:
        for qa in doc.qa_pairs:
            text = (qa.question + " " + qa.answer).lower()
            score = sum(text.count(t) for t in terms)
            if score > 0:
                results.append((score, qa))
    results.sort(key=lambda x: x[0], reverse=True)
    return [qa for _, qa in results[:limit]]


def extract_pdf_chunks(path: str | Path) -> list[PdfChunk]:
    return ingest_pdf(path).chunks
