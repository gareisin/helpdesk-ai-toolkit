"""Tier-1 support agent — resolve with the user, or escalate to a technician.

This is the real workflow, not a triage-and-stop proposal:

  1. A ticket comes in (here, from text or the synthetic CSV; in production, email).
  2. Two-stage routing classifies it and scores confidence.
  3. High-confidence, routine issue  -> a canned, KB-backed self-service answer
     (no model call at all).
  4. Otherwise -> the conversational LLM path: the agent retrieves knowledge-base
     context, then talks the user through the fix one step at a time.
  5. If the user confirms it's fixed within a few turns -> resolved, no ticket.
  6. If not -> the agent creates a categorized ticket and routes it to the help
     desk queue for a human technician.

The model call goes through llm_client.get_client(), so the same loop runs against the
deterministic mock (default), a local Ollama model, or a frontier Claude model — chosen
by the LLM_PROVIDER environment variable, nothing else.

Examples:
  python support_agent.py --demo                 # scripted scenarios (no input needed)
  python support_agent.py --chat                 # you play the end user
  python support_agent.py --text "VPN keeps dropping and I can't connect"
  python support_agent.py --ticket TS-1004
"""

import argparse
import csv
import json
import os

import config
from llm_client import get_client
from scoring import classify, score_categories


# --- Knowledge base retrieval ---------------------------------------------

def load_kb_article(category: str):
    path = os.path.join(config.KB_DIR, f"{config.slug(category)}.json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def retrieve_kb_context(text: str, top_n: int = 1) -> str:
    """Pick the most relevant KB article(s) by keyword score and format as text."""
    ranked = [(c, s) for c, s in score_categories(text) if s > 0]
    chunks = []
    for category, _ in ranked[:top_n]:
        article = load_kb_article(category)
        if not article:
            continue
        steps = "\n".join(f"- {s}" for s in article["steps"])
        chunks.append(f"# {article['title']}\n{steps}")
    return "\n\n".join(chunks)


# --- Helpers --------------------------------------------------------------

def derive_priority(text: str, category: str) -> str:
    low = text.lower()
    for level in ("urgent", "high", "low"):
        if any(cue in low for cue in config.PRIORITY_CUES[level]):
            return level
    return "high" if category == "Phishing / Spam" else "medium"


def create_ticket(ticket_id, category, priority, summary, transcript) -> str:
    """Write an escalated ticket to the help desk queue. Returns the file path."""
    os.makedirs(config.QUEUE_DIR, exist_ok=True)
    record = {
        "ticket_id": ticket_id,
        "status": "escalated",
        "category": category,
        "priority": priority,
        "summary": summary,
        "transcript": transcript,
    }
    path = os.path.join(config.QUEUE_DIR, f"{ticket_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)
    return path


def _say(role, text):
    print(f"{role}> {text}")


# --- The agent loop -------------------------------------------------------

def handle_ticket(initial_text, client, ticket_id="ADHOC",
                  user_replies=None, interactive=False):
    """Run one ticket end to end. Returns the outcome string.

    user_replies: a list of scripted user turns (for --demo). When it and
    `interactive` are both falsy, the agent can't continue a conversation, so it
    escalates after the first model turn.
    """
    category, confidence, _ = classify(initial_text)
    priority = derive_priority(initial_text, category)
    print(f"--- routing: {category} (confidence {confidence}) ", end="")

    # Stage 1 — canned self-service for high-confidence routine issues.
    if confidence >= config.CANNED_CONFIDENCE and category in config.CANNED_RESPONSES:
        print("-> canned self-service (no model call) ---")
        _say("agent", config.CANNED_RESPONSES[category])
        print(">> OUTCOME: resolved via self-service  (no ticket created)\n")
        return "canned"

    # Stage 2 — conversational LLM path.
    print("-> conversational agent ---")
    kb_context = retrieve_kb_context(initial_text)
    conversation = [{"role": "user", "text": initial_text}]
    replies = list(user_replies or [])
    status = "in_progress"

    for _turn in range(config.MAX_TURNS):
        result = client.respond(conversation, kb_context)
        _say("agent", result["message"])
        conversation.append({"role": "agent", "text": result["message"]})
        status = result["status"]

        if status == "resolved":
            print(">> OUTCOME: resolved through conversation  (no ticket created)\n")
            return "resolved"
        if status == "escalate":
            break

        # Get the user's next turn.
        if interactive:
            try:
                reply = input("user> ").strip()
            except EOFError:
                reply = ""
            if not reply:
                break
        elif replies:
            reply = replies.pop(0)
            _say("user", reply)
        else:
            break  # no more scripted turns — fall through to escalation
        conversation.append({"role": "user", "text": reply})

    # Escalate — create a categorized ticket for a human technician.
    summary = initial_text.splitlines()[0][:120]
    transcript = [f'{m["role"]}: {m["text"]}' for m in conversation]
    path = create_ticket(ticket_id, category, priority, summary, transcript)
    _say("agent", "I've escalated this to a technician with the full history - "
                  "someone will follow up shortly.")
    print(f">> OUTCOME: escalated -> ticket {ticket_id} created "
          f"(category={category}, priority={priority})")
    print(f"   routed to the help desk queue: {os.path.relpath(path, config.ROOT)}\n")
    return "escalated"


# --- Scripted demo --------------------------------------------------------

DEMO_SCENARIOS = [
    {
        "id": "DEMO-001",
        "label": "Self-service (canned, no model call)",
        "ticket": "Subject: Password expired\n\nI forgot my password and need to reset it.",
        "replies": [],
    },
    {
        "id": "DEMO-002",
        "label": "Resolved through conversation",
        "ticket": "Subject: Software install failing\n\n"
                  "I need our design app installed and the installer keeps failing with an error.",
        "replies": ["I have an approved request, ticket #4412.", "That worked, thanks!"],
    },
    {
        "id": "DEMO-003",
        "label": "Escalated to a technician",
        "ticket": "Subject: Laptop won't power on\n\n"
                  "My laptop won't turn on at all this morning, even plugged in.",
        "replies": ["Tried a different outlet, still nothing.",
                    "Held the power button 30 seconds, no change.",
                    "Still completely dead."],
    },
]


def run_demo(client):
    for sc in DEMO_SCENARIOS:
        print("=" * 72)
        print(f"SCENARIO: {sc['label']}")
        print("=" * 72)
        _say("user", sc["ticket"].replace("\n\n", " | ").replace("\n", " "))
        handle_ticket(sc["ticket"], client, ticket_id=sc["id"], user_replies=sc["replies"])


# --- CLI ------------------------------------------------------------------

def load_tickets():
    if not os.path.exists(config.TICKETS_CSV):
        raise SystemExit("No tickets found. Run: python data/generate_synthetic.py")
    with open(config.TICKETS_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main():
    ap = argparse.ArgumentParser(description="Tier-1 support agent (resolve or escalate).")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--demo", action="store_true", help="run scripted scenarios, no input")
    g.add_argument("--chat", action="store_true", help="type your own ticket and play the user")
    g.add_argument("--text", help="start from an ad-hoc ticket string (interactive)")
    g.add_argument("--ticket", help="start from a ticket_id in data/tickets.csv (interactive)")
    args = ap.parse_args()

    client = get_client()
    print(f"[provider: {client.name}]\n")

    if args.demo:
        run_demo(client)
        return

    if args.chat:
        text = input("Describe your issue (as the end user):\nuser> ").strip()
        if not text:
            raise SystemExit("No ticket entered.")
        handle_ticket(text, client, ticket_id="CHAT-001", interactive=True)
        return

    if args.text:
        handle_ticket(args.text, client, ticket_id="ADHOC-001", interactive=True)
        return

    rows = load_tickets()
    match = next((r for r in rows if r["ticket_id"] == args.ticket), None)
    if not match:
        raise SystemExit(f"Ticket {args.ticket} not found.")
    text = f"Subject: {match['subject']}\n\n{match['body']}"
    _say("user", text.replace("\n\n", " | "))
    handle_ticket(text, client, ticket_id=match["ticket_id"], interactive=True)


if __name__ == "__main__":
    main()
