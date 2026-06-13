"""Generate synthetic help desk data — fake tickets + a small mock knowledge base.

Everything here is invented. There is no resemblance to any real organization, person,
or ticket system. The generator is seeded, so it produces the same data every run, which
keeps the demo reproducible.

Outputs:
  data/tickets.csv      ~300 synthetic tickets (some uncategorized, some mis-categorized,
                        to mimic a real backlog the categorizer would clean up)
  data/kb/<slug>.json   one mock KB article per category

Run:  python data/generate_synthetic.py
"""

import csv
import json
import os
import random
import sys

# Allow running from anywhere — import config from the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402

SEED = 1729
N_TICKETS = 300

FAKE_NAMES = [
    "Jordan Avery", "Priya Nair", "Marcus Hale", "Lena Ortiz", "Sam Okafor",
    "Dana Whitfield", "Theo Bauer", "Ruth Calderon", "Owen Pace", "Mira Sandoval",
    "Felix Romano", "Nadia Khan", "Beck Sullivan", "Cora Bell", "Ian Frost",
]

# Per-category content: subject lines and body fragments. Deliberately generic.
TEMPLATES = {
    "Password Reset": {
        "subjects": ["Locked out of my account", "Password expired", "Can't log in this morning"],
        "bodies": [
            "I tried logging in and it says my password expired. Can you reset it? My username is {user}.",
            "I'm locked out after a few bad attempts and need to get back in. No rush.",
            "Forgot my password over the weekend and now I can't log in. Can you unlock account {user}?",
        ],
    },
    "Email": {
        "subjects": ["Outlook won't send", "Missing emails", "Calendar invite issue"],
        "bodies": [
            "My Outlook keeps showing 'cannot send' with an error. Inbox loads fine though.",
            "A calendar invite I sent isn't showing up for the recipients. Can you check?",
            "My mailbox stopped syncing on my phone this morning. Desktop is okay.",
        ],
    },
    "Hardware": {
        "subjects": ["Laptop won't turn on", "Second monitor dead", "Keyboard not working"],
        "bodies": [
            "My laptop (asset {asset}) won't turn on at all this morning, even plugged in.",
            "The second monitor on my docking station is black. Cable seems fine.",
            "A few keys on my keyboard stopped working. Started yesterday, getting worse.",
        ],
    },
    "Network / VPN": {
        "subjects": ["VPN keeps disconnecting", "No internet at my desk", "Can't connect to Wi-Fi"],
        "bodies": [
            "The VPN disconnects every few minutes while I'm working remote. Can't work like this.",
            "No internet at my desk since this morning — wired connection shows no network.",
            "I can't connect to Wi-Fi on my laptop, but my phone connects fine.",
        ],
    },
    "Software Install": {
        "subjects": ["Need software installed", "License activation failing", "Update won't apply"],
        "bodies": [
            "Can you install the latest version of our design application? I have an approved request.",
            "The license activation for my software keeps failing with an error code.",
            "A required update won't apply — it downloads then rolls back every time.",
        ],
    },
    "Printer": {
        "subjects": ["Printer paper jam", "Can't print to the shared printer", "Scanner not working"],
        "bodies": [
            "The printer by the break room has a paper jam I can't clear. Print queue is stuck.",
            "I can't print to the shared printer — jobs just sit in the queue.",
            "The scanner on the multifunction printer isn't responding when I try to scan.",
        ],
    },
    "Account Access": {
        "subjects": ["Need access to shared drive", "MFA not working", "Permission denied on folder"],
        "bodies": [
            "I need access to the shared drive for my new project. What do you need from me?",
            "My two-factor (MFA) prompt isn't arriving on my phone, so I can't get in.",
            "I get 'permission denied' opening a folder I'm supposed to have access to.",
        ],
    },
    "Phishing / Spam": {
        "subjects": ["Suspicious email", "Is this a scam?", "Got a phishing message"],
        "bodies": [
            "I got a suspicious email with a link asking me to log in. I did not click it.",
            "This message looks like phishing — it's pretending to be from IT. Forwarding now.",
            "Someone emailed asking for gift cards from the director's name. Looks like a scam.",
        ],
    },
    "Performance": {
        "subjects": ["Computer is very slow", "App keeps freezing", "Everything is lagging"],
        "bodies": [
            "My whole machine is very slow and freezing since the last update. A restart helps briefly.",
            "One application keeps freezing and I have to force-close it. Started this week.",
            "Everything is lagging and the cursor spins constantly. Hard to get work done.",
        ],
    },
    "Other": {
        "subjects": ["Question about policy", "General request", "Not sure who to ask"],
        "bodies": [
            "I had a general question about the new equipment policy — who should I talk to?",
            "Not sure this is the right place, but I need help with something unusual.",
            "Quick question about how to request a new piece of equipment.",
        ],
    },
}

# Mock KB: one article per real category (skip "Other").
KB_ARTICLES = {
    "Password Reset": ["Verify the user's identity per policy.",
                       "Reset the password in the directory and require change at next logon.",
                       "Confirm the user can log in and clear any account lockout."],
    "Email": ["Confirm whether the issue is desktop, web, or mobile.",
              "Check mailbox quota and connection status.",
              "Recreate the mail profile if sync is broken."],
    "Hardware": ["Confirm power and try a different outlet/cable.",
                 "For docking issues, reseat the dock and test ports directly.",
                 "Log the asset tag and arrange a loaner if needed."],
    "Network / VPN": ["Confirm on-site vs remote and Wi-Fi vs wired vs VPN.",
                      "Restart the network adapter and renew DHCP.",
                      "For VPN drops, check the client version and signal stability."],
    "Software Install": ["Verify the request is approved and a license is available.",
                         "Install from the managed software catalog.",
                         "Confirm activation and version after install."],
    "Printer": ["Identify the printer by name/location.",
                "Clear the print queue and the physical jam.",
                "Run a test print and scan to confirm both functions."],
    "Account Access": ["Confirm the resource and the expected access level.",
                       "Verify group membership and request approval if needed.",
                       "For MFA, re-register the device or push a new prompt."],
    "Phishing / Spam": ["Do not click links or open attachments.",
                        "Have the user forward the message as an attachment.",
                        "Report to security and block the sender; check for other recipients."],
    "Performance": ["Identify whether one app or the whole machine is slow.",
                    "Check CPU/memory and recent updates; restart.",
                    "Escalate for hardware diagnostics if it persists."],
}


def build_tickets(rng):
    rows = []
    cats = config.CATEGORY_NAMES
    for i in range(1, N_TICKETS + 1):
        category = rng.choice(cats)
        tpl = TEMPLATES[category]
        subject = rng.choice(tpl["subjects"])
        body = rng.choice(tpl["bodies"]).format(
            user=rng.choice(FAKE_NAMES).split()[0].lower(),
            asset=f"LT-{rng.randint(100, 999)}",
        )
        # Simulate a messy backlog: ~60% uncategorized, ~15% mis-categorized,
        # ~25% correctly categorized. The categorizer's job is to fix this field.
        roll = rng.random()
        if roll < 0.60:
            current = ""
        elif roll < 0.75:
            current = rng.choice([c for c in cats if c != category])
        else:
            current = category
        rows.append({
            "ticket_id": f"TS-{1000 + i}",
            "created": f"2026-{rng.randint(1, 6):02d}-{rng.randint(1, 28):02d}",
            "subject": subject,
            "body": body,
            "current_category": current,
            # _seed_category is the ground truth used only to score the demo.
            "_seed_category": category,
        })
    return rows


def write_tickets(rows):
    os.makedirs(config.DATA_DIR, exist_ok=True)
    fields = ["ticket_id", "created", "subject", "body", "current_category", "_seed_category"]
    with open(config.TICKETS_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def write_kb():
    os.makedirs(config.KB_DIR, exist_ok=True)
    for category, steps in KB_ARTICLES.items():
        article = {
            "category": category,
            "title": f"{category}: Tier-1 troubleshooting",
            "steps": steps,
        }
        path = os.path.join(config.KB_DIR, f"{config.slug(category)}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(article, f, indent=2)


def main():
    rng = random.Random(SEED)
    rows = build_tickets(rng)
    write_tickets(rows)
    write_kb()
    print(f"Wrote {len(rows)} tickets -> {config.TICKETS_CSV}")
    print(f"Wrote {len(KB_ARTICLES)} KB articles -> {config.KB_DIR}")


if __name__ == "__main__":
    main()
