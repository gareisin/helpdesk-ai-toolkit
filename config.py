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

# Keyword cues for priority — used by the deterministic provider. Real models infer
# priority from the full ticket text.
PRIORITY_CUES = {
    "urgent": ["whole office", "everyone", "production down", "all users",
               "ransomware", "data breach", "actively spreading"],
    "high": ["can't work", "cant work", "deadline", "presentation", "urgent",
             "asap", "blocked", "interview"],
    "low": ["whenever", "no rush", "low priority", "when you get a chance"],
}

# --- Resolution-agent behavior --------------------------------------------

# Stage-1 routing: a ticket that classifies at or above this confidence AND has a
# canned self-service answer is resolved without ever calling the model. Below it,
# the conversational LLM path takes over. This is the cost/latency lever — routine
# tickets never touch the model.
CANNED_CONFIDENCE = 0.75

# How many back-and-forth turns the agent attempts with the user before it gives
# up on self-service and escalates to a human-handled ticket.
MAX_TURNS = 4

# Canned, KB-backed self-service answers for the most routine categories. A ticket
# that routes to one of these is handled at zero model cost. Categories without an
# entry always go to the conversational LLM path.
CANNED_RESPONSES = {
    "Password Reset": (
        "You can reset your own password in under a minute: go to the company "
        "sign-in page, click \"Forgot password,\" and follow the verification "
        "prompts. If you're fully locked out, reply here and we'll unlock the account."
    ),
    "Network / VPN": (
        "Most Wi-Fi/VPN drops clear with a quick reset: toggle Wi-Fi off and on, "
        "forget and rejoin the network, then reconnect the VPN client. If you're on "
        "wired, reseat the cable. Reply if it's still down after that."
    ),
    "Printer": (
        "For a stuck print job: open your print queue, cancel all jobs, then clear "
        "any physical paper jam and power-cycle the printer. Send a test page once "
        "it's back. Reply if it still won't print or scan."
    ),
    "Performance": (
        "If the machine is slow or frozen, save your work and restart — it clears "
        "most temporary slowdowns. If one specific app is the problem, close and "
        "reopen just that app first. Reply if it's still sluggish after a restart."
    ),
}

# --- Paths ----------------------------------------------------------------

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "data")
KB_DIR = os.path.join(DATA_DIR, "kb")
QUEUE_DIR = os.path.join(DATA_DIR, "queue")   # escalated tickets land here
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
