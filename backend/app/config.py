from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    secret_key: str = "dev-secret-key-change-me"

    admin_username: str = "admin"
    admin_password: str = "admin"

    user1_username: str = "user1"
    user1_password: str = "user1"

    user2_username: str = "user2"
    user2_password: str = "user2"

    db_path: str = "data/expenses.db"
    tz: str = "Europe/Budapest"
    domain: str = "localhost"

    session_cookie_name: str = "session"
    session_max_age_days: int = 30

    login_max_attempts: int = 5
    login_window_minutes: int = 15

    rates_primary_url: str = "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@{date}/v1/currencies/huf.json"
    rates_fallback_url: str = "https://open.er-api.com/v6/latest/HUF"

    backup_dir: str = "/data/backups"
    backup_keep: int = 14


settings = Settings()
