import sqlite3
import logging
import os
from pathlib import Path

log = logging.getLogger("mention-bot")

DB_PATH = Path(__file__).parent.parent / "mentions_dedup.db"

def init_db():
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS processed_mentions (
                    comment_id TEXT PRIMARY KEY,
                    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.commit()
    except Exception as e:
        log.exception("Failed to initialize dedup database: %s", e)

def lock_comment(comment_id: str) -> bool:
    """
    Returns True if successfully locked (meaning this is the first time processing it).
    Returns False if already exists (already processed).
    """
    if not comment_id:
        return False
        
    init_db()
    
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute('INSERT INTO processed_mentions (comment_id) VALUES (?)', (comment_id,))
            conn.commit()
            return True
    except sqlite3.IntegrityError:
        # comment_id already exists in table
        return False
    except Exception as e:
        log.exception("Failed to lock comment_id %s: %s", comment_id, e)
        # If DB fails, allow it to proceed rather than blocking completely,
        # but this might risk double posting if Meta retries.
        return True
