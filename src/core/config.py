from dotenv import load_dotenv
import os
from urllib.parse import quote
from typing import Optional

load_dotenv()

class Settings:
    PROJECT_NAME: str = "APITTK"
    PROJECT_VERSION: str = "2.7"
    POSTGRES_USER: str = os.getenv("POSTGRES_USER", "postgres")
    POSTGRES_PASSWORD: Optional[str] = os.getenv("POSTGRES_PASSWORD")
    POSTGRES_SERVER: str = os.getenv("POSTGRES_SERVER", "localhost")
    POSTGRES_PORT: str = os.getenv("POSTGRES_PORT", "5432")
    POSTGRES_DB: str = os.getenv("POSTGRES_DB", "app_db")
    SECRET_KEY: str = os.getenv("SECRET_KEY", "secret-key-placeholder")
    ALGORITHM: str = os.getenv("ALGORITHM", "HS256")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 60))
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379")
    UPLOAD_DIR:  str = os.getenv("UPLOAD_DIR", "redis://localhost:6379")
    @property
    def ASYNC_DATABASE_URL(self) -> str:
        password = quote(self.POSTGRES_PASSWORD) if self.POSTGRES_PASSWORD else ""
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{password}"
            f"@{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def SYNC_DATABASE_URL(self) -> str:
        password = quote(self.POSTGRES_PASSWORD) if self.POSTGRES_PASSWORD else ""
        return (
            f"postgresql://{self.POSTGRES_USER}:{password}"
            f"@{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings()

#     print(f"Async Database URL: {settings.ASYNC_DATABASE_URL}")
#     print(f"Sync Database URL: {settings.SYNC_DATABASE_URL}")