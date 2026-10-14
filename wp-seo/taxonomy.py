"""
Resolves the genre names a scraper produced into the site's genre taxonomy.

FlixGarden has no category taxonomy on its content types: a movie/tv entry
carries `genre` and `actor` terms and nothing else. So the split here is
deliberately narrow:

  - genre terms are only ever ASSIGNED when they already exist in WordPress
  - anything the site does not recognise is returned as `unmatched` and never
    silently created, because inventing genre terms would pollute a closed set
  - actor names are a separate concern and are resolved by the caller, since
    that taxonomy is deliberately free-form

Nothing here touches the network. resolve_genres() takes an already-built
GenreLookup so it can be tested offline; wp_client turns the names it returns
into term IDs.
"""
import sys

from genre_lookup import GenreLookup, GenreMatch, normalize


def resolve_genres(scraped_genres: list[str], genre_lookup: GenreLookup) -> dict:
    """
    Matches scraped genre names against the site's existing genre terms.

    Returns:
      {
        "genre":    [{"id": int, "name": str}, ...],  # canonical WP terms
        "unmatched": [str, ...],                      # not a known genre
        "matches":  [GenreMatch, ...],                # for the run summary
        "raw":      [str, ...],                       # input, deduped
      }

    Two scraped names hitting the same genre assign it once. A name that
    matches nothing is kept out of `genre` rather than created in WordPress.
    """
    seen_raw: dict[str, str] = {}
    for genre in scraped_genres or []:
        if not isinstance(genre, str):
            continue
        key = normalize(genre)
        if key and key not in seen_raw:
            seen_raw[key] = genre.strip()
    raw_genres = list(seen_raw.values())

    genres: dict[int, dict] = {}
    matches: list[GenreMatch] = []
    unmatched: dict[str, str] = {}

    for genre in raw_genres:
        match = genre_lookup.match(genre)
        if match:
            matches.append(match)
            genres.setdefault(match.term_id, {"id": match.term_id, "name": match.name})
        else:
            unmatched.setdefault(normalize(genre), genre)

    return {
        "genre": list(genres.values()),
        "unmatched": list(unmatched.values()),
        "matches": matches,
        "raw": raw_genres,
    }


def resolve_actors(scraped_cast: list[str]) -> list[str]:
    """
    Cleans a scraped cast list down to unique names, preserving page order.

    No lookup happens here: unlike genres, actor terms are created on demand,
    so this only has to reject blanks, non-strings and duplicates.
    """
    out: dict[str, str] = {}
    for name in scraped_cast or []:
        if not isinstance(name, str):
            continue
        cleaned = " ".join(name.split())
        if not cleaned:
            continue
        out.setdefault(cleaned.casefold(), cleaned)
    return list(out.values())


def format_summary(resolved: dict) -> str:
    """Renders a human-readable breakdown of one resolution for a draft run."""
    lines = ["", "=" * 72, "GENRE RESOLUTION", "=" * 72]

    raw = resolved.get("raw", [])
    lines.append(f"Scraped genres ({len(raw)}): {', '.join(raw) if raw else '(none)'}")
    lines.append("")

    matches = resolved.get("matches", [])
    lines.append(
        f"Genres matched ({len(resolved.get('genre', []))} unique "
        f"from {len(matches)} names):"
    )
    if matches:
        for m in matches:
            score = "" if m.score >= 1.0 else f" score={m.score}"
            via = ""
            if m.method == "fuzzy" and normalize(m.matched_on) != normalize(m.scraped):
                via = f" ~ {m.matched_on!r}"
            lines.append(f"  {m.scraped!r} -> {m.name!r}  [{m.method}{score}]{via}")
    else:
        lines.append("  (none)")
    lines.append("")

    unmatched = resolved.get("unmatched", [])
    lines.append(
        f"Unmatched, not assigned ({len(unmatched)}): "
        f"{', '.join(unmatched) if unmatched else '(none)'}"
    )

    fuzzy = [m for m in matches if m.method == "fuzzy"]
    if fuzzy:
        lines.append("")
        lines.append("Review these fuzzy matches; promote good ones into genre_aliases.py:")
        for m in fuzzy:
            lines.append(
                f"  {m.scraped!r} -> {m.name!r} (score {m.score}, via {m.matched_on!r})"
            )

    lines.append("=" * 72)
    return "\n".join(lines)


def log_summary(resolved: dict) -> None:
    """Writes the summary to stderr, so it never pollutes MCP stdout."""
    print(format_summary(resolved), file=sys.stderr)