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
    etsy_scope: str = "listings_r listings_w"

    # AI providers
    removebg_api_key: str = ""
    gemini_api_key: str = ""

    # Notion integration
    notion_api_key: str = ""
    notion_database_id: str = ""
    notion_data_source_id: str = ""
    # Notion Idea Bank DB — separate database for saving reference listings
    notion_idea_bank_data_source_id: str = ""

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

    # v0.8.0 — Idea miner: Etsy public API keyword search + signal collection.
    # etsy_miner_interval_sec: scheduler interval between full mining runs (default 1h).
    # etsy_miner_per_keyword_limit: max listings fetched per keyword per run (Etsy max 100).
    # etsy_miner_throttle_ms: delay between consecutive detail calls in milliseconds.
    # idea_mining_enabled: set False to prevent scheduler job registration.
    etsy_miner_interval_sec: int = 3600
    etsy_miner_per_keyword_limit: int = 100
    etsy_miner_throttle_ms: int = 200
    idea_mining_enabled: bool = True


# Singleton — import this throughout the app
settings = Settings()
