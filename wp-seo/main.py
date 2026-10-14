"""
MCP entrypoint; initializes server, registers the tools from tools.py.
"""
from fastmcp import FastMCP

from tools import create_draft_from_scraped_data, generate_seo_for_draft

# Initialize FastMCP server
mcp = FastMCP("wp-seo")


@mcp.tool()
def run_generate_seo_for_draft(post_id: str, content_type: str = "movie") -> str:
    """
    Automates SEO for an existing WordPress entry.

    post_id is the numeric ID; content_type is "movie" or "tv".
    """
    result = generate_seo_for_draft(post_id, content_type)
    if result.get("status") == "error":
        return f"Failed to generate SEO for {content_type} {post_id}. Reason: {result.get('message')}"
    return (
        f"Completed SEO for {content_type} {post_id}. "
        f"SEO data: {result.get('seo_data')}"
    )


@mcp.tool()
def run_create_draft_from_scraped_data(scraped_data: dict) -> dict:
    """
    Creates a new WordPress draft from scraped FlixGarden metadata
    (title, synopsis, genres, cast, year, etc).

    Honours scraped_data['content_type'] ("movie" or "tv"), assigns genres that
    already exist on the site, creates cast terms as needed, and uploads the
    poster as the featured image.
    """
    return create_draft_from_scraped_data(scraped_data)


if __name__ == "__main__":
    mcp.run()