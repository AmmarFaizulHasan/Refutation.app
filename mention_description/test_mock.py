import json
import logging
from app import receive_webhook, app

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("mention-bot")

# A mock payload simulating a "comments" webhook field delivering an external mention
mock_payload = {
  "object": "instagram",
  "entry": [
    {
      "id": "17841436318496311",
      "time": 1710000000,
      "changes": [
        {
          "field": "comments",
          "value": {
            "id": "17899999999999999", # This acts as comment_id
            "text": "hey @refutation.app check this out",
            "from": {
              "id": "999999999",
              "username": "random_user"
            }
          }
        }
      ]
    }
  ]
}

def run_test():
    with app.test_request_context('/webhook', method='POST', json=mock_payload, headers={"X-Hub-Signature-256": "sha256=MOCK_SIG"}):
        # We temporarily bypass signature check for the test
        import os
        os.environ["BYPASS_SIGNATURE_CHECK"] = "1"
        
        log.info("--- STARTING DRY RUN ---")
        response = receive_webhook()
        log.info("--- TEST COMPLETED ---")
        print("Response:", response)

if __name__ == "__main__":
    run_test()
