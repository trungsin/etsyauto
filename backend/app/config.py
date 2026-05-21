"""Application configuration via Pydantic Settings — loads from .env file."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Etsy OAuth
    etsy_api_key: str = ""
    etsy_shared_secret: str = ""
    etsy_redirect_uri: str = "http://localhost:8787/auth/etsy/callback"
    etsy_scope: str = "listings_r listings_w shops_r"
    # Fallback shipping_profile_id used when template doesn't override.
    # Etsy requires it for physical listings. List via `GET /shops/{id}/shipping-profiles`.
    etsy_default_shipping_profile_id: int = 0
    # Etsy processing profile (readiness_state_id) — required for physical listings.
    # List via `GET /shops/{id}/readiness-state-definitions`.
    etsy_default_readiness_state_id: int = 0

    # AI providers
    # removebg_api_keys: comma-separated list of keys (tried in order on 402/429).
    # If set, takes precedence over legacy removebg_api_key + removebg_api_key_backup.
    removebg_api_keys: str = ""
    removebg_api_key: str = ""        # legacy primary — used when removebg_api_keys is empty
    removebg_api_key_backup: str = "" # legacy backup  — used when removebg_api_keys is empty
    gemini_api_keys: str = ""   # comma-separated; takes precedence over gemini_api_key
    gemini_api_key: str = ""    # legacy single key
    # Model for admin AI buttons. 2.5-flash-lite: higher free-tier quota than 2.5-flash (20 RPD) — sufficient quality.
    gemini_ai_button_model: str = "gemini-2.5-flash-lite"

    # Notion integration
    notion_api_key: str = ""
    notion_database_id: str = ""
    notion_data_source_id: str = ""
    # Notion Idea Bank DB — separate database for saving reference listings
    notion_idea_bank_data_source_id: str = ""
    # v0.8.1 hotfix — gate review-sync jobs (sync_to_notion + pull_approvals) and
    # review-DB schema validate on startup. Default False per v0.8 deprecation plan.
    # Does NOT affect Idea Bank save (separate workflow used by extension reference mode).
    notion_sync_enabled: bool = False

    # Cloudflare R2
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket_name: str = ""
    r2_public_url: str = ""

    # App internals
    database_url: str = "sqlite:///./etsyauto.db"
    static_dir: str = "./static"
    # Admin API protection — set in .env to enable /admin/* endpoints
    admin_token: str = ""
    # Feature flag — disable mockup pipeline (Imagen/Nano Banana require paid billing).
    # When False, listings skip mockup stage and go straight from title-done → review.
    enable_mockup: bool = False

    # v0.7.0 — Etsy dry-run mode for safe end-to-end testing without quota burn.
    # When True, EtsyApiClient methods short-circuit to canned fixture responses.
    # Scenarios: happy | rate_limit | taxonomy_error | auth_fail | image_too_small.
    etsy_dry_run: bool = False
    etsy_dry_run_scenario: str = "happy"

    # OpenAI — used by Cloden Design artwork pipeline (GPT-Image-1 edit)
    openai_api_key: str = ""
    # Feature flag — disable artwork pipeline if OpenAI key not configured
    enable_artwork: bool = False

    # Upscayl — path to upscayl-bin CLI (https://github.com/upscayl/upscayl)
    # Set UPSCAYL_BIN in .env to the full path of the binary, e.g. /opt/upscayl-bin
    # Leave empty to search PATH. Set UPSCAYL_MODELS to override models directory.
    upscayl_bin: str = "upscayl-bin"
    upscayl_models: str = ""  # if empty, binary uses its bundled/default models

    # v0.8.0 — Idea miner: Etsy public API keyword search + signal collection.
    # etsy_miner_interval_sec: scheduler interval between full mining runs (default 1h).
    # etsy_miner_per_keyword_limit: max listings fetched per keyword per run (Etsy max 100).
    #   v0.8.2 default lowered from 100 → 10 to honor v0.8 success criterion
    #   "<30% of 10K QPD with 10 keywords on hourly schedule". User may raise manually
    #   via .env if they accept quota risk; admin UI warns above the 30% threshold.
    # etsy_miner_throttle_ms: delay between consecutive detail calls in milliseconds.
    # idea_mining_enabled: set False to prevent scheduler job registration.
    etsy_miner_interval_sec: int = 3600
    etsy_miner_per_keyword_limit: int = 10
    etsy_miner_throttle_ms: int = 200
    # Quality gate: after fetching details, keep only top-N new candidates by
    # num_favorers per run. Existing ideas (already in DB) are always processed
    # so we don't lose signal history on previously-tracked items. Set to 0 to
    # disable filter and keep every fetched listing (legacy behaviour).
    etsy_miner_keep_top_n_per_run: int = 20
    idea_mining_enabled: bool = True
    # v0.8.2 — quota guard threshold (calls/day) above which /admin/keywords renders a
    # warn banner. Default 3000 = 30% of Etsy 10K QPD. Pure UI signal, no enforcement.
    etsy_quota_warn_threshold_calls_per_day: int = 3000


# Singleton — import this throughout the app
settings = Settings()
