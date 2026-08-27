import requests
import logging
from .config import IG_ACCESS_TOKEN, GRAPH_BASE, IG_USER_ID

log = logging.getLogger("mention-bot")

def fetch_comment_context(comment_id: str) -> dict | None:
    """
    Fetches the comment and associated media context via the Graph API.
    Expected response format used:
        text, username
        media.media_type, media.caption, media.media_url, media.permalink, media.children
    """
    if not comment_id:
        return None

    url = f"{GRAPH_BASE}/{IG_USER_ID}/mentioned_comment"
    params = {
        "comment_id": comment_id,
        "fields": "text,username,media{media_type,caption,media_url,permalink,children{media_type,media_url}}",
        "access_token": IG_ACCESS_TOKEN
    }

    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.exception("Failed to fetch context for comment_id %s: %s", comment_id, e)
        return None

    media = data.get("media")
    if not media:
        log.warning("No media found attached to comment_id %s", comment_id)
        return None

    media_type = media.get("media_type")
    media_urls = []
    
    if media_type == "CAROUSEL_ALBUM":
        children = media.get("children", {}).get("data", [])
        # Extract first 2 children
        for child in children[:2]:
            child_url = child.get("media_url")
            if child_url:
                media_urls.append(child_url)
    else:
        # IMAGE or VIDEO
        single_url = media.get("media_url")
        if single_url:
            media_urls.append(single_url)

    return {
        "comment_text": data.get("text", ""),
        "username": data.get("username", ""),
        "media_type": media_type,
        "caption": media.get("caption", ""),
        "media_urls": media_urls,
        "permalink": media.get("permalink", "")
    }
