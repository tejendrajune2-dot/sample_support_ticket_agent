"""
Support Agent — Streamlit UI
Entry point. Run with: streamlit run app.py
"""

import streamlit as st
from dotenv import load_dotenv

import orchestrator
from utils.supabase_client import get_supabase

load_dotenv()

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Support Agent",
    page_icon="🎧",
    layout="centered",
)

# ── CSS: clean chat bubbles ───────────────────────────────────────────────────
st.markdown(
    """
    <style>
    .stChatMessage { border-radius: 12px; margin-bottom: 4px; }
    .stSpinner > div { font-size: 0.9rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ── Supabase connection ───────────────────────────────────────────────────────
try:
    supabase = get_supabase()
except ValueError as e:
    st.error(
        f"**Configuration error:** {e}\n\n"
        "Please create a `.env` file based on `.env.example` and restart the app."
    )
    st.stop()

# ── Session state initialisation ─────────────────────────────────────────────
REQUIRED_FIELDS = ["name", "phone", "email", "location", "issue_description", "priority"]

st.session_state.setdefault("chat_history", [])
st.session_state.setdefault("flow_state", "idle")
st.session_state.setdefault("collected_fields", {f: None for f in REQUIRED_FIELDS})
st.session_state.setdefault("current_ticket_id", None)
st.session_state.setdefault("pending_field", None)

# ── Header ────────────────────────────────────────────────────────────────────
st.title("🎧 Support Agent")
st.caption(
    "Chat with our support bot to file a complaint or manage existing tickets. "
    "Note: refreshing the page resets your session (your tickets remain saved)."
)

# ── Initial greeting ──────────────────────────────────────────────────────────
if not st.session_state.chat_history:
    greeting = (
        "Hello! Welcome to our Support Centre. 👋\n\n"
        "I'm here to help you with any complaints or issues you may have.\n\n"
        "Do you have a complaint you'd like to raise today?"
    )
    st.session_state.chat_history.append({"role": "assistant", "content": greeting})

# ── Render chat history ───────────────────────────────────────────────────────
for msg in st.session_state.chat_history:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# ── Chat input ────────────────────────────────────────────────────────────────
user_input = st.chat_input("Type your message here...")

if user_input and user_input.strip():
    # Show user message immediately
    with st.chat_message("user"):
        st.markdown(user_input)

    # Append to history BEFORE calling agents (so extraction sees the full conversation)
    st.session_state.chat_history.append({"role": "user", "content": user_input})

    # ── Route to orchestrator ─────────────────────────────────────────────────
    with st.spinner("Thinking..."):
        result = orchestrator.handle_message(
            user_input=user_input,
            session=st.session_state,
            supabase=supabase,
        )

    # Update session state from orchestrator result
    st.session_state.flow_state = result["new_state"]
    st.session_state.collected_fields = result["collected_fields"]
    st.session_state.current_ticket_id = result["ticket_id"]

    bot_response = result["response"]

    # ── Trigger DB submission if orchestrator says all fields are ready ───────
    if result.get("trigger_submit"):
        with st.spinner("Filing your ticket..."):
            submit_result = orchestrator.submit_ticket(
                collected_fields=result["collected_fields"],
                supabase=supabase,
            )

        if submit_result["success"]:
            st.session_state.flow_state = "done"
            st.session_state.current_ticket_id = submit_result["ticket_id"]
            bot_response = submit_result["response"]
        else:
            # Stay in collecting state so user can retry
            st.session_state.flow_state = "collecting"
            bot_response = submit_result["response"]

    # Show bot response
    with st.chat_message("assistant"):
        st.markdown(bot_response)

    # Append bot response to history
    st.session_state.chat_history.append({"role": "assistant", "content": bot_response})

# ── Sidebar: quick-action hints ───────────────────────────────────────────────
with st.sidebar:
    st.header("Quick Commands")
    st.markdown(
        """
        Once a ticket is filed, try:

        - `show pending tickets`
        - `show all tickets`
        - `show ticket 47291`
        - `mark ticket 47291 as completed`
        - `put ticket 47291 on hold`
        - `reopen ticket 47291`

        ---
        **Ticket Statuses**
        - 🟡 In Progress
        - ✅ Completed
        - ⏸️ On Hold
        """
    )

    if st.session_state.current_ticket_id:
        st.success(f"Current ticket: **{st.session_state.current_ticket_id}**")

    st.divider()
    if st.button("🔄 Start New Session", use_container_width=True):
        for key in ["chat_history", "flow_state", "collected_fields", "current_ticket_id", "pending_field"]:
            del st.session_state[key]
        st.rerun()
