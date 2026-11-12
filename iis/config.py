from pathlib import Path
from typing import Annotated, Any

from pydantic import BeforeValidator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _empty_str_to_none(v: Any) -> Any:
    """Treat blank .env values as unset (optional int/str fields)."""
    if v is None:
        return None
    if isinstance(v, str) and not v.strip():
        return None
    return v


OptionalInt = Annotated[int | None, BeforeValidator(_empty_str_to_none)]
OptionalStr = Annotated[str | None, BeforeValidator(_empty_str_to_none)]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="IIS_", env_file=".env", extra="ignore")

    data_dir: Path = Path.home() / ".iis"
    # LLM backend: "ollama" (local) or "hive" (thehive.ai OpenAI-compatible cloud)
    llm_provider: str = "hive"
    # Hive (https://docs.thehive.ai) — put secret in .env as IIS_HIVE_API_KEY=...
    hive_api_key: OptionalStr = None
    hive_base_url: str = "https://api-cdn.thehive.ai/api/v3"
    hive_model: str = "deepseek-ai/deepseek-v4.1-flash-1017"
    hive_finance_model: str = "deepseek-ai/deepseek-v4.1-flash-1017"
    llm_chat_timeout_sec: float = 90.0
    # Ollama (local fallback when IIS_LLM_PROVIDER=ollama)
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"
    ollama_finance_model: str = "iis-finance"
    ollama_finance_fallback: bool = True
    ollama_embed_model: str = "nomic-embed-text"
    enable_external_search: bool = True
    max_pdf_chunk_chars: int = 2000
    auth_required: bool = False
    bootstrap_admin: bool = True
    default_admin_email: str = "admin@iis.local"
    default_admin_password: str = "changeme"
    # When set, IIS accepts Inertia-minted SSO tokens (X-Inertia-SSO / Bearer).
    inertia_sso_secret: OptionalStr = None
    # Public URL prefix when proxied under Inertia (e.g. "/iis"). Empty = root.
    public_base_path: str = ""
    # Hide IIS login UI and require SSO / token when true.
    inertia_auth_only: bool = False
    fetch_live_prices: bool = True
    # Live price: same Google Sheet the lean server cron reads (GOOGLEFINANCE-fed "Prices" tab),
    # then yfinance NSE/BSE. Never an LLM or web-snippet price.
    price_sheet_enabled: bool = True
    price_sheet_id: str = "1Cd-TYldviG1HHMVo8coAjj-zzcu_nAr64slRZsFFmgM"
    price_sheet_prices_tab: str = "Prices"
    price_sheet_universe_tab: str = "Stocks"
    price_sheet_service_account_path: Path = (
        Path.home() / ".iis" / "google" / "price_sheet_service_account.json"
    )
    price_sheet_cache_sec: float = 600.0
    # Global research rules (apply to every company) + LLM call log
    research_context_path: Path = Path.home() / ".iis" / "research_context.json"
    llm_call_log_path: Path = Path.home() / ".iis" / "logs" / "llm_calls.jsonl"
    # Full request/response bodies (can be large). Index stays in llm_calls.jsonl.
    llm_log_full_transcripts: bool = True
    llm_transcript_dir: Path = Path.home() / ".iis" / "logs" / "llm_transcripts"
    llm_max_retries: int = 2
    llm_min_interval_sec: float = 0.25
    # Strip sentences/table rows with numbers not traceable to Python-collected facts
    llm_verify_numbers: bool = True
    # Chat: prefer Python modules; Ollama only for light polish / optional routing
    enable_query_planner: bool = False
    # Prefer offline Python modules + analysis_pack.md before calling Ollama
    prefer_analysis_pack: bool = True
    # LLM planner only when deterministic routing misses (saves Mac Mini CPU/GPU)
    llm_planner_for_unknown_only: bool = True
    ollama_chat_timeout_sec: float = 20.0
    # Never use Ollama for PDF RAG, transcript synthesis, or pack-grounded long answers
    chat_llm_complex: bool = False
    # Mid-pipeline LLM during AR/guidelines/Porter — OFF. Python collects all files first.
    analyze_llm_enrichment: bool = False
    # DCF / intrinsic-value model — OFF (valuation = CIQ multiples + street only)
    dcf_enabled: bool = False
    # Analyze report polish tool-loop — OFF (final Analyze call is initiating coverage).
    # Chat uses chat_llm_analyst separately (Hive → Python tools → answer).
    llm_analyst_enabled: bool = False
    llm_analyst_max_tool_rounds: int = 1
    llm_analyst_timeout_sec: float = 90.0
    # Chat: Hive plans/calls Python tools then answers (independent of llm_analyst_enabled)
    chat_llm_analyst_max_tool_rounds: int = 4
    chat_llm_analyst_timeout_sec: float = 180.0
    # Final Analyze Hive call: act as fundamental analyst → initiating coverage report
    initiating_coverage_llm_enabled: bool = True
    # Quality first — 1L completion tokens, 15 min wall for the write-up
    initiating_coverage_timeout_sec: float = 900.0
    initiating_coverage_max_tokens: int = 100000
    # Report stage wall (Python draft + initiating coverage write-up)
    report_stage_timeout_sec: float = 1200.0
    # Hard wall for entire Analyze job (soft wrap-up, not a fail)
    analyze_max_wall_sec: float = 3600.0
    # Early Hive: company card → what to look for (guides one-shot digest)
    research_planner_enabled: bool = True
    research_planner_timeout_sec: float = 90.0
    research_planner_max_tokens: int = 1800
    # Analyze runs LLM analyst after Python draft (Hive via llm.gate).
    analyze_skip_llm_analyst: bool = False
    # Parallel Hive — fast path uses 1 synthesis call; keep at 1 to avoid pile-up
    llm_max_parallel: int = 1
    # ── Fast path (default): pack extracted PDF text → ONE Hive synthesis ──
    # Multi-pass section loops (relevance→deep-read→critic) are OFF — those ran 60–90+ min.
    # Re-enable via env for max rigor when you have hours.
    unified_doc_llm_enabled: bool = True
    unified_pack_max_chars: int = 140000
    unified_pack_max_ar_files: int = 2
    unified_pack_max_concall_files: int = 3
    unified_pack_max_broker_files: int = 2
    unified_doc_timeout_sec: float = 180.0
    unified_doc_max_tokens: int = 4500
    # Legacy multi-pass AR/concall/broker Hive — OFF (was the 90-min path)
    ar_structured_llm_enabled: bool = False
    ar_relevance_llm_enabled: bool = False
    ar_critic_llm_enabled: bool = False
    ar_gap_fill_enabled: bool = False
    # Resume interrupted Analyze (only matters if structured loops re-enabled)
    analyze_checkpoint_enabled: bool = True
    # Quick-read / deep-read knobs kept for env re-enable of structured loops
    ar_relevance_batch_enabled: bool = True
    ar_relevance_batch_sections: int = 20
    ar_relevance_excerpt_chars: int = 1400
    ar_relevance_batch_chars: int = 120000
    ar_relevance_batch_max_tokens: int = 4000
    ar_relevance_timeout_sec: float = 120.0
    ar_deep_read_batch_enabled: bool = True
    ar_deep_read_solo_chars: int = 18000
    ar_deep_read_batch_chars: int = 90000
    ar_deep_read_batch_sections: int = 8
    ar_deep_read_include_skims: bool = False
    ar_max_deep_read_sections: int = 12
    ar_section_max_chars: int = 36000
    ar_section_timeout_sec: float = 120.0
    ar_section_max_tokens: int = 2000
    ar_relevance_max_chars: int = 40000
    ar_relevance_max_tokens: int = 600
    ar_critic_timeout_sec: float = 120.0
    ar_critic_max_tokens: int = 2500
    # Prefer none/low reasoning on JSON scoring (Hive DeepSeek)
    hive_json_reasoning_effort: str = "none"
    concall_structured_llm_enabled: bool = False
    concall_relevance_llm_enabled: bool = False
    concall_critic_llm_enabled: bool = False
    concall_gap_fill_enabled: bool = False
    concall_unit_max_chars: int = 36000
    concall_section_max_chars: int = 48000
    concall_section_timeout_sec: float = 120.0
    concall_section_max_tokens: int = 2500
    concall_relevance_timeout_sec: float = 90.0
    concall_relevance_max_tokens: int = 600
    concall_critic_timeout_sec: float = 120.0
    concall_critic_max_tokens: int = 2500
    concall_critic_source_chars: int = 28000
    broker_structured_llm_enabled: bool = False
    broker_relevance_llm_enabled: bool = False
    broker_critic_llm_enabled: bool = False
    broker_hive_max_files: int = 4
    broker_hive_max_page: int = 8
    broker_unit_max_chars: int = 36000
    broker_section_max_chars: int = 48000
    broker_section_timeout_sec: float = 120.0
    broker_section_max_tokens: int = 2500
    broker_relevance_timeout_sec: float = 90.0
    broker_relevance_max_tokens: int = 600
    broker_critic_timeout_sec: float = 120.0
    broker_critic_max_tokens: int = 2500
    broker_critic_source_chars: int = 28000
    # Direct LLM Compare path — OFF during Analyze (run from Direct tab when needed)
    direct_llm_report_enabled: bool = False
    concall_max_deep_units: int = 6
    broker_max_deep_units: int = 4
    direct_llm_max_files: int = 8
    # Pages rendered per PDF / per Hive call (Hive accepts text+image, not PDF upload)
    direct_llm_pages_per_file: int = 24
    direct_llm_pages_per_batch: int = 6
    direct_llm_image_scale: float = 1.15
    direct_llm_jpeg_quality: int = 70
    direct_llm_synth_chars: int = 28000
    direct_llm_timeout_sec: float = 120.0
    direct_llm_max_tokens: int = 4096
    direct_llm_synth_max_tokens: int = 5000
    # Legacy text-budget knobs (unused by raw-page Direct LLM; kept for env compat)
    direct_llm_chars_per_file: int = 14000
    direct_llm_batch_chars: int = 28000
    # Legacy single-shot assemble (superseded by llm_analyst when enabled)
    report_llm_assemble: bool = False
    # Chat: LLM accepts question → may call Python tools → refine → respond
    chat_llm_analyst: bool = True
    # Light polish without tools (fallback if chat_llm_analyst is False)
    chat_llm_polish: bool = False
    # Chart / screenshot vision (Ollama granite3.2-vision + optional DePlot)
    chart_vision_enabled: bool = True
    chart_vision_backend: str = "auto"  # auto | ollama | deplot | unichart
    ollama_vision_model: str = "granite3.2-vision"
    deplot_model_id: str = "google/deplot"
    unichart_model_id: str = "ahmed-masry/unichart-base-960"
    chart_vision_timeout_sec: float = 120.0
    # Optional path to checklist_profile.json (default: ~/.iis/checklist_profile.json)
    checklist_profile_path: Path | None = None
    annual_report_chunk_chars: int = 1200
    annual_report_chunk_overlap: int = 250
    annual_report_max_sections_per_file: int = 8
    annual_report_financial_sections_max: int = 4
    use_json_store: bool = False
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_user: str = "iis"
    mysql_password: str = "iis"
    mysql_database: str = "iis"
    database_url: str | None = None
    enable_semantic_search: bool = True
    analyze_async_default: bool = True

    # Google Shared Drive — company folders + dated research notes
    google_drive_enabled: bool = True
    google_drive_folder_id: str = "0AFi5YXTg5Ar4Uk9PVA"
    google_drive_credentials_path: Path = Path.home() / ".iis" / "google" / "credentials.json"
    google_drive_token_path: Path = Path.home() / ".iis" / "google" / "token.json"
    google_drive_service_account_path: Path | None = None
    google_drive_index_path: Path = Path.home() / ".iis" / "google" / "folder_index.json"

    # Telegram → Shared Drive "Telegram PDFs" inbox (personal account via Telethon)
    # Set IIS_TELEGRAM_API_ID / IIS_TELEGRAM_API_HASH from https://my.telegram.org — never commit secrets.
    telegram_enabled: bool = True
    telegram_api_id: OptionalInt = None
    telegram_api_hash: OptionalStr = None
    telegram_session_path: Path = Path.home() / ".iis" / "telegram" / "session"
    telegram_group: OptionalStr = None  # @username or numeric chat id
    telegram_phone: OptionalStr = None  # e.g. +9198xxxxxxxx for phone auth
    telegram_drive_inbox_name: str = "Telegram PDFs"
    telegram_seen_path: Path = Path.home() / ".iis" / "telegram" / "seen.json"
    telegram_local_inbox: Path = Path.home() / ".iis" / "telegram" / "inbox"
    # Daily cross-ref: filename → broker, company, report type, date, rating
    telegram_report_index_path: Path = Path.home() / ".iis" / "telegram" / "report_index.json"

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return (
            f"mysql+pymysql://{self.mysql_user}:{self.mysql_password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}?charset=utf8mb4"
        )


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
