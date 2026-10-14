"""
Offline tests for genre resolution (genre_lookup + taxonomy). No network, no
WordPress, no Mongo: GenreLookup is built from a snapshot of the site's live
genre terms.

Snapshot taken from https://flixgarden.site/wp-json/wp/v2/genre (26 terms).
Note the stored names keep their HTML entities, exactly as the REST API
returns them — that is what genre_lookup.normalize() has to cope with.

Run:  python test_taxonomy.py   (in the wp-seo/ folder)
"""
import sys

from genre_aliases import MANUAL_GENRE_ALIASES
from genre_lookup import GenreLookup, derive_candidates, normalize
from taxonomy import resolve_actors, resolve_genres

# Mirror of the live taxonomy fetch (ids and names as returned by WP).
GENRE_TERMS = [
    {"id": 9, "name": "Action"},
    {"id": 11806, "name": "Action &amp; Adventure"},
    {"id": 13, "name": "Adventure"},
    {"id": 19, "name": "Animation"},
    {"id": 17, "name": "Comedy"},
    {"id": 10, "name": "Crime"},
    {"id": 1549, "name": "Documentary"},
    {"id": 15, "name": "Drama"},
    {"id": 16, "name": "Family"},
    {"id": 8, "name": "Fantasy"},
    {"id": 778, "name": "History"},
    {"id": 22, "name": "Horror"},
    {"id": 13065, "name": "Kids"},
    {"id": 643, "name": "Music"},
    {"id": 20, "name": "Mystery"},
    {"id": 11815, "name": "Reality"},
    {"id": 21, "name": "Romance"},
    {"id": 11807, "name": "Sci-Fi &amp; Fantasy"},
    {"id": 12, "name": "Science Fiction"},
    {"id": 12942, "name": "Soap"},
    {"id": 13048, "name": "Talk"},
    {"id": 18, "name": "Thriller"},
    {"id": 1198, "name": "TV Movie"},
    {"id": 275, "name": "War"},
    {"id": 11835, "name": "War &amp; Politics"},
    {"id": 1779, "name": "Western"},
]

passed = 0
failed = 0


def check(label: str, cond: bool) -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok  {label}")
    else:
        failed += 1
        print(f"FAIL  {label}")


def check_match(label: str, match, expected_name: str | None) -> None:
    if expected_name is None:
        check(label, match is None)
    else:
        check(label, match is not None and normalize(match.name) == normalize(expected_name))


def main() -> None:
    print("== normalize decodes HTML entities ==")
    check("stored term name decodes", normalize("Sci-Fi &amp; Fantasy") == "sci-fi & fantasy")
    check("plain name unchanged", normalize("  Comedy ") == "comedy")
    check(
        "stored and plain spellings normalize identically",
        normalize("Sci-Fi &amp; Fantasy") == normalize("Sci-Fi & Fantasy"),
    )

    print("\n== derive_candidates ==")
    # "&" is deliberately NOT a split point: "Sci-Fi &amp; Fantasy" and
    # "Action &amp; Adventure" are curated terms distinct from plain "Fantasy"
    # and "Action", so splitting them would invent wrong matches.
    check(
        "ampersand compound is NOT split into fragments",
        derive_candidates("Sci-Fi &amp; Fantasy") == ["Sci-Fi &amp; Fantasy"],
    )
    check(
        "plain: Thriller",
        derive_candidates("Thriller") == ["Thriller"],
    )
    check(
        "parens still split: TV Movie (Film)",
        set(derive_candidates("TV Movie (Film)")) == {"TV Movie (Film)", "TV Movie", "Film"},
    )

    print("\n== GenreLookup matching ==")
    lookup = GenreLookup(GENRE_TERMS)

    check_match("exact canonical 'Comedy'", lookup.match("Comedy"), "Comedy")
    check_match("exact canonical 'Thriller'", lookup.match("Thriller"), "Thriller")
    check_match("case+space-insensitive '  HORROR '", lookup.match("  HORROR "), "Horror")
    check_match("entity-decoded 'Sci-Fi & Fantasy'", lookup.match("Sci-Fi & Fantasy"), "Sci-Fi &amp; Fantasy")
    check_match(
        "alias 'Science Fiction & Fantasy' -> 'Sci-Fi &amp; Fantasy'",
        lookup.match("Science Fiction & Fantasy"),
        "Sci-Fi &amp; Fantasy",
    )
    check_match(
        "alias 'Action and Adventure' -> 'Action &amp; Adventure'",
        lookup.match("Action and Adventure"),
        "Action &amp; Adventure",
    )
    check_match("plain 'Science Fiction' is its own term", lookup.match("Science Fiction"), "Science Fiction")
    check_match("'Sci Fi' alias -> 'Science Fiction'", lookup.match("Sci Fi"), "Science Fiction")
    check_match("no match -> None", lookup.match("Cyberpunk Noir"), None)

    print("\n== Exact term names beat derived fragments ==")
    # "Sci-Fi & Fantasy" derives the fragment "Fantasy", but a real "Fantasy"
    # term exists and must win that exact string outright.
    check_match("'Fantasy' -> the plain Fantasy term", lookup.match("Fantasy"), "Fantasy")
    check_match("'Adventure' -> the plain Adventure term", lookup.match("Adventure"), "Adventure")
    check_match("'Action' -> the plain Action term", lookup.match("Action"), "Action")
    check_match("'War' -> the plain War term", lookup.match("War"), "War")

    print("\n== Manual alias health (every key maps to a real term) ==")
    for key in MANUAL_GENRE_ALIASES:
        check(f"alias key {key!r}", lookup.match(key) is not None)

    print("\n== resolve_genres (integration) ==")
    scraped = [
        "Comedy",              # exact
        "  THRILLER ",         # exact, noisy whitespace
        "Action & Adventure",  # entity-decoded match to the stored term
        "Sci-Fi & Fantasy",    # entity-decoded match
        "Science Fiction",     # its own distinct term, must NOT be merged
        "comedy",              # duplicate after normalization
        "Cyberpunk Noir",      # no match -> unmatched, not created
        "Time Travel",         # no match -> unmatched, not created
    ]
    resolved = resolve_genres(scraped, lookup)
    genre_names = [normalize(g["name"]) for g in resolved["genre"]]

    check("genre: Comedy assigned", normalize("Comedy") in genre_names)
    check("genre: Thriller assigned", normalize("Thriller") in genre_names)
    check("genre: Action & Adventure assigned", normalize("Action &amp; Adventure") in genre_names)
    check("genre: Sci-Fi & Fantasy assigned", normalize("Sci-Fi &amp; Fantasy") in genre_names)
    check("genre: Science Fiction assigned separately", normalize("Science Fiction") in genre_names)
    check("dedupe: 'Comedy'/'comedy' collapsed", genre_names.count(normalize("Comedy")) == 1)
    check("dedupe: 7 unique raw genres from 8 inputs", len(resolved["raw"]) == 7)

    check(
        "unmatched: 'Cyberpunk Noir' reported, not assigned",
        "Cyberpunk Noir" in resolved["unmatched"],
    )
    check(
        "unmatched: 'Time Travel' reported, not assigned",
        "Time Travel" in resolved["unmatched"],
    )
    check("unmatched: exactly the 2 unknown genres", len(resolved["unmatched"]) == 2)
    check(
        "no matched genre leaked into unmatched",
        set(resolved["unmatched"]).isdisjoint({g["name"] for g in resolved["genre"]}),
    )

    canonical = {normalize(g["name"]) for g in resolved["genre"]}
    check(
        "assigned genres are canonical WP names only",
        canonical == {normalize(m.name) for m in resolved["matches"]},
    )
    check(
        "every assigned name is a real term",
        canonical.issubset({normalize(t["name"]) for t in GENRE_TERMS}),
    )

    print("\n== resolve_actors ==")
    cast = resolve_actors(
        ["Emma Stone", "  emma stone ", "Woody Harrelson", "", None, "Zoe Deutch", "Emma  Stone"]
    )
    check("cast: duplicates collapsed", cast.count("Emma Stone") == 1)
    check("cast: internal whitespace collapsed", "Zoe Deutch" in cast)
    check("cast: blanks dropped", all(c.strip() for c in cast))
    check("cast: order preserved", cast[0] == "Emma Stone")
    check("cast: all three kept", len(cast) == 3)
    check("cast: empty input -> empty list", resolve_actors([]) == [])

    print("\n-- run summary --")
    from taxonomy import format_summary

    print(format_summary(resolved))

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()