FROM python:3.11-slim

# ── Static Unraid UI hints (don't change) ─────────────────────────────────────
LABEL net.unraid.docker.webui="http://[IP]:[PORT:8080]/"
LABEL net.unraid.docker.managed="dockerman"

# ── OCI labels — overridden at build time by docker/metadata-action ───────────
ARG IMAGE_TITLE="Media Manager"
ARG IMAGE_DESCRIPTION="Unraid media management tool — enumerate incoming directories, detect and extract RAR archives, track async jobs."
ARG IMAGE_URL="https://github.com/BluPhant/StavenMediaManager"
ARG IMAGE_SOURCE="https://github.com/BluPhant/StavenMediaManager"
ARG IMAGE_VERSION="dev"
ARG IMAGE_REVISION=""
ARG IMAGE_LICENSES="MIT"
ARG BUILD_DATE=""

LABEL org.opencontainers.image.title="${IMAGE_TITLE}"
LABEL org.opencontainers.image.description="${IMAGE_DESCRIPTION}"
LABEL org.opencontainers.image.url="${IMAGE_URL}"
LABEL org.opencontainers.image.source="${IMAGE_SOURCE}"
LABEL org.opencontainers.image.version="${IMAGE_VERSION}"
LABEL org.opencontainers.image.revision="${IMAGE_REVISION}"
LABEL org.opencontainers.image.licenses="${IMAGE_LICENSES}"

# ── System deps ───────────────────────────────────────────────────────────────
# ffmpeg provides both `ffmpeg` (FLAC→MP3 conversion) and `ffprobe` (tag/stream
# inspection), used by app/services/music.py for the music import pipeline.
RUN apt-get update && \
    apt-get install -y --no-install-recommends p7zip-full openssh-client curl ffmpeg && \
    rm -rf /var/lib/apt/lists/* && \
    curl -sL https://www.rarlab.com/rar/rarlinux-x64-722.tar.gz | tar xz -C /tmp && \
    cp /tmp/rar/unrar /usr/local/bin/unrar && chmod 755 /usr/local/bin/unrar && \
    rm -rf /tmp/rar

# ── Python deps ───────────────────────────────────────────────────────────────
ENV APP_VERSION=${IMAGE_VERSION}
ENV APP_REVISION=${IMAGE_REVISION}
ENV APP_BUILD_DATE=${BUILD_DATE}

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── App source ────────────────────────────────────────────────────────────────
COPY app/ ./app/
COPY static/ ./static/

# /config  → appdata volume (SQLite DB lives here)
# /incoming → incoming share mount
# /media   → destination media share mount
VOLUME ["/config", "/incoming", "/media"]

EXPOSE 8080

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
