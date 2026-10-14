"""
Loads env vars: WP_BASE_URL, WP_USERNAME, WP_APP_PASSWORD, MONGO_URI; exports as constants/settings object.
"""
import os
import urllib.parse
from pathlib import Path
from dotenv import load_dotenv

# .env is loaded from this file's own folder, not from cwd —
# this matters because Claude Code launches the MCP server as a
# subprocess whose cwd isn't guaranteed to be wp-seo/
load_dotenv(dotenv_path=Path(__file__).parent / ".env")

WP_BASE_URL = os.getenv("WP_BASE_URL")
WP_USERNAME = os.getenv("WP_USERNAME")
WP_APP_PASSWORD = os.getenv("WP_APP_PASSWORD")
MONGO_URI = os.getenv("MONGO_URI")

# FlixGarden does not store titles as regular posts. Each title is an entry in
# one of two custom post types, and each has its own REST base. The type is the
# same segment in the permalink (/movie/<slug>/, /tv/<slug>/) and in the REST
# path (/wp-json/wp/v2/movie, /wp-json/wp/v2/tv).
CONTENT_TYPE_MOVIE = "movie"
CONTENT_TYPE_TV = "tv"
CONTENT_TYPES = (CONTENT_TYPE_MOVIE, CONTENT_TYPE_TV)

# Taxonomies attached to movie/tv. These are the REST bases, which are not
# always the taxonomy slug. Verified live against the site's
# /wp-json/wp/v2/taxonomies: `genre` and `actor` both register for movie,tv.
# wp_client raises a clear error if either 404s, which is what happens if the
# taxonomy is renamed or show_in_rest is turned off.
WP_GENRE_TAXONOMY = os.getenv("WP_GENRE_TAXONOMY", "genre")
WP_ACTOR_TAXONOMY = os.getenv("WP_ACTOR_TAXONOMY", "actor")

# Genre terms rarely change, so cache them on disk between MCP subprocess runs.
GENRE_CACHE_PATH = str(Path(__file__).parent / ".genre_cache.json")
GENRE_CACHE_TTL = int(os.getenv("GENRE_CACHE_TTL", "86400"))  # seconds

# difflib ratio a scraped term must clear to be accepted as a genre match.
# Lower catches more variants but risks wrong assignments; every fuzzy hit is
# logged in the draft-run summary for review.
GENRE_FUZZY_THRESHOLD = float(os.getenv("GENRE_FUZZY_THRESHOLD", "0.78"))

# Poster downloads are streamed with a hard byte ceiling and their type is
# sniffed from magic bytes rather than trusted from the remote Content-Type
# header, which is attacker-influenced and gets forwarded into the media
# library. Raise only if the site genuinely uses oversized posters.
MAX_IMAGE_BYTES = int(os.getenv("WP_MAX_IMAGE_BYTES", str(5 * 1024 * 1024)))  # 5 MB
MAX_IMAGE_REDIRECTS = int(os.getenv("WP_MAX_IMAGE_REDIRECTS", "3"))

# Hosts a poster may be fetched from. Defaults to the site's own host, which is
# where FlixGarden's posters actually live. This is the single strongest control
# on the fetch: image_url comes off a scraped page, so it is attacker-influenced
# input, and pinning it to hosts you control removes the DNS-rebinding and
# open-redirect attack surface entirely. Widen it only if posters are genuinely
# served from elsewhere, e.g. WP_POSTER_HOSTS=flixgarden.site,cdn.example.com
POSTER_HOSTS = tuple(
    host.strip().lower()
    for host in os.getenv("WP_POSTER_HOSTS", "").split(",")
    if host.strip()
) or ((urllib.parse.urlparse(WP_BASE_URL or "").hostname or "").lower(),)

# Wall-clock ceiling for one poster download. `timeout=` on requests is a
# per-socket-read value, so a server dribbling one byte every two seconds never
# trips it while holding a worker thread for as long as it likes.
POSTER_DOWNLOAD_DEADLINE = float(os.getenv("WP_POSTER_DOWNLOAD_DEADLINE", "30"))  # seconds


def _placeholder(name: str) -> bool:
    """True when an env var is still the unfilled placeholder from .env."""
    return os.getenv(name, "").startswith("REPLACE_WITH_")


def validate_credentials() -> list[str]:
    """
    Returns a list of human-readable problems with the configured credentials.

    Called before any write so the pipeline fails with an explanation instead of
    a bare 401 from the REST API. An empty list means the config is usable.
    """
    problems = []
    if not WP_BASE_URL:
        problems.append("WP_BASE_URL is not set (expected https://flixgarden.site).")
    if _placeholder("WP_USERNAME") or not WP_USERNAME:
        problems.append(
            "WP_USERNAME is not set. Create a user on the FlixGarden site and put its "
            "username here."
        )
    if _placeholder("WP_APP_PASSWORD") or not WP_APP_PASSWORD:
        problems.append(
            "WP_APP_PASSWORD is not set. Create one under wp-admin -> Users -> Profile "
            "-> Application Passwords (it looks like 'abcd efgh ijkl mnop qrst')."
        )
    return problems