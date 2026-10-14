"""
MCP tool definitions.

  generate_seo_for_draft(post_id)          -> fetch entry -> fill SEO -> update -> log
  create_draft_from_scraped_data(data)     -> resolve taxonomies -> create draft -> poster
"""
import html
import re
from typing import Any

import config
import db
import genre_lookup
import seo_template
import taxonomy
import wp_client


def generate_seo_for_draft(post_id: str, content_type: str = "movie") -> dict[str, Any]:
    """
    Automates SEO for an existing WordPress movie/tv entry.

    1. Fetches the entry
    2. Fills SEO fields
    3. Updates it in WordPress
    4. Logs completion to MongoDB
    """
    problems = config.validate_credentials()
    if problems:
        return {"status": "error", "message": "WordPress is not configured: " + "; ".join(problems)}

    try:
        content_type = wp_client.normalize_content_type(content_type)
    except ValueError as e:
        return {"status": "error", "message": str(e)}

    try:
        entry = wp_client.get_entry(post_id, content_type)
    except Exception as e:
        return {"status": "error", "message": f"Failed to fetch entry: {str(e)}"}

    year = _year_from_entry(entry)

    seo_data = seo_template.fill_seo(
        entry.get("title", ""),
        entry.get("content", ""),
        content_type=content_type,
        year=year,
    )

    if entry.get("link"):
        seo_data["canonical_url"] = entry.get("link")

    try:
        updated = wp_client.update_entry(post_id, seo_data, content_type)
    except Exception as e:
        return {"status": "error", "message": f"Failed to update entry in WordPress: {str(e)}"}

    db.log_seo_done(post_id, content_type)

    return {
        "status": "success",
        "post_id": post_id,
        "content_type": content_type,
        "seo_data": seo_data,
        "updated_post": updated,
    }


def _envelope_error(payload: dict) -> str:
    """
    Returns the scraper's own error message if this dict reports a failure.

    Checks more than `status == "error"` because an envelope can express
    failure several ways, and missing any one of them re-creates the exact
    masking this function exists to prevent: the failure would be reported as
    "missing 'title'" and the real cause lost.
    """
    status = payload.get("status")
    status = status.strip().lower() if isinstance(status, str) else ""

    errored = (
        (bool(status) and status != "success")
        or bool(payload.get("error"))
        or payload.get("success") is False
    )
    if not errored:
        return ""

    message = payload.get("message") or payload.get("error")
    if not isinstance(message, str) or not message.strip():
        message = f"unknown failure (envelope keys: {sorted(payload)})"
    return message


def _unwrap_scraped(scraped_data: Any) -> tuple[dict | None, str]:
    """
    Accepts either the scraper's envelope or the flat metadata dict.

    The scraper MCP tool returns {"status": ..., "data": {...}} while this tool
    wants the inner object, so an agent that hands over the raw tool result
    would otherwise hit "missing 'title'". Accept both shapes so the caller
    cannot get that wrong.

    Returns (payload, error_message). Exactly one is non-None.
    """
    if not isinstance(scraped_data, dict):
        return None, f"scraped_data must be a dict, got {type(scraped_data).__name__}."

    current = scraped_data

    # Bounded loop so a nested envelope (an agent that re-wrapped the tool
    # result) unwraps fully instead of stopping one level in.
    for _ in range(3):
        message = _envelope_error(current)
        if message:
            return None, f"scraper reported an error: {message}"

        if "data" not in current:
            break

        inner = current.get("data")
        if inner is None:
            # An envelope whose payload is null. If it came with a message,
            # report that rather than falling through to "missing 'title'".
            if current.get("message") or current.get("error"):
                return None, f"scraper reported an error: {current.get('message') or current.get('error')}"
            break

        if not isinstance(inner, dict) or inner is current:
            # A flat payload may legitimately carry its own `data` field of any
            # type. An envelope whose `data` is the wrong type is malformed, so
            # only that case is an error - distinguished by the outer dict not
            # already looking like a payload.
            if not isinstance(inner, dict) and inner is not None and "title" not in current:
                return None, (
                    f"scraped_data['data'] must be a dict, got {type(inner).__name__}."
                )
            break

        # A flat scraped payload may legitimately carry its own `data` field.
        # Only descend when this dict does not already look like a payload.
        if "title" in current:
            break

        current = inner

    return current, ""


def _validate_scraped(data: dict) -> list[str]:
    """
    Rejects malformed scraped payloads before anything reaches WordPress.

    This is the guard that keeps bad shapes from becoming permanent damage.
    `cast: "Zendaya"` (a bare string instead of a list) previously created six
    junk actor terms on the live site, one per letter, and actor terms are only
    ever created -- never reused -- so nothing cleans that up automatically.
    """
    problems = []

    for key in ("title", "synopsis", "image_url"):
        value = data.get(key)
        if value is not None and not isinstance(value, str):
            problems.append(
                f"'{key}' must be a string, got {type(value).__name__}."
            )

    for key in ("genres", "cast", "extra_images"):
        value = data.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            problems.append(
                f"'{key}' must be a list of strings, but got the bare string "
                f"{value!r}. Split it into a list first."
            )
        elif not isinstance(value, list):
            problems.append(f"'{key}' must be a list, got {type(value).__name__}.")
        elif any(not isinstance(item, str) for item in value):
            problems.append(f"'{key}' must contain only strings.")

    year = data.get("year")
    if year not in (None, ""):
        if not isinstance(year, str) or not re.fullmatch(r"(19|20)[0-9]{2}", year.strip()):
            problems.append(
                f"'year' must look like 1999, got {year!r}. Send an empty value "
                "instead of a guess."
            )

    return problems


def create_draft_from_scraped_data(scraped_data: dict) -> dict:
    """
    Creates a WordPress movie/tv draft from scraped FlixGarden metadata,
    assigning matched genres and cast, and uploading the poster.

    Accepts either the flat metadata dict or the scraper tool's
    {"status": ..., "data": {...}} envelope.

    Genres are assigned only when the site already has a matching term;
    anything unrecognised is returned as `unmatched_genres` rather than
    invented. Cast terms are created on demand.
    """
    problems = config.validate_credentials()
    if problems:
        return {"status": "error", "message": "WordPress is not configured: " + "; ".join(problems)}

    scraped_data, envelope_error = _unwrap_scraped(scraped_data)
    if envelope_error:
        return {"status": "error", "message": envelope_error}

    type_problems = _validate_scraped(scraped_data)
    if type_problems:
        return {
            "status": "error",
            "message": "scraped_data is malformed: " + "; ".join(type_problems),
        }

    # Tracked so a failure after the draft exists can report its id instead of
    # stranding an orphan draft and tempting a retry that duplicates it.
    entry_id = None
    try:
        title = scraped_data.get("title", "")
        if not isinstance(title, str) or not title.strip():
            return {"status": "error", "message": "scraped_data is missing 'title'."}

        content_type = scraped_data.get("content_type") or config.CONTENT_TYPE_MOVIE
        content_type = wp_client.normalize_content_type(content_type)

        synopsis = scraped_data.get("synopsis", "")
        image_url = scraped_data.get("image_url", "")
        year = scraped_data.get("year", "")
        genres = scraped_data.get("genres") or []
        cast = scraped_data.get("cast") or []

        content = _build_content(scraped_data)

        try:
            resolved = taxonomy.resolve_genres(genres, genre_lookup.get_lookup())
        except Exception as e:
            return {"status": "error", "message": f"Failed to load genre terms: {str(e)}"}
        taxonomy.log_summary(resolved)

        genre_ids = [g["id"] for g in resolved["genre"]]
        cast_names = taxonomy.resolve_actors(cast)
        actor_ids = [wp_client.get_or_create_actor(name) for name in cast_names]

        draft = wp_client.create_draft_entry(
            title=title,
            content=content,
            content_type=content_type,
            genre_ids=genre_ids,
            actor_ids=actor_ids,
        )
        entry_id = draft.get("id")
        if not entry_id:
            raise ValueError(
                "WordPress accepted the request but returned no draft id; "
                "check for a duplicate entry before retrying."
            )

        featured_image_set = False
        featured_image_error = ""
        if image_url:
            featured_image_set, featured_image_error = wp_client.upload_featured_image(
                entry_id, image_url, content_type
            )

        return {
            "status": "success",
            "post_id": entry_id,
            "content_type": content_type,
            "link": draft.get("link"),
            "featured_image_set": featured_image_set,
            # A bare false is undiagnosable from the tool result, so the reason
            # travels with it: a renamed taxonomy or a CDN host moving off
            # WP_POSTER_HOSTS would otherwise fail silently on every draft.
            "featured_image_error": featured_image_error or None,
            "resolved": {
                "genre": [g["name"] for g in resolved["genre"]],
                "cast": cast_names,
                "unmatched_genres": resolved["unmatched"],
            },
        }
    except Exception as e:
        result = {"status": "error", "message": str(e)}
        if entry_id:
            # The draft is real and already on the site. Reporting it is the
            # difference between one stranded draft and a duplicate on retry.
            result["status"] = "partial"
            result["post_id"] = entry_id
            result["message"] = (
                f"{e} — but the draft WAS created as post_id {entry_id}. "
                "Do not retry; inspect that entry instead."
            )
        return result


def _build_content(scraped: dict) -> str:
    """
    Renders the entry body from scraped metadata.

    Every value is HTML-escaped because it came off a scraped page, and the
    optional rows are omitted entirely when the field is empty rather than
    printing "Unknown".
    """
    rows = [f"<p>{html.escape(str(scraped.get('synopsis', '') or ''))}</p>"]

    facts = [
        ("Year", scraped.get("year")),
        ("Duration", scraped.get("duration")),
        ("Quality", scraped.get("quality")),
        ("Language", scraped.get("language")),
        ("Seasons", scraped.get("season_count")),
        ("Episodes", scraped.get("episode_count")),
        ("Network", scraped.get("network")),
        ("Status", scraped.get("status")),
    ]
    lines = [
        f"<strong>{label}:</strong> {html.escape(str(value))}"
        for label, value in facts
        if value not in (None, "")
    ]
    if lines:
        rows.append("<p>" + "<br>".join(lines) + "</p>")

    genres = _unique_names(scraped.get("genres"))
    if genres:
        escaped = ", ".join(html.escape(g) for g in genres)
        rows.append(f"<p><strong>Genres:</strong> {escaped}</p>")

    return "".join(rows)


def _unique_names(values) -> list[str]:
    """
    Cleans a scraped name list, collapsing entity variants.

    The scraper merges the DOM and JSON-LD views of the same page, so the same
    genre can arrive both as "Sci-Fi & Fantasy" and as "Sci-Fi &amp; Fantasy".
    Escaping both would publish "Sci-Fi &amp; Fantasy, Sci-Fi &amp;amp;
    Fantasy" into the post body.
    """
    if not values:
        return []
    seen = set()
    out = []
    for value in values:
        if not isinstance(value, str):
            continue
        cleaned = value.strip()
        key = html.unescape(cleaned).casefold()
        if cleaned and key not in seen:
            seen.add(key)
            out.append(cleaned)
    return out


def _strip_html(markup) -> str:
    """
    Flattens post HTML to plain text.

    Entities are decoded *before* tags are stripped, not after: escaped markup
    such as `&lt;b&gt;` would otherwise survive tag-stripping and then be turned
    back into live tags afterwards, so a function documented as producing plain
    text would return markup. WordPress also renders the body through
    wptexturize, so separators can arrive as `&nbsp;`.
    """
    if not isinstance(markup, str):
        return ""
    text = html.unescape(markup).replace("\xa0", " ")
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _year_from_entry(entry: dict) -> str:
    """
    Best available release year for an existing entry, or "" when unsure.

    A wrong year is worse than no year here: it goes straight into the published
    meta title. So sources are tried strongest-first and anything unverified is
    deliberately dropped rather than guessed.

    1. The "Year:" row in the body. `_build_content` writes this from the value
       the scraper read off the detail page, so it is the only corroborated
       source available for entries this pipeline created.
    2. A parenthesised year in the title, e.g. "Blade Runner (2049)".
    3. A slug that is *entirely* a year, e.g. "1917".

    The old behaviour scanned any 4-digit run anywhere in the slug, which is
    wrong whenever a title contains a number that reads like a year:
    "2001-a-space-odyssey" -> 2001 (actually 1968),
    "blade-runner-2049" -> 2049 (actually 2017).
    """
    if not isinstance(entry, dict):
        return ""

    # 1. Body row written by _build_content. Anchored to the exact markup that
    # function emits, because an unanchored "Year:" search let prose win: the
    # synopsis paragraph renders *before* the fact row, so a synopsis mentioning
    # "set in the Year: 1994" outranked the real value on every entry.
    content = entry.get("content")
    if isinstance(content, str):
        normalized = html.unescape(content).replace("\xa0", " ")
        match = _YEAR_ROW.search(normalized)
        if match:
            year = _plausible_year(match.group(1))
            if year:
                return year

    title = entry.get("title")
    title = title if isinstance(title, str) else ""

    # 2. Explicit parenthesised year in the title.
    match = _YEAR_IN_TITLE.search(title)
    if match:
        year = _plausible_year(match.group(1))
        if year:
            return year

    # 3. Slug that is nothing but a year. Anything else is too ambiguous.
    slug = entry.get("slug")
    slug = slug.strip() if isinstance(slug, str) else ""
    if _YEAR_SLUG.fullmatch(slug):
        year = _plausible_year(slug)
        if year:
            return year

    return ""


# Anchored, ASCII-only, and range-checked by _plausible_year.
_YEAR_ROW = re.compile(r"<strong>\s*Year\s*:\s*</strong>\s*([0-9]{4})\b", re.IGNORECASE)
_YEAR_IN_TITLE = re.compile(r"\(([0-9]{4})\)")
_YEAR_SLUG = re.compile(r"[0-9]{4}")


def _plausible_year(value: str) -> str:
    """Returns the year only if it sits inside the film/television window.

    ASCII digits only. Python's `\\d` matches any Unicode decimal digit, so an
    Arabic-Indic "٢٠١٩" would otherwise reach a published meta title verbatim.
    """
    if not value or not value.isascii() or not value.isdigit():
        return ""
    year = int(value)
    return value if 1900 <= year <= 2099 else ""