import json
import re
import urllib.error
import urllib.parse
import urllib.request

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

    year_m = re.search(r"\b(19[5-9]\d|20[0-3]\d)\b", name)
    year = int(year_m.group(1)) if year_m else None

    name = _RELEASE_TAGS.sub(" ", name)
    if year:
        name = name.replace(str(year), "")
    name = re.sub(r"[()]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name, year


def _is_tag_word(word: str) -> bool:
    """True for words that look like scene release tags: all-caps or mostly uppercase."""
    if len(word) < 3:
        return False
    upper = sum(1 for c in word if c.isupper())
    return upper / len(word) > 0.6


def _search_candidates(query: str) -> list[str]:
    """Return query variants: original first, then with tag-like words stripped
    right-to-left, stopping before the query drops below 2 words."""
    words = query.split()
    tag_indices = sorted(
        [i for i, w in enumerate(words) if _is_tag_word(w)],
        reverse=True,  # rightmost first — scene tags cluster at the end
    )
    candidates = [query]
    current = list(words)
    for idx in tag_indices:
        current.pop(idx)
        if len(current) >= 2:
            candidates.append(" ".join(current))
    return candidates


def search_tmdb(query: str, year: int | None, api_key: str) -> list[dict]:
    for candidate in _search_candidates(query):
        results = _tmdb_search(candidate, year, api_key)
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


def _get(path: str, params: dict) -> dict:
    url = f"{TMDB_BASE}{path}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"TMDb API error {exc.code}: {exc.reason}") from exc
    except Exception as exc:
        raise RuntimeError(f"TMDb request failed: {exc}") from exc
