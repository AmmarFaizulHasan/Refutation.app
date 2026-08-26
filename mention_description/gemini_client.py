import os
import time
import tempfile
import requests
import logging
from google import genai
from google.genai import types

from .config import GEMINI_API_KEY, GEMINI_MODEL

log = logging.getLogger("mention-bot")

# Initialize isolated client
gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

def _download_media_file(media_url: str, media_type: str | None) -> str | None:
    if not media_url:
        return None

    is_video = (media_type in ("VIDEO", "REELS")) or (".mp4" in media_url.lower())
    suffix = ".mp4" if is_video else ".jpg"

    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        with requests.get(media_url, headers=headers, stream=True, timeout=30) as r:
            r.raise_for_status()
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
                for chunk in r.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        tmp_file.write(chunk)
                return tmp_file.name
    except Exception as e:
        log.warning("Failed to download media: %s", e)
        return None

def generate_post_description(caption: str, media_type: str, media_urls: list[str]) -> str:
    """
    Downloads media, uploads to Gemini, generates factual description, cleans up.
    """
    if not gemini_client:
        log.error("Gemini API key missing.")
        return ""

    temp_paths = []
    gemini_files = []
    
    try:
        # Download and upload media
        for url in media_urls:
            local_path = _download_media_file(url, media_type)
            if local_path:
                temp_paths.append(local_path)
                
                is_video = (media_type in ("VIDEO", "REELS")) or local_path.endswith(".mp4")
                mime_type = "video/mp4" if is_video else "image/jpeg"
                
                log.info("Uploading %s to Gemini...", mime_type)
                g_file = gemini_client.files.upload(
                    file=local_path,
                    config={"mime_type": mime_type},
                )
                gemini_files.append(g_file)
                
                if is_video:
                    log.info("Polling for video processing...")
                    for _ in range(30):
                        info = gemini_client.files.get(name=g_file.name)
                        if info.state.name == "ACTIVE":
                            break
                        elif info.state.name == "FAILED":
                            log.warning("Video failed processing.")
                            gemini_files.remove(g_file)
                            break
                        time.sleep(2)

        # Build prompt
        media_note = "The image/video is provided below." if gemini_files else "Visual content is not available -- base your description only on the caption."
        prompt = (
            "You are describing an Instagram post for someone who cannot see it.\n\n"
            f"Caption: \"{caption}\"\n"
            f"Media type: {media_type}\n"
            f"{media_note}\n\n"
            "Write a single flowing paragraph, under 150 words, describing exactly "
            "what this post shows: the subject, setting, and any visible action. "
            "Do not add opinions, commentary, hashtags, or calls to action. Do not "
            "address the commenter directly. Plain prose only -- no markdown, no "
            "bullet points, no emojis."
        )

        contents = [prompt]
        for f in gemini_files:
            contents.append(f)

        log.info("Requesting description from Gemini...")
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=contents,
        )
        
        return (response.text or "").strip()

    except Exception as e:
        log.exception("Error during Gemini generation: %s", e)
        return ""

    finally:
        # Cleanup remote Gemini files
        for f in gemini_files:
            try:
                gemini_client.files.delete(name=f.name)
            except Exception as cleanup_err:
                log.warning("Failed to delete Gemini file: %s", cleanup_err)
        
        # Cleanup local files
        for p in temp_paths:
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception as local_err:
                    log.warning("Failed to delete local file %s: %s", p, local_err)
