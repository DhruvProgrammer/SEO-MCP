"""
Hand-maintained alias table for genre matching.

Maps a CANONICAL WordPress genre term name (exactly as it appears in the WP
`genre` taxonomy) to a list of variant strings that a scraper might produce.

This file is meant to be edited by hand. It is deliberately kept separate from
the candidates genre_lookup.py derives automatically from the term names
themselves, so there is no need to repeat those here.

Three things worth knowing about FlixGarden genre names:

  - Term names store HTML entities literally. The site stores "Sci-Fi &amp;
    Fantasy", and both the REST API and the rendered page return it that way.
    genre_lookup.normalize() decodes those entities, so the key below is the
    decoded, human-readable spelling.
  - Some genres overlap ("Science Fiction" and "Sci-Fi & Fantasy" are separate
    terms). A real term name always beats an alias, so an entry here can never
    steal a name that exists on its own.
  - Genres are a closed set: this pipeline assigns existing terms and never
    creates new ones.

Rules:
  - Keys must match a real WP genre term name (case-insensitive, entities
    decoded). GenreLookup prints a warning to stderr for any key it cannot
    find, so a typo or a renamed term shows up on the next draft run instead of
    silently doing nothing.
  - Values are matched case-insensitively with whitespace collapsed.

Workflow: when a fuzzy match shows up in the draft-run summary and looks
correct, promote it into this table. That turns a confidence guess into a
deterministic hit.
"""

MANUAL_GENRE_ALIASES: dict[str, list[str]] = {
    # Compound "&" genres. derive_candidates() only splits on "/" and
    # parentheses, so the wordier spellings need listing by hand. Note these are
    # three separate terms from the plain "Action", "Fantasy" and "War".
    "Action & Adventure": [
        "Action & Adventure",
        "Action and Adventure",
        "Adventure & Action",
        "Action/Adventure",
        "Action-Adventure",
    ],
    "Sci-Fi & Fantasy": [
        "Sci-Fi & Fantasy",
        "SciFi & Fantasy",
        "Sci Fi & Fantasy",
        "Science Fiction & Fantasy",
        "Sci-Fi/Fantasy",
    ],
    "War & Politics": [
        "War & Politics",
        "War and Politics",
        "Politics & War",
        "War/Politics",
        "Military & Political",
    ],
    # "Science Fiction" exists as its own term, so only variants that clearly
    # mean the combined genre belong here.
    "Science Fiction": ["Sci Fi", "Science-Fiction", "SciFi"],
    # Small editorial distinctions that show up in scraped labels.
    "TV Movie": ["TV Film", "Television Movie", "Made for TV"],
    "Kids": ["Children", "Kid Friendly", "Kids & Family"],
    "Talk": ["Talk Show", "Talkshow"],
}