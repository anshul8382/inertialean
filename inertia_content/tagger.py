"""
Content tagger — suggests the 7 adviser-set behavioural tags via Anthropic, with Ollama fallback.

Auto tags (wealth_accum, wealth_preserv, fin_complexity, misaligned_prod) are NOT
sent to the LLM — they are inferred from person profile fields in tags.py.

Only content text is sent to the model. Client/person data never touches this module.
"""

import os
from dataclasses import dataclass

from .models import Tag

ADVISER_TAG_DEFINITIONS = {
    Tag.BEHAV_FRICTION:  "Content addresses inaction, procrastination, deferral, or analysis paralysis",
    Tag.EMOTIONAL_INV:   "Content addresses fear, greed, panic-selling, or market-noise-driven decisions",
    Tag.HIGH_AWARENESS:  "Content is for financially literate people who read widely but may not act",
    Tag.UNDERSERVED:     "Content speaks to people who have a CA/RM/MFD but not a true advisory relationship",
    Tag.LIFE_TRANSITION: "Content is relevant during or after a major life event — job, marriage, child, business exit, NRI move",
    Tag.RELSHIP_DRIFT:   "Content is designed to re-engage someone who has gone quiet or disengaged",
    Tag.HIGH_EARNER:     "Content is specifically relevant to high-income or high-networth individuals",
}

SYSTEM_PROMPT = (
    "You are a content tagging assistant for Inertia Equities, a fee-only SEBI-registered "
    "investment adviser in India focused on long-term equity wealth creation. "
    "Analyse content pieces and assign behavioural tags from a fixed vocabulary. "
    "Return valid JSON only. No markdown, no preamble."
)


@dataclass
class TagSuggestion:
    tags:     dict  # all 11 tag_id -> bool (auto tags always False here)
    reasons:  dict  # tag_id -> one sentence
    raw:      str
    provider: str = "anthropic"


def _build_tag_prompt(content_text: str) -> str:
    tag_list = "\n".join(
        f"- {tid}: {defn}" for tid, defn in ADVISER_TAG_DEFINITIONS.items()
    )
    return f"""Analyse this content piece and assign ONLY these 7 behavioural tags.
Note: wealth/complexity/product tags are handled separately — do not include them.

TAGS TO ASSESS:
{tag_list}

Return ONLY this JSON — no markdown, no extra text:
{{
  "tags": {{
    "behav_friction": true/false,
    "emotional_inv": true/false,
    "high_awareness": true/false,
    "underserved": true/false,
    "life_transition": true/false,
    "relship_drift": true/false,
    "high_earner": true/false
  }},
  "reasons": {{
    "behav_friction": "one sentence",
    "emotional_inv": "one sentence",
    "high_awareness": "one sentence",
    "underserved": "one sentence",
    "life_transition": "one sentence",
    "relship_drift": "one sentence",
    "high_earner": "one sentence"
  }}
}}

CONTENT:
\"\"\"
{content_text[:3000]}
\"\"\""""


def suggest_tags(content_text: str, anthropic_client=None, api_key: str = None) -> TagSuggestion:
    """
    Suggests the 7 adviser-set behavioural tags for a content piece.
    Tries Anthropic first; on failure uses Ollama when LLM_FALLBACK_TO_OLLAMA is enabled.
    """
    from services.llm_fallback_service import (
        LLMFallbackUnavailable,
        extract_json_object,
        generate_text_via_ollama,
        llm_fallback_to_ollama_enabled,
    )

    prompt = _build_tag_prompt(content_text)
    raw = None
    provider = "anthropic"
    cloud_err = None

    key = api_key or os.getenv("ANTHROPIC_API_KEY")
    client = anthropic_client
    if client is None and key:
        import anthropic

        client = anthropic.Anthropic(api_key=key)

    if client is not None:
        try:
            message = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=600,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = message.content[0].text
        except Exception as exc:
            cloud_err = exc

    if raw is None:
        if not llm_fallback_to_ollama_enabled():
            if cloud_err:
                raise cloud_err
            raise RuntimeError("ANTHROPIC_API_KEY not configured and LLM fallback is disabled")

        try:
            raw = generate_text_via_ollama(SYSTEM_PROMPT, prompt, max_tokens=600)
            provider = "ollama"
        except LLMFallbackUnavailable as exc:
            if cloud_err:
                raise cloud_err from exc
            raise

    parsed = extract_json_object(raw)

    tags = {tag: False for tag in Tag.AUTO}
    reasons = {tag: "Auto-inferred from person profile — not assessed here" for tag in Tag.AUTO}

    for tag_id in Tag.ADVISER:
        tags[tag_id] = bool(parsed.get("tags", {}).get(tag_id, False))
        reasons[tag_id] = str(parsed.get("reasons", {}).get(tag_id, ""))

    return TagSuggestion(tags=tags, reasons=reasons, raw=raw, provider=provider)


def apply_suggestions_to_content(content_piece, suggestion: TagSuggestion):
    """Apply adviser tag suggestions to a ContentPiece (adviser should review before save)."""
    content_piece.tag_behav_friction = suggestion.tags.get(Tag.BEHAV_FRICTION, False)
    content_piece.tag_emotional_inv = suggestion.tags.get(Tag.EMOTIONAL_INV, False)
    content_piece.tag_high_awareness = suggestion.tags.get(Tag.HIGH_AWARENESS, False)
    content_piece.tag_underserved = suggestion.tags.get(Tag.UNDERSERVED, False)
    content_piece.tag_life_transition = suggestion.tags.get(Tag.LIFE_TRANSITION, False)
    content_piece.tag_relship_drift = suggestion.tags.get(Tag.RELSHIP_DRIFT, False)
    content_piece.tag_high_earner = suggestion.tags.get(Tag.HIGH_EARNER, False)
    return content_piece
