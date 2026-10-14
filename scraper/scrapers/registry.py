import urllib.parse

from .base import BaseScraper
from .flixgarden import FlixGardenScraper

# Site this pipeline reads from. Its content lives under /movie/<slug>/ and
# /tv/<slug>/, which is what the scraper validates.
_HOSTS = ("flixgarden.site", "www.flixgarden.site")

_REGISTRY = {
    "flixgarden.site": FlixGardenScraper(),
}


def get_scraper_for_url(url: str) -> BaseScraper | None:
    """Resolves a URL to its scraper, or None if the host is not registered."""
    if not isinstance(url, str):
        # An MCP client can send a non-string; urlparse would raise rather
        # than return None, so the caller never gets its error envelope.
        return None

    try:
        host = urllib.parse.urlparse(url).netloc.lower().split(":")[0]
    except (ValueError, AttributeError):
        return None

    if host not in _HOSTS:
        return None
    return _REGISTRY["flixgarden.site"]