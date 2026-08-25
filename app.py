"""
Instagram Mention-Reply Bot with Multimodal Video & Reel Understanding
-----------------------------------------------------------------------
Listens for @mentions of your Instagram Business/Creator account under
OTHER people's posts (Reels, Videos, Photos, Carousels, or Comments),
downloads and analyzes the video/media content using Google Gemini's multimodal
vision & audio engine, drafts a contextual reply, publishes it via Instagram's
Mentions API, and alerts you on Telegram.

See README.md for the full setup walkthrough.
"""

import hashlib
import hmac
import logging
import os
import sys
import tempfile
import time
from pathlib import Path

# Configure UTF-8 encoding for Windows console output
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import requests
from dotenv import load_dotenv
from flask import Flask, abort, request
from google import genai
from google.genai import types

load_dotenv()

# ---- Configuration ----
IG_USER_ID = os.environ.get("IG_USER_ID", "17841436318496311").strip()
IG_ACCESS_TOKEN = os.environ.get("IG_ACCESS_TOKEN", "EAAVwYwcJ5Y8BSUF8QCqXVwwZBGWWWz7F7TSPGYhJhnikIemk1TtwgwIse78fDziqd4SwQXbxnvXvDc9ddnpmu8OzAtI4GzOwlQv0zNRQ6TcbAb0PUuhdwRUje9t7zV8LulZC7urRLLvbwiwkn7ZC2GGXTW20MUCZB57Dcu9n3lKlsbDpEg0BieMeaLtkoU5iAomZCX1TZA").strip()
META_APP_SECRET = os.environ.get("META_APP_SECRET", "a50c3050dbaf2a529b9aeab03b462c7f").strip()
WEBHOOK_VERIFY_TOKEN = os.environ.get("WEBHOOK_VERIFY_TOKEN", "refutation_webhook_secure_token_2026").strip()
GRAPH_API_VERSION = os.environ.get("GRAPH_API_VERSION", "v22.0")
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
# "gemini-3.6-flash" and "gemini-3.7-flash" provide state-of-the-art multimodal video/audio comprehension.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Optional path to a knowledge base / brand guidelines text file
KNOWLEDGE_BASE_FILE = os.environ.get("KNOWLEDGE_BASE_FILE", "knowledge_base.txt")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("mention-bot")

app = Flask(__name__)
gemini_client = genai.Client(api_key=GEMINI_API_KEY)


# ---------------------------------------------------------------------------
# Knowledge Base Loader
# ---------------------------------------------------------------------------
def _load_knowledge_base() -> str:
    """Load custom brand guidelines or dataset from file if present."""
    kb_path = Path(KNOWLEDGE_BASE_FILE)
    if kb_path.exists() and kb_path.is_file():
        try:
            content = kb_path.read_text(encoding="utf-8").strip()
            if content:
                log.info("Loaded custom knowledge base (%d chars)", len(content))
                return content
        except Exception as e:
            log.warning("Could not read knowledge base file: %s", e)
    return ""


# ---------------------------------------------------------------------------
# Webhook verification (Meta pings this once when registering the endpoint)
# ---------------------------------------------------------------------------
@app.route("/webhook", methods=["GET"])
def verify_webhook():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == WEBHOOK_VERIFY_TOKEN:
        log.info("Webhook verification succeeded.")
        return challenge, 200
    log.warning("Webhook verification failed. Token mismatch or bad mode.")
    return "Forbidden", 403


# ---------------------------------------------------------------------------
# Webhook event receiver
# ---------------------------------------------------------------------------
@app.route("/webhook", methods=["POST"])
def receive_webhook():
    if not _verify_signature(request):
        log.warning("Invalid request signature received.")
        abort(403)

    body = request.get_json(force=True, silent=True) or {}
    log.info("Received valid webhook event from Meta: %s", body)

    if body.get("object") != "instagram":
        return "EVENT_RECEIVED", 200

    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") == "mentions":
                try:
                    handle_mention(change.get("value", {}))
                except requests.exceptions.HTTPError as http_err:
                    # In Meta Dashboard test triggers, mock IDs (e.g. 0 or 17899...) return 400
                    log.warning("Graph API request for mention failed (likely mock/test event): %s", http_err)
                except Exception:
                    log.exception("Failed to handle mention event: %s", change)

            # >>> Direct Comments Change Event Handler with AI Generation <<<
            elif change.get("field") == "comments":
                try:
                    comment_value = change.get("value", {})
                    comment_id = comment_value.get("id")
                    comment_text = comment_value.get("text", "")

                    try:
                        print(f"[DEBUG] Received comment text: {comment_text}")
                    except Exception:
                        pass
                    log.info("[DEBUG] Received comment text: %s", comment_text)

                    if comment_id:
                        ai_text = generate_ai_response(comment_text)
                        try:
                            print(f"[DEBUG] AI generated reply: {ai_text}")
                        except Exception:
                            pass
                        log.info("[DEBUG] AI generated reply: %s", ai_text)

                        send_comment_reply(comment_id, ai_text)
                except Exception:
                    log.exception("Failed to handle comments event: %s", change)

    # Respond fast with 200 OK so Meta registers the test as successful
    return "EVENT_RECEIVED", 200


def _verify_signature(req) -> bool:
    """Confirms the request originated from Meta using HMAC SHA-256 (or SHA-1 fallback)."""
    # If signature check is bypassed for testing/development, allow all requests immediately
    if os.environ.get("BYPASS_SIGNATURE_CHECK", "").lower() in ("1", "true"):
        return True

    sig_header = req.headers.get("X-Hub-Signature-256") or req.headers.get("X-Hub-Signature")
    if not sig_header:
        log.warning("Signature header (X-Hub-Signature-256 / X-Hub-Signature) missing.")
        return False

    secret = META_APP_SECRET.encode("utf-8")
    raw_data = req.get_data()

    if sig_header.startswith("sha256="):
        algo = hashlib.sha256
        received_sig = sig_header[7:].strip()
    elif sig_header.startswith("sha1="):
        algo = hashlib.sha1
        received_sig = sig_header[5:].strip()
    else:
        log.warning("Unknown signature format: %s", sig_header)
        return False

    expected_sig = hmac.new(secret, raw_data, algo).hexdigest()
    is_valid = hmac.compare_digest(received_sig, expected_sig)
    
    if not is_valid:
        log.warning(
            "Signature mismatch! Expected: %s, Received: %s (Raw Data: %r, Secret: %s...)",
            expected_sig,
            received_sig,
            raw_data,
            META_APP_SECRET[:6] if META_APP_SECRET else "EMPTY",
        )

    return is_valid


# ---------------------------------------------------------------------------
# Core flow: fetch context -> download media -> analyze with Gemini -> reply
# ---------------------------------------------------------------------------
def handle_mention(value: dict):
    comment_id = value.get("comment_id")
    media_id = value.get("media_id")

    log.info("Processing mention event: comment_id=%s, media_id=%s", comment_id, media_id)

    if comment_id:
        context = _fetch_comment_mention(comment_id)
        reply_text, analyzed_media = generate_reply(
            comment_text=context.get("comment_text"),
            caption=context.get("caption"),
            username=context.get("username"),
            media_type=context.get("media_type"),
            media_url=context.get("media_url"),
        )
        result_id = post_mention_reply(reply_text, comment_id=comment_id)
        permalink = context.get("permalink")
        media_type = context.get("media_type")
    elif media_id:
        context = _fetch_media_mention(media_id)
        reply_text, analyzed_media = generate_reply(
            comment_text=None,
            caption=context.get("caption"),
            username=context.get("username"),
            media_type=context.get("media_type"),
            media_url=context.get("media_url"),
        )
        result_id = post_mention_reply(reply_text, media_id=media_id)
        permalink = context.get("permalink")
        media_type = context.get("media_type")
    else:
        log.warning("Mention event missing both comment_id and media_id: %s", value)
        return

    notify_telegram(
        reply_text=reply_text,
        permalink=permalink,
        media_type=media_type,
        analyzed_media=analyzed_media,
    )
    log.info("Successfully replied to mention! Result ID: %s", result_id)


def _fetch_comment_mention(comment_id: str) -> dict:
    """
    Fetch comment text, parent media metadata (media_type, media_url, caption, permalink)
    for a comment where your account was @mentioned.
    """
    params = {
        "fields": (
            f"mentioned_comment.comment_id({comment_id})"
            "{text,username,timestamp,media{id,caption,media_type,media_url,permalink}}"
        ),
        "access_token": IG_ACCESS_TOKEN,
    }
    resp = requests.get(f"{GRAPH_BASE}/{IG_USER_ID}", params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json().get("mentioned_comment", {})
    media = data.get("media", {}) or {}

    return {
        "comment_text": data.get("text", ""),
        "username": data.get("username"),
        "caption": media.get("caption", ""),
        "media_type": media.get("media_type", "UNKNOWN"),
        "media_url": media.get("media_url"),
        "permalink": media.get("permalink"),
    }


def _fetch_media_mention(media_id: str) -> dict:
    """
    Fetch post caption, media_url, media_type, and link for a post where your account
    was @mentioned in the caption.
    """
    params = {
        "fields": (
            f"mentioned_media.media_id({media_id})"
            "{caption,username,permalink,media_type,media_url}"
        ),
        "access_token": IG_ACCESS_TOKEN,
    }
    resp = requests.get(f"{GRAPH_BASE}/{IG_USER_ID}", params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json().get("mentioned_media", {})

    return {
        "caption": data.get("caption", ""),
        "username": data.get("username"),
        "media_type": data.get("media_type", "UNKNOWN"),
        "media_url": data.get("media_url"),
        "permalink": data.get("permalink"),
    }


# ---------------------------------------------------------------------------
# Media Downloader
# ---------------------------------------------------------------------------
def _download_media_file(media_url: str, media_type: str | None) -> str | None:
    """
    Stream and download media (Video / Reel / Image) from Instagram CDN to a temp file.
    Returns the path to the local temporary file.
    """
    if not media_url:
        return None

    # Determine file extension
    is_video = (media_type in ("VIDEO", "REELS")) or (".mp4" in media_url.lower())
    suffix = ".mp4" if is_video else ".jpg"

    try:
        log.info("Downloading media file (%s) from Instagram...", media_type or "MEDIA")
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        with requests.get(media_url, headers=headers, stream=True, timeout=30) as r:
            r.raise_for_status()
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
                for chunk in r.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        tmp_file.write(chunk)
                tmp_path = tmp_file.name

        file_size_mb = os.path.getsize(tmp_path) / (1024 * 1024)
        log.info("Media downloaded successfully: %s (%.2f MB)", tmp_path, file_size_mb)
        return tmp_path
    except Exception as e:
        log.warning("Failed to download media from %s: %s", media_url, e)
        return None


# ---------------------------------------------------------------------------
# Multimodal Reply Generator (Google Gemini)
# ---------------------------------------------------------------------------
def generate_reply(
    comment_text: str | None,
    caption: str | None,
    username: str | None,
    media_type: str | None,
    media_url: str | None,
) -> tuple[str, bool]:
    """
    Feeds video/image content and text context to Gemini to draft an authentic reply.
    Returns a tuple of (reply_text, analyzed_media_boolean).
    """
    kb_context = _load_knowledge_base()
    kb_section = ""
    if kb_context:
        kb_section = (
            f"\n--- BRAND KNOWLEDGE BASE / RULES ---\n"
            f"{kb_context}\n"
            f"------------------------------------\n"
            f"Adhere strictly to the facts, tone, and guidelines in the knowledge base above.\n"
        )

    # Contextual prompt
    prompt_parts = [
        "You are replying as our official Instagram account to a post/Reel where you were @mentioned.",
        f"Mentioned by user: @{username or 'someone'}.",
    ]

    if caption:
        prompt_parts.append(f'Original Post Caption: """{caption}"""')
    if comment_text:
        prompt_parts.append(f'User Comment mentioning you: """{comment_text}"""')

    prompt_parts.append(
        "\nInstructions:\n"
        "1. Write ONE concise, natural, engaging Instagram reply (1 to 2 sentences max).\n"
        "2. Directly acknowledge or react to what is happening in the video/image and text.\n"
        "3. Maintain a friendly, authentic brand voice. Avoid robotic greetings or spammy emojis.\n"
        "4. Do NOT include hashtags unless the original context explicitly calls for it.\n"
        "5. Output only the exact reply text."
    )

    if kb_section:
        prompt_parts.append(kb_section)

    system_and_user_prompt = "\n".join(prompt_parts)

    temp_media_path = None
    uploaded_file = None
    analyzed_media = False

    try:
        # Attempt to download media if URL is provided
        if media_url:
            temp_media_path = _download_media_file(media_url, media_type)

        if temp_media_path and os.path.exists(temp_media_path):
            is_video = (media_type in ("VIDEO", "REELS")) or temp_media_path.endswith(".mp4")
            mime_type = "video/mp4" if is_video else "image/jpeg"

            log.info("Uploading %s to Google Gemini Files API...", mime_type)
            uploaded_file = gemini_client.files.upload(
                file=temp_media_path,
                config={"mime_type": mime_type},
            )

            # If video, wait for server-side processing to reach ACTIVE state
            if is_video:
                log.info("Waiting for Gemini to process video frames...")
                for _ in range(30):  # Wait up to 60 seconds (2s per step)
                    file_info = gemini_client.files.get(name=uploaded_file.name)
                    if file_info.state.name == "ACTIVE":
                        log.info("Video is ACTIVE and ready for reasoning.")
                        break
                    elif file_info.state.name == "FAILED":
                        log.warning("Gemini video processing failed on server.")
                        uploaded_file = None
                        break
                    time.sleep(2)

            if uploaded_file and (not is_video or file_info.state.name == "ACTIVE"):
                log.info("Generating multimodal reply with Gemini (%s)...", GEMINI_MODEL)
                response = gemini_client.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=[uploaded_file, system_and_user_prompt],
                )
                reply_text = (response.text or "").strip()
                analyzed_media = True
                return reply_text, analyzed_media

        # Fallback to text-only if media is unavailable (e.g. copyrighted audio or no media_url)
        log.info("Generating text-only reply with Gemini (%s)...", GEMINI_MODEL)
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=system_and_user_prompt,
        )
        reply_text = (response.text or "").strip()
        return reply_text, analyzed_media

    except Exception as e:
        log.exception("Error during Gemini generation: %s", e)
        # Safe fallback
        return f"Thanks for the mention @{username or 'friend'}! 🙌", False

    finally:
        # Clean up remote Gemini file
        if uploaded_file:
            try:
                log.info("Cleaning up uploaded file from Gemini API (%s)...", uploaded_file.name)
                gemini_client.files.delete(name=uploaded_file.name)
            except Exception as del_err:
                log.warning("Could not delete Gemini remote file: %s", del_err)

        # Clean up local temporary file
        if temp_media_path and os.path.exists(temp_media_path):
            try:
                os.remove(temp_media_path)
            except Exception as tmp_err:
                log.warning("Could not delete local temp file: %s", tmp_err)


# ---------------------------------------------------------------------------
# AI Comment Reply Generator
# ---------------------------------------------------------------------------
def generate_ai_response(user_text: str) -> str:
    """
    Use Google Gemini API to generate a short, polite, context-aware Instagram reply.
    """
    kb_context = _load_knowledge_base()
    kb_section = ""
    if kb_context:
        kb_section = f"\nBrand Knowledge Base / Guidelines:\n{kb_context}\n"

    prompt = (
        "You are replying as our official Instagram account (@refutation.app) to a user comment on our post.\n"
        f"User comment: \"\"\"{user_text}\"\"\"\n\n"
        "Instructions:\n"
        "1. Write ONE short, natural, polite, and engaging Instagram reply (1-2 sentences max).\n"
        "2. Directly address what the user said.\n"
        "3. Maintain a friendly brand tone, no hashtag spam, no robotic filler.\n"
        "4. Output only the reply message text."
    )
    if kb_section:
        prompt += f"\n{kb_section}"

    try:
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
        )
        ai_reply = (response.text or "").strip()
        if ai_reply:
            return ai_reply
    except Exception as e:
        log.exception("Error generating AI response: %s", e)

    return "Thanks for reaching out! 🙌"


# ---------------------------------------------------------------------------
# Direct Comment Reply Helper
# ---------------------------------------------------------------------------
def send_comment_reply(comment_id: str, message_text: str):
    """
    Send an HTTP POST reply to an Instagram comment using the Graph API.
    POST https://graph.facebook.com/v19.0/{comment_id}/replies
    """
    url = f"https://graph.facebook.com/v19.0/{comment_id}/replies"
    payload = {
        "message": message_text,
        "access_token": IG_ACCESS_TOKEN,
    }
    try:
        response = requests.post(url, data=payload, timeout=15)
        if response.status_code == 200:
            log.info("Successfully replied to comment %s: %s", comment_id, response.json())
        else:
            log.error("Failed to reply to comment %s (Status %d): %s", comment_id, response.status_code, response.text)
    except Exception as e:
        log.exception("Exception occurred while replying to comment %s: %s", comment_id, e)


# ---------------------------------------------------------------------------
# Instagram Mentions API Publishing
# ---------------------------------------------------------------------------
def post_mention_reply(
    message: str,
    comment_id: str | None = None,
    media_id: str | None = None,
) -> str:
    """
    Publish the reply via POST /<IG_ID>/mentions.
    Requires comment_id if replying to a comment, or media_id if replying to a caption.
    """
    params = {"message": message, "access_token": IG_ACCESS_TOKEN}
    if comment_id:
        params["comment_id"] = comment_id
    elif media_id:
        params["media_id"] = media_id
    else:
        raise ValueError("Either comment_id or media_id must be provided to post a mention reply.")

    resp = requests.post(f"{GRAPH_BASE}/{IG_USER_ID}/mentions", params=params, timeout=15)
    resp.raise_for_status()
    return resp.json().get("id", "")


# ---------------------------------------------------------------------------
# Telegram Notifications
# ---------------------------------------------------------------------------
def notify_telegram(
    reply_text: str,
    permalink: str | None,
    media_type: str | None = None,
    analyzed_media: bool = False,
):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID or TELEGRAM_BOT_TOKEN.startswith("your_"):
        log.info("Telegram notification skipped (bot token/chat ID not configured).")
        return

    media_tag = f" [{media_type}]" if media_type else ""
    analyzed_tag = "🎬 Video/Media Watched" if analyzed_media else "📝 Text Context Analyzed"

    text = (
        f"🤖 *Replied to Instagram Mention*{media_tag}\n"
        f"Status: {analyzed_tag}\n\n"
        f"💬 *Reply:*\n_{reply_text}_"
    )
    if permalink:
        text += f"\n\n🔗 [View Post on Instagram]({permalink})"

    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": False,
            },
            timeout=10,
        )
    except Exception as e:
        log.warning("Failed to send Telegram notification: %s", e)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    log.info("Starting Instagram Mention-Reply Bot on port %d...", port)
    app.run(host="0.0.0.0", port=port)
