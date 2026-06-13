"""Bulk ticket categorizer — a portable, config-driven backlog-cleanup utility.

Takes a CSV of tickets with unreliable categories and assigns a clean category to each,
using a two-stage pipeline:

  Stage 1 — keyword scorer (offline, no LLM). Resolves the obvious tickets for free.
  Stage 2 — LLM fallback. Only the ambiguous / low-signal / no-match tickets are sent
            to a model, so cost scales with difficulty, not volume.

Results are written to a new CSV. A --dry-run mode shows what would change without
writing anything. Because it reads a generic CSV and uses the same swappable LLM client,
it drops into any environment: point it at a different file and provider and it runs.

Examples:
  python bulk_categorize.py --dry-run
  python bulk_categorize.py --in data/tickets.csv --out data/tickets_categorized.csv
"""

import argparse
import csv
import os

import config
from llm_client import get_client
from scoring import classify

# Stage-1 keyword classification is "confident" at or above this confidence.
CONFIDENT = 0.55


def categorize_rows(rows, client, text_field):
    """Run both stages over rows. Returns (results, stats)."""
    results = []
    stats = {"stage1": 0, "stage2": 0, "total": len(rows)}
    for row in rows:
        text = f"{row.get('subject', '')} {row.get(text_field, '')}".strip()
        category, confidence, _ = classify(text)

        if category != "Other" and confidence >= CONFIDENT:
            decided_by = "keyword"
            stats["stage1"] += 1
        else:
            # Stage 2 — hand the hard ones to the model.
            verdict = client.categorize(text)
            category = verdict["category"]
            confidence = round(float(verdict.get("confidence", 0.5)), 2)
            decided_by = f"llm:{client.name}"
            stats["stage2"] += 1

        results.append({
            "ticket_id": row.get("ticket_id", ""),
            "subject": row.get("subject", ""),
            "old_category": row.get("current_category", ""),
            "new_category": category,
            "confidence": confidence,
            "decided_by": decided_by,
        })
    return results, stats


def score_accuracy(rows, results):
    """If the synthetic ground-truth column is present, report accuracy (demo only)."""
    truth = {r["ticket_id"]: r.get("_seed_category") for r in rows if r.get("_seed_category")}
    if not truth:
        return None
    correct = sum(1 for r in results if truth.get(r["ticket_id"]) == r["new_category"])
    return correct, len(results)


def main():
    ap = argparse.ArgumentParser(description="Two-stage bulk ticket categorizer.")
    ap.add_argument("--in", dest="infile", default=config.TICKETS_CSV, help="input CSV")
    ap.add_argument("--out", dest="outfile",
                    default=os.path.join(config.DATA_DIR, "tickets_categorized.csv"),
                    help="output CSV")
    ap.add_argument("--text-field", default="body", help="CSV column holding ticket text")
    ap.add_argument("--dry-run", action="store_true",
                    help="print a summary without writing the output CSV")
    args = ap.parse_args()

    if not os.path.exists(args.infile):
        raise SystemExit(f"Input not found: {args.infile}\n"
                         f"Run: python data/generate_synthetic.py")

    with open(args.infile, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    client = get_client()
    print(f"[provider: {client.name}]  categorizing {len(rows)} tickets...\n")

    results, stats = categorize_rows(rows, client, args.text_field)

    changed = sum(1 for r in results if r["old_category"] != r["new_category"])
    print(f"  stage 1 (keyword, free) : {stats['stage1']}")
    print(f"  stage 2 (llm fallback)  : {stats['stage2']}")
    print(f"  category changed        : {changed} / {stats['total']}")

    acc = score_accuracy(rows, results)
    if acc:
        print(f"  accuracy vs synthetic ground truth : {acc[0]}/{acc[1]} "
              f"({acc[0] / acc[1] * 100:.1f}%)")

    if args.dry_run:
        print("\n  -- dry run: no file written. Sample of proposed changes --")
        for r in results[:8]:
            print(f"   {r['ticket_id']}: {r['old_category'] or '(none)':<18} -> "
                  f"{r['new_category']:<18} [{r['decided_by']}]")
        return

    fields = ["ticket_id", "subject", "old_category", "new_category", "confidence", "decided_by"]
    with open(args.outfile, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(results)
    print(f"\n  wrote {len(results)} rows -> {args.outfile}")


if __name__ == "__main__":
    main()
