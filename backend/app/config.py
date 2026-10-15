from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./smartvision.db"
    vision_api_key: str = ""
    heartbeat_timeout: int = 20
    cors_origins: str = "http://localhost:3000,http://localhost:8080,http://127.0.0.1:5500"
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
