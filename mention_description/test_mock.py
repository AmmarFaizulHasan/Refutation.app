import json
import logging
from app import receive_webhook, app
import mention_description.handler

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("mention-bot")

# Mock payload strictly shaped as a 'mentions' webhook delivery
mock_mention_payload = {
    "object": "instagram",
    "entry": [{
        "changes": [{
            "field": "mentions",
            "value": {
                "comment_id": "TEST_MENTION_COMMENT_ID_456",
                "media_id": "TEST_MEDIA_ID_789"
            }
        }]
    }]
}

# Mock the context fetcher so it doesn't crash on graph API call
def mock_fetch_comment_context(comment_id):
    log.info("MOCK: fetch_comment_context called with %s", comment_id)
    return {
        "comment_text": "Check this out @refutation.app",
        "username": "tester_user",
        "media_type": "IMAGE",
        "caption": "A sunset over the mountains",
        "media_urls": [],
        "permalink": "https://instagram.com/p/mock_mention"
    }

def mock_post_mention_reply(comment_id, final_reply):
    log.info("MOCK: post_mention_reply called with %s: %s", comment_id, final_reply)
    return True

mention_description.handler.fetch_comment_context = mock_fetch_comment_context
mention_description.handler.post_mention_reply = mock_post_mention_reply

def run_test():
    with app.test_request_context('/webhook', method='POST', json=mock_mention_payload, headers={"X-Hub-Signature-256": "sha256=MOCK_SIG"}):
        import os
        os.environ["BYPASS_SIGNATURE_CHECK"] = "1"
        
        log.info("--- STARTING MENTIONS FIELD DRY RUN ---")
        response = receive_webhook()
        log.info("--- TEST COMPLETED ---")
        print("Response:", response)

if __name__ == "__main__":
    run_test()
