"""
FlixGarden scraper.

FlixGarden publishes one page per title under /movie/<slug>/ or /tv/<slug>/,
and every page carries two redundant descriptions of the same data:

  1. a schema.org JSON-LD block (Movie / TVSeries) — the most reliable source,
     because the theme generates it from the post's own fields
  2. the rendered hero markup (.mf-* classes)

JSON-LD is read first and the DOM is used to fill gaps and to cross-check, so
a page that drops one source still scrapes. Read-only: this never downloads
media, it only reads the public HTML.
"""
import html
import json
import re
import urllib.parse
from typing import Optional

import requests
from bs4 import BeautifulSoup

from .base import (
    CONTENT_TYPE_MOVIE,
    CONTENT_TYPE_TV,
    BaseScraper,
    ScrapedPost,
)

BASE_URL = "https://flixgarden.site"

# The hero meta grid renders as value/label pairs, e.g.
#   <div class="mf-meta-v">4K</div><div class="mf-meta-l">Quality</div>
# Labels are normalised to a single key so movie and TV pages share one parser.
# "first aired" is recognised but not kept: ScrapedPost has no field for it, and
# an unrecognised label is skipped by _hero_meta just the same.
_META_LABEL_KEYS = {
    "imdb": "imdb_rating",
    "duration": "duration",
    "year": "year",
    "quality": "quality",
    "language": "language",
    "seasons": "season_count",
    "episodes": "episode_count",
    "first aired": "first_aired",
    "network": "network",
    "status": "status",
}
_INT_META_KEYS = {"season_count", "episode_count"}

# Keeps the payload a sane size; nothing downstream uploads more than the poster.
MAX_EXTRA_IMAGES = 6

# schema.org types this scraper reads, as they appear in the JSON-LD block.
CONTENT_TYPE_SCHEMA = {"Movie", "TVSeries"}


class FlixGardenScraper(BaseScraper):
    def scrape_post(self, url: str) -> ScrapedPost:
        self._require_content_url(url)
        html = self._fetch(url)
        soup = BeautifulSoup(html, "html.parser")
        ld = self._json_ld(soup)
        meta = self._hero_meta(soup)

        content_type = self._content_type(url, ld)
        title, ld_year = self._title(soup, ld)

        return ScrapedPost(
            title=title,
            content_type=content_type,
            synopsis=self._synopsis(soup, ld),
            image_url=self._image(soup, ld),
            genres=self._genres(soup, ld),
            cast=self._cast(soup, ld),
            year=self._year(soup, meta, ld_year),
            duration=meta.get("duration") or self._duration_from_iso(ld.get("duration")),
            quality=meta.get("quality"),
            language=meta.get("language"),
            imdb_rating=meta.get("imdb_rating"),
            season_count=meta.get("season_count"),
            episode_count=meta.get("episode_count"),
            network=meta.get("network"),
            status=meta.get("status"),
            extra_images=self._extra_images(soup),
            external_id=self._slug(url),
            source_url=url,
        )

    def _require_content_url(self, url: str) -> None:
        """
        Rejects anything that is not a /movie/<slug>/ or /tv/<slug>/ page.

        Without this the scraper happily returns an empty-but-successful result
        for pages like /about/, which is worse than an error: a caller cannot
        tell "no data" apart from "wrong page".
        """
        segments = [s for s in urllib.parse.urlparse(url).path.split("/") if s]
        if len(segments) < 2 or segments[0] not in (CONTENT_TYPE_MOVIE, CONTENT_TYPE_TV):
            raise ValueError(
                f"Not a FlixGarden content URL: expected /{CONTENT_TYPE_MOVIE}/<slug>/ or "
                f"/{CONTENT_TYPE_TV}/<slug>/, got {urllib.parse.urlparse(url).path!r}"
            )

    def _fetch(self, url: str) -> str:
        resp = requests.get(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
                )
            },
            timeout=20,
        )
        resp.raise_for_status()
        return resp.text

    def _json_ld(self, soup: BeautifulSoup) -> dict:
        """Returns the Movie/TVSeries block, skipping BreadcrumbList and friends."""
        for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
            raw = tag.string or tag.get_text() or ""
            try:
                data = json.loads(raw.strip())
            except (ValueError, TypeError):
                continue
            if isinstance(data, dict) and _types(data) & CONTENT_TYPE_SCHEMA:
                return data
        return {}

    def _content_type(self, url: str, ld: dict) -> str:
        if "TVSeries" in _types(ld):
            return CONTENT_TYPE_TV
        segments = [s for s in urllib.parse.urlparse(url).path.split("/") if s]
        if segments and segments[0] == CONTENT_TYPE_TV:
            return CONTENT_TYPE_TV
        return CONTENT_TYPE_MOVIE

    def _title(self, soup: BeautifulSoup, ld: dict) -> tuple[str, Optional[str]]:
        """
        Returns (title, year).

        The h1 nests the year in a span, so the h1's own text has to be read
        with that span excluded; JSON-LD `name` is used first because it is
        already clean.
        """
        ld_name = (ld.get("name") or "").strip()
        if ld_name:
            return ld_name, _year_from_label(ld.get("alternateName"))

        h1 = soup.find("h1", class_="mf-h1")
        if h1:
            year_span = h1.find("span", class_="mf-h1-year")
            year = year_span.get_text(strip=True) if year_span else None
            if year_span:
                year_span.extract()
            title = h1.get_text(strip=True)
            if title:
                return title, year

        og = soup.find("meta", property="og:title")
        return (og["content"].strip() if og and og.get("content") else ""), None

    def _synopsis(self, soup: BeautifulSoup, ld: dict) -> str:
        if ld.get("description"):
            return str(ld["description"]).strip()
        # TV pages put the logline in .mf-tagline, movies in .mf-desc.
        for cls in ("mf-desc", "mf-tagline"):
            node = soup.find(class_=cls)
            if node:
                text = node.get_text(strip=True)
                if text:
                    return text
        return ""

    def _image(self, soup: BeautifulSoup, ld: dict) -> str:
        if ld.get("image"):
            return str(ld["image"]).strip()
        og = soup.find("meta", property="og:image")
        return og["content"].strip() if og and og.get("content") else ""

    def _genres(self, soup: BeautifulSoup, ld: dict) -> list[str]:
        """
        The hero kicker pills and the JSON-LD genre array are merged, pills
        first.

        Order is preserved and duplicates collapsed, since the pills are the
        order the editor chose. Either source may be absent, so both are read
        rather than falling back.
        """
        from_pills = [
            a.get_text(strip=True)
            for a in soup.select(".mf-kicker a.mf-pill")
            if a.get_text(strip=True)
        ]
        return _dedupe(from_pills + _name_list(ld.get("genre")))

    def _cast(self, soup: BeautifulSoup, ld: dict) -> list[str]:
        """
        The hero cast list and the JSON-LD actor array are merged, DOM first.

        They are not the same length — the rendered list is capped, while
        JSON-LD carries the full credit — so taking either one alone silently
        drops cast members.
        """
        from_links = [
            a.get_text(strip=True)
            for a in soup.select(".mf-cast a")
            if a.get_text(strip=True)
        ]
        return _dedupe(from_links + _name_list(ld.get("actor")))

    def _hero_meta(self, soup: BeautifulSoup) -> dict:
        """Flattens the .mf-meta grid into {key: value}."""
        out: dict = {}
        for item in soup.select(".mf-meta-item"):
            label_node = item.find(class_="mf-meta-l")
            value_node = item.find(class_="mf-meta-v")
            if not label_node or not value_node:
                continue
            label = " ".join(label_node.get_text(strip=True).lower().split())
            value = value_node.get_text(strip=True)
            key = _META_LABEL_KEYS.get(label)
            if not key or not value:
                continue
            if key in _INT_META_KEYS:
                digits = re.search(r"\d+", value)
                if digits:
                    out[key] = int(digits.group())
            else:
                out[key] = value
        return out

    def _year(self, soup: BeautifulSoup, meta: dict, ld_year: Optional[str]) -> Optional[str]:
        for candidate in (
            meta.get("year"),
            ld_year,
            self._h1_year(soup),
        ):
            if candidate:
                match = re.search(r"(\d{4})", str(candidate))
                if match:
                    return match.group(1)
        return None

    def _h1_year(self, soup: BeautifulSoup) -> Optional[str]:
        h1 = soup.find("h1", class_="mf-h1")
        span = h1.find("span", class_="mf-h1-year") if h1 else None
        return span.get_text(strip=True) if span else None

    def _duration_from_iso(self, value: Optional[str]) -> Optional[str]:
        """
        Converts the schema.org duration ("PT1H39M") to the rendered form the
        page itself displays ("1H 39M"), so both sources agree.
        """
        if not value:
            return None
        hours = re.search(r"(\d+)H", str(value))
        minutes = re.search(r"(\d+)M", str(value))
        if hours and minutes:
            return f"{hours.group(1)}H {minutes.group(1)}M"
        if hours:
            return f"{hours.group(1)}H"
        if minutes:
            return f"{minutes.group(1)}M"
        return None

    def _extra_images(self, soup: BeautifulSoup) -> list[str]:
        """The hero backdrop; the poster is already carried in image_url."""
        images = []
        for img in soup.find_all("img", src=True):
            alt = (img.get("alt") or "").lower()
            src = img["src"].strip()
            if "backdrop" not in alt or not src or src in images:
                continue
            images.append(src)
        return images[:MAX_EXTRA_IMAGES]

    def _slug(self, url: str) -> str:
        segments = [s for s in urllib.parse.urlparse(url).path.split("/") if s]
        return segments[-1] if segments else ""


def _name_list(raw) -> list[str]:
    """
    Normalises a schema.org name field into a list of names.

    Handles every shape these fields appear in: a bare string, a list of
    strings, a list of {"name": ...} objects (schema.org DefinedTerm / Person),
    and a single object rather than a list.
    """
    if raw is None:
        return []
    entries = raw if isinstance(raw, (list, tuple)) else [raw]

    names = []
    for entry in entries:
        if isinstance(entry, dict):
            name = entry.get("name")
        else:
            name = entry
        if isinstance(name, str) and name.strip():
            names.append(name.strip())
    return names


def _types(node: dict) -> set[str]:
    """
    Reads schema.org @type as a set.

    @type may be a bare string or a list, and this site uses both across
    different blocks, so every read goes through here rather than assuming a
    string.
    """
    raw = node.get("@type")
    if isinstance(raw, str):
        return {raw}
    if isinstance(raw, (list, tuple, set)):
        return {str(t) for t in raw}
    return set()


def _dedupe(values: list[str]) -> list[str]:
    """
    Removes duplicates, comparing names as HTML entities would decode them.

    The DOM and JSON-LD describe the same genre differently — one yields
    "Sci-Fi & Fantasy", the other the stored "Sci-Fi &amp; Fantasy" — so a raw
    string comparison keeps both and the caller ends up with a duplicate. The
    first spelling wins, which is the DOM's decoded form.
    """
    seen = set()
    out = []
    for value in values:
        key = html.unescape(value).strip().casefold()
        if key and key not in seen:
            seen.add(key)
            out.append(value)
    return out


def _year_from_label(label: Optional[str]) -> Optional[str]:
    """Pulls the year out of an alternateName like "Zombieland: Double Tap (2019)"."""
    if not label:
        return None
    match = re.search(r"\((\d{4})\)", str(label))
    return match.group(1) if match else None