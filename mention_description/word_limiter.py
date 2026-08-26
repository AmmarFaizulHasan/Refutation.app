import re

def limit_to_150_words(text: str) -> str:
    """
    Enforces a hard 150-word limit. If the text exceeds this, truncates at the 
    last complete sentence boundary (. ! ?) within the limit.
    """
    if not text:
        return ""
        
    words = text.split()
    if len(words) <= 150:
        return text.strip()
        
    # Take first 150 words
    truncated = " ".join(words[:150])
    
    # Find the last sentence-ending punctuation mark
    match = re.search(r'[.!?](?=[^.!?]*$)', truncated)
    if match:
        return truncated[:match.end()].strip()
    
    # Fallback if no punctuation is found at all in the first 150 words
    return truncated.strip() + "..."
