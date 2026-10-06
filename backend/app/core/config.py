from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    APP_NAME: str = "Business OS"
    APP_TAGLINE_TJ: str = "Biznesingizni boshqaring. Yoqotishlarni toping. Keyingi qadamni oldindan biling."
    SECRET_KEY: str = "CHANGE_ME_IN_PRODUCTION"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7
    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/business_os"
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"

    class Config:
        env_file = ".env"


settings = Settings()
