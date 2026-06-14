"""Provider-swappable LLM client.

One interface, three implementations, selected by config.LLM_PROVIDER:

    MockClient       deterministic, offline, no dependencies
    OllamaClient     local open-source model over HTTP (cheap runtime tier)
    AnthropicClient  frontier Claude model (architect tier)

The interface is domain-aware. Two methods are all the rest of the codebase calls:

    respond(conversation, kb_context)  -> the agent's next turn in a support chat
    categorize(summary)                -> single-label classification (bulk tool)

`respond` drives the conversational Tier-1 agent: given the conversation so far and
retrieved knowledge-base context, it returns the agent's next message to the user plus
a status that tells the agent loop whether the issue is resolved, still in progress, or
should be escalated to a human. Swapping providers changes cost and where data goes —
never the calling code.
"""

import json
import os

import config
from scoring import classify

VALID_STATUS = ("in_progress", "resolved", "escalate")

# Phrases that signal the user's problem is fixed (used by the deterministic
# provider; the real models infer resolution from the conversation).
_RESOLVED_SIGNALS = (
    "worked", "that fixed", "fixed it", "resolved", "did it", "all set",
    "all good", "thanks", "thank you", "that did it", "no longer", "back up",
)


class LLMClient:
    name = "base"

    def respond(self, conversation: list, kb_context: str) -> dict:
        raise NotImplementedError

    def categorize(self, summary: str) -> dict:
        raise NotImplementedError

    # Model output is data, not a command — coerce it into a known shape before
    # the agent loop acts on it.
    def _clean_turn(self, raw: dict) -> dict:
        message = str(raw.get("message", "")).strip()
        status = str(raw.get("status", "in_progress")).lower()
        if status not in VALID_STATUS:
            status = "in_progress"
        if not message:
            message = "Could you tell me a bit more about what you're seeing?"
        return {"message": message, "status": status}


# --------------------------------------------------------------------------
# Mock — deterministic, offline. The default.
# --------------------------------------------------------------------------

class MockClient(LLMClient):
    name = "mock"

    @staticmethod
    def _kb_steps(kb_context: str) -> list:
        """Pull the bulleted troubleshooting steps out of retrieved KB text."""
        steps = []
        for line in (kb_context or "").splitlines():
            line = line.strip()
            if line.startswith("- "):
                steps.append(line[2:].strip())
        return steps

    def respond(self, conversation: list, kb_context: str) -> dict:
        user_msgs = [m for m in conversation if m["role"] == "user"]
        last_user = user_msgs[-1]["text"].lower() if user_msgs else ""

        # 1) Did the user just tell us it's fixed?
        if len(user_msgs) > 1 and any(sig in last_user for sig in _RESOLVED_SIGNALS):
            return self._clean_turn({
                "message": "Glad that resolved it - I'll close this out. "
                           "If it comes back, just reply here and I'll reopen it.",
                "status": "resolved",
            })

        # 2) Otherwise walk the KB troubleshooting steps, one per turn.
        steps = self._kb_steps(kb_context)
        step_idx = len(user_msgs) - 1  # 0-based: first user msg -> first step
        if steps and step_idx < len(steps):
            step = steps[step_idx]
            if step_idx == 0:
                msg = (f"Thanks for the details - let's start here: {step} "
                       f"Did that help? If not, tell me what happened.")
            else:
                msg = (f"Okay. Next, try this: {step} "
                       f"Let me know if that resolves it.")
            return self._clean_turn({"message": msg, "status": "in_progress"})

        # 3) Out of standard steps — hand off to a human.
        return self._clean_turn({
            "message": "I've run through the standard steps without luck. I'll escalate "
                       "this to a technician with everything we've covered so far.",
            "status": "escalate",
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

    def _chat_json(self, messages: list) -> dict:
        payload = {"model": self.model, "messages": messages,
                   "format": "json", "stream": False}
        req = self._urllib.Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with self._urllib.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return json.loads(body.get("message", {}).get("content", "{}"))

    def respond(self, conversation: list, kb_context: str) -> dict:
        messages = _build_chat(conversation, kb_context)
        return self._clean_turn(self._chat_json(messages))

    def categorize(self, summary: str) -> dict:
        messages = [
            {"role": "system", "content": _categorize_system_prompt()},
            {"role": "user", "content": f"Ticket summary:\n{summary}\n\n"
                                        f"Return JSON: {{category, confidence}}."},
        ]
        raw = self._chat_json(messages)
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

    def _message_json(self, system: str, messages: list) -> dict:
        resp = self._client.messages.create(
            model=self.model, max_tokens=1024, system=system, messages=messages,
        )
        text = next((b.text for b in resp.content if b.type == "text"), "{}")
        return json.loads(_extract_json(text))

    def respond(self, conversation: list, kb_context: str) -> dict:
        system = _support_system_prompt(kb_context)
        messages = [
            {"role": "user" if m["role"] == "user" else "assistant",
             "content": m["text"]}
            for m in conversation
        ]
        # Nudge the model to answer in the required JSON shape on this turn.
        messages.append({"role": "user", "content": "Respond with the JSON object "
                                                     "{message, status} for your next reply."})
        return self._clean_turn(self._message_json(system, messages))

    def categorize(self, summary: str) -> dict:
        raw = self._message_json(
            _categorize_system_prompt(),
            [{"role": "user", "content": f"Ticket summary:\n{summary}\n\n"
                                         f"Return JSON: {{category, confidence}}."}],
        )
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


def _support_system_prompt(kb_context: str) -> str:
    base = _load_prompt(
        "support_system.txt",
        "You are a Tier-1 IT help desk technician talking directly to an end user. "
        "Be concise and friendly. Work the issue one step at a time: ask for the "
        "minimum information you need, then suggest a concrete fix. Use the knowledge "
        "base context when relevant. Every turn, respond with a single JSON object "
        "{\"message\": <your reply to the user>, \"status\": <\"in_progress\" | "
        "\"resolved\" | \"escalate\">}. Use \"resolved\" only when the user confirms "
        "the issue is fixed, and \"escalate\" when it needs a human technician.",
    )
    return f"{base}\n\nKnowledge base context:\n{kb_context or '(none found)'}"


def _build_chat(conversation: list, kb_context: str) -> list:
    """Map the conversation into provider chat messages with a leading system turn."""
    messages = [{"role": "system", "content": _support_system_prompt(kb_context)}]
    for m in conversation:
        messages.append({"role": "user" if m["role"] == "user" else "assistant",
                         "content": m["text"]})
    return messages


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
    start, end = text.find("{"), text.rfind("}")
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
