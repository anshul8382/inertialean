"""
MySQL storage layer for Inertia Content Intelligence.

Uses the same DB credentials as the Flask app (DB_HOST, DB_USER, etc.) when
Database.from_app_config() is called from services. Falls back to INERTIA_DB_* env vars.
"""

import os
from datetime import date
from typing import Optional, List
from contextlib import contextmanager

from .models import (
    Person, ContentPiece, SendLog,
    LifeEvent, Milestone, Decision, Engagement,
)


def _mysql_connector():
    try:
        import mysql.connector
        return mysql.connector
    except ImportError as e:
        raise ImportError("Run: pip install mysql-connector-python") from e


CREATE_PERSONS = """
CREATE TABLE IF NOT EXISTS ic_persons (
    id                  INT AUTO_INCREMENT PRIMARY KEY,
    name                VARCHAR(150) NOT NULL,
    category            VARCHAR(50),
    profession          VARCHAR(100),
    age_bracket         VARCHAR(10),
    gender              VARCHAR(20),
    city                VARCHAR(100),
    is_nri              TINYINT(1) DEFAULT 0,
    onboarded_date      DATE,

    holds_mf            TINYINT(1) DEFAULT 0,
    holds_stock         TINYINT(1) DEFAULT 0,
    holds_lic           TINYINT(1) DEFAULT 0,
    holds_ulip          TINYINT(1) DEFAULT 0,
    holds_fd            TINYINT(1) DEFAULT 0,
    holds_real_estate   TINYINT(1) DEFAULT 0,
    holds_pms_aif       TINYINT(1) DEFAULT 0,

    investable_assets   VARCHAR(20),
    monthly_surplus     VARCHAR(20),
    has_ca_rm_mfd       TINYINT(1) DEFAULT 0,
    prior_adviser       TINYINT(1) DEFAULT 0,

    recent_life_event   VARCHAR(100) DEFAULT 'None',
    is_business_owner   TINYINT(1) DEFAULT 0,
    upcoming_milestone  VARCHAR(100) DEFAULT 'None',
    is_cross_border     TINYINT(1) DEFAULT 0,

    has_deferred_decision   TINYINT(1) DEFAULT 0,
    misses_review_calls     TINYINT(1) DEFAULT 0,
    reacted_to_market_event TINYINT(1) DEFAULT 0,
    driven_by_market_news   TINYINT(1) DEFAULT 0,
    reads_financial_content TINYINT(1) DEFAULT 0,
    uses_diy_platforms      TINYINT(1) DEFAULT 0,
    ca_manages_investments  TINYINT(1) DEFAULT 0,
    is_senior_professional  TINYINT(1) DEFAULT 0,

    tag_behav_friction  TINYINT(1) DEFAULT 0,
    tag_emotional_inv   TINYINT(1) DEFAULT 0,
    tag_high_awareness  TINYINT(1) DEFAULT 0,
    tag_underserved     TINYINT(1) DEFAULT 0,
    tag_life_transition TINYINT(1) DEFAULT 0,
    tag_relship_drift   TINYINT(1) DEFAULT 0,
    tag_high_earner     TINYINT(1) DEFAULT 0,

    last_contacted      DATE,
    last_engaged        TINYINT(1) DEFAULT 0,
    engagement_score    TINYINT DEFAULT 0,
    notes               TEXT,
    max_msgs_per_month  TINYINT DEFAULT 2,

    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

CREATE_CONTENT = """
CREATE TABLE IF NOT EXISTS ic_content (
    id                  INT AUTO_INCREMENT PRIMARY KEY,
    title               VARCHAR(300) NOT NULL,
    format              VARCHAR(50),
    created_date        DATE,
    topic_category      VARCHAR(100),
    notes               TEXT,
    campaign_studio_campaign_id INT NULL,

    tag_wealth_accum    TINYINT(1) DEFAULT 0,
    tag_wealth_preserv  TINYINT(1) DEFAULT 0,
    tag_fin_complexity  TINYINT(1) DEFAULT 0,
    tag_misaligned_prod TINYINT(1) DEFAULT 0,
    tag_behav_friction  TINYINT(1) DEFAULT 0,
    tag_emotional_inv   TINYINT(1) DEFAULT 0,
    tag_high_awareness  TINYINT(1) DEFAULT 0,
    tag_underserved     TINYINT(1) DEFAULT 0,
    tag_life_transition TINYINT(1) DEFAULT 0,
    tag_relship_drift   TINYINT(1) DEFAULT 0,
    tag_high_earner     TINYINT(1) DEFAULT 0,

    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    INDEX idx_ic_content_campaign (campaign_studio_campaign_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

CREATE_SEND_LOG = """
CREATE TABLE IF NOT EXISTS ic_send_log (
    id                  INT AUTO_INCREMENT PRIMARY KEY,
    date_sent           DATE,
    person_id           INT,
    person_name         VARCHAR(150),
    content_id          INT,
    content_title       VARCHAR(300),
    decision            VARCHAR(20),
    channel             VARCHAR(50),
    engagement          VARCHAR(30) DEFAULT 'None',
    followup_needed     TINYINT(1) DEFAULT 0,
    notes               TEXT,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (person_id)  REFERENCES ic_persons(id) ON DELETE SET NULL,
    FOREIGN KEY (content_id) REFERENCES ic_content(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


class Database:
    def __init__(
        self,
        host: str = None,
        port: int = None,
        name: str = None,
        user: str = None,
        password: str = None,
    ):
        self.config = {
            "host": host or os.getenv("INERTIA_DB_HOST") or os.getenv("DB_HOST", "127.0.0.1"),
            "port": port or int(os.getenv("INERTIA_DB_PORT") or os.getenv("DB_PORT", "3306")),
            "database": name or os.getenv("INERTIA_DB_NAME") or os.getenv("DB_NAME", "inertia_app2025"),
            "user": user or os.getenv("INERTIA_DB_USER") or os.getenv("DB_USER", "inertia_admin"),
            "password": password if password is not None else (
                os.getenv("INERTIA_DB_PASSWORD") or os.getenv("DB_PASSWORD", "")
            ),
            "charset": "utf8mb4",
            "autocommit": False,
        }

    @classmethod
    def from_app_config(cls):
        """Single source of truth: Flask Config DB_* or SQLAlchemy URI."""
        try:
            from flask import current_app

            cfg = current_app.config
            host = cfg.get("DB_HOST") or os.getenv("DB_HOST", "127.0.0.1")
            port = int(cfg.get("DB_PORT") or os.getenv("DB_PORT", "3306"))
            name = cfg.get("DB_NAME") or os.getenv("DB_NAME", "inertia_app2025")
            user = cfg.get("DB_USER") or os.getenv("DB_USER", "inertia_admin")
            password = cfg.get("DB_PASSWORD") or os.getenv("DB_PASSWORD", "")
            return cls(host=host, port=port, name=name, user=user, password=password)
        except Exception:
            return cls()

    @contextmanager
    def _conn(self):
        mysql = _mysql_connector()
        conn = mysql.connect(**self.config)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def create_tables(self):
        with self._conn() as conn:
            cur = conn.cursor()
            for ddl in [CREATE_PERSONS, CREATE_CONTENT, CREATE_SEND_LOG]:
                cur.execute(ddl)
        self._ensure_optional_columns()

    def _ensure_optional_columns(self):
        """Additive columns for existing ic_* tables (prod cutover safe)."""
        alters = [
            "ALTER TABLE ic_persons ADD COLUMN has_deferred_decision TINYINT(1) DEFAULT 0",
            "ALTER TABLE ic_persons ADD COLUMN misses_review_calls TINYINT(1) DEFAULT 0",
            "ALTER TABLE ic_persons ADD COLUMN reacted_to_market_event TINYINT(1) DEFAULT 0",
            "ALTER TABLE ic_persons ADD COLUMN driven_by_market_news TINYINT(1) DEFAULT 0",
            "ALTER TABLE ic_persons ADD COLUMN reads_financial_content TINYINT(1) DEFAULT 0",
            "ALTER TABLE ic_persons ADD COLUMN uses_diy_platforms TINYINT(1) DEFAULT 0",
            "ALTER TABLE ic_persons ADD COLUMN ca_manages_investments TINYINT(1) DEFAULT 0",
            "ALTER TABLE ic_persons ADD COLUMN is_senior_professional TINYINT(1) DEFAULT 0",
            "ALTER TABLE ic_content ADD COLUMN campaign_studio_campaign_id INT NULL",
        ]
        with self._conn() as conn:
            cur = conn.cursor()
            for sql in alters:
                try:
                    cur.execute(sql)
                except Exception:
                    pass

    def save_person(self, p: Person) -> int:
        fields = _person_to_dict(p)
        if p.id:
            fields.pop("id", None)
            set_clause = ", ".join(f"{k}=%s" for k in fields)
            sql = f"UPDATE ic_persons SET {set_clause} WHERE id=%s"
            with self._conn() as conn:
                conn.cursor().execute(sql, list(fields.values()) + [p.id])
            return p.id
        cols = ", ".join(fields.keys())
        placeholders = ", ".join(["%s"] * len(fields))
        sql = f"INSERT INTO ic_persons ({cols}) VALUES ({placeholders})"
        with self._conn() as conn:
            cur = conn.cursor()
            cur.execute(sql, list(fields.values()))
            return cur.lastrowid

    def get_person(self, person_id: int) -> Optional[Person]:
        with self._conn() as conn:
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT * FROM ic_persons WHERE id=%s", (person_id,))
            row = cur.fetchone()
        return _row_to_person(row) if row else None

    def get_all_persons(self, category: str = None) -> List[Person]:
        sql = "SELECT * FROM ic_persons"
        params = []
        if category:
            sql += " WHERE category=%s"
            params.append(category)
        sql += " ORDER BY name"
        with self._conn() as conn:
            cur = conn.cursor(dictionary=True)
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [_row_to_person(r) for r in rows]

    def save_content(self, c: ContentPiece, campaign_studio_campaign_id: int = None) -> int:
        if campaign_studio_campaign_id is not None:
            c.campaign_studio_campaign_id = campaign_studio_campaign_id
        fields = _content_to_dict(c)
        if c.id:
            fields.pop("id", None)
            set_clause = ", ".join(f"{k}=%s" for k in fields)
            sql = f"UPDATE ic_content SET {set_clause} WHERE id=%s"
            with self._conn() as conn:
                conn.cursor().execute(sql, list(fields.values()) + [c.id])
            return c.id
        cols = ", ".join(fields.keys())
        placeholders = ", ".join(["%s"] * len(fields))
        sql = f"INSERT INTO ic_content ({cols}) VALUES ({placeholders})"
        with self._conn() as conn:
            cur = conn.cursor()
            cur.execute(sql, list(fields.values()))
            return cur.lastrowid

    def get_content(self, content_id: int) -> Optional[ContentPiece]:
        with self._conn() as conn:
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT * FROM ic_content WHERE id=%s", (content_id,))
            row = cur.fetchone()
        return _row_to_content(row) if row else None

    def get_all_content(self) -> List[ContentPiece]:
        with self._conn() as conn:
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT * FROM ic_content ORDER BY created_date DESC")
            rows = cur.fetchall()
        return [_row_to_content(r) for r in rows]

    def log_send(self, entry: SendLog) -> int:
        fields = _log_to_dict(entry)
        fields.pop("id", None)
        cols = ", ".join(fields.keys())
        placeholders = ", ".join(["%s"] * len(fields))
        sql = f"INSERT INTO ic_send_log ({cols}) VALUES ({placeholders})"
        with self._conn() as conn:
            cur = conn.cursor()
            cur.execute(sql, list(fields.values()))
            return cur.lastrowid

    def update_engagement(self, log_id: int, engagement: str, followup: bool, notes: str = ""):
        sql = "UPDATE ic_send_log SET engagement=%s, followup_needed=%s, notes=%s WHERE id=%s"
        with self._conn() as conn:
            conn.cursor().execute(sql, (engagement, followup, notes, log_id))


def _person_to_dict(p: Person) -> dict:
    return {
        k: v
        for k, v in {
            "id": p.id,
            "name": p.name,
            "category": p.category,
            "profession": p.profession,
            "age_bracket": p.age_bracket,
            "gender": p.gender,
            "city": p.city,
            "is_nri": int(p.is_nri),
            "onboarded_date": p.onboarded_date,
            "holds_mf": int(p.holds_mf),
            "holds_stock": int(p.holds_stock),
            "holds_lic": int(p.holds_lic),
            "holds_ulip": int(p.holds_ulip),
            "holds_fd": int(p.holds_fd),
            "holds_real_estate": int(p.holds_real_estate),
            "holds_pms_aif": int(p.holds_pms_aif),
            "investable_assets": p.investable_assets,
            "monthly_surplus": p.monthly_surplus,
            "has_ca_rm_mfd": int(p.has_ca_rm_mfd),
            "prior_adviser": int(p.prior_adviser),
            "recent_life_event": p.recent_life_event,
            "is_business_owner": int(p.is_business_owner),
            "upcoming_milestone": p.upcoming_milestone,
            "is_cross_border": int(p.is_cross_border),
            "has_deferred_decision": int(p.has_deferred_decision),
            "misses_review_calls": int(p.misses_review_calls),
            "reacted_to_market_event": int(p.reacted_to_market_event),
            "driven_by_market_news": int(p.driven_by_market_news),
            "reads_financial_content": int(p.reads_financial_content),
            "uses_diy_platforms": int(p.uses_diy_platforms),
            "ca_manages_investments": int(p.ca_manages_investments),
            "is_senior_professional": int(p.is_senior_professional),
            "tag_behav_friction": int(p.tag_behav_friction),
            "tag_emotional_inv": int(p.tag_emotional_inv),
            "tag_high_awareness": int(p.tag_high_awareness),
            "tag_underserved": int(p.tag_underserved),
            "tag_life_transition": int(p.tag_life_transition),
            "tag_relship_drift": int(p.tag_relship_drift),
            "tag_high_earner": int(p.tag_high_earner),
            "last_contacted": p.last_contacted,
            "last_engaged": int(p.last_engaged),
            "engagement_score": p.engagement_score,
            "notes": p.notes,
            "max_msgs_per_month": p.max_msgs_per_month,
        }.items()
        if k != "id" or v is not None
    }


def _row_to_person(r: dict) -> Person:
    return Person(
        id=r["id"],
        name=r["name"],
        category=r.get("category", ""),
        profession=r.get("profession", ""),
        age_bracket=r.get("age_bracket", ""),
        gender=r.get("gender", ""),
        city=r.get("city", ""),
        is_nri=bool(r.get("is_nri", 0)),
        onboarded_date=r.get("onboarded_date"),
        holds_mf=bool(r.get("holds_mf", 0)),
        holds_stock=bool(r.get("holds_stock", 0)),
        holds_lic=bool(r.get("holds_lic", 0)),
        holds_ulip=bool(r.get("holds_ulip", 0)),
        holds_fd=bool(r.get("holds_fd", 0)),
        holds_real_estate=bool(r.get("holds_real_estate", 0)),
        holds_pms_aif=bool(r.get("holds_pms_aif", 0)),
        investable_assets=r.get("investable_assets", ""),
        monthly_surplus=r.get("monthly_surplus", ""),
        has_ca_rm_mfd=bool(r.get("has_ca_rm_mfd", 0)),
        prior_adviser=bool(r.get("prior_adviser", 0)),
        recent_life_event=r.get("recent_life_event", LifeEvent.NONE),
        is_business_owner=bool(r.get("is_business_owner", 0)),
        upcoming_milestone=r.get("upcoming_milestone", Milestone.NONE),
        is_cross_border=bool(r.get("is_cross_border", 0)),
        has_deferred_decision=bool(r.get("has_deferred_decision", 0)),
        misses_review_calls=bool(r.get("misses_review_calls", 0)),
        reacted_to_market_event=bool(r.get("reacted_to_market_event", 0)),
        driven_by_market_news=bool(r.get("driven_by_market_news", 0)),
        reads_financial_content=bool(r.get("reads_financial_content", 0)),
        uses_diy_platforms=bool(r.get("uses_diy_platforms", 0)),
        ca_manages_investments=bool(r.get("ca_manages_investments", 0)),
        is_senior_professional=bool(r.get("is_senior_professional", 0)),
        tag_behav_friction=bool(r.get("tag_behav_friction", 0)),
        tag_emotional_inv=bool(r.get("tag_emotional_inv", 0)),
        tag_high_awareness=bool(r.get("tag_high_awareness", 0)),
        tag_underserved=bool(r.get("tag_underserved", 0)),
        tag_life_transition=bool(r.get("tag_life_transition", 0)),
        tag_relship_drift=bool(r.get("tag_relship_drift", 0)),
        tag_high_earner=bool(r.get("tag_high_earner", 0)),
        last_contacted=r.get("last_contacted"),
        last_engaged=bool(r.get("last_engaged", 0)),
        engagement_score=r.get("engagement_score", 0),
        notes=r.get("notes", ""),
        max_msgs_per_month=r.get("max_msgs_per_month", 2),
    )


def _content_to_dict(c: ContentPiece) -> dict:
    return {
        k: v
        for k, v in {
            "id": c.id,
            "title": c.title,
            "format": c.format,
            "created_date": c.created_date,
            "topic_category": c.topic_category,
            "notes": c.notes,
            "campaign_studio_campaign_id": c.campaign_studio_campaign_id,
            "tag_wealth_accum": int(c.tag_wealth_accum),
            "tag_wealth_preserv": int(c.tag_wealth_preserv),
            "tag_fin_complexity": int(c.tag_fin_complexity),
            "tag_misaligned_prod": int(c.tag_misaligned_prod),
            "tag_behav_friction": int(c.tag_behav_friction),
            "tag_emotional_inv": int(c.tag_emotional_inv),
            "tag_high_awareness": int(c.tag_high_awareness),
            "tag_underserved": int(c.tag_underserved),
            "tag_life_transition": int(c.tag_life_transition),
            "tag_relship_drift": int(c.tag_relship_drift),
            "tag_high_earner": int(c.tag_high_earner),
        }.items()
        if k != "id" or v is not None
    }


def _row_to_content(r: dict) -> ContentPiece:
    return ContentPiece(
        id=r["id"],
        title=r["title"],
        format=r.get("format", ""),
        created_date=r.get("created_date"),
        topic_category=r.get("topic_category", ""),
        notes=r.get("notes", ""),
        campaign_studio_campaign_id=r.get("campaign_studio_campaign_id"),
        tag_wealth_accum=bool(r.get("tag_wealth_accum", 0)),
        tag_wealth_preserv=bool(r.get("tag_wealth_preserv", 0)),
        tag_fin_complexity=bool(r.get("tag_fin_complexity", 0)),
        tag_misaligned_prod=bool(r.get("tag_misaligned_prod", 0)),
        tag_behav_friction=bool(r.get("tag_behav_friction", 0)),
        tag_emotional_inv=bool(r.get("tag_emotional_inv", 0)),
        tag_high_awareness=bool(r.get("tag_high_awareness", 0)),
        tag_underserved=bool(r.get("tag_underserved", 0)),
        tag_life_transition=bool(r.get("tag_life_transition", 0)),
        tag_relship_drift=bool(r.get("tag_relship_drift", 0)),
        tag_high_earner=bool(r.get("tag_high_earner", 0)),
    )


def _log_to_dict(s: SendLog) -> dict:
    return {
        k: v
        for k, v in {
            "id": s.id,
            "date_sent": s.date_sent or date.today(),
            "person_id": s.person_id,
            "person_name": s.person_name,
            "content_id": s.content_id,
            "content_title": s.content_title,
            "decision": s.decision,
            "channel": s.channel,
            "engagement": s.engagement,
            "followup_needed": int(s.followup_needed),
            "notes": s.notes,
        }.items()
        if k != "id" or v is not None
    }
