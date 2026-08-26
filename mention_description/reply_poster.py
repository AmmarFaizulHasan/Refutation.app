import requests
import logging
from .config import IG_USER_ID, IG_ACCESS_TOKEN, GRAPH_BASE

log = logging.getLogger("mention-bot")

def post_mention_reply(comment_id: str, message: str) -> bool:
    """
    Publish the reply via POST /<IG_ID>/mentions.
    """
    if not comment_id or not message:
        return False
        
    url = f"{GRAPH_BASE}/{IG_USER_ID}/mentions"
    payload = {
        "message": message,
        "comment_id": comment_id,
        "access_token": IG_ACCESS_TOKEN
    }
    
    try:
        log.info("Posting reply to mention %s...", comment_id)
        resp = requests.post(url, data=payload, timeout=15)
        if resp.status_code == 200:
            log.info("Successfully posted reply to comment_id %s. Result: %s", comment_id, resp.json())
            return True
        else:
            log.error("Failed to post reply (Status %d): %s", resp.status_code, resp.text)
            return False
    except Exception as e:
        log.exception("Exception occurred while posting reply to %s: %s", comment_id, e)
        return False
