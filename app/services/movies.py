import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

TMDB_BASE = "https://api.themoviedb.org/3"
TMDB_IMG = "https://image.tmdb.org/t/p/w200"

_RELEASE_TAGS = re.compile(
    r"\b(?:"
    r"1080p|720p|480p|2160p|4k|uhd|hdr10?|sdr|dv|"
    r"blu[_\-.]?ray|bluray|bdrip|brrip|bdremux|bdmv|"
    r"web[_\-.]?dl|webrip|webdl|amzn|nf|hmax|dsnp|atvp|"
    r"hdtv|pdtv|dvdrip|dvdscr|dvd|r[0-9]|"
    r"x264|x265|h264|h265|hevc|avc|xvid|divx|av1|"
    r"aac|ac3|dts|dd[25][_\-.]?1|truehd|flac|mp3|eac3|atmos|"
    r"extended|theatrical|directors?[_\-. ]?cut|remastered|unrated|uncut|"
    r"dubbed|multi|truefrench|french|english|hindi|korean|"
    r"proper|repack|readnfo|internal|retail|sample|"
    r"yify|rarbg|ettv|eztv|fgt|sparks|ion10|tigole|qxr"
    r")\b",
    re.IGNORECASE,
)


def clean_for_search(raw: str) -> tuple[str, int | None]:
    """Return (search_query, year_hint) from a raw directory name."""
    name = re.sub(r"[._]", " ", raw)
    name = re.sub(r"\s*-\s*", " ", name)
    name = re.sub(r"\[[^\]]*\]", " ", name)  # strip [tag] blocks

    # Match any plausible media year: 1900-2099
    year_m = re.search(r"\b(19\d{2}|20[0-2]\d)\b", name)
    year = int(year_m.group(1)) if year_m else None

    name = _RELEASE_TAGS.sub(" ", name)
    if year:
        name = name.replace(str(year), "")
    name = re.sub(r"[()]", "", name)
    name = re.sub(r"\s+", " ", name).strip()

    # Strip trailing all-caps tokens that look like release groups (e.g. LAMA, YIFY)
    # These survive the release-tag regex but shouldn't appear in the search suggestion.
    name = re.sub(r"(?:^|\s+)[A-Z]{2,12}$", "", name).strip()

    return name, year


def _is_tag_word(word: str) -> bool:
    """True for words that look like scene release tags: all-caps or mostly uppercase."""
    if len(word) < 2:
        return False
    upper = sum(1 for c in word if c.isupper())
    return upper / len(word) > 0.6


def _search_candidates(query: str) -> list[str]:
    """Return query variants: original first, then with tag-like words stripped
    right-to-left down to a single word."""
    words = query.split()
    tag_indices = sorted(
        [i for i, w in enumerate(words) if _is_tag_word(w)],
        reverse=True,  # rightmost first — scene tags cluster at the end
    )
    candidates = [query]
    current = list(words)
    for idx in tag_indices:
        current.pop(idx)
        if current:
            candidates.append(" ".join(current))
    return candidates


def search_tmdb(query: str, year: int | None, api_key: str) -> list[dict]:
    """
    Search TMDb with progressive query broadening.
    If the year-filtered search finds nothing, retry the same cascade without
    the year constraint so older/misdated titles still surface.
    """
    candidates = _search_candidates(query)

    # Pass 1: with year filter (most precise)
    for candidate in candidates:
        results = _tmdb_search(candidate, year, api_key)
        if results:
            return results

    # Pass 2: without year (catches pre-1950 films and mislabelled years)
    if year is not None:
        for candidate in candidates:
            results = _tmdb_search(candidate, None, api_key)
            if results:
                return results

    return []


def _tmdb_search(query: str, year: int | None, api_key: str) -> list[dict]:
    params: dict[str, str] = {
        "api_key": api_key,
        "query": query,
        "include_adult": "false",
        "language": "en-US",
        "page": "1",
    }
    if year:
        params["primary_release_year"] = str(year)

    data = _get("/search/movie", params)
    results = []
    for m in data.get("results", [])[:12]:
        yr_str = (m.get("release_date") or "")[:4]
        yr = int(yr_str) if yr_str.isdigit() else None
        poster = m.get("poster_path")
        results.append({
            "tmdb_id": m["id"],
            "title": m["title"],
            "year": yr,
            "overview": (m.get("overview") or "")[:300],
            "poster_url": f"{TMDB_IMG}{poster}" if poster else None,
            "formatted_name": f"{m['title']} ({yr_str})" if yr_str else m["title"],
        })
    return results


def auto_match_movie(item_name: str, api_key: str, db) -> "MovieMatch | None":
    """
    Auto-match a torrent item to TMDB and persist the result.

    Runs at grab time so the match is ready before the sync job downloads the file.
    Returns the saved MovieMatch, or None if no confident result found.

    Confidence rule: the best TMDB result's normalised title must contain every
    meaningful word from the cleaned query (ignores short words < 3 chars).
    """
    from ..database import SessionLocal
    from ..models import MovieMatch

    query, year = clean_for_search(item_name)
    if not query or len(query.split()) < 1:
        return None

    try:
        results = search_tmdb(query, year, api_key)
    except Exception:
        return None

    if not results:
        return None

    best = results[0]

    # Confidence check: every meaningful query word must appear in the TMDB title
    q_words = [w.lower() for w in query.split() if len(w) >= 3]
    title_lower = best["title"].lower()
    if q_words and not all(w in title_lower for w in q_words):
        return None   # ambiguous — leave for manual review

    # Upsert the match
    _db = db if db is not None else SessionLocal()
    try:
        existing = (
            _db.query(MovieMatch)
            .filter(MovieMatch.category == "movies", MovieMatch.item_name == item_name)
            .first()
        )
        if existing:
            return existing          # already matched, don't overwrite
        # Resolve IMDB ID from TMDB details
        imdb_id = None
        try:
            details = get_tmdb_details(best["tmdb_id"], api_key)
            if details:
                imdb_id = details.get("imdb_id") or None
        except Exception:
            pass
        record = MovieMatch(
            category="movies",
            item_name=item_name,
            tmdb_id=best["tmdb_id"],
            imdb_id=imdb_id,
            formatted_name=best["formatted_name"],
            year=best["year"],
            poster_url=best.get("poster_url"),
            overview=best.get("overview"),
        )
        _db.add(record)
        _db.commit()
        _db.refresh(record)
        return record
    except Exception:
        _db.rollback()
        return None
    finally:
        if db is None:
            _db.close()


def get_tmdb_details(tmdb_id: int, api_key: str) -> dict | None:
    """
    Fetch full movie details from TMDB for a known tmdb_id.
    Returns a dict that includes imdb_id (the tt-ID), or None on failure.
    """
    try:
        data = _get(f"/movie/{tmdb_id}", {"api_key": api_key, "language": "en-US"})
        yr_str = (data.get("release_date") or "")[:4]
        yr = int(yr_str) if yr_str.isdigit() else None
        poster = data.get("poster_path")
        return {
            "tmdb_id":        data["id"],
            "imdb_id":        data.get("imdb_id") or "",   # e.g. "tt1234567"
            "title":          data["title"],
            "year":           yr,
            "overview":       (data.get("overview") or "")[:500],
            "poster_url":     f"{TMDB_IMG}{poster}" if poster else None,
            "formatted_name": f"{data['title']} ({yr_str})" if yr_str else data["title"],
            "tagline":        data.get("tagline") or "",
            "vote_average":   data.get("vote_average"),
            "runtime":        data.get("runtime"),
            "genres":         [g["name"] for g in data.get("genres", [])],
        }
    except Exception as exc:
        logger.warning(f"TMDB details fetch failed for tmdb_id={tmdb_id}: {exc}")
        return None


def _get(path: str, params: dict) -> dict:
    url = f"{TMDB_BASE}{path}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"TMDb API error {exc.code}: {exc.reason}") from exc
    except Exception as exc:
        raise RuntimeError(f"TMDb request failed: {exc}") from exc
