# Help Desk Triage Agent

An LLM-powered ticket triage system for an IT help desk. It reads an inbound support
ticket, retrieves relevant knowledge base context, proposes a classification and
priority, identifies the minimum information a technician still needs, and drafts a
Tier-1 reply — **as a proposal for human review, never an automated action.**

It ships with a second utility, a portable **bulk categorizer** that cleans up a backlog
of mis-categorized tickets with a two-stage keyword-then-LLM pipeline.

The headline design choice is a **hybrid model architecture exposed as a single config
boundary**: a frontier model can design and supervise while a local open-source model
handles routine, high-volume inference cheaply and with no ticket data leaving the
building. Which one runs is one environment variable.

> Self-contained and runnable with zero setup. All data is synthetic — there is no
> resemblance to any real organization or ticket data.

---

## The problem

Inbound tickets arrive with wildly uneven detail. Technicians spend their first contact
gathering basics — device, error message, what the user already tried — before any real
work starts. Categorization and priority are applied inconsistently at intake, which
makes routing, reporting, and trend analysis unreliable.

This project standardizes first contact: every ticket arrives pre-classified, pre-enriched
with KB context, and accompanied by a drafted reply and a short list of the exact questions
a technician needs answered — while keeping a human firmly in the loop.

---

## Architecture

```mermaid
flowchart TD
    A[Inbound ticket text] --> B[KB retrieval<br/>keyword match over mock KB]
    B --> C{LLM client<br/>LLM_PROVIDER}
    C -->|mock| D[Deterministic<br/>offline, zero-setup]
    C -->|ollama| E[Local open-source model<br/>cheap runtime tier]
    C -->|anthropic| F[Frontier Claude model<br/>architect tier]
    D --> G[Validate + coerce<br/>untrusted model output]
    E --> G
    F --> G
    G --> H[Triage proposal:<br/>category, priority, missing info, draft reply]
    H --> I[[Human technician review<br/>no automated action]]
```

The same three providers back the bulk categorizer's second stage. **`config.py` is the
swap point** — set `LLM_PROVIDER` and every model call in the project moves between tiers
without touching any other code.

### Why hybrid — the cost & privacy thesis

- **Cost scales with difficulty, not volume.** Routine, high-confidence tickets are
  resolved by a deterministic/keyword pass or a cheap local model. Only genuinely
  ambiguous cases need a frontier model. In the bundled demo, the keyword stage alone
  resolves ~85% of a 300-ticket backlog before any model is called.
- **Data privacy by default.** Point `LLM_PROVIDER` at a local model and no ticket text
  — which routinely contains names, asset tags, and account details — ever leaves your
  infrastructure. The frontier tier is opt-in, for the work that genuinely benefits from it.
- **Frontier as architect, local as runtime.** A frontier model is ideal for design,
  prompt engineering, and supervising edge cases; a small local model is ideal for the
  thousand routine inferences a day. Making the boundary a single config line means you
  choose per-deployment, not per-rewrite.

### Security posture (carried over from the real system's design)

- **Human-in-the-loop is mandatory.** The agent only ever produces a proposal; it never
  creates, edits, or closes a ticket.
- **Model output is untrusted data.** Every model response passes through a validation /
  coercion layer (`LLMClient._clean_triage`) before use — the category is allowlisted,
  the priority is constrained, confidence is clamped.

---

## Quickstart

No dependencies are needed for the default (mock) provider — just Python 3.9+.

```bash
# 1. Generate the synthetic tickets + mock knowledge base
python data/generate_synthetic.py

# 2. Triage a few tickets (proposal only)
python triage_agent.py --demo 3
python triage_agent.py --text "VPN keeps dropping and I can't work"

# 3. Clean up the backlog — see what would change without writing
python bulk_categorize.py --dry-run

# 4. Write the cleaned CSV
python bulk_categorize.py --out data/tickets_categorized.csv
```

See [`examples/sample_run.txt`](examples/sample_run.txt) for expected output.

### Switching providers (the hybrid swap)

```bash
# Default — deterministic, offline, no setup
set LLM_PROVIDER=mock           # (PowerShell: $env:LLM_PROVIDER="mock")

# Local open-source model via Ollama — install Ollama, then `ollama pull llama3.1:8b`
set LLM_PROVIDER=ollama

# Frontier Claude model — pip install -r requirements.txt, set ANTHROPIC_API_KEY
set LLM_PROVIDER=anthropic
```

Nothing else changes. Copy `.env.example` to `.env` to set these persistently.

---

## Repo layout

```
helpdesk-triage-agent/
├── README.md
├── requirements.txt          # only needed for the anthropic provider
├── .env.example              # the LLM_PROVIDER swap point
├── config.py                 # providers, taxonomy, paths — central config
├── scoring.py                # deterministic keyword scorer (shared)
├── llm_client.py             # mock / ollama / anthropic behind one interface
├── triage_agent.py           # core agent: retrieve KB -> propose -> human review
├── bulk_categorize.py        # two-stage CSV backlog categorizer (dry-run capable)
├── prompts/                  # prompt templates used by the real providers
├── data/
│   ├── generate_synthetic.py # builds tickets.csv + kb/
│   ├── tickets.csv           # ~300 synthetic tickets (generated)
│   └── kb/                   # one mock KB article per category (generated)
└── examples/
    └── sample_run.txt
```

---

## Notes

- **Synthetic only.** `data/generate_synthetic.py` is seeded, so the data is reproducible.
  The ground-truth column (`_seed_category`) exists only so the demo can report accuracy.
- **Mock provider is a real implementation of the interface**, not a stub — it classifies
  with the shared keyword scorer and produces valid, structured proposals. That is what
  lets the whole pipeline run with zero setup while still exercising the real control flow.
- The triage taxonomy and keyword cues live in `config.py`; edit them there to retarget
  the system to a different environment.
