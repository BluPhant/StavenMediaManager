import os

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    incoming_dir: str = "/incoming"
    media_dir: str = "/media"
    # Mounted appdata volume — holds the SQLite DB and any future config files
    config_dir: str = "/config"

    @property
    def database_url(self) -> str:
        return f"sqlite:///{os.path.join(self.config_dir, 'media_manager.db')}"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
