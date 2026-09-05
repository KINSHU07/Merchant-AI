"""
Central configuration. All secrets come from environment variables — never
hardcoded, never passed into LLM prompts, never logged.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    database_url: str = "sqlite:///./dev.db"

    # LLM provider — Groq hosts open-source models (Llama, etc.) behind a
    # standard hosted API. No model weights are ever downloaded locally.
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"

    # Razorpay — TEST MODE ONLY. Never point this at live keys.
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""

    # App
    environment: str = "development"


settings = Settings()