# Staven Media Manager

A personal home-media management tool I built for my own Unraid setup and am making available here in case it's a useful foundation for someone else. It is not a polished product — it does exactly what I need, and the edges are rough.

![SMM](static/img/icon.svg)

---

## What it does

- **Movie discovery** — Search by title, identify the movie via TMDB (IMDB ID as the internal key), then check Plex, your seedbox, and IPTorrents in parallel. Shows a unified status card: already in library (at what resolution), currently downloading, or available to grab. Quality tiers: 2160p → 1440p → 1080p → 720p. If a movie is in your library below 2160p, surfaces better copies automatically.
- **Upgrade reviews** — When a better copy of a movie is grabbed, the old file is moved to a `.trash` folder and a Pending Review is created. Side-by-side comparison of old vs new (filename, size, resolution) with Confirm or Revert actions.
- **Watching queue** — Add movies that aren't on IPT yet to a queue. A background job checks every 4 hours and auto-grabs when a copy meeting your minimum quality appears.
- **Search history** — Every confirmed movie search is recorded with Plex/seedbox/IPT status. One-click refresh re-runs all checks live.
- **Seedbox sync** — Polls an rTorrent seedbox for completed torrents tagged with a label, downloads them over FTPS (parallel byte-range segments), and moves them into the local media library.
- **IPTorrents search** — Search the IPTorrents RSS feed by title or IMDB ID, grab a torrent, and load it directly into rTorrent with one click.
- **BTN search** — Search BroadcasTheNet for TV series, grab and load into rTorrent.
- **Plex integration** — Targeted path refresh after each import; full library scan with resolution data cached for movie status checks.
- **Job tracking** — All sync, move, extract, and queue-check jobs are tracked in a local SQLite database with live progress in the UI.

---

## Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11, FastAPI |
| Database | SQLite via SQLAlchemy |
| Seedbox protocol | rTorrent XMLRPC, FTPS (curl), SFTP |
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
  -v /path/to/media:/media \
  -v /path/to/appdata:/config \
  -e TMDB_API_KEY=your_key \
  -e PLEX_URL=http://192.168.1.x:32400 \
  -e PLEX_TOKEN=your_plex_token \
  ghcr.io/bluphant/stavenmediamanager:latest
```

The app will be available at `http://localhost:8080`.

> **Note:** A single `/media` mount covers both incoming downloads and the library. Incoming files land at `/media/temp/Incoming` by default. Do **not** add a separate `/incoming` mount — it would cause full file copies instead of instant renames.

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
      - /path/to/media:/media
      - /path/to/appdata:/config
    environment:
      - TMDB_API_KEY=your_key
      - PLEX_URL=http://192.168.1.x:32400
      - PLEX_TOKEN=your_plex_token
```

---

## Configuration

All configuration is via environment variables. Nothing is stored in the image.

### Core

| Variable | Default | Description |
|---|---|---|
| `TMDB_API_KEY` | — | Free API key from [themoviedb.org](https://www.themoviedb.org/settings/api) |
| `INCOMING_DIR` | `/media/temp/Incoming` | Where torrents are downloaded before sorting. Must be under the `/media` mount. |

### Plex (optional — required for movie status checks)

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

### BTN (BroadcasTheNet) search (optional)

| Variable | Default | Description |
|---|---|---|
| `BTN_API_KEY` | — | API key from your BTN profile → Manage API Keys |

---

## Volumes

| Mount | Purpose |
|---|---|
| `/media` | Your media library root — subdirectories become categories. Incoming files land at `/media/temp/Incoming`. |
| `/config` | Persistent data: SQLite database, optional SSH keys |

---

## Notes

- The seedbox sync uses **FTPS** (FTP over TLS) rather than SFTP for download throughput.
- Category detection is driven by the subdirectory structure under `/media` — create a folder called `movies`, `audiobooks`, etc. and the UI picks it up automatically.
- Movie upgrades: when a better copy is imported over an existing one, the old file moves to `/media/movies/.trash/` pending a review in the **Movies → Pending Review** tab.
- Plex is the system of record for local media. The movie tracking tables in this app store workflow state only — what you searched, what's downloading, what needs a review.
- No authentication on the web UI — intended for use on a private LAN or behind a VPN.
- Built and tested against an **Ultra Seedbox** (usbx.me) setup. Other rTorrent providers should work but YMMV.
