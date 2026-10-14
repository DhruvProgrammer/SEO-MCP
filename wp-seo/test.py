"""
Manual check: fills SEO for one real movie/tv entry and pushes it.

Set ENTRY_ID and CONTENT_TYPE below, then run from the wp-seo/ folder:
    python test.py

Needs working credentials in .env. This writes to the live site, so point it
at a draft you are happy to have modified.
"""
import os

from tools import generate_seo_for_draft

ENTRY_ID = os.getenv("ENTRY_ID", "")
CONTENT_TYPE = os.getenv("CONTENT_TYPE", "movie")


def main():
    if not ENTRY_ID:
        raise SystemExit(
            "Set ENTRY_ID to the numeric WordPress ID of a movie/tv entry, e.g.\n"
            "    $env:ENTRY_ID=5620"
        )

    result = generate_seo_for_draft(ENTRY_ID, CONTENT_TYPE)
    if result.get("status") == "error":
        raise SystemExit(f"Failed: {result.get('message')}")

    print("SEO written:", result["seo_data"])
    print("WordPress returned:", result["updated_post"])
    print("Logged to Mongo.")


if __name__ == "__main__":
    main()