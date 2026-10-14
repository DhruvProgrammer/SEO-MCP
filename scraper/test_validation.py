"""
Tests for the scraper's "did this actually scrape anything?" guard.

Run: python test_validation.py

No network access: scrape_post_info is exercised against a stub scraper, so
these assert the envelope logic only.
"""
import sys

import tools
from scrapers.base import ScrapedPost

PASS = 0
FAIL = 0
FAILURES = []


def check(label, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {label}")
    else:
        FAIL += 1
        FAILURES.append(label)
        print(f"  FAIL  {label}")


def full_post(**over):
    """A realistically populated ScrapedPost."""
    base = dict(
        title="Dune: Part Two",
        content_type="movie",
        synopsis="Paul Atreides unites with Chani.",
        image_url="https://cdn.example/poster.jpg",
        genres=["Action &amp; Adventure", "Sci-Fi &amp; Fantasy"],
        cast=["Timothée Chalamet", "Zendaya"],
        year="2024",
        duration="2H 46M",
        quality="4K",
        language="English",
        imdb_rating="8.6",
        season_count=None,
        episode_count=None,
        network=None,
        status=None,
        extra_images=["https://cdn.example/1.jpg"],
        external_id="dune-part-two",
        source_url="https://flixgarden.site/movie/dune-part-two/",
    )
    base.update(over)
    return ScrapedPost(**base)


class StubScraper:
    def __init__(self, result=None, raises=None):
        self._result = result
        self._raises = raises
        self.seen_url = None

    def scrape_post(self, url):
        self.seen_url = url
        if self._raises:
            raise self._raises
        return self._result


def run_with(stub, url="https://flixgarden.site/movie/dune-part-two/"):
    """Calls scrape_post_info with a stubbed registry entry."""
    real = tools.get_scraper_for_url
    tools.get_scraper_for_url = lambda u: stub
    try:
        return tools.scrape_post_info(url), stub
    finally:
        tools.get_scraper_for_url = real


def test_empty_detection():
    print("\n== _looks_empty (bot-challenge / hollow page detection) ==")

    check("title only is still empty", tools._looks_empty({"title": "Dune"}) is True)

    check(
        "title + one core field is enough",
        tools._looks_empty({"title": "Dune", "synopsis": "x"}) is False,
    )
    check(
        "full post is not empty",
        tools._looks_empty(vars(full_post())) is False,
    )
    check(
        "title + non-empty list counts",
        tools._looks_empty({"title": "Dune", "genres": ["Comedy"]}) is False,
    )

    # The challenge-page shapes.
    check("empty dict", tools._looks_empty({}) is True)
    check("whitespace title", tools._looks_empty({"title": "   "}) is True)
    check("None title", tools._looks_empty({"title": None}) is True)
    check("empty genres list does not count", tools._looks_empty({"title": "Dune", "genres": []}) is True)
    check("all-None core fields", tools._looks_empty({"title": "D", "synopsis": None, "image_url": None}) is True)

    # Non-string titles must not crash.
    check("int title", tools._looks_empty({"title": 5, "synopsis": "x"}) is False)
    check("list title", tools._looks_empty({"title": ["a"], "synopsis": "x"}) is False)


def test_scrape_envelope():
    print("\n== scrape_post_info envelope ==")

    res, _ = run_with(StubScraper(result=full_post()))
    check("good scrape -> success", res["status"] == "success")
    check("good scrape carries data", res["data"]["title"] == "Dune: Part Two")

    # A hollow scrape must NOT report success - this is the whole point.
    hollow = full_post(title="", synopsis="", image_url="", genres=[], cast=[])
    res, _ = run_with(StubScraper(result=hollow))
    check("hollow scrape -> error, not success", res["status"] == "error")
    check("hollow error explains itself", "no metadata" in res["message"].lower())
    check("hollow error still returns data for debugging", res.get("data", {}).get("title") == "")

    # Title-only (typical Cloudflare interstitial).
    res, _ = run_with(StubScraper(result=full_post(title="Just a moment...", synopsis="", image_url="", genres=[], cast=[])))
    check("challenge-page shape -> error", res["status"] == "error")

    res, _ = run_with(StubScraper(raises=RuntimeError("404 Client Error")))
    check("upstream exception -> error", res["status"] == "error")
    check("exception message preserved", "404" in res["message"])

    # url validation
    for bad in (None, 123, [], "", "   "):
        res = tools.scrape_post_info(bad)
        check(f"rejects url {bad!r}", res["status"] == "error")

    res, stub = run_with(StubScraper(result=full_post()), url="  https://flixgarden.site/movie/dune-part-two/  ")
    check("url is trimmed before fetching", stub.seen_url == "https://flixgarden.site/movie/dune-part-two/")

    res = tools.scrape_post_info("https://example.com/movie/x/")
    check("unregistered host -> error", res["status"] == "error")
    check("unregistered host explains", "no scraper registered" in res["message"].lower())


def main():
    print("=" * 72)
    print("SCRAPER VALIDATION TESTS")
    print("=" * 72)

    test_empty_detection()
    test_scrape_envelope()

    print("\n" + "=" * 72)
    print(f"{PASS} passed, {FAIL} failed")
    if FAILURES:
        print("\nFailures:")
        for f in FAILURES:
            print(f"  - {f}")
    print("=" * 72)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()