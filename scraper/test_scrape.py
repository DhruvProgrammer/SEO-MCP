"""
Manual smoke check: scrapes one movie page and one TV page and prints the
result. Not an assertion suite — see the MCP test run for that.

Run from the scraper/ folder:  python test_scrape.py
"""
import json

from tools import scrape_post_info

URLS = [
    "https://flixgarden.site/movie/zombieland-double-tap/",
    "https://flixgarden.site/tv/the-expanse/",
]


def test():
    for url in URLS:
        result = scrape_post_info(url)
        print(f"=== {url}")
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    test()