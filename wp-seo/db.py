"""
Database operations for wp-seo. Connects to MongoDB Atlas and logs SEO completion.

Schema for collection 'seo_logs' in database 'wp_seo':
{
    "wp_post_id": int,       # The WordPress entry ID
    "post_type": str,        # "movie" or "tv"
    "status": str,           # Always "seo_done"
    "timestamp": datetime    # UTC timestamp of when the SEO processing completed
}
"""
import sys
from datetime import datetime, timezone
import pymongo
from pymongo.errors import PyMongoError
from config import MONGO_URI

# Module-level singleton MongoDB client connection
_client = None

def get_client():
    global _client
    if _client is None and MONGO_URI:
        try:
            _client = pymongo.MongoClient(MONGO_URI)
        except PyMongoError as e:
            print(f"Warning: Failed to initialize MongoDB client: {e}", file=sys.stderr)
    return _client

def log_seo_done(post_id: int, post_type: str = "movie") -> None:
    """
    Logs the completion of SEO updates for a specific entry to MongoDB.
    Fails gracefully by logging to stderr if the database operation fails,
    ensuring the main SEO tool chain doesn't crash.
    """
    client = get_client()
    if not client:
        print(f"Warning: No MongoDB client available (check MONGO_URI). Skipping log for entry {post_id}.", file=sys.stderr)
        return

    try:
        db = client["wp_seo"]
        collection = db["seo_logs"]

        doc = {
            "wp_post_id": int(post_id),
            "post_type": post_type,
            "status": "seo_done",
            "timestamp": datetime.now(timezone.utc)
        }

        collection.insert_one(doc)
    except PyMongoError as e:
        print(f"Warning: Failed to insert SEO log for entry {post_id} into MongoDB: {e}", file=sys.stderr)
    except Exception as e:
        print(f"Warning: Unexpected error while logging to MongoDB: {e}", file=sys.stderr)
