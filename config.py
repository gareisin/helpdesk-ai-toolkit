"""Central configuration — and the visible hybrid-architecture swap point.

The whole point of this repo's architecture is that *where inference runs* is a
single configuration boundary, not something threaded through the code. Change
LLM_PROVIDER in your environment (or .env) and every model call in the project
moves between:

    mock      -> deterministic, offline, zero-setup (the default; runs anywhere)
    ollama    -> a local open-source model (the cheap runtime tier)
    anthropic -> a frontier Claude model (the "architect" tier)

Nothing else in the codebase knows or cares which one is active. That is the
selling point: a frontier model can design and supervise, while a local model
handles routine, high-volume inference at near-zero marginal cost and with no
ticket data leaving the building.
"""

import os

# --- The swap point -------------------------------------------------------

# mock | ollama | anthropic
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "mock").lower()

# Per-provider model + endpoint. Only the active provider's settings are used.
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")

ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-8")
# ANTHROPIC_API_KEY is read by the SDK from the environment — never hardcode it.

# --- Domain configuration -------------------------------------------------

# The triage taxonomy. Keywords drive the deterministic mock classifier and the
# offline first-stage scorer in bulk_categorize.py; the real models receive the
# category *names* and decide for themselves. "Other" is the catch-all and has
# no keywords on purpose.
CATEGORIES = {
    "Password Reset": [
        "password", "reset", "locked out", "can't log in", "cant log in",
        "forgot password", "expired password", "unlock account",
    ],
    "Email": [
        "email", "outlook", "mailbox", "smtp", "can't send", "cant send",
        "inbox", "calendar invite", "distribution list",
    ],
    "Hardware": [
        "laptop", "monitor", "keyboard", "mouse", "won't turn on", "wont turn on",
        "battery", "screen", "docking station", "webcam",
    ],
    "Network / VPN": [
        "vpn", "wifi", "wi-fi", "network", "no internet", "disconnect",
        "ethernet", "can't connect", "cant connect", "dns",
    ],
    "Software Install": [
        "install", "license", "update", "version", "software", "application",
        "download", "activation",
    ],
    "Printer": [
        "printer", "print", "toner", "paper jam", "scan", "scanner", "print queue",
    ],
    "Account Access": [
        "account", "access", "permission", "shared drive", "folder access",
        "security group", "mfa", "two-factor", "two factor",
    ],
    "Phishing / Spam": [
        "phishing", "spam", "suspicious email", "scam", "malware", "virus",
        "suspicious link", "ransomware",
    ],
    "Performance": [
        "slow", "freezing", "crashing", "crash", "lagging", "hangs",
        "high cpu", "out of memory", "spinning",
    ],
    "Other": [],
}

CATEGORY_NAMES = list(CATEGORIES.keys())

# Keyword cues for priority — used only by the mock provider. Real models infer
# priority from the full ticket text.
PRIORITY_CUES = {
    "urgent": ["whole office", "everyone", "production down", "all users",
               "ransomware", "data breach", "actively spreading"],
    "high": ["can't work", "cant work", "deadline", "presentation", "urgent",
             "asap", "blocked", "interview"],
    "low": ["whenever", "no rush", "low priority", "when you get a chance"],
}

# --- Paths ----------------------------------------------------------------

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "data")
KB_DIR = os.path.join(DATA_DIR, "kb")
TICKETS_CSV = os.path.join(DATA_DIR, "tickets.csv")
PROMPTS_DIR = os.path.join(ROOT, "prompts")
EXAMPLES_DIR = os.path.join(ROOT, "examples")


def slug(category: str) -> str:
    """Filesystem-safe slug for a category name (used for KB filenames)."""
    return (
        category.lower()
        .replace(" / ", "-")
        .replace("/", "-")
        .replace(" ", "-")
    )
