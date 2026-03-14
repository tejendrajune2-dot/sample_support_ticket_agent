"""
Orchestrator — State Machine
Routes each user message to the appropriate agent based on the current flow_state.
app.py calls only handle_message(); agents are stateless and receive everything they need.

States:
  idle        → greeted, waiting to see if user has a complaint
  collecting  → gathering complaint fields one by one
  submitting  → DB write in progress (transient, managed by app.py spinner)
  done        → ticket submitted; Query Agent handles all further messages
"""

import random

import agents.extraction_agent as extraction_agent
import agents.database_agent as database_agent
import agents.query_agent as query_agent

# Ordered list of fields the bot needs to collect
REQUIRED_FIELDS = ["name", "phone", "email", "location", "issue_description", "priority"]

FIELD_QUESTIONS = {
    "name": "What is your full name?",
    "phone": "What is your phone number?",
    "email": "What is your email address?",
    "location": "What is your location or city?",
    "issue_description": "Please describe your issue in detail.",
    "priority": "How urgent is this issue? Please say Low, Medium, or High.",
}

RESTART_KEYWORDS = {"restart", "start over", "reset", "new complaint", "begin again"}
COMPLAINT_KEYWORDS = {
    "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "complaint",
    "issue", "problem", "help", "support", "trouble", "error", "broken",
    "not working", "i have", "i need", "please", "want to",
}
GREETING_KEYWORDS = {
    "hi", "hello", "hey", "howdy", "good morning", "good afternoon",
    "good evening", "how are you", "what's up", "greetings",
}
GREETING_RESPONSES = [
    "Hello! 😊 I'm doing great, thanks for asking! I'm here to help you with any support needs.\n\nDo you have a complaint you'd like to raise today?",
    "Hi there! 👋 I'm here and ready to help. Do you have an issue or complaint you'd like to file?",
    "Hey! Good to hear from you. If you have any complaints or need support, I'm here for you. Would you like to raise a complaint?",
]


def _is_greeting(text: str) -> bool:
    t = text.lower().strip()
    return any(kw in t for kw in GREETING_KEYWORDS)


def _wants_complaint(text: str) -> bool:
    t = text.lower().strip()
    return any(kw in t for kw in COMPLAINT_KEYWORDS)


def _wants_restart(text: str) -> bool:
    t = text.lower().strip()
    return any(kw in t for kw in RESTART_KEYWORDS)


def _next_missing_field(collected: dict) -> str | None:
    """Return the first field that hasn't been collected yet."""
    for field in REQUIRED_FIELDS:
        if not collected.get(field):
            return field
    return None


def handle_message(user_input: str, session: dict, supabase) -> dict:
    """
    Process one user message and return a response.

    Args:
        user_input:  The user's raw text.
        session:     st.session_state (or any dict with the required keys).
        supabase:    Supabase client.

    Returns:
        {
            "response": str,           # Bot reply to show in UI
            "new_state": str,          # Updated flow_state
            "collected_fields": dict,  # Updated collected fields
            "ticket_id": str | None,   # Set when ticket is created
            "trigger_submit": bool,    # True = app.py should show spinner and call submit()
        }
    """
    flow_state: str = session.get("flow_state", "idle")
    collected: dict = session.get("collected_fields", {f: None for f in REQUIRED_FIELDS})
    chat_history: list = session.get("chat_history", [])
    ticket_id: str | None = session.get("current_ticket_id")

    result = {
        "response": "",
        "new_state": flow_state,
        "collected_fields": collected,
        "ticket_id": ticket_id,
        "trigger_submit": False,
    }

    # ── DONE: Query Agent handles everything ──────────────────────────────────
    if flow_state == "done":
        agent_result = query_agent.handle(user_input, supabase, chat_history)
        result["response"] = agent_result.get("response_text", "Something went wrong.")
        return result

    # ── COLLECTING: Check for restart intent first ────────────────────────────
    if flow_state == "collecting" and _wants_restart(user_input):
        reset_collected = {f: None for f in REQUIRED_FIELDS}
        result.update({
            "new_state": "collecting",
            "collected_fields": reset_collected,
            "response": "No problem! Let's start fresh.\n\n" + FIELD_QUESTIONS["name"],
        })
        return result

    # ── IDLE: Handle greetings and complaint intent ───────────────────────────
    if flow_state == "idle":
        if _is_greeting(user_input) and not _wants_complaint(user_input):
            result["response"] = random.choice(GREETING_RESPONSES)
        elif _wants_complaint(user_input):
            # Run extraction immediately — the trigger message may already contain fields
            # (e.g. "yes, my name is John, email john@x.com")
            reset_collected: dict[str, str | None] = {f: None for f in REQUIRED_FIELDS}
            initial = extraction_agent.extract(chat_history)
            if initial.get("data"):
                for field in REQUIRED_FIELDS:
                    value = str(initial["data"].get(field) or "").strip()
                    if value:
                        reset_collected[field] = value
            next_field = _next_missing_field(reset_collected) or "name"
            result.update({
                "new_state": "collecting",
                "collected_fields": reset_collected,
                "response": (
                    "I'm sorry to hear that. I'll help you file a complaint.\n\n"
                    "Let's collect a few details. " + FIELD_QUESTIONS[next_field]
                ),
            })
        else:
            # Not a greeting or complaint — try the Query Agent in case the user
            # is asking about or updating an existing ticket
            agent_result = query_agent.handle(user_input, supabase, chat_history)
            result["response"] = agent_result.get("response_text", "Something went wrong.")
        return result

    # ── COLLECTING: Run Extraction Agent on full conversation history ─────────
    if flow_state == "collecting":
        extraction = extraction_agent.extract(chat_history)

        if extraction.get("success"):
            data = extraction["data"]
            # Guard: verify every field is truly non-empty before submitting
            truly_missing = [f for f in REQUIRED_FIELDS if not str(data.get(f) or "").strip()]
            if not truly_missing:
                print("\n[ORCHESTRATOR] All fields ready — triggering submit")
                print(f"  name             : {data.get('name')}")
                print(f"  phone            : {data.get('phone')}")
                print(f"  email            : {data.get('email')}")
                print(f"  location         : {data.get('location')}")
                print(f"  issue_description: {data.get('issue_description')}")
                print(f"  priority         : {data.get('priority')}\n")
                result.update({
                    "new_state": "submitting",
                    "collected_fields": data,
                    "trigger_submit": True,
                    "response": (
                        "Thank you! I have all the details I need. Let me file your ticket now..."
                    ),
                })
            else:
                # Extraction falsely reported success — merge what we have and ask for next field
                for field in REQUIRED_FIELDS:
                    value = str(data.get(field) or "").strip()
                    if value:
                        collected[field] = value
                next_field = _next_missing_field(collected)
                result.update({
                    "new_state": "collecting",
                    "collected_fields": collected,
                    "response": FIELD_QUESTIONS[next_field] if next_field else FIELD_QUESTIONS["name"],
                })
        else:
            # Update what we have so far from what the agent could extract
            missing = extraction.get("missing_fields", [])
            raw = extraction.get("data")
            partial: dict = raw if isinstance(raw, dict) else {}
            for field in REQUIRED_FIELDS:
                value = str(partial.get(field) or "").strip()
                if field not in missing and value:
                    collected[field] = value

            next_field = _next_missing_field(collected)
            if next_field:
                result.update({
                    "new_state": "collecting",
                    "collected_fields": collected,
                    "response": FIELD_QUESTIONS[next_field],
                })
            else:
                # All fields now in collected — proceed to submit
                result.update({
                    "new_state": "submitting",
                    "collected_fields": collected,
                    "trigger_submit": True,
                    "response": "Thank you! Filing your ticket now...",
                })

        return result

    # Fallback
    result["response"] = "I'm not sure how to help with that. Please type 'restart' to start over."
    return result


def submit_ticket(collected_fields: dict, supabase) -> dict:
    """
    Call the Database Agent to store the ticket.
    Called by app.py when trigger_submit is True (inside a spinner).

    Returns:
        {"success": True, "ticket_id": "...", "response": "..."} or
        {"success": False, "response": "..."}
    """
    db_result = database_agent.store(collected_fields, supabase)

    if db_result.get("success"):
        tid = db_result["ticket_id"]
        return {
            "success": True,
            "ticket_id": tid,
            "response": (
                f"Your complaint has been successfully registered!\n\n"
                f"**Ticket ID: {tid}**\n\n"
                f"Status: *In Progress*\n\n"
                f"Our team will reach out to you shortly. You can use this ticket ID to:\n"
                f"- Check your ticket details (e.g. *'show ticket {tid}'*)\n"
                f"- Update its status (e.g. *'mark ticket {tid} as completed'*)\n"
                f"- View all pending tickets (e.g. *'show pending tickets'*)"
            ),
        }
    else:
        return {
            "success": False,
            "response": (
                f"I'm sorry, something went wrong while saving your ticket. "
                f"{db_result.get('message', '')} Please try again."
            ),
        }
