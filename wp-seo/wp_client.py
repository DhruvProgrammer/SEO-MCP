"""
WordPress REST API client for FlixGarden.

Titles live in the `movie` and `tv` custom post types rather than `post`, and
carry the `genre` and `actor` taxonomies. Everything here therefore addresses
those types and taxonomies by name instead of the usual categories/tags.
"""
import base64
import ipaddress
import re
import socket
import sys
import time
import urllib.parse

import requests
import urllib3.util

from config import (
    CONTENT_TYPE_MOVIE,
    CONTENT_TYPES,
    MAX_IMAGE_BYTES,
    MAX_IMAGE_REDIRECTS,
    POSTER_DOWNLOAD_DEADLINE,
    POSTER_HOSTS,
    WP_ACTOR_TAXONOMY,
    WP_BASE_URL,
    WP_GENRE_TAXONOMY,
    WP_USERNAME,
    WP_APP_PASSWORD,
)


class WordPressAPIError(Exception):
    """A non-2xx response from the WordPress REST API."""

    def __init__(self, msg, status, details):
        super().__init__(msg)
        self.status = status
        self.details = details


def get_base_url():
    return (WP_BASE_URL or "").rstrip("/")


def auth_header():
    token = f"{WP_USERNAME}:{WP_APP_PASSWORD}"
    encoded = base64.b64encode(token.encode("utf-8")).decode("utf-8")
    return f"Basic {encoded}"


def wp_fetch(path: str, method: str = "GET", body: dict = None, timeout: int = 30) -> dict:
    url = f"{get_base_url()}/wp-json{path}"
    headers = {
        "Authorization": auth_header(),
        "Content-Type": "application/json",
    }

    response = requests.request(method, url, headers=headers, json=body, timeout=timeout)

    try:
        data = response.json() if response.text else None
    except ValueError:
        data = response.text

    if not response.ok:
        message = ""
        if isinstance(data, dict):
            message = data.get("message") or data.get("code")
        if not message:
            message = f"WordPress REST API error ({response.status_code})"

        # An unset application password fails as 401, not 403, so word the
        # message to cover both auth failure modes.
        if response.status_code in (401, 403):
            message = (
                f"{message} — check WP_USERNAME / WP_APP_PASSWORD in .env "
                f"(application password, not the account password)"
            )

        raise WordPressAPIError(
            f"{message} [{method} {path}]", response.status_code, data
        )

    return data


def normalize_content_type(content_type: str) -> str:
    """
    Returns the canonical content type, or raises ValueError.

    Always raises the same error for anything unusable, including non-strings,
    so callers only have to handle one failure mode.
    """
    if not isinstance(content_type, str):
        raise ValueError(
            f"Unknown content type {content_type!r}; expected one of {CONTENT_TYPES}."
        )
    value = content_type.strip().lower()
    if value not in CONTENT_TYPES:
        raise ValueError(
            f"Unknown content type {content_type!r}; expected one of {CONTENT_TYPES}."
        )
    return value


def _seo_from_meta(meta: dict) -> dict:
    meta = meta or {}
    return {
        "rank_math_title": meta.get("rank_math_title", ""),
        "rank_math_description": meta.get("rank_math_description", ""),
        "rank_math_focus_keyword": meta.get("rank_math_focus_keyword", ""),
        "rank_math_canonical_url": meta.get("rank_math_canonical_url", ""),
    }


def get_entry(entry_id, content_type: str = CONTENT_TYPE_MOVIE) -> dict:
    """
    Fetches one movie/tv entry by ID, normalized to the shape the SEO tool wants.

    `context=edit` is what exposes the raw title text and the meta box; it
    requires the application password.
    """
    post_type = normalize_content_type(content_type)
    item = wp_fetch(f"/wp/v2/{post_type}/{entry_id}?context=edit")

    title = ""
    if isinstance(item.get("title"), dict):
        title = item["title"].get("raw") or item["title"].get("rendered", "")

    content = ""
    if isinstance(item.get("content"), dict):
        content = item["content"].get("raw") or item["content"].get("rendered", "")

    return {
        "id": item.get("id"),
        "type": post_type,
        "title": title,
        "content": content,
        "status": item.get("status"),
        "slug": item.get("slug"),
        "link": item.get("link"),
        "modified": item.get("modified"),
        "seo": _seo_from_meta(item.get("meta", {})),
    }


def update_entry(entry_id, seo_data: dict, content_type: str = CONTENT_TYPE_MOVIE) -> dict:
    """Pushes RankMath SEO fields back to a movie/tv entry."""
    post_type = normalize_content_type(content_type)
    meta = {}
    if "title" in seo_data:
        meta["rank_math_title"] = seo_data["title"]
    if "description" in seo_data:
        meta["rank_math_description"] = seo_data["description"]
    if "focus_keyword" in seo_data:
        meta["rank_math_focus_keyword"] = seo_data["focus_keyword"]
    if "canonical_url" in seo_data:
        meta["rank_math_canonical_url"] = seo_data["canonical_url"]

    if not meta:
        return {"id": entry_id, "link": None, "seo": {}}

    # context=edit is what makes WordPress include `meta` in the response. Without
    # it the reply is view-context, `meta` is omitted entirely, and the check
    # below could never distinguish a successful write from a silently dropped
    # one - which is how a completely failed RankMath update ended up reported
    # as a success with four empty strings.
    item = wp_fetch(
        f"/wp/v2/{post_type}/{entry_id}?context=edit", method="POST", body={"meta": meta}
    )

    written = _seo_from_meta(item.get("meta", {}))
    missing = [key for key, value in written.items() if not value and meta.get(key)]
    if missing:
        raise WordPressAPIError(
            f"RankMath meta did not persist on {post_type}/{entry_id}: {missing}. "
            "The entry was updated but those fields were silently dropped - check "
            "that RankMath is active and registered for this post type.",
            200,
            written,
        )

    return {
        "id": item.get("id"),
        "link": item.get("link"),
        "seo": written,
    }


def check_connection() -> dict:
    """Connection check for manual testing."""
    me = wp_fetch("/wp/v2/users/me?context=edit")
    return {
        "connected": True,
        "site": get_base_url(),
        "user": me.get("name"),
        "roles": me.get("roles"),
    }


def _fetch_all_terms(rest_base: str) -> list[dict]:
    """
    Pages through a taxonomy endpoint and returns every term.

    wp_fetch discards response headers, so X-WP-TotalPages is unavailable;
    instead we stop on the first short page. A page request past the end
    returns a 400 (rest_post_invalid_page_number), which we treat as the end
    rather than an error.
    """
    terms: list[dict] = []
    per_page = 100
    page = 1

    while True:
        params = urllib.parse.urlencode({
            "per_page": str(per_page),
            "page": str(page),
            "hide_empty": "false",
            "context": "edit",
            "_fields": "id,name,slug",
        })
        try:
            batch = wp_fetch(f"/wp/v2/{rest_base}?{params}")
        except WordPressAPIError as e:
            if page > 1 and e.status == 400:
                break
            raise

        if not isinstance(batch, list) or not batch:
            break

        terms.extend(batch)
        if len(batch) < per_page:
            break

        page += 1
        if page > 50:  # 5000 terms; a runaway guard, not a real limit
            print(
                f"Warning: stopped paging /wp/v2/{rest_base} at page 50.",
                file=sys.stderr,
            )
            break

    return terms


def get_genre_terms() -> list[dict]:
    """
    Fetches every term in the custom genre taxonomy.

    Called by genre_lookup, which caches the result with a TTL. Genre terms are
    only ever read, never created.
    """
    try:
        return _fetch_all_terms(WP_GENRE_TAXONOMY)
    except WordPressAPIError as e:
        if e.status == 404:
            raise RuntimeError(
                f"Genre taxonomy '/wp/v2/{WP_GENRE_TAXONOMY}' not found. Confirm the "
                f"taxonomy is registered for movie/tv with show_in_rest => true and that "
                f"its rest_base matches WP_GENRE_TAXONOMY in config.py."
            ) from e
        raise


def _find_existing_term(rest_base: str, name: str) -> int | None:
    """Searches a taxonomy for an exact name match. Returns None when absent."""
    params = urllib.parse.urlencode({
        "search": name, "per_page": "100", "page": "1",
        "hide_empty": "false", "context": "edit",
    })
    try:
        terms = wp_fetch(f"/wp/v2/{rest_base}?{params}")
    except WordPressAPIError as e:
        if e.status == 404:
            return None
        raise

    for term in terms or []:
        if _same_term_name(term.get("name", ""), name):
            return term["id"]
    return None


def _same_term_name(left: str, right: str) -> bool:
    """
    Compares term names the way WordPress displays them.

    Term names in this site store HTML entities literally — the REST API
    returns "Sci-Fi &amp; Fantasy", and so does the rendered page — while a
    name typed by hand arrives as "Sci-Fi & Fantasy". Comparing the decoded
    forms makes those two the same term instead of a duplicate.
    """
    return _normalize_term_name(left) == _normalize_term_name(right)


def _normalize_term_name(name: str) -> str:
    decoded = re.sub(r"&amp;", "&", name or "")
    return re.sub(r"\s+", " ", decoded).strip().casefold()


def get_or_create_actor(name: str) -> int:
    """
    Resolves an actor name to its term ID, creating the term when absent.

    Actors are free-form here (the site has thousands), so creation is expected
    rather than an error — unlike genres, which are a closed set.
    """
    existing = _find_existing_term(WP_ACTOR_TAXONOMY, name)
    if existing is not None:
        return existing

    created = wp_fetch(f"/wp/v2/{WP_ACTOR_TAXONOMY}", method="POST", body={"name": name})
    if not isinstance(created, dict) or "id" not in created:
        raise RuntimeError(
            f"WordPress accepted the actor term '{name}' but returned no id; "
            f"cannot assign cast without a term ID."
        )
    return created["id"]


def create_draft_entry(
    title: str,
    content: str,
    content_type: str = CONTENT_TYPE_MOVIE,
    genre_ids: list[int] | None = None,
    actor_ids: list[int] | None = None,
) -> dict:
    """
    Creates a movie/tv entry as a draft with taxonomies already resolved.

    Resolution happens in taxonomy.resolve_genres and tools.create_draft_from_
    scraped_data; this function only assigns what it is given.
    """
    post_type = normalize_content_type(content_type)
    body = {
        "title": title,
        "content": content,
        "status": "draft",
        WP_GENRE_TAXONOMY: genre_ids or [],
        WP_ACTOR_TAXONOMY: actor_ids or [],
    }
    item = wp_fetch(f"/wp/v2/{post_type}", method="POST", body=body)
    return {"id": item["id"], "link": item.get("link"), "type": post_type}


# Hosts that must never be fetched even though they resolve. The metadata
# endpoint in particular is the classic SSRF pivot for leaking cloud creds.
_BLOCKED_HOSTNAMES = {
    "localhost",
    "metadata.google.internal",
    "instance-data",
}

_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".heic")


def _sniff_image(data: bytes) -> tuple[str | None, str | None]:
    """
    Identifies an image from its magic bytes, returning (mime, extension).

    The remote server's Content-Type is attacker-influenced and used to be
    forwarded straight into the media library, so a hostile host could make an
    HTML page (or anything else) land in the library tagged as an image. The
    payload itself is the only trustworthy signal.
    """
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif", ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", ".webp"
    if data[4:8] == b"ftyp":
        brand = data[8:12]
        if brand in (b"avif", b"avis"):
            return "image/avif", ".avif"
        if brand in (b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1"):
            return "image/heic", ".heic"
    return None, None


def _parsed_host(url: str) -> str:
    """
    Returns the host that requests will ACTUALLY connect to.

    Two parsers matter here and they do not agree. urllib.parse takes the text
    after the last '@' as the host; urllib3 - which requests uses to build the
    connection - takes the text before it. One backslash desynchronises them:

        http://127.0.0.1\\@allowed.host/x
          urlparse().hostname  -> allowed.host   (what we would validate)
          urllib3 parse_url()  -> 127.0.0.1      (what we connect to)

    Rather than reason about which parser is authoritative, both are consulted
    and required to agree, and a backslash in the authority is refused outright.
    """
    parsed = urllib.parse.urlparse(url)
    netloc = parsed.netloc or ""

    if "\\" in netloc:
        raise ValueError(
            "image_url contains a backslash in its authority, which different "
            "URL parsers disagree about. Refusing it."
        )
    if netloc.count("@") > 1:
        raise ValueError("image_url contains more than one '@' in its authority.")

    host = (parsed.hostname or "").strip().lower()

    try:
        upstream = urllib3.util.parse_url(url).host
    except Exception:
        upstream = None
    if upstream and host and upstream.lower() != host:
        raise ValueError(
            f"image_url is ambiguous: it parses as host {host!r} one way and "
            f"{upstream!r} another. Refusing it."
        )

    return host


def _assert_fetchable_url(url: str) -> None:
    """
    Rejects anything that isn't a public http(s) image URL on an allowed host.

    Three independent controls, because any one alone has been insufficient:

    1. A host allowlist (config.POSTER_HOSTS, defaulting to the site's own
       host). image_url is scraped, hence attacker-influenced; pinning fetches
       to hosts we control removes the open-redirect and DNS-rebinding surface
       before any of it matters. Fails CLOSED: an unresolvable allowlist is an
       error, never "allow everything".
    2. Parser agreement (_parsed_host), so the host validated is the host
       dialled.
    3. Resolved-address inspection, so decimal/octal/hex IP forms and friends
       are classified by the address they resolve to rather than by their
       spelling. Every getaddrinfo result is checked, not just the first.
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"image_url must be http or https, got {parsed.scheme or 'no scheme'!r}.")

    host = _parsed_host(url)
    if not host:
        raise ValueError("image_url has no host.")
    if host in _BLOCKED_HOSTNAMES:
        raise ValueError(f"refusing to fetch internal host {host!r}.")

    allowed = tuple(h for h in POSTER_HOSTS if h)
    if not allowed:
        raise ValueError(
            "no poster hosts are configured, so the fetch would be unrestricted. "
            "Set WP_BASE_URL (with a scheme) or WP_POSTER_HOSTS."
        )
    if host not in allowed:
        raise ValueError(
            f"refusing to fetch {host!r}: poster hosts are restricted to "
            f"{list(allowed)}. Set WP_POSTER_HOSTS to widen this."
        )

    try:
        addresses = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as e:
        raise ValueError(f"could not resolve host {host!r}.") from e

    for info in addresses:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise ValueError(
                f"refusing to fetch {host!r}: it resolves to {ip}, a private or internal address."
            )


def _download_capped(url: str, headers: dict) -> bytes:
    """
    Streams an image into memory with a hard byte ceiling and a wall-clock cap.

    Redirects are followed *manually*, and only because allow_redirects=False is
    passed explicitly. requests follows the whole chain itself by default, which
    means the hop-by-hop validation below would never run and a public host
    could bounce the fetch straight at an internal address.
    """
    current = url
    deadline = time.monotonic() + POSTER_DOWNLOAD_DEADLINE

    # A read timeout is per-socket-read, not a budget: a server dribbling one
    # byte every 19s never trips a 20s timeout. Sizing it as a fraction of the
    # deadline also bounds the overshoot -- the deadline is only consulted
    # between reads, so the worst case is "deadline + one read timeout".
    read_timeout = max(0.5, min(5.0, POSTER_DOWNLOAD_DEADLINE / 3))

    for _ in range(MAX_IMAGE_REDIRECTS + 1):
        if time.monotonic() > deadline:
            raise ValueError(
                f"download exceeded the {POSTER_DOWNLOAD_DEADLINE}s wall-clock deadline."
            )

        _assert_fetchable_url(current)

        with requests.get(
            current,
            headers=headers,
            timeout=(10, read_timeout),
            stream=True,
            allow_redirects=False,
        ) as res:
            if res.is_redirect or res.is_permanent_redirect:
                location = res.headers.get("location")
                if not location:
                    raise ValueError(f"redirect from {current!r} had no Location header.")
                current = urllib.parse.urljoin(current, location)
                continue

            res.raise_for_status()

            declared = (res.headers.get("content-length") or "").strip()
            # isascii() matters: str.isdigit() is true for Unicode superscripts,
            # which then blow up int() and turn any image into a hard failure.
            if declared and declared.isascii() and declared.isdigit():
                if int(declared) > MAX_IMAGE_BYTES:
                    raise ValueError(
                        f"image declares {declared} bytes, over the {MAX_IMAGE_BYTES} byte limit."
                    )

            buffer = bytearray()
            # A small read size keeps the deadline check fine-grained; at 64 KiB
            # the clock is only consulted once per 64 KiB delivered, which a
            # slow server can stretch indefinitely.
            for chunk in res.iter_content(16384):
                buffer.extend(chunk)
                if len(buffer) > MAX_IMAGE_BYTES:
                    raise ValueError(f"image exceeded the {MAX_IMAGE_BYTES} byte limit while downloading.")
                if time.monotonic() > deadline:
                    raise ValueError(
                        f"download exceeded the {POSTER_DOWNLOAD_DEADLINE}s wall-clock deadline."
                    )
            return bytes(buffer)

    raise ValueError(f"too many redirects while fetching the image (limit {MAX_IMAGE_REDIRECTS}).")


def _safe_filename(image_url: str, extension: str) -> str:
    """
    Derives a filesystem-safe attachment name from the URL path.

    Any extension the URL carries is discarded and replaced with the sniffed
    one, so the filename and the Content-Type header can never disagree (a URL
    ending ".jpg" that actually serves a PNG is common on these CDNs).

    Only the final extension is stripped, so an intermediate one would survive:
    "/shell.php.jpg" -> "shell-php.jpg". Not exploitable on a default
    Apache/nginx, but it is on an AddHandler/SetHandler catch-all, so any dot
    left after stripping is removed rather than raised on. Refusing outright
    would be marginally stricter, but would also break permanently on an
    ordinary upload named "Movie.Title.2024.jpg".
    """
    path = urllib.parse.urlparse(image_url).path
    raw = urllib.parse.unquote(path.rsplit("/", 1)[-1])
    stem = re.sub(r"[^A-Za-z0-9._-]", "-", raw).strip("-._")
    for known in _IMAGE_EXTENSIONS:
        if stem.lower().endswith(known):
            stem = stem[: -len(known)]
            break
    stem = stem.replace(".", "-").strip("-._")[:60]
    return (stem or "poster") + extension


def upload_featured_image(
    entry_id,
    image_url: str,
    content_type: str = CONTENT_TYPE_MOVIE,
) -> tuple[bool, str]:
    """
    Downloads image_url and attaches it as the entry's featured image.

    Returns (ok, reason). A missing poster is never worth failing a whole draft
    creation over, but a bare `False` is undiagnosable to the caller, so the
    reason travels with the result.

    `content_type` is required because movie and tv share an ID space: guessing
    the type from the media object's own permalink would target the wrong post.

    The fetch is bounded on every axis that matters: scheme and host are
    checked (including after each redirect), the body is streamed under a byte
    cap, and the image type is sniffed from the payload rather than taken from
    the remote Content-Type header.
    """
    post_type = "?"
    try:
        post_type = normalize_content_type(content_type)
        if not isinstance(image_url, str) or not image_url.strip():
            raise ValueError("image_url was empty.")

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
            )
        }
        content = _download_capped(image_url.strip(), headers)

        if not content:
            raise ValueError("downloaded image was empty.")

        mime_type, extension = _sniff_image(content)
        if not mime_type:
            preview = content[:12]
            raise ValueError(
                f"downloaded body is not a recognised image (sniffed {preview!r}); "
                "refusing to add it to the media library."
            )

        filename = _safe_filename(image_url, extension)

        upload_headers = {
            "Authorization": auth_header(),
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Type": mime_type,
        }

        upload_url = f"{get_base_url()}/wp-json/wp/v2/media"
        upload_res = requests.post(upload_url, headers=upload_headers, data=content, timeout=60)
        upload_res.raise_for_status()

        try:
            media = upload_res.json()
        except ValueError as e:
            raise ValueError(
                f"media upload returned non-JSON (HTTP {upload_res.status_code})"
            ) from e

        # A 200 here means the upload was accepted but no attachment was created.
        media_id = media.get("id") if isinstance(media, dict) else None
        if not media_id:
            raise ValueError(f"media upload returned no id (HTTP {upload_res.status_code})")

        wp_fetch(
            f"/wp/v2/{post_type}/{entry_id}", method="POST", body={"featured_media": media_id}
        )
        return True, ""
    except Exception as e:
        reason = str(e) or type(e).__name__
        print(
            f"Warning: Failed to upload featured image for {post_type} entry "
            f"{entry_id}: {reason}",
            file=sys.stderr,
        )
        return False, reason