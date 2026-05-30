# Staven Media Manager

A personal home-media management tool I built for my own Unraid setup and am making available here in case it's a useful foundation for someone else. It is not a polished product — it does exactly what I need, and the edges are rough.

![SMM](static/img/icon.svg)

---

## What it does

- **Seedbox sync** — polls an rTorrent seedbox for completed torrents tagged with a label, downloads them over FTPS (parallel byte-range segments), and moves them into a local media library organised by category.
- **IPTorrents search** — search the IPTorrents RSS feed by title, grab a torrent, and load it directly into rTorrent with one click.
- **Movie matching** — suggests a clean title + year for movie directories using the TMDb API, with progressive query broadening so obscure and older titles still surface.
- **Plex integration** — triggers a Plex library refresh after each import.
- **Job tracking** — all sync and extract jobs are tracked in a local SQLite database with live progress in the UI.

---

## Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11, FastAPI |
| Database | SQLite via SQLAlchemy |
| Scheduling | APScheduler |
| Seedbox protocol | rTorrent XMLRPC, FTPS (curl), SFTP (paramiko) |
| Frontend | Vanilla JS SPA, Bootstrap 5, no build step |
| Container | Docker (single image) |
| CI | GitHub Actions → `ghcr.io` |

No external Python HTTP libraries — all outbound calls use `urllib` and `xmlrpc.client` from the stdlib.

---

## Running it

### Docker (recommended)

```bash
docker run -d \
  --name StavenMediaManager \
  --restart unless-stopped \
  -p 8080:8080 \
  -v /path/to/incoming:/incoming \
  -v /path/to/media:/media \
  -v /path/to/appdata:/config \
  -e TMDB_API_KEY=your_key \
  ghcr.io/bluphant/stavenmediamanager:latest
```

The app will be available at `http://localhost:8080`.

### Docker Compose

```yaml
services:
  stavenmediamanager:
    image: ghcr.io/bluphant/stavenmediamanager:latest
    container_name: StavenMediaManager
    restart: unless-stopped
    ports:
      - "8080:8080"
    volumes:
      - /path/to/incoming:/incoming
      - /path/to/media:/media
      - /path/to/appdata:/config
    environment:
      - TMDB_API_KEY=your_key
```

---

## Configuration

All configuration is via environment variables. Nothing is stored in the image.

### Core

| Variable | Default | Description |
|---|---|---|
| `TMDB_API_KEY` | — | Free API key from [themoviedb.org](https://www.themoviedb.org/settings/api) |

### Plex (optional)

| Variable | Default | Description |
|---|---|---|
| `PLEX_URL` | — | e.g. `http://192.168.1.x:32400` |
| `PLEX_TOKEN` | — | [How to find your token](https://support.plex.tv/articles/204059436) |

### rTorrent / Seedbox sync (optional)

| Variable | Default | Description |
|---|---|---|
| `RTORRENT_URL` | — | XMLRPC endpoint, e.g. `https://user.host.usbx.me/RPC2` |
| `RTORRENT_USER` | — | HTTP basic auth username |
| `RTORRENT_PASS` | — | HTTP basic auth password |
| `RTORRENT_TAG` | `import` | ruTorrent label to watch for completed torrents |
| `RTORRENT_LOOKBACK_HOURS` | `24` | How far back to check for completed torrents |
| `RTORRENT_SSH_HOST` | — | Seedbox SSH hostname |
| `RTORRENT_SSH_PORT` | `22` | SSH port |
| `RTORRENT_SSH_USER` | — | SSH username |
| `RTORRENT_SSH_PASS` | — | SSH password (used only if no key is set) |
| `RTORRENT_SSH_KEY_PATH` | — | Path to a mounted SSH private key, e.g. `/config/ssh/id_rsa` |
| `RTORRENT_FTP_HOST` | — | FTPS host for downloading files |
| `RTORRENT_FTP_PORT` | `21` | FTPS port |
| `RTORRENT_FTP_ROOT` | — | Absolute FTP root on the server, e.g. `/home/username` |
| `RTORRENT_FTP_THREADS` | `4` | Parallel connections per torrent download |

### IPTorrents search (optional)

| Variable | Default | Description |
|---|---|---|
| `IPTORRENTS_USER_ID` | — | Numeric user ID from your IPT profile |
| `IPTORRENTS_PASSKEY` | — | Passkey / API key from your IPT profile |
| `IPTORRENTS_DOMAIN` | `iptorrents.com` | Domain override if needed |

---

## Volumes

| Mount | Purpose |
|---|---|
| `/incoming` | Where torrents are downloaded before being sorted |
| `/media` | Your media library root — subdirectories become categories |
| `/config` | Persistent data: SQLite database, optional SSH keys |

---

## Notes

- The seedbox sync uses **FTPS** (FTP over TLS) rather than SFTP for download throughput; SFTP is used only for directory listing.
- Category detection is driven by the subdirectory structure under `/media` — create a folder called `movies`, `audiobooks`, etc. and the UI picks it up automatically.
- This was built and tested against an **Ultra Seedbox** (usbx.me) setup. Other providers should work but YMMV.
- No authentication on the web UI — intended for use on a private LAN or behind a VPN.
