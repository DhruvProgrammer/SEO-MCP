from fastmcp import FastMCP
from tools import scrape_post_info

mcp = FastMCP("scraper")


@mcp.tool()
def run_scrape_post_info(url: str) -> dict:
    """
    Extracts metadata for a movie or TV series page on flixgarden.site
    (title, year, synopsis, genres, cast, runtime, quality, language, poster).

    Read-only: reads the public page, downloads no media.
    """
    return scrape_post_info(url)


if __name__ == "__main__":
    mcp.run()