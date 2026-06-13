"""Core triage agent.

Takes one ticket, retrieves relevant knowledge base context, and produces a structured
triage proposal: category, priority, the minimum information a technician still needs,
and a draft Tier-1 reply. It NEVER acts on the ticket — it observes and proposes. Every
output is a draft for human review. That human-in-the-loop boundary is deliberate and
load-bearing.

The model call goes through llm_client.get_client(), so the same flow runs against the
mock provider (default), a local Ollama model, or a frontier Claude model — chosen by
the LLM_PROVIDER environment variable, nothing else.

Examples:
  python triage_agent.py --ticket TS-1004
  python triage_agent.py --text "VPN keeps dropping and I can't work"
  python triage_agent.py --demo 3
"""

import argparse
import csv
import json
import os

import config
from llm_client import get_client
from scoring import score_categories


# --- Knowledge base retrieval ---------------------------------------------

def load_kb_article(category: str):
    """Load the mock KB article for a category, or None."""
    path = os.path.join(config.KB_DIR, f"{config.slug(category)}.json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def retrieve_kb_context(ticket_text: str, top_n: int = 1) -> str:
    """Pick the most relevant KB article(s) by keyword score and format as text.

    Simple keyword retrieval is enough for a small KB and keeps the behavior
    transparent — you can always explain why a given article was injected.
    """
    ranked = [(c, s) for c, s in score_categories(ticket_text) if s > 0]
    chunks = []
    for category, _ in ranked[:top_n]:
        article = load_kb_article(category)
        if not article:
            continue
        steps = "\n".join(f"- {s}" for s in article["steps"])
        chunks.append(f"# {article['title']}\n{steps}")
    return "\n\n".join(chunks)


# --- Ticket loading -------------------------------------------------------

def load_tickets():
    if not os.path.exists(config.TICKETS_CSV):
        raise SystemExit("No tickets found. Run: python data/generate_synthetic.py")
    with open(config.TICKETS_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def ticket_text(row: dict) -> str:
    return f"Subject: {row['subject']}\n\n{row['body']}"


# --- Triage flow ----------------------------------------------------------

def triage_one(text: str, client) -> dict:
    kb_context = retrieve_kb_context(text)
    proposal = client.triage(text, kb_context)
    proposal["_kb_used"] = bool(kb_context)
    return proposal


def render(text: str, proposal: dict):
    print("=" * 70)
    print(text)
    print("-" * 70)
    print(f"  category    : {proposal['category']}")
    print(f"  priority    : {proposal['priority']}")
    print(f"  confidence  : {proposal['confidence']}")
    print(f"  kb context  : {'yes' if proposal.get('_kb_used') else 'no'}")
    if proposal["missing_info"]:
        print("  missing info:")
        for q in proposal["missing_info"]:
            print(f"     - {q}")
    print("  draft reply :")
    for line in proposal["draft_reply"].splitlines() or [""]:
        print(f"     {line}")
    print("  >> PROPOSAL ONLY - awaiting technician review. No action taken.")
    print("=" * 70)


def main():
    ap = argparse.ArgumentParser(description="Help desk triage agent (proposal only).")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--ticket", help="triage a ticket_id from data/tickets.csv")
    g.add_argument("--text", help="triage an ad-hoc ticket string")
    g.add_argument("--demo", type=int, metavar="N", help="triage the first N tickets")
    args = ap.parse_args()

    client = get_client()
    print(f"[provider: {client.name}]\n")

    if args.text:
        render(args.text, triage_one(args.text, client))
        return

    rows = load_tickets()
    if args.ticket:
        match = next((r for r in rows if r["ticket_id"] == args.ticket), None)
        if not match:
            raise SystemExit(f"Ticket {args.ticket} not found.")
        t = ticket_text(match)
        render(t, triage_one(t, client))
        return

    for row in rows[: args.demo]:
        t = ticket_text(row)
        render(t, triage_one(t, client))


if __name__ == "__main__":
    main()
