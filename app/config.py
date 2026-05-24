import os

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    incoming_dir: str = "/incoming"
    media_dir: str = "/media"
    # Mounted appdata volume — holds the SQLite DB and any future config files
    config_dir: str = "/config"
    # Free API key from https://www.themoviedb.org/settings/api
    tmdb_api_key: str = ""

    # Plex integration (optional) — triggers library refresh after a move
    # PLEX_URL  e.g. http://192.168.1.x:32400
    # PLEX_TOKEN — find yours at https://support.plex.tv/articles/204059436
    plex_url: str = ""
    plex_token: str = ""

    @property
    def database_url(self) -> str:
        return f"sqlite:///{os.path.join(self.config_dir, 'media_manager.db')}"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
