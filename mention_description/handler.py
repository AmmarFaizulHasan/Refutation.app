import logging
import time
import random

from .dedup_store import lock_comment
from .context_fetcher import fetch_comment_context
from .gemini_client import generate_post_description
from .word_limiter import limit_to_150_words
from .reply_poster import post_mention_reply

log = logging.getLogger("mention-bot")

def process_mention(value: dict):
    """
    Main orchestration function for external mentions.
    """
    comment_id = value.get("comment_id")
    
    if not comment_id:
        log.warning("External mention event missing comment_id (likely a caption-only or unsupported mention). Skipping. %s", value)
        return

    # 1. Dedup Lock
    if not lock_comment(comment_id):
        log.info("Comment ID %s already processed. Skipping duplicate.", comment_id)
        return

    log.info("Processing external mention for comment_id=%s", comment_id)

    # 2. Fetch Context
    context = fetch_comment_context(comment_id)
    if not context:
        log.warning("Could not fetch context for %s. Aborting.", comment_id)
        return

    # 3. Generate Description (Downloads, Gemini Upload, Generation, Cleanup)
    caption = context.get("caption", "")
    media_type = context.get("media_type", "UNKNOWN")
    media_urls = context.get("media_urls", [])

    log.info("Requesting post description for %s (media_type: %s, urls: %d)...", comment_id, media_type, len(media_urls))
    raw_description = generate_post_description(
        caption=caption,
        media_type=media_type,
        media_urls=media_urls
    )

    if not raw_description:
        log.warning("Failed to generate description for %s. Aborting.", comment_id)
        return

    # 4. Enforce 150-word cap
    final_reply = limit_to_150_words(raw_description)
    
    # 5. Jittered Delay (30 to 180 seconds)
    delay = random.randint(30, 180)
    log.info("Sleeping for %d seconds before posting reply to %s...", delay, comment_id)
    time.sleep(delay)

    # 6. Post Reply
    post_mention_reply(comment_id, final_reply)
