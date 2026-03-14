# Support Agent — Multi-Agent Support Ticket Chatbot

A conversational customer support chatbot that collects complaint details, files support tickets, and lets users query or update tickets — all through natural language.

Built with **Streamlit**, **OpenAI GPT-4o**, and **Supabase**.

---

## Features

- Conversational complaint filing — collects name, phone, email, location, issue, and priority
- Handles multi-field messages, mid-conversation corrections, and partial input across turns
- Generates unique 5-digit ticket IDs stored in Supabase
- Natural language ticket queries: list pending tickets, fetch by ID, update status
- Query tickets at any time — before or after filing a complaint

---

## Architecture

Three specialized agents orchestrated by a central state machine:

```
User → Streamlit UI (app.py)
          → Orchestrator (state machine)
               → Extraction Agent   — parse conversation → structured data
               → Database Agent     — validate + store ticket to Supabase
               → Query/Update Agent — fetch and update tickets
```

### State Machine
```
idle → collecting → submitting → done
```

| State | Behaviour |
|-------|-----------|
| `idle` | Greet user, detect complaint intent or ticket queries |
| `collecting` | Run Extraction Agent each turn, ask for next missing field |
| `submitting` | Write ticket to Supabase |
| `done` | All messages handled by Query Agent |

---

## Tech Stack

| Component | Technology |
|-----------|------------|
| UI | Streamlit |
| Extraction Agent | OpenAI `gpt-4o-2024-08-06` — Structured Outputs (Pydantic) |
| Database Agent | Python validation + Supabase insert |
| Query Agent | OpenAI `gpt-4o-mini` — Function Calling |
| Database | Supabase (PostgreSQL) |

---

## Project Structure

```
Support Agent/
├── app.py                    # Streamlit UI + session state
├── orchestrator.py           # State machine, wires agents together
├── agents/
│   ├── extraction_agent.py   # Agent 1: conversation → structured data
│   ├── database_agent.py     # Agent 2: validate + store ticket
│   └── query_agent.py        # Agent 3: fetch/update tickets
├── utils/
│   └── supabase_client.py    # Supabase singleton client
├── test_agent.py             # End-to-end test suite (5 scenarios)
├── requirements.txt
├── .env.example              # Template for environment variables
└── CLAUDE.md                 # Full project documentation
```

---

## Setup

### 1. Clone the repository

```bash
git clone https://github.com/tejendrajune2-dot/sample_support_ticket_agent.git
cd sample_support_ticket_agent
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Set up Supabase

Create a free project at [supabase.com](https://supabase.com), then run this SQL in the **SQL Editor**:

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

### 4. Configure environment variables

Copy `.env.example` to `.env` and fill in your keys:

```
OPENAI_API_KEY=sk-...
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=eyJ...
```

> Get your Supabase URL and key from **Settings → API → Publishable and secret API keys** (not the Legacy tab).

### 5. Run the app

```bash
streamlit run app.py
```

---

## Usage

### Filing a complaint
Start by saying `yes` or describing your issue. The bot collects the required fields and files a ticket:

```
You: yes, I have a complaint
Bot: I'll help you file a complaint. What is your full name?
You: John Smith, phone 9876543210, email john@example.com
Bot: What is your location or city?
...
Bot: Your complaint has been registered! Ticket ID: 47291
```

### Querying tickets
Once a ticket is filed (or at any time), you can ask:

```
show pending tickets
show ticket 47291
mark ticket 47291 as completed
put ticket 47291 on hold
reopen ticket 47291
show all tickets
```

---

## Running Tests

The test suite runs 5 end-to-end scenarios through the full pipeline and cleans up after itself:

```bash
python test_agent.py
```

Expected output: **8/8 — ALL PASSED**

Test scenarios:
1. Internet outage — all fields in one message
2. Billing issue — fields spread across multiple turns
3. Delivery delay — user corrects email mid-conversation
4. App crash — high priority technical issue
5. Rude customer service — medium priority

---

## Ticket Statuses

| Status | Description |
|--------|-------------|
| 🟡 In Progress | Default on creation |
| ✅ Completed | Issue resolved |
| ⏸️ On Hold | Waiting / paused |
