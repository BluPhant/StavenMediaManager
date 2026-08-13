"""
Audiobook metadata clients.

Two-step workflow (mirrors Audiobookshelf):
  1. search_audible(title, author) — Audible Catalog API → candidates with ASINs
  2. get_audnexus_book(asin)       — Audnexus public API → narrator, cover, duration

The Audible Catalog API is undocumented/unofficial but is the same source Audnexus
and Audiobookshelf use internally. No auth required for basic reads.
"""
import json
import logging
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

AUDIBLE_BASE = "https://api.audible.com/1.0"
AUDNEXUS_BASE = "https://api.audnex.us"

_RESPONSE_GROUPS = "product_desc,contributors,product_attrs,series,media"


def search_audible(title: str, author: str = "") -> list[dict]:
    """
    Search the Audible catalog by title and/or author.
    When only an author is given, falls back to a keyword search so author-only
    queries return results.
    """
    params: dict[str, str] = {
        "num_results": "25",
        "products_sort_by": "Relevance",
        "response_groups": _RESPONSE_GROUPS,
    }
    if title:
        params["title"] = title
        if author:
            params["author"] = author
    elif author:
        # Author-only: use keywords for a broader search
        params["keywords"] = author
    else:
        return []
    try:
        data = _audible_get("/catalog/products", params)
    except Exception as exc:
        logger.warning(f"Audible search failed title={title!r} author={author!r}: {exc}")
        return []
    return [_parse_product(p) for p in data.get("products", [])]


def lookup_by_asin(asin: str) -> dict | None:
    """
    Look up a single book by ASIN. Tries Audnexus first (best metadata), then
    falls back to the Audible Catalog single-product endpoint across multiple
    marketplaces (US → UK) to cover region-specific editions like Dolby Atmos.
    Returns a candidate dict in the same shape as search_audible results, or None.
    """
    # Try Audnexus — richest metadata when available
    try:
        data = _audnexus_get(f"/books/{asin}")
        if data.get("title"):
            author_str   = ", ".join(a["name"] for a in data.get("authors", []))
            narrator_str = ", ".join(n["name"] for n in data.get("narrators", []))
            year         = _year_from_date(data.get("releaseDate", ""))
            return {
                "asin":             asin,
                "title":            data.get("title", ""),
                "author":           author_str,
                "narrator":         narrator_str,
                "duration_minutes": data.get("runtimeLengthMin"),
                "series_title":     None,
                "series_sequence":  None,
                "year":             year,
                "cover_url":        data.get("image"),
                "publisher":        data.get("publisherName", ""),
                "formatted_name":   _make_formatted_name(author_str, data.get("title", ""), year, None, None),
            }
    except Exception:
        pass

    # Fall back to Audible catalog single-product endpoint.
    # Try US first, then UK for region-exclusive editions (e.g. Dolby Atmos).
    for base in (AUDIBLE_BASE, "https://api.audible.co.uk/1.0"):
        try:
            data = _audible_get_raw(f"{base}/catalog/products/{asin}",
                                    {"response_groups": _RESPONSE_GROUPS})
            product = data.get("product", {})
            if product.get("title"):
                return _parse_product(product)
        except Exception as exc:
            logger.debug("ASIN lookup failed on %s for %s: %s", base, asin, exc)

    logger.warning("ASIN lookup exhausted all sources for %s", asin)
    return None


def get_audnexus_book(asin: str) -> dict | None:
    """
    Fetch enriched book detail from Audnexus (narrator, cover, duration, genres).
    Returns None on failure. Intended to enrich a candidate from search_audible.
    """
    try:
        data = _audnexus_get(f"/books/{asin}")
        return {
            "asin":             data.get("asin", asin),
            "title":            data.get("title", ""),
            "author":           ", ".join(a["name"] for a in data.get("authors", [])),
            "narrator":         ", ".join(n["name"] for n in data.get("narrators", [])),
            "duration_minutes": data.get("runtimeLengthMin"),
            "cover_url":        data.get("image"),
            "year":             _year_from_date(data.get("releaseDate", "")),
            "publisher":        data.get("publisherName", ""),
            "genres":           ", ".join(g["name"] for g in data.get("genres", [])),
            "description":      _strip_html(data.get("description", "")),
            "rating":           data.get("rating"),
        }
    except Exception as exc:
        logger.warning(f"Audnexus fetch failed for {asin}: {exc}")
        return None


# ── Internal helpers ──────────────────────────────────────────────────────────

def _parse_product(p: dict) -> dict:
    authors   = [a["name"] for a in p.get("authors", [])]
    narrators = [n["name"] for n in p.get("narrators", [])]

    series_list     = p.get("series") or []
    series_title    = series_list[0]["title"] if series_list else None
    series_sequence = series_list[0].get("sequence") if series_list else None

    images    = p.get("product_images") or {}
    cover_url = images.get("500") or images.get("300") or None

    # runtime_length_min lives at the top level in the catalog response
    duration = p.get("runtime_length_min")

    year = _year_from_date(p.get("release_date", ""))

    author_str   = ", ".join(authors)
    narrator_str = ", ".join(narrators)
    title        = p.get("title", "")

    return {
        "asin":             p.get("asin", ""),
        "title":            title,
        "author":           author_str,
        "narrator":         narrator_str,
        "duration_minutes": duration,
        "series_title":     series_title,
        "series_sequence":  series_sequence,
        "year":             year,
        "cover_url":        cover_url,
        "publisher":        p.get("publisher_name", ""),
        "formatted_name":   _make_formatted_name(
            author_str, title, year, series_title, series_sequence
        ),
    }


def _make_formatted_name(author: str, title: str, year: int | None,
                          series_title: str | None, series_sequence: str | None) -> str:
    """
    Canonical library folder name.
    "Patrick Rothfuss - Kingkiller Chronicle 1 - The Name of the Wind (2007)"
    "Andy Weir - Project Hail Mary (2021)"
    """
    parts = []
    if author:
        parts.append(author)
    if series_title and series_sequence:
        parts.append(f"{series_title} {series_sequence}")
    elif series_title:
        parts.append(series_title)
    parts.append(title + (f" ({year})" if year else ""))
    return " - ".join(parts)


def _year_from_date(date_str: str) -> int | None:
    if not date_str:
        return None
    try:
        return int(str(date_str)[:4])
    except (ValueError, TypeError):
        return None


def _strip_html(text: str) -> str:
    """Remove HTML tags from Audnexus description field."""
    import re
    return re.sub(r"<[^>]+>", "", text or "").strip()


def _audible_get(path: str, params: dict) -> dict:
    return _audible_get_raw(f"{AUDIBLE_BASE}{path}", params)


def _audible_get_raw(full_url: str, params: dict) -> dict:
    url = f"{full_url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url, headers={"User-Agent": "StavenMediaManager/1.0 (media manager)"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Audible API error {exc.code}: {exc.reason}") from exc
    except Exception as exc:
        raise RuntimeError(f"Audible request failed: {exc}") from exc


def _audnexus_get(path: str) -> dict:
    url = f"{AUDNEXUS_BASE}{path}"
    req = urllib.request.Request(
        url, headers={"User-Agent": "StavenMediaManager/1.0 (media manager)"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Audnexus API error {exc.code}: {exc.reason}") from exc
    except Exception as exc:
        raise RuntimeError(f"Audnexus request failed: {exc}") from exc
