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


# Singleton — import this throughout the app
settings = Settings()
