from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text, UniqueConstraint

from .database import Base


class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, index=True)
    type = Column(String(50), nullable=False)
    category = Column(String(200), nullable=False)
    item_name = Column(String(500), nullable=False)
    source_path = Column(String(1000), nullable=False)
    dest_path = Column(String(1000), nullable=True)
    # pending | running | done | error | cancelled
    status = Column(String(20), default="pending", nullable=False)
    progress = Column(Integer, default=0)
    message = Column(Text, nullable=True)
    error_detail = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class MovieMatch(Base):
    __tablename__ = "movie_matches"

    id = Column(Integer, primary_key=True, index=True)
    category = Column(String(200), nullable=False)
    item_name = Column(String(500), nullable=False)   # original directory name
    formatted_name = Column(String(500), nullable=False)  # "Title (Year)"
    tmdb_id = Column(Integer, nullable=False)
    imdb_id = Column(String(20), nullable=True)        # tt-ID, resolved from TMDB
    year = Column(Integer, nullable=True)
    poster_url = Column(String(500), nullable=True)
    overview = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("category", "item_name", name="uq_movie_match_item"),
    )


class MovieSearch(Base):
    """
    Tracks every confirmed movie search, keyed by IMDB ID.
    Plex is the system of record; this table is for workflow tracking only.
    """
    __tablename__ = "movie_searches"

    imdb_id           = Column(String(20), primary_key=True)
    tmdb_id           = Column(Integer, nullable=True)
    title             = Column(String(500), nullable=False)
    year              = Column(Integer, nullable=True)
    poster_url        = Column(String(500), nullable=True)
    overview          = Column(Text, nullable=True)

    # Search tracking
    first_searched    = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_searched     = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Plex status cache (refreshed on each confirm/refresh)
    plex_found        = Column(Integer, default=0, nullable=False)   # bool
    plex_resolution   = Column(String(20), nullable=True)            # '4k','1080','720'
    plex_path         = Column(String(1000), nullable=True)
    plex_checked_at   = Column(DateTime, nullable=True)

    # Seedbox status cache
    sbx_hash          = Column(String(100), nullable=True)           # torrent info hash
    sbx_pct           = Column(Integer, nullable=True)
    sbx_checked_at    = Column(DateTime, nullable=True)

    # IPT last search cache
    ipt_best_res      = Column(String(20), nullable=True)            # '2160p','1080p'
    ipt_checked_at    = Column(DateTime, nullable=True)

    # Lifecycle status
    status            = Column(String(50), default="searched", nullable=False)
    # searched | wanted | grabbed | upgrading | in_library

    # Future queue (watch for availability)
    queued            = Column(Integer, default=0, nullable=False)   # bool
    queue_min_res     = Column(String(20), nullable=True)            # '2160p','1080p'
    queue_checked_at  = Column(DateTime, nullable=True)
    queue_check_count = Column(Integer, default=0, nullable=False)


class MusicMatch(Base):
    """Discogs match confirmed for a music item in the incoming folder."""
    __tablename__ = "music_matches"

    id         = Column(Integer, primary_key=True, index=True)
    category   = Column(String(200), nullable=False)
    item_name  = Column(String(500), nullable=False)
    discogs_id = Column(Integer, nullable=False)
    artist     = Column(String(500), nullable=False)
    album      = Column(String(500), nullable=False)
    year       = Column(Integer, nullable=True)
    label      = Column(String(500), nullable=True)
    cover_url  = Column(String(1000), nullable=True)
    genres     = Column(String(500), nullable=True)   # comma-separated
    country    = Column(String(100), nullable=True)
    tracks_json = Column(Text, nullable=True)          # JSON [{position,title,duration}]
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("category", "item_name", name="uq_music_match_item"),
    )


class UpgradeReview(Base):
    """
    Pending review when a better copy of a movie replaces an existing one.
    Old file is in .trash; new file is in the library. User must confirm or revert.
    """
    __tablename__ = "upgrade_reviews"

    id             = Column(Integer, primary_key=True, index=True)
    imdb_id        = Column(String(20), nullable=True)
    title          = Column(String(500), nullable=False)
    old_path       = Column(String(1000), nullable=False)   # folder in .trash
    new_path       = Column(String(1000), nullable=False)   # folder in movies
    old_filename   = Column(String(500), nullable=True)
    new_filename   = Column(String(500), nullable=True)
    old_size_bytes = Column(Integer, nullable=True)
    new_size_bytes = Column(Integer, nullable=True)
    old_resolution = Column(String(20), nullable=True)
    new_resolution = Column(String(20), nullable=True)
    status         = Column(String(20), default="pending", nullable=False)
    # pending | confirmed | reverted
    created_at     = Column(DateTime, default=datetime.utcnow, nullable=False)
    resolved_at    = Column(DateTime, nullable=True)


class SyncedItem(Base):
    """Tracks torrent hashes that have already been imported, preventing re-download."""
    __tablename__ = "synced_items"

    id = Column(Integer, primary_key=True, index=True)
    source = Column(String(50), nullable=False)   # e.g. "rtorrent"
    item_id = Column(String(200), nullable=False)  # torrent hash
    name = Column(String(500), nullable=True)
    synced_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("source", "item_id", name="uq_synced_item"),
    )
