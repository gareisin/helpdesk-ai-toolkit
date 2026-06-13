"""Provider-swappable LLM client.

One interface, three implementations, selected by config.LLM_PROVIDER:

    MockClient       deterministic, offline, no dependencies
    OllamaClient     local open-source model over HTTP (cheap runtime tier)
    AnthropicClient  frontier Claude model (architect tier)

The interface is deliberately *domain-aware* rather than a thin `complete(text)`
wrapper. The two methods below are the only things the rest of the codebase calls:

    triage(ticket_text, kb_context)   -> structured triage proposal
    categorize(summary)               -> single-label classification

Because the contract is fixed, swapping providers changes cost and where data
goes — not the calling code. That is the hybrid architecture made concrete.
"""

import json
import os

import config
from scoring import classify, score_categories

# Reusable description of the JSON contract both real providers must satisfy.
_TRIAGE_KEYS = "category, priority, confidence, missing_info, draft_reply"
_PRIORITIES = "low | medium | high | urgent"


# --------------------------------------------------------------------------
# Base class
# --------------------------------------------------------------------------

class LLMClient:
    name = "base"

    def triage(self, ticket_text: str, kb_context: str) -> dict:
        raise NotImplementedError

    def categorize(self, summary: str) -> dict:
        raise NotImplementedError

    # Shared validation so a malformed model response can't poison downstream
    # steps. Model output is untrusted data — coerce it into a known shape.
    def _clean_triage(self, raw: dict) -> dict:
        cat = raw.get("category")
        if cat not in config.CATEGORY_NAMES:
            cat = "Other"
        pri = str(raw.get("priority", "medium")).lower()
        if pri not in ("low", "medium", "high", "urgent"):
            pri = "medium"
        try:
            conf = float(raw.get("confidence", 0.5))
        except (TypeError, ValueError):
            conf = 0.5
        conf = max(0.0, min(1.0, conf))
        missing = raw.get("missing_info") or []
        if not isinstance(missing, list):
            missing = [str(missing)]
        draft = str(raw.get("draft_reply", "")).strip()
        return {
            "category": cat,
            "priority": pri,
            "confidence": round(conf, 2),
            "missing_info": [str(m) for m in missing][:6],
            "draft_reply": draft,
        }


# --------------------------------------------------------------------------
# Mock — deterministic, offline. The default.
# --------------------------------------------------------------------------

# Per-category follow-up questions the mock asks to reach "minimum viable info".
_MISSING_INFO = {
    "Password Reset": ["What is the username or email on the affected account?",
                       "Are you fully locked out, or just need a reset?"],
    "Email": ["What is the exact error message, if any?",
              "Does this affect Outlook desktop, web, or mobile?"],
    "Hardware": ["What is the device asset tag or serial number?",
                 "When did the issue start, and is it constant or intermittent?"],
    "Network / VPN": ["Are you on-site or remote?",
                      "Does it affect Wi-Fi, wired, or VPN specifically?"],
    "Software Install": ["What is the exact application name and version?",
                         "Do you have an approved license or request ticket?"],
    "Printer": ["Which printer (name or location)?",
                "Is it failing to print, scan, or both?"],
    "Account Access": ["What system or resource are you trying to access?",
                       "What access level do you expect to have?"],
    "Phishing / Spam": ["Did you click any link or open any attachment?",
                        "Please forward the message as an attachment — do not delete it."],
    "Performance": ["Which application is slow, or is it the whole machine?",
                    "When did it start, and does a restart help temporarily?"],
    "Other": ["Can you describe the issue in a bit more detail?",
              "What outcome are you hoping for?"],
}


class MockClient(LLMClient):
    name = "mock"

    def _priority(self, text: str, category: str) -> str:
        low = text.lower()
        for level in ("urgent", "high", "low"):
            if any(cue in low for cue in config.PRIORITY_CUES[level]):
                return level
        # Security categories default up; everything else is medium.
        if category == "Phishing / Spam":
            return "high"
        return "medium"

    def triage(self, ticket_text: str, kb_context: str) -> dict:
        category, confidence, _ = classify(ticket_text)
        priority = self._priority(ticket_text, category)
        first_kb_line = (kb_context.strip().splitlines() or [""])[0].lstrip("# ").strip()
        if first_kb_line:
            draft = (
                f"Thanks for reaching out. This looks like a {category} issue. "
                f"Based on our knowledge base ({first_kb_line}), please try the steps "
                f"below while we confirm a few details. If they don't resolve it, "
                f"a technician will follow up."
            )
        else:
            draft = (
                f"Thanks for reaching out. This looks like a {category} issue. "
                f"A technician will follow up once we confirm a few details."
            )
        return self._clean_triage({
            "category": category,
            "priority": priority,
            "confidence": confidence,
            "missing_info": _MISSING_INFO.get(category, _MISSING_INFO["Other"]),
            "draft_reply": draft,
        })

    def categorize(self, summary: str) -> dict:
        category, confidence, ranked = classify(summary)
        return {"category": category, "confidence": round(confidence, 2),
                "ranked": ranked[:3]}


# --------------------------------------------------------------------------
# Ollama — local open-source model (cheap runtime tier)
# --------------------------------------------------------------------------

class OllamaClient(LLMClient):
    name = "ollama"

    def __init__(self):
        import urllib.request  # stdlib — no extra dependency
        self._urllib = urllib.request
        self.base_url = config.OLLAMA_BASE_URL.rstrip("/")
        self.model = config.OLLAMA_MODEL

    def _chat_json(self, system: str, user: str) -> dict:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "format": "json",   # ask Ollama to constrain output to JSON
            "stream": False,
        }
        req = self._urllib.Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with self._urllib.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        content = body.get("message", {}).get("content", "{}")
        return json.loads(content)

    def triage(self, ticket_text: str, kb_context: str) -> dict:
        system = _triage_system_prompt()
        user = _triage_user_prompt(ticket_text, kb_context)
        return self._clean_triage(self._chat_json(system, user))

    def categorize(self, summary: str) -> dict:
        system = _categorize_system_prompt()
        user = f"Ticket summary:\n{summary}\n\nReturn JSON: {{category, confidence}}."
        raw = self._chat_json(system, user)
        cat = raw.get("category")
        if cat not in config.CATEGORY_NAMES:
            cat = "Other"
        return {"category": cat, "confidence": float(raw.get("confidence", 0.5))}


# --------------------------------------------------------------------------
# Anthropic — frontier model (architect tier)
# --------------------------------------------------------------------------

class AnthropicClient(LLMClient):
    name = "anthropic"

    def __init__(self):
        import anthropic  # pip install anthropic
        self._client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
        self.model = config.ANTHROPIC_MODEL

    def _message_json(self, system: str, user: str) -> dict:
        # Simple JSON-by-instruction + parse. For production you would pin the
        # shape with output_config.format (structured outputs); kept simple here
        # so the contract is obvious when reading the code.
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = next((b.text for b in resp.content if b.type == "text"), "{}")
        return json.loads(_extract_json(text))

    def triage(self, ticket_text: str, kb_context: str) -> dict:
        system = _triage_system_prompt()
        user = _triage_user_prompt(ticket_text, kb_context)
        return self._clean_triage(self._message_json(system, user))

    def categorize(self, summary: str) -> dict:
        system = _categorize_system_prompt()
        user = f"Ticket summary:\n{summary}\n\nReturn JSON: {{category, confidence}}."
        raw = self._message_json(system, user)
        cat = raw.get("category")
        if cat not in config.CATEGORY_NAMES:
            cat = "Other"
        return {"category": cat, "confidence": float(raw.get("confidence", 0.5))}


# --------------------------------------------------------------------------
# Shared prompt builders (used by the real providers only)
# --------------------------------------------------------------------------

def _load_prompt(name: str, fallback: str) -> str:
    path = os.path.join(config.PROMPTS_DIR, name)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return fallback


def _triage_system_prompt() -> str:
    cats = ", ".join(config.CATEGORY_NAMES)
    return _load_prompt(
        "triage_system.txt",
        f"You are a Tier-1 IT help desk triage assistant. You never take action; "
        f"you produce a proposal a technician reviews. Respond with a single JSON "
        f"object with keys: {_TRIAGE_KEYS}. category must be one of: {cats}. "
        f"priority must be one of: {_PRIORITIES}. confidence is 0-1. missing_info "
        f"is a list of short questions needed before a technician can act. "
        f"draft_reply is a polite Tier-1 response for human review.",
    )


def _triage_user_prompt(ticket_text: str, kb_context: str) -> str:
    return (
        f"Knowledge base context:\n{kb_context or '(none found)'}\n\n"
        f"Ticket:\n{ticket_text}\n\n"
        f"Return only the JSON object."
    )


def _categorize_system_prompt() -> str:
    cats = ", ".join(config.CATEGORY_NAMES)
    return _load_prompt(
        "categorize_system.txt",
        f"You classify IT help desk tickets into exactly one category. "
        f"Categories: {cats}. Respond with a JSON object {{category, confidence}} "
        f"where confidence is 0-1. Use 'Other' only if nothing else fits.",
    )


def _extract_json(text: str) -> str:
    """Pull the first {...} block out of a model response, tolerant of prose."""
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start:end + 1]
    return "{}"


# --------------------------------------------------------------------------
# Factory
# --------------------------------------------------------------------------

def get_client() -> LLMClient:
    """Return the client for the configured provider. This is the swap point."""
    provider = config.LLM_PROVIDER
    if provider == "mock":
        return MockClient()
    if provider == "ollama":
        return OllamaClient()
    if provider == "anthropic":
        return AnthropicClient()
    raise ValueError(
        f"Unknown LLM_PROVIDER {provider!r}. Use one of: mock, ollama, anthropic."
    )
