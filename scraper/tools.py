from dataclasses import asdict
from typing import Any

from scrapers.registry import get_scraper_for_url

# A scrape that produced nothing usable usually still "succeeded" at the HTTP
# layer: Cloudflare interstitials, JS shells, soft-404s and age gates all return
# 200, so raise_for_status passes and the selectors quietly match nothing.
#
# `image_url` is deliberately NOT counted. Those shells still carry og:image
# (usually the site logo), so including it let every one of them pass as a valid
# scrape. A real detail page always has a synopsis, genres or cast.
_CONTENT_FIELDS = ("synopsis", "genres", "cast")

# Stripped before emptiness testing so a zero-width-space title counts as empty.
_ZERO_WIDTH = "\u200b\u200c\u200d\ufeff"


def _is_populated(value: Any) -> bool:
    """Whether a scraped field carries real content.

    Containers are judged by their contents, not their length, so [""] is empty
    while ["x", ""] is not.
    """
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.replace(_ZERO_WIDTH, "").strip())
    if isinstance(value, (list, tuple, set)):
        return any(_is_populated(v) for v in value)
    if isinstance(value, dict):
        return bool(value)
    return True


def _looks_empty(data: dict[str, Any]) -> bool:
    """
    True when extraction produced nothing worth publishing.

    A title plus any one real content field is the bar. Deliberately strict:
    the cost of a false negative is one reported error, while a false positive
    publishes an empty draft to the live site.
    """
    if not isinstance(data, dict):
        return True
    return not (
        _is_populated(data.get("title"))
        and any(_is_populated(data.get(f)) for f in _CONTENT_FIELDS)
    )


def _empty_reason(data: dict[str, Any]) -> str:
    """Distinguishes a bot wall from a broken selector, so the fix is obvious."""
    if any(_is_populated(data.get(f)) for f in _CONTENT_FIELDS + ("image_url",)):
        return (
            "content was extracted but no title was, which points at a layout "
            "or selector change rather than a block page."
        )
    return (
        "no metadata could be extracted. The page is most likely a bot-challenge, "
        "a JS shell, or a soft-404."
    )


def scrape_post_info(url: str) -> dict[str, Any]:
    """
    Scrapes one FlixGarden movie/tv page.

    Always returns an envelope rather than raising, because this is called
    straight from an MCP tool: an unhandled exception would surface to the
    client as a transport error instead of a readable message.
    """
    if not isinstance(url, str):
        return {
            "status": "error",
            "message": f"url must be a non-empty string, got {type(url).__name__}.",
        }
    url = url.strip()
    if not url:
        return {
            "status": "error",
            "message": "url must be a non-empty string, got an empty string.",
        }

    scraper = get_scraper_for_url(url)
    if not scraper:
        return {
            "status": "error",
            "message": (
                "No scraper registered for this site. Expected a flixgarden.site "
                f"movie or tv URL, got {url!r}."
            ),
        }

    try:
        post = scraper.scrape_post(url)
        data = asdict(post)
    except Exception as e:
        return {"status": "error", "message": str(e)}

    if _looks_empty(data):
        return {
            "status": "error",
            "message": (
                f"Fetched {url!r} but {_empty_reason(data)} Nothing was returned "
                "rather than reporting an empty success."
            ),
            "data": data,
        }

    return {"status": "success", "data": data}