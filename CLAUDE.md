# Support Agent — Project Documentation

## Overview
A multi-agent customer support chatbot built with:
- **UI**: Streamlit (`app.py`)
- **AI**: OpenAI GPT-4o (NOT Claude/Anthropic API)
- **Database**: Supabase (PostgreSQL)

Run with: `streamlit run app.py`
Test with: `python test_agent.py`

---

## File Structure

```
Support Agent/
├── app.py                    # Streamlit UI + session state
├── orchestrator.py           # State machine, wires agents together
├── agents/
│   ├── extraction_agent.py   # Agent 1: conversation → structured data
│   ├── database_agent.py     # Agent 2: validate + store ticket to Supabase
│   └── query_agent.py        # Agent 3: fetch/update tickets via natural language
├── utils/
│   └── supabase_client.py    # Supabase singleton client
├── test_agent.py             # End-to-end test suite (5 scenarios)
├── requirements.txt
├── .env                      # Never commit — contains API keys
├── .env.example
├── .gitignore                # Excludes .env from git
├── .claudeignore             # Excludes .env from Claude reads
└── pyrightconfig.json        # Pyright/Pylance config (pythonVersion: 3.11)
```

---

## Architecture

### State Machine (orchestrator.py)
4 states: `idle → collecting → submitting → done`

| State | Behaviour |
|-------|-----------|
| `idle` | Greet user, detect complaint intent or greeting |
| `collecting` | Run Extraction Agent each turn; ask for next missing field |
| `submitting` | Trigger DB write (handled by app.py spinner) |
| `done` | All messages routed to Query Agent |

**Data flow (one direction only):**
`app.py → orchestrator.py → agents/*.py → utils/supabase_client.py`

**Key session state keys** (in `st.session_state`):
- `chat_history`: list of `{role, content}` dicts — full conversation
- `flow_state`: current state string
- `collected_fields`: dict of 6 fields (`name`, `phone`, `email`, `location`, `issue_description`, `priority`)
- `current_ticket_id`: set after successful DB insert
- `pending_field`: last field the bot asked for

### Required Fields
```python
REQUIRED_FIELDS = ["name", "phone", "email", "location", "issue_description", "priority"]
```
Priority must be exactly: `"Low"`, `"Medium"`, or `"High"`

---

## Agents

### Agent 1 — Extraction Agent (`agents/extraction_agent.py`)
- **Model**: `gpt-4o-2024-08-06` with OpenAI Structured Outputs (`client.beta.chat.completions.parse()`)
- **Input**: Full `chat_history` list
- **Output**: `{"success": bool, "data": {...}, "missing_fields": [...], "message": ""}`
- Uses `PartialComplaintData` Pydantic model — all fields Optional
- **Sanity check**: after parsing, recomputes `success` by checking all fields are non-null/non-whitespace (strips before check) — prevents model contradictions
- Retry with exponential backoff on `RateLimitError` (3 attempts)

### Agent 2 — Database Agent (`agents/database_agent.py`)
- **Input**: `complaint_data: dict`, `supabase` client
- **Output**: `{"success": True, "ticket_id": "XXXXX"}` or `{"success": False, "message": "..."}`
- Uses **`_fallback_validate()`** (Python-based, deterministic) — NOT `_validate_with_ai()` (was replaced because AI model applied extra judgement beyond the rules)
- Generates unique 5-digit ticket ID with collision detection (10 attempts)
- `status` defaults to `"In Progress"` via SQL DEFAULT
- Fallback validation rules:
  - phone: digits only, length 7–15
  - email: must contain `@` and `.` after it
  - issue_description: at least 10 characters
  - priority: must be `Low`, `Medium`, or `High`

### Agent 3 — Query Agent (`agents/query_agent.py`)
- **Model**: `gpt-4o-mini` with Function Calling
- **Input**: `user_message`, `supabase`, optional `chat_history`
- **Tools**: `list_tickets(status?)`, `get_ticket(ticket_id)`, `update_ticket_status(ticket_id, new_status)`
- Two-step loop: model picks tool → execute Supabase op → model formats response
- Status synonyms: "pending/open" → `In Progress`, "done/closed" → `Completed`, "held/paused" → `On Hold`

---

## Supabase Schema

```sql
CREATE TABLE tickets (
    ticket_id         VARCHAR(5)   PRIMARY KEY,
    name              TEXT         NOT NULL,
    phone             TEXT         NOT NULL,
    email             TEXT         NOT NULL,
    location          TEXT         NOT NULL,
    issue_description TEXT         NOT NULL,
    priority          TEXT         NOT NULL CHECK (priority IN ('Low', 'Medium', 'High')),
    status            TEXT         NOT NULL DEFAULT 'In Progress'
                                   CHECK (status IN ('In Progress', 'Completed', 'On Hold')),
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
ALTER TABLE tickets DISABLE ROW LEVEL SECURITY;
```

---

## Environment Variables (`.env`)
```
OPENAI_API_KEY=sk-...
SUPABASE_URL=https://xyzcompany.supabase.co
SUPABASE_ANON_KEY=eyJ...
```
- Use the **"Publishable and secret API keys"** tab in Supabase Settings → API (not "Legacy" tab)
- URL format: `https://` prefix, no trailing slash

---

## Key Bugs Fixed

| Bug | Cause | Fix |
|-----|-------|-----|
| Name not captured when provided in first message | `idle→collecting` transition reset fields and always asked for name | Now runs extraction on the trigger message immediately; asks for first truly missing field |
| Validation failed for valid phone/email | `_validate_with_ai()` applied extra AI judgement beyond the rules | Replaced with deterministic `_fallback_validate()` |
| `success=False` with `missing_fields=[]` | OpenAI model contradicted itself on email-correction scenario | Extraction agent recomputes `success` from actual field presence |
| Whitespace-only fields passed as valid | Truthy check `if not data.get(f)` passes `" "` | Changed to `not str(data.get(f) or "").strip()` |
| False submit with stale empty fields | Orchestrator's fallback branch triggered submit using stale `collected` | Added pre-submit guard verifying all fields are non-empty |
| Unicode crash on Windows terminal | `✔` / `✗` chars not in cp1252 encoding | Replaced with `[OK]`/`[FAIL]`; forced UTF-8 stdout in test_agent.py |

---

## Terminal Logging
When a ticket is submitted via the UI, the Streamlit terminal prints:
```
[ORCHESTRATOR] All fields ready — triggering submit
  name             : ...
  phone            : ...
  ...

[DATABASE AGENT] Received data for validation:
  name                : '...'
  ...
[DATABASE AGENT] Validation result: is_valid=True/False
  Sanitized: {...}
```

---

## Test Suite (`test_agent.py`)
5 end-to-end scenarios testing the full pipeline (Extraction → Database → Query → Cleanup):
1. Internet outage — all fields in one message
2. Billing issue — fields spread across turns
3. Delivery delay — user corrects email mid-conversation
4. App crash — high priority
5. Rude customer service — medium priority

Plus 3 Query Agent tests: list pending, fetch by ID, update status + Supabase verify.
All test tickets are deleted from Supabase at the end.

**Expected result**: `8/8 — ALL PASSED`
