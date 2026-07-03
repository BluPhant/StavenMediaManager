import os

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Both paths must be under the same Docker volume mount so os.rename() works
    # (cross-mount-point renames fail with EXDEV and force a full file copy).
    # Default: /media/temp/Incoming — subdirectory of the /media mount.
    # Override with INCOMING_DIR env var if needed.
    incoming_dir: str = "/media/temp/Incoming"
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

    # FTPS (FTP over TLS) — faster than SFTP, matches FileZilla defaults
    # RTORRENT_FTP_HOST   FTP host, e.g. 216.163.184.165 or servername.usbx.me
    # RTORRENT_FTP_PORT   default 21
    # RTORRENT_FTP_ROOT   absolute path that is the FTP root on the server
    #                     e.g. /home/emuhack  (paths below this become FTP-relative)
    # RTORRENT_FTP_THREADS  parallel connections per torrent (default 4)
    rtorrent_ftp_host: str = ""
    rtorrent_ftp_port: int = 21
    rtorrent_ftp_root: str = ""
    rtorrent_ftp_threads: int = 4

    # ── IPTorrents search (optional) ──────────────────────────────────────
    # IPTORRENTS_USER_ID   numeric user ID shown on your profile page
    # IPTORRENTS_PASSKEY   passkey / API key from your profile page
    # IPTORRENTS_DOMAIN    override the domain (default: iptorrents.com)
    iptorrents_user_id: str = ""
    iptorrents_passkey: str = ""
    iptorrents_domain: str = "iptorrents.com"

    # ── BTN (BroadcasTheNet) search (optional) ────────────────────────────
    # BTN_API_KEY   API key from your BTN profile → Manage API Keys
    btn_api_key: str = ""

    # ── Discogs (optional) — personal access token for music metadata ────────
    # DISCOGS_TOKEN — get one at https://www.discogs.com/settings/developers
    discogs_token: str = ""

    # ── IGDB (optional) — Switch game search via Twitch API ───────────────────
    # Register at https://dev.twitch.tv/console then enable IGDB access.
    # IGDB_CLIENT_ID     Twitch application client ID
    # IGDB_CLIENT_SECRET Twitch application client secret
    igdb_client_id: str = ""
    igdb_client_secret: str = ""

    # ── Switch install (optional) — IP this server advertises to Switches ────
    # When pushing files to a Switch via Awoo, game files are served over HTTP.
    # SWITCH_HOST_IP must be the IP the Switch can reach this server at.
    switch_host_ip: str = ""

    # ── Build info (injected by Docker ARG→ENV at image build time) ───────────
    app_version:    str = "dev"
    app_revision:   str = ""
    app_build_date: str = ""

    @property
    def database_url(self) -> str:
        return f"sqlite:///{os.path.join(self.config_dir, 'media_manager.db')}"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
