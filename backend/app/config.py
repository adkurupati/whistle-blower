from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_expiry_days: int = 7
    bsky_identifier: str | None = None
    bsky_app_password: str | None = None
    youtube_api_key: str | None = None
    qdrant_url: str = "http://localhost:6333"
    ollama_url: str = "http://localhost:11434"
    # Phase 7 AI Verdict synthesis model. qwen2.5:3b-instruct-q4_K_M chosen
    # for the 8 GB M2 target: ~2.5 GB resident, strong JSON-mode compliance.
    ollama_model: str = "qwen2.5:3b-instruct-q4_K_M"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
