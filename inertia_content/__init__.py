from .models import (
    Person, ContentPiece, SendLog, MatchResult,
    Tag, Category, AgeBracket, AssetBracket, SurplusBracket,
    LifeEvent, Milestone, ContentFormat, Decision, Engagement,
    TagSuggestionResult, Confidence,
)
from .tags import (
    infer_auto_tags, get_active_tags, get_adviser_signals,
    suggest_adviser_tags, apply_confirmed_tags,
)
from .matcher import (
    rank_persons_for_content,
    find_content_for_person,
    match_person_to_content,
    format_match_results,
)
from .tagger import suggest_tags, apply_suggestions_to_content

__all__ = [
    "Person", "ContentPiece", "SendLog", "MatchResult",
    "Tag", "Category", "AgeBracket", "AssetBracket", "SurplusBracket",
    "LifeEvent", "Milestone", "ContentFormat", "Decision", "Engagement",
    "TagSuggestionResult", "Confidence",
    "infer_auto_tags", "get_active_tags", "get_adviser_signals",
    "suggest_adviser_tags", "apply_confirmed_tags",
    "rank_persons_for_content", "find_content_for_person",
    "match_person_to_content", "format_match_results",
    "Database",
    "suggest_tags", "apply_suggestions_to_content",
]


def __getattr__(name):
    if name == "Database":
        from .storage import Database
        return Database
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
