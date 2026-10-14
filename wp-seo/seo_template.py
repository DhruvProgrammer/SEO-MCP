"""
Rule-based logic to fill SEO fields (meta title, meta description, focus keyword).
This uses fixed formulas without any free-form AI text generation.

Copy follows the wording the site already uses in its own metadata
("Watch & download on FlixGarden", quality and language badges), so generated
titles match the existing pages rather than reading like a separate catalogue.
"""
from config import CONTENT_TYPE_TV

SITE_NAME = "FlixGarden"

# RankMath's guidance is to keep the title under 60 characters, but that
# budget has to hold the title, the year, a keyword phrase and the brand at
# once. 70 keeps a normal title plus its year intact while still cutting the
# headline rather than the suffix (see fill_seo).
MAX_TITLE_LEN = 70
MAX_DESCRIPTION_LEN = 160


def fill_seo(title: str, content: str = "", content_type: str = "movie", year: str = "") -> dict:
    """
    Generates RankMath SEO fields from fixed rule-based formulas.

    `title` should exclude the year; it is appended here when known.
    `canonical_url` is intentionally excluded, as it is derived from the
    entry's actual link in tools.py.
    """
    clean_title = " ".join((title or "").split())

    # Case-insensitive so the copy matches the entry even when the caller
    # passes "TV" rather than the normalised "tv".
    is_tv = str(content_type or "").strip().lower() == CONTENT_TYPE_TV
    if is_tv:
        suffix = f" - Watch & Download Series | {SITE_NAME}"
    else:
        suffix = f" - Watch & Download in HD | {SITE_NAME}"

    # The title has to be shortened to fit the suffix, not the other way
    # round: a long film name would otherwise eat the brand and end in a
    # dangling ellipsis.
    label = _headline(clean_title, year)
    label = _truncate(label, MAX_TITLE_LEN - len(suffix))
    seo_title = f"{label}{suffix}"

    description = (
        f"Watch and download {label} in HD on {SITE_NAME}. "
        f"Multiple qualities and audio tracks available, with full details "
        f"before you watch."
    )
    description = _truncate(description, MAX_DESCRIPTION_LEN)

    return {
        "title": seo_title,
        "description": description,
        "focus_keyword": clean_title,
    }


def _headline(title: str, year: str) -> str:
    """The 'Title (Year)' form used throughout, skipping an empty year."""
    if not title:
        return "Untitled"
    year = " ".join(str(year or "").split())
    return f"{title} ({year})" if year else title


def _truncate(text: str, max_len: int) -> str:
    """Cuts at a word boundary and appends an ellipsis, never mid-word."""
    if len(text) <= max_len:
        return text
    cut = text[: max_len - 3].rstrip()
    last_space = cut.rfind(" ")
    if last_space > 0:
        cut = cut[:last_space]
    return f"{cut}..."