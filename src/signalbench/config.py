from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://signalbench:signalbench@localhost:5432/signalbench"
    sec_user_agent: str = "SignalBench/0.1 (dev@example.com)"
    model_version: str = "deepseek-ai/DeepSeek-V4-Flash-0731"
    prompt_version: str = "v1"
    together_api_key: str | None = None


settings = Settings()
