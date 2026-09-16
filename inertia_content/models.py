from dataclasses import dataclass, field
from typing import Optional
from datetime import date


# ── Tag constants ────────────────────────────────────────────────────────────
class Tag:
    WEALTH_ACCUM     = "wealth_accum"
    WEALTH_PRESERV   = "wealth_preserv"
    FIN_COMPLEXITY   = "fin_complexity"
    MISALIGNED_PROD  = "misaligned_prod"
    BEHAV_FRICTION   = "behav_friction"
    EMOTIONAL_INV    = "emotional_inv"
    HIGH_AWARENESS   = "high_awareness"
    UNDERSERVED      = "underserved"
    LIFE_TRANSITION  = "life_transition"
    RELSHIP_DRIFT    = "relship_drift"
    HIGH_EARNER      = "high_earner"

    ALL = [
        WEALTH_ACCUM, WEALTH_PRESERV, FIN_COMPLEXITY, MISALIGNED_PROD,
        BEHAV_FRICTION, EMOTIONAL_INV, HIGH_AWARENESS, UNDERSERVED,
        LIFE_TRANSITION, RELSHIP_DRIFT, HIGH_EARNER,
    ]

    AUTO    = [WEALTH_ACCUM, WEALTH_PRESERV, FIN_COMPLEXITY, MISALIGNED_PROD]
    ADVISER = [BEHAV_FRICTION, EMOTIONAL_INV, HIGH_AWARENESS, UNDERSERVED,
               LIFE_TRANSITION, RELSHIP_DRIFT, HIGH_EARNER]

    LABELS = {
        WEALTH_ACCUM:    "Wealth Accumulation",
        WEALTH_PRESERV:  "Wealth Preservation",
        FIN_COMPLEXITY:  "Financial Complexity",
        MISALIGNED_PROD: "Misaligned Products",
        BEHAV_FRICTION:  "Behavioural Friction",
        EMOTIONAL_INV:   "Emotional Investor",
        HIGH_AWARENESS:  "High Awareness",
        UNDERSERVED:     "Underserved Setup",
        LIFE_TRANSITION: "Life Transition",
        RELSHIP_DRIFT:   "Relationship Drift",
        HIGH_EARNER:     "High Earner",
    }


# ── Enums as string constants (avoids enum import complexity) ────────────────
class Category:
    CLIENT          = "Client"
    LEAD            = "Lead"
    PROSPECT        = "Prospect"
    INACTIVE_CLIENT = "Inactive Client"
    ALL = [CLIENT, LEAD, PROSPECT, INACTIVE_CLIENT]

class AgeBracket:
    A25_34 = "25-34"
    A35_44 = "35-44"
    A45_54 = "45-54"
    A55_64 = "55-64"
    A65P   = "65+"
    ALL = [A25_34, A35_44, A45_54, A55_64, A65P]

class AssetBracket:
    LT10L    = "<10L"
    L10_25   = "10L-25L"
    L25_50   = "25L-50L"
    L50_1CR  = "50L-1Cr"
    L1_2CR   = "1Cr-2Cr"
    GT2CR    = "2Cr+"
    ALL = [LT10L, L10_25, L25_50, L50_1CR, L1_2CR, GT2CR]

class SurplusBracket:
    LT25K  = "<25K"
    K25_50 = "25K-50K"
    K50_1L = "50K-1L/mo"
    L1_2   = "1L-2L/mo"
    L2_3   = "2L-3L/mo"
    GT3L   = "3L+/mo"
    ALL = [LT25K, K25_50, K50_1L, L1_2, L2_3, GT3L]

class LifeEvent:
    NONE           = "None"
    NEW_JOB        = "New job / promotion"
    MARRIAGE       = "Marriage"
    CHILD          = "Child birth"
    DIVORCE        = "Divorce / separation"
    INHERITANCE    = "Inheritance"
    BUSINESS_EXIT  = "Business exit / ESOP"
    NRI_RETURN     = "NRI return / relocation"
    PROPERTY       = "Property purchase"
    RETIREMENT     = "Retirement"
    OTHER          = "Other"
    ALL = [NONE, NEW_JOB, MARRIAGE, CHILD, DIVORCE, INHERITANCE,
           BUSINESS_EXIT, NRI_RETURN, PROPERTY, RETIREMENT, OTHER]

class Milestone:
    NONE           = "None"
    RETIREMENT_5   = "Retirement in <5yr"
    CHILD_EDU      = "Child education"
    BIZ_TRANSITION = "Business transition"
    PROPERTY_SALE  = "Property sale"
    NRI_MOVE       = "NRI move"
    ESTATE         = "Estate planning"
    OTHER          = "Other"
    ALL = [NONE, RETIREMENT_5, CHILD_EDU, BIZ_TRANSITION,
           PROPERTY_SALE, NRI_MOVE, ESTATE, OTHER]

class ContentFormat:
    LINKEDIN    = "LinkedIn Post"
    YOUTUBE     = "YouTube Video"
    EMAIL       = "Email"
    WHATSAPP    = "WhatsApp Note"
    INSTAGRAM   = "Instagram"
    BLOG        = "Blog Post"
    REEL        = "Reel / Short"
    ALL = [LINKEDIN, YOUTUBE, EMAIL, WHATSAPP, INSTAGRAM, BLOG, REEL]

class Decision:
    SEND = "Send"
    SKIP = "Skip"
    HOLD = "Hold"
    ALL = [SEND, SKIP, HOLD]

class Engagement:
    RESPONDED = "Responded"
    OPENED    = "Opened"
    NONE      = "None"
    ALL = [RESPONDED, OPENED, NONE]


# ── Person ───────────────────────────────────────────────────────────────────
@dataclass
class Person:
    # Identity
    id:               Optional[int]  = None
    name:             str            = ""

    # Profile
    category:         str            = Category.PROSPECT   # Category.*
    profession:       str            = ""
    age_bracket:      str            = ""                  # AgeBracket.*
    gender:           str            = ""
    city:             str            = ""
    is_nri:           bool           = False
    onboarded_date:   Optional[date] = None

    # Products held (separate flags)
    holds_mf:         bool           = False
    holds_stock:      bool           = False
    holds_lic:        bool           = False
    holds_ulip:       bool           = False
    holds_fd:         bool           = False   # FD as investment vehicle
    holds_real_estate:bool           = False
    holds_pms_aif:    bool           = False

    # Financial setup
    investable_assets: str           = ""      # AssetBracket.*
    monthly_surplus:   str           = ""      # SurplusBracket.*
    has_ca_rm_mfd:     bool          = False
    prior_adviser:     bool          = False

    # Life & transitions
    recent_life_event: str           = LifeEvent.NONE
    is_business_owner: bool          = False
    upcoming_milestone:str           = Milestone.NONE
    is_cross_border:   bool          = False

    # Adviser-set tags (behavioural — cannot be inferred by formula)
    tag_behav_friction: bool         = False
    tag_emotional_inv:  bool         = False
    tag_high_awareness: bool         = False
    tag_underserved:    bool         = False
    tag_life_transition:bool         = False
    tag_relship_drift:  bool         = False
    tag_high_earner:    bool         = False

    # Questionnaire fields (annual profile — feed suggest_adviser_tags)
    has_deferred_decision:   bool = False
    misses_review_calls:     bool = False
    reacted_to_market_event: bool = False
    driven_by_market_news:   bool = False
    reads_financial_content: bool = False
    uses_diy_platforms:      bool = False
    ca_manages_investments:  bool = False
    is_senior_professional:  bool = False

    # Engagement
    last_contacted:    Optional[date] = None
    last_engaged:      bool           = False
    engagement_score:  int            = 0      # 1-5
    notes:             str            = ""

    # Frequency
    max_msgs_per_month: int           = 2


@dataclass
class TagSuggestionResult:
    """Rule-based suggestion for a single adviser tag (questionnaire UI)."""
    tag:        str
    suggested:  bool
    confidence: str
    signals:    list
    reason:     str


class Confidence:
    HIGH   = "high"
    MEDIUM = "medium"
    LOW    = "low"


# ── ContentPiece ─────────────────────────────────────────────────────────────
@dataclass
class ContentPiece:
    id:             Optional[int] = None
    title:          str           = ""
    format:         str           = ContentFormat.EMAIL   # ContentFormat.*
    created_date:   Optional[date]= None
    topic_category: str           = ""
    notes:          str           = ""
    campaign_studio_campaign_id: Optional[int] = None

    # Tags — all 11, stored as booleans
    tag_wealth_accum:    bool = False
    tag_wealth_preserv:  bool = False
    tag_fin_complexity:  bool = False
    tag_misaligned_prod: bool = False
    tag_behav_friction:  bool = False
    tag_emotional_inv:   bool = False
    tag_high_awareness:  bool = False
    tag_underserved:     bool = False
    tag_life_transition: bool = False
    tag_relship_drift:   bool = False
    tag_high_earner:     bool = False

    def tag_count(self) -> int:
        return sum([
            self.tag_wealth_accum, self.tag_wealth_preserv,
            self.tag_fin_complexity, self.tag_misaligned_prod,
            self.tag_behav_friction, self.tag_emotional_inv,
            self.tag_high_awareness, self.tag_underserved,
            self.tag_life_transition, self.tag_relship_drift,
            self.tag_high_earner,
        ])

    def active_tags(self) -> list:
        mapping = {
            Tag.WEALTH_ACCUM:    self.tag_wealth_accum,
            Tag.WEALTH_PRESERV:  self.tag_wealth_preserv,
            Tag.FIN_COMPLEXITY:  self.tag_fin_complexity,
            Tag.MISALIGNED_PROD: self.tag_misaligned_prod,
            Tag.BEHAV_FRICTION:  self.tag_behav_friction,
            Tag.EMOTIONAL_INV:   self.tag_emotional_inv,
            Tag.HIGH_AWARENESS:  self.tag_high_awareness,
            Tag.UNDERSERVED:     self.tag_underserved,
            Tag.LIFE_TRANSITION: self.tag_life_transition,
            Tag.RELSHIP_DRIFT:   self.tag_relship_drift,
            Tag.HIGH_EARNER:     self.tag_high_earner,
        }
        return [tag for tag, active in mapping.items() if active]


# ── SendLog ──────────────────────────────────────────────────────────────────
@dataclass
class SendLog:
    id:               Optional[int]  = None
    date_sent:        Optional[date] = None
    person_id:        Optional[int]  = None
    person_name:      str            = ""
    content_id:       Optional[int]  = None
    content_title:    str            = ""
    decision:         str            = Decision.SEND   # Decision.*
    channel:          str            = ""
    engagement:       str            = Engagement.NONE # Engagement.*
    followup_needed:  bool           = False
    notes:            str            = ""


# ── MatchResult ──────────────────────────────────────────────────────────────
@dataclass
class MatchResult:
    person:           Person
    match_count:      int
    matched_tags:     list           # list of Tag.* strings
    days_since_contact: Optional[int]
    ok_to_contact:    bool
    contact_reason:   str            = ""  # reason if not ok
