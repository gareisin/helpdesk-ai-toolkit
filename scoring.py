"""Deterministic keyword scorer — the offline, no-LLM classification layer.

Shared by two callers:
  * the mock LLM provider (so the whole pipeline runs with zero setup), and
  * stage 1 of bulk_categorize.py (the cheap pass that resolves the obvious
    tickets before anything is sent to a model).

Keeping this logic in one place means the "what counts as a clear match" rule is
defined once. It is intentionally simple: count keyword hits per category and
rank them. Simple, legible, and fast — exactly what a first-pass filter should be.
"""

from config import CATEGORIES


def score_categories(text: str):
    """Return a list of (category, score) sorted high-to-low.

    score = number of distinct keywords for that category found in `text`.
    """
    low = (text or "").lower()
    scores = []
    for category, keywords in CATEGORIES.items():
        hits = sum(1 for kw in keywords if kw in low)
        scores.append((category, hits))
    scores.sort(key=lambda kv: kv[1], reverse=True)
    return scores


def classify(text: str, threshold: int = 1):
    """Best-effort single-label classification from keywords alone.

    Returns (category, confidence, ranked).

    A ticket is confidently classified only when exactly one category leads and
    it clears the threshold. Ties, or a top score below threshold, fall back to
    "Other" with low confidence — the signal that a smarter (LLM) pass is needed.
    """
    ranked = score_categories(text)
    top_cat, top_score = ranked[0]
    runner_score = ranked[1][1] if len(ranked) > 1 else 0

    if top_score < threshold:
        return "Other", 0.2, ranked
    if top_score == runner_score:
        # Ambiguous — two categories tied. Let a model break the tie.
        return "Other", 0.3, ranked

    # Confidence grows with the margin over the runner-up, capped.
    margin = top_score - runner_score
    confidence = min(0.95, 0.55 + 0.15 * margin)
    return top_cat, confidence, ranked
