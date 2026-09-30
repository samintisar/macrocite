from datetime import date

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://signalbench:signalbench@localhost:5432/signalbench"
    sec_user_agent: str = "SignalBench/0.1 (dev@example.com)"
    finnhub_api_key: str | None = None
    openrouter_api_key: str | None = None
    telegram_bot_token: str | None = None  # from @BotFather (spec 05); never logged
    telegram_chat_id: int | None = None  # the owner's chat: the bot's allowlist
    price_history_start: date = date(2010, 1, 1)
    filings_backfill_start: date = date(2016, 1, 1)


settings = Settings()
