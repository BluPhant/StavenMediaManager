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

    # ── rTorrent / seedbox sync (optional) ────────────────────────────────
    # RTORRENT_URL        e.g. https://username.servername.usbx.me/RPC2
    # RTORRENT_USER       HTTP basic auth username
    # RTORRENT_PASS       HTTP basic auth password
    # RTORRENT_TAG        ruTorrent label to watch (e.g. "import")
    # RTORRENT_LOOKBACK_HOURS  how far back to check for completed torrents
    # RTORRENT_SSH_HOST   e.g. servername.usbx.me
    # RTORRENT_SSH_PORT   default 22
    # RTORRENT_SSH_USER   SSH username (usually same as RTORRENT_USER)
    # RTORRENT_SSH_KEY_PATH  path to mounted SSH private key (e.g. /config/ssh/id_rsa)
    # RTORRENT_SSH_PASS   SSH password — used only if no key path is set
    rtorrent_url: str = ""
    rtorrent_user: str = ""
    rtorrent_pass: str = ""
    rtorrent_tag: str = "import"
    rtorrent_lookback_hours: int = 24
    rtorrent_ssh_host: str = ""
    rtorrent_ssh_port: int = 22
    rtorrent_ssh_user: str = ""
    rtorrent_ssh_key_path: str = ""
    rtorrent_ssh_pass: str = ""

    @property
    def database_url(self) -> str:
        return f"sqlite:///{os.path.join(self.config_dir, 'media_manager.db')}"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
