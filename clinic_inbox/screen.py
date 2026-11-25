"""Deterministic teaching screen for urgent warning signs."""
import re
import unicodedata

PATTERNS = {
    "chest": r"chest (?:pain|pressure|tightness)|pressure (?:in|on) (?:my|the) chest",
    "stroke": r"(?:face|facial|mouth) (?:feels |is )?(?:droop\w*|sag\w*)|(?:slurred speech|speech is slurred)|(?:sudden|new) (?:one-sided )?(?:weakness|numbness)",
    "breathing": r"struggl\w* to breathe|can(?:not|'t) (?:breathe|catch my breath)|(?:severe )?(?:shortness of breath|difficulty breathing)|saans (?:lene (?:mein|me|main) (?:bahut )?takl\w*|nahi aa rahi)|सांस (?:लेने में|नहीं)|no puedo respirar",
    "collapse": r"faint(?:ed|ing)|pass(?:ed|ing) out|lost consciousness|blacked out",
    "self_harm": r"end(?:ing)? my life|kill(?:ing)? myself|suicid\w*|hurt(?:ing)? myself|take my own life",
    "bleeding": r"(?:heavy|uncontrolled) bleeding|bleeding (?:won't|will not) stop",
}
NEGATION = re.compile(
    r"\b(?:no|not|without|never|denies|don't have|do not have|haven't had|didn't|did not|has not|have no)\b"
    r"(?:\s+[\w'-]+){0,4}\s*$"
)


def warning_signs(text):
    normalized = unicodedata.normalize("NFKC", text).casefold().replace("’", "'")
    clauses = re.split(r"[.!?;:,\n]|\b(?:but|however|although|also|instead|now)\b", normalized)
    return [
        category for category, pattern in PATTERNS.items()
        if any(
            not NEGATION.search(clause[:match.start()])
            for clause in clauses
            for match in re.finditer(rf"\b(?:{pattern})\b", clause)
        )
    ]
