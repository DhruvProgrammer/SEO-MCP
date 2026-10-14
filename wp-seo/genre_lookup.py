"""
Fetches the WordPress `genre` taxonomy terms, caches them with a TTL, and
matches the genre names a scraper produced against them.

Matching pipeline, in order (see GenreLookup.match):
  1. exact canonical WP term name
  2. manual aliases from genre_aliases.py
  3. candidates auto-derived from the WP term name itself:
        "TV Movie (Film)"    -> "TV Movie (Film)", "TV Movie", "Film"
        "Foo / Bar"           -> "Foo / Bar", "Foo", "Bar"
  4. difflib fuzzy fallback over whole candidate names

Exact canonical names deliberately outrank both aliases and derived fragments:
if a derived fragment is also a real term on its own, the real term wins that
string outright.

Fuzzy matching only ever considers full candidate names, never the derived
fragments. difflib's ratio is length-biased, so a short fragment would let
short or unrelated input score highly and be reported under a compound term.

Every match resolves back to the canonical WP term (id + full name). Genre
terms are never created; a name that matches nothing is handed back to the
caller as unmatched (see taxonomy.py).
"""
import json
import re
import sys
import time
import difflib
from dataclasses import dataclass

import wp_client
from genre_aliases import MANUAL_GENRE_ALIASES
from config import GENRE_CACHE_PATH, GENRE_CACHE_TTL, GENRE_FUZZY_THRESHOLD

# Match methods, in the order they are attempted.
METHOD_EXACT = "exact"
METHOD_MANUAL = "manual_alias"
METHOD_DERIVED = "auto_derived"
METHOD_FUZZY = "fuzzy"


@dataclass
class GenreMatch:
    """A scraped term successfully resolved to a canonical WP genre term."""
    term_id: int
    name: str        # canonical WP term name, always what gets assigned
    method: str      # one of the METHOD_* constants
    scraped: str     # the raw scraped term, as it arrived
    matched_on: str  # the candidate string that actually hit
    score: float     # 1.0 for exact/alias/derived hits, difflib ratio for fuzzy


def normalize(value: str) -> str:
    """
    Lowercase, decode entities, trim, and collapse internal whitespace.

    The entity step matters here: FlixGarden genre terms store HTML entities
    literally, so the REST API returns "Sci-Fi &amp; Fantasy" while a name that
    arrives by any other route is "Sci-Fi & Fantasy". Decoding both sides is
    what stops those being treated as two different genres.
    """
    decoded = re.sub(r"&amp;", "&", value or "")
    return re.sub(r"\s+", " ", decoded).strip().lower()


def derive_candidates(name: str) -> list[str]:
    """
    Builds the extra matchable strings implied by a compound term name.

    "TV Movie (Film)"    -> ["TV Movie (Film)", "TV Movie", "Film"]
    "Foo / Bar"          -> ["Foo / Bar", "Foo", "Bar"]
    "Comedy"             -> ["Comedy"]

    The full original name is always included. Slash splitting is applied to
    the parenthesis-derived pieces too, so an awkward name like
    "Foo (Bar / Baz)" still yields "Bar" and "Baz".

    "&" is deliberately not a split point. The site has curated compound
    genres such as "Sci-Fi & Fantasy" that are distinct terms in their own
    right, so splitting them would hand a plain "Fantasy" search to the
    compound term.
    """
    candidates = [name]

    parentheticals = re.findall(r"\(([^)]*)\)", name)
    candidates.extend(parentheticals)
    if parentheticals:
        # The name with every "(...)" group removed, e.g. "TV Movie".
        candidates.append(re.sub(r"\([^)]*\)", " ", name))

    for candidate in list(candidates):
        if "/" in candidate:
            candidates.extend(candidate.split("/"))

    seen: dict[str, str] = {}
    for candidate in candidates:
        key = normalize(candidate)
        if key and key not in seen:
            seen[key] = candidate.strip()
    return list(seen.values())


def _read_cache() -> dict | None:
    try:
        with open(GENRE_CACHE_PATH, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        print(f"Warning: Failed to read genre cache {GENRE_CACHE_PATH}: {e}", file=sys.stderr)
        return None

    terms = payload.get("terms")
    if not isinstance(terms, list) or not terms:
        return None
    return payload


def _write_cache(terms: list[dict]) -> None:
    try:
        with open(GENRE_CACHE_PATH, "w", encoding="utf-8") as handle:
            json.dump({"fetched_at": time.time(), "terms": terms}, handle, indent=2)
    except OSError as e:
        print(f"Warning: Failed to write genre cache {GENRE_CACHE_PATH}: {e}", file=sys.stderr)


def _load_terms() -> list[dict]:
    """
    Returns the WP genre terms, preferring a fresh cache.

    Falls back to a stale cache when WordPress is unreachable, since stale
    genre names are far better than dumping every scraped genre into the
    unmatched list. Raises only when there is no cache at all and the fetch
    fails.
    """
    cached = _read_cache()
    if cached and (time.time() - cached.get("fetched_at", 0)) < GENRE_CACHE_TTL:
        return cached["terms"]

    try:
        terms = wp_client.get_genre_terms()
        if terms:
            _write_cache(terms)
            return terms
        print("Warning: WordPress returned no genre terms.", file=sys.stderr)
    except Exception as e:
        if cached:
            print(f"Warning: Failed to refresh genre terms ({e}); using stale cache.", file=sys.stderr)
            return cached["terms"]
        raise

    if cached:
        return cached["terms"]
    return []


class GenreLookup:
    """
    Holds the fetched genre terms and resolves scraped strings against them.

    Pass `terms` explicitly to build a lookup without touching the network —
    that is what test_taxonomy.py does.
    """

    def __init__(
        self,
        terms: list[dict] | None = None,
        aliases: dict[str, list[str]] | None = None,
        threshold: float = GENRE_FUZZY_THRESHOLD,
    ):
        self.terms = terms if terms is not None else _load_terms()
        self.aliases = MANUAL_GENRE_ALIASES if aliases is None else aliases
        self.threshold = threshold

        # Each maps a normalized candidate -> (term_id, canonical name).
        self._exact: dict[str, tuple[int, str]] = {}
        self._manual: dict[str, tuple[int, str]] = {}
        self._derived: dict[str, tuple[int, str]] = {}
        self._build()

    def _build(self) -> None:
        # Pass 1 — canonical term names. These outrank everything else, so
        # they are collected before any derived fragment can claim the string.
        for term in self.terms:
            term_id, name = term.get("id"), term.get("name")
            if term_id is None or not name:
                continue
            key = normalize(name)
            clash = self._exact.get(key)
            if clash is None:
                self._exact[key] = (term_id, name)
            elif clash[0] != term_id:
                print(
                    f"Warning: two genre terms share the name '{name}' "
                    f"(ids {clash[0]} and {term_id}); keeping {clash[0]}.",
                    file=sys.stderr,
                )

        # Pass 2 — fragments derived from compound names, but never over a
        # real term name. "Dark / Fantasy" yields "Fantasy", which must not
        # hijack the genuine "Fantasy" term.
        for term in self.terms:
            term_id, name = term.get("id"), term.get("name")
            if term_id is None or not name:
                continue
            for candidate in derive_candidates(name):
                key = normalize(candidate)
                if key in self._exact:
                    continue
                owner = self._derived.get(key)
                if owner is None:
                    self._derived[key] = (term_id, name)
                elif owner[0] != term_id:
                    # Two compound terms derive the same fragment. First wins;
                    # add a manual alias to force the other if that is wrong.
                    print(
                        f"Warning: genre fragment '{candidate}' is ambiguous between "
                        f"'{owner[1]}' and '{name}'; keeping '{owner[1]}'.",
                        file=sys.stderr,
                    )

        # Pass 3 — hand-maintained aliases. Beat derived fragments, but a real
        # term name always wins, so a shadowing alias is skipped and reported.
        for canonical, variants in self.aliases.items():
            target = self._exact.get(normalize(canonical))
            if target is None:
                print(
                    f"Warning: manual alias key '{canonical}' matches no WP genre term; "
                    f"entry ignored (check genre_aliases.py).",
                    file=sys.stderr,
                )
                continue
            for variant in variants:
                key = normalize(variant)
                if not key:
                    continue
                clash = self._exact.get(key)
                if clash and clash[0] != target[0]:
                    print(
                        f"Warning: manual alias '{variant}' for '{canonical}' is itself the "
                        f"name of genre '{clash[1]}'; ignoring the alias.",
                        file=sys.stderr,
                    )
                    continue
                self._manual[key] = target

        # Fuzzy matching is restricted to full names: canonical terms and manual
        # aliases. Derived fragments are deliberately excluded — difflib's
        # ratio is length-biased, so a short fragment can score well against
        # short unrelated input and quietly file it under the wrong genre.
        # (derive_candidates() only ever yields fragments shorter than a name
        # it came from, and those are already covered by pass 1.)
        self._candidates = {**self._manual, **self._exact}
        self._candidate_keys = list(self._candidates)

    def match(self, term: str) -> GenreMatch | None:
        """Resolves one scraped term to a canonical genre, or None."""
        raw = (term or "").strip()
        key = normalize(raw)
        if not key:
            return None

        for table, method in (
            (self._exact, METHOD_EXACT),
            (self._manual, METHOD_MANUAL),
            (self._derived, METHOD_DERIVED),
        ):
            hit = table.get(key)
            if hit:
                return GenreMatch(hit[0], hit[1], method, raw, raw, 1.0)

        close = difflib.get_close_matches(key, self._candidate_keys, n=1, cutoff=self.threshold)
        if close:
            best = close[0]
            term_id, name = self._candidates[best]
            score = difflib.SequenceMatcher(None, key, best).ratio()
            return GenreMatch(term_id, name, METHOD_FUZZY, raw, best, round(score, 3))

        return None


_lookup: GenreLookup | None = None


def get_lookup() -> GenreLookup:
    """Module-level singleton, so one MCP process fetches genres once."""
    global _lookup
    if _lookup is None:
        _lookup = GenreLookup()
    return _lookup
