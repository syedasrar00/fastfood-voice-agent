"""Application configuration."""

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    openai_api_key: str = ""
    groq_api_key: str = ""
    llm_model: str = ""

    deepgram_api_key: str = ""
    cartesia_api_key: str = ""
    cartesia_voice_id: str = "a0e99841-438c-4a64-b679-ae501e7d6091"

    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    human_transfer_number: str = ""

    menu_file_path: str = "data/menu.json"
    orders_file_path: str = "data/orders.txt"


@lru_cache
def get_settings() -> Settings:
    return Settings()