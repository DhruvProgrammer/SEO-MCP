from dataclasses import dataclass, field
from abc import ABC, abstractmethod
from typing import List, Optional

# The two content types the site publishes. Each has its own WordPress
# post type; see wp-seo/config.py.
CONTENT_TYPE_MOVIE = "movie"
CONTENT_TYPE_TV = "tv"
CONTENT_TYPES = (CONTENT_TYPE_MOVIE, CONTENT_TYPE_TV)


@dataclass
class ScrapedPost:
    """
    One movie or TV entry as published on FlixGarden.

    Field notes, all taken from what the live pages actually render:
      title          from JSON-LD `name`, which excludes the year
      year           JSON-LD `alternateName` carries the "(2019)" suffix
      genres         the mf-pill links in the hero kicker, cross-checked
                     against JSON-LD `genre`
      cast           the mf-cast links, cross-checked against JSON-LD `actor`
      extra_images   gallery/backdrop <img> sources, capped for payload size

    Genre and actor names are returned exactly as the site spells them,
    including HTML entities in names like "Sci-Fi &amp; Fantasy". The wp-seo
    side matches against WordPress term names, which store the same escaped
    text, so the entities are preserved deliberately rather than cleaned up.
    """
    title: str
    content_type: str                      # CONTENT_TYPE_MOVIE or CONTENT_TYPE_TV
    synopsis: str
    image_url: str
    genres: List[str] = field(default_factory=list)
    cast: List[str] = field(default_factory=list)
    year: Optional[str] = None
    duration: Optional[str] = None         # rendered form, e.g. "1H 39M"
    quality: Optional[str] = None          # e.g. "4K"
    language: Optional[str] = None         # e.g. "Hindi, English"
    imdb_rating: Optional[str] = None      # e.g. "6.9"
    season_count: Optional[int] = None     # tv only
    episode_count: Optional[int] = None    # tv only
    network: Optional[str] = None          # tv only
    status: Optional[str] = None           # tv only, e.g. "Ended"
    extra_images: List[str] = field(default_factory=list)
    external_id: str = ""
    source_url: str = ""


class BaseScraper(ABC):
    @abstractmethod
    def scrape_post(self, url: str) -> ScrapedPost:
        pass