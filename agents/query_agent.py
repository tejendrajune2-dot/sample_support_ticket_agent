"""
Agent 3 — Query/Update Agent
Interprets free-text user queries about tickets and executes Supabase operations.
Uses OpenAI function calling with a two-step loop:
  1. Model picks a tool and extracts parameters
  2. Orchestrator executes the Supabase operation
  3. Model formats the result as a natural-language response
"""

import json
import os
import time

from dotenv import load_dotenv
from openai import OpenAI, RateLimitError

load_dotenv()

_client: OpenAI | None = None

QUERY_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_tickets",
            "description": (
                "Retrieve support tickets from the database, optionally filtered by status. "
                "Use status='In Progress' for pending/open tickets, "
                "'Completed' for done/resolved/closed tickets, "
                "'On Hold' for paused/held tickets. "
                "Omit status to retrieve ALL tickets."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["In Progress", "Completed", "On Hold"],
                        "description": "Filter by ticket status. Omit for all tickets.",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_ticket",
            "description": "Retrieve the full details of a single ticket by its 5-digit ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticket_id": {
                        "type": "string",
                        "description": "The 5-digit ticket ID (e.g. '47291').",
                    }
                },
                "required": ["ticket_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_ticket_status",
            "description": (
                "Update the status of a ticket identified by its 5-digit ID. "
                "Use 'Completed' for done/resolved/closed/finished, "
                "'On Hold' for paused/held/waiting, "
                "'In Progress' to reopen a ticket."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ticket_id": {
                        "type": "string",
                        "description": "The 5-digit ticket ID.",
                    },
                    "new_status": {
                        "type": "string",
                        "enum": ["In Progress", "Completed", "On Hold"],
                        "description": "The new status to set.",
                    },
                },
                "required": ["ticket_id", "new_status"],
            },
        },
    },
]

SYSTEM_PROMPT = """You are a ticket management assistant for a customer support system.

Users will ask you to list, view, or update support tickets. Use the provided tools to fulfil their requests.

Status synonym mapping:
- "pending" / "open" / "active" / "in progress" → status: "In Progress"
- "done" / "resolved" / "closed" / "completed" / "finished" → status: "Completed"
- "on hold" / "held" / "paused" / "waiting" → status: "On Hold"

After calling a tool, format the result clearly and concisely for the user.
- For ticket lists: show ticket ID, name, priority, status, and a short issue summary.
- For single tickets: show all fields in a readable format.
- For status updates: confirm the change with the ticket ID and new status.
- If a query returns no data (empty list), tell the user clearly (e.g. "No In Progress tickets found.").
- If a ticket ID was not found after an update, say "Ticket [ID] was not found."

Only answer questions about support tickets. If the user asks about something unrelated,
politely say: "I can only help with support ticket queries and updates."
"""


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _client


def _execute_tool(tool_name: str, tool_args: dict, supabase) -> str:
    """Execute the Supabase operation requested by the model."""
    try:
        if tool_name == "list_tickets":
            status = tool_args.get("status")
            query = supabase.table("tickets").select("*").order("created_at", desc=True)
            if status:
                query = query.eq("status", status)
            result = query.execute()
            return json.dumps(result.data)

        elif tool_name == "get_ticket":
            ticket_id = tool_args["ticket_id"]
            result = (
                supabase.table("tickets")
                .select("*")
                .eq("ticket_id", ticket_id)
                .execute()
            )
            return json.dumps(result.data)

        elif tool_name == "update_ticket_status":
            ticket_id = tool_args["ticket_id"]
            new_status = tool_args["new_status"]
            result = (
                supabase.table("tickets")
                .update({"status": new_status})
                .eq("ticket_id", ticket_id)
                .execute()
            )
            if result.data:
                return json.dumps({"updated": True, "ticket_id": ticket_id, "new_status": new_status})
            else:
                return json.dumps({"updated": False, "ticket_id": ticket_id})

        else:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})

    except Exception as e:
        return json.dumps({"error": str(e)})


def handle(user_message: str, supabase, chat_history: list[dict] | None = None) -> dict:
    """
    Interpret a user query about tickets and execute the appropriate Supabase operation.

    Args:
        user_message: The user's natural-language query.
        supabase: Supabase client.
        chat_history: Recent conversation context (optional).

    Returns:
        {"success": True, "action": "...", "response_text": "..."} or
        {"success": False, "response_text": "..."}
    """
    client = _get_client()

    # Build message list: system + recent history + current user message
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if chat_history:
        # Include last 10 messages for context without blowing the context window
        messages += chat_history[-10:]
    messages.append({"role": "user", "content": user_message})

    action_taken = "none"

    for attempt in range(3):
        try:
            # Step 1: Ask model to pick a tool
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                tools=QUERY_TOOLS,
                temperature=0.2,
            )

            choice = response.choices[0]

            # No tool call → model responded directly (e.g., unrelated question)
            if choice.finish_reason != "tool_calls" or not choice.message.tool_calls:
                return {
                    "success": True,
                    "action": "direct_response",
                    "response_text": choice.message.content or "I'm not sure how to help with that.",
                }

            tool_call = choice.message.tool_calls[0]
            tool_name = tool_call.function.name
            tool_args = json.loads(tool_call.function.arguments)
            action_taken = tool_name

            # Step 2: Execute the Supabase operation
            tool_result = _execute_tool(tool_name, tool_args, supabase)

            # Step 3: Send result back to model for natural-language formatting
            messages.append(choice.message)  # assistant's tool call message
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": tool_result,
            })

            final_response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                temperature=0.3,
            )

            return {
                "success": True,
                "action": action_taken,
                "response_text": final_response.choices[0].message.content,
            }

        except RateLimitError:
            if attempt < 2:
                time.sleep(2 ** attempt)
            else:
                return {
                    "success": False,
                    "response_text": "OpenAI rate limit reached. Please try again in a moment.",
                }
        except Exception as e:
            return {
                "success": False,
                "response_text": f"An error occurred: {str(e)}",
            }

    return {"success": False, "response_text": "Failed after multiple retries."}
