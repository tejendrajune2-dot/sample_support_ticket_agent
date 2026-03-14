"""
Agent 2 — Database Agent
Validates and sanitizes extracted complaint data using OpenAI function calling,
generates a unique 5-digit ticket ID, and inserts the record into Supabase.
"""

import json
import os
import random
import time

from dotenv import load_dotenv
from openai import OpenAI, RateLimitError

load_dotenv()

_client: OpenAI | None = None

VALIDATION_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "validate_complaint_data",
            "description": "Validate and sanitize complaint data before storing to the database.",
            "parameters": {
                "type": "object",
                "properties": {
                    "is_valid": {
                        "type": "boolean",
                        "description": "True if all fields pass validation.",
                    },
                    "issues": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of validation problems found (empty if valid).",
                    },
                    "sanitized_data": {
                        "type": "object",
                        "description": "Cleaned version of all fields.",
                        "properties": {
                            "name": {"type": "string"},
                            "phone": {"type": "string"},
                            "email": {"type": "string"},
                            "location": {"type": "string"},
                            "issue_description": {"type": "string"},
                            "priority": {
                                "type": "string",
                                "enum": ["Low", "Medium", "High"],
                            },
                        },
                        "required": [
                            "name", "phone", "email", "location",
                            "issue_description", "priority",
                        ],
                    },
                },
                "required": ["is_valid", "issues", "sanitized_data"],
            },
        },
    }
]

VALIDATION_SYSTEM_PROMPT = """You are a data validation agent for a support ticketing system.

Given a complaint record, validate and sanitize each field:
- name: Trim whitespace. Must not be empty.
- phone: Keep digits only. Must be 7–15 digits long.
- email: Lowercase, trim. Must contain "@" and a "." after it.
- location: Trim whitespace. Must not be empty.
- issue_description: Trim. Must be at least 10 characters.
- priority: Must be exactly "Low", "Medium", or "High".

Always call validate_complaint_data with your findings.
"""


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _client


def _generate_unique_ticket_id(supabase) -> str:
    """Generate a 5-digit string ID not already in the database."""
    for _ in range(10):
        candidate = str(random.randint(10000, 99999))
        result = (
            supabase.table("tickets")
            .select("ticket_id")
            .eq("ticket_id", candidate)
            .execute()
        )
        if not result.data:
            return candidate
    raise RuntimeError("Could not generate a unique ticket ID after 10 attempts.")


def _validate_with_ai(data: dict) -> dict:
    """Use OpenAI function calling to validate and sanitize the data."""
    client = _get_client()

    messages = [
        {"role": "system", "content": VALIDATION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Please validate this complaint record:\n{json.dumps(data, indent=2)}",
        },
    ]

    for attempt in range(3):
        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                tools=VALIDATION_TOOLS,
                tool_choice={"type": "function", "function": {"name": "validate_complaint_data"}},
                temperature=0,
            )

            tool_call = response.choices[0].message.tool_calls[0]
            result = json.loads(tool_call.function.arguments)
            return result

        except RateLimitError:
            if attempt < 2:
                time.sleep(2 ** attempt)
            else:
                # Fall back to basic Python validation if AI is unavailable
                return _fallback_validate(data)
        except Exception:
            return _fallback_validate(data)

    return _fallback_validate(data)


def _fallback_validate(data: dict) -> dict:
    """Basic Python-side validation fallback (no AI needed)."""
    issues = []
    sanitized = {
        "name": str(data.get("name", "")).strip(),
        "phone": "".join(filter(str.isdigit, str(data.get("phone", "")))),
        "email": str(data.get("email", "")).strip().lower(),
        "location": str(data.get("location", "")).strip(),
        "issue_description": str(data.get("issue_description", "")).strip(),
        "priority": str(data.get("priority", "Medium")).strip(),
    }

    if not sanitized["name"]:
        issues.append("Name is empty.")
    if len(sanitized["phone"]) < 7:
        issues.append("Phone number is too short.")
    if "@" not in sanitized["email"] or "." not in sanitized["email"].split("@")[-1]:
        issues.append("Email address is invalid.")
    if not sanitized["location"]:
        issues.append("Location is empty.")
    if len(sanitized["issue_description"]) < 10:
        issues.append("Issue description is too short.")
    if sanitized["priority"] not in ("Low", "Medium", "High"):
        sanitized["priority"] = "Medium"

    return {"is_valid": len(issues) == 0, "issues": issues, "sanitized_data": sanitized}


def store(complaint_data: dict, supabase) -> dict:
    """
    Validate, sanitize, and store a complaint ticket in Supabase.

    Args:
        complaint_data: Dict with name, phone, email, location, issue_description, priority.
        supabase: Supabase client from utils/supabase_client.py.

    Returns:
        {"success": True, "ticket_id": "47291"} or
        {"success": False, "message": "..."}
    """
    # Step 1: Validate and sanitize via AI
    print("\n[DATABASE AGENT] Received data for validation:")
    for k, v in complaint_data.items():
        print(f"  {k:<20}: {repr(v)}")

    validation = _fallback_validate(complaint_data)

    print(f"[DATABASE AGENT] Validation result: is_valid={validation.get('is_valid')}")
    if validation.get("issues"):
        print(f"  Issues: {validation.get('issues')}")
    print(f"  Sanitized: {validation.get('sanitized_data')}\n")

    if not validation.get("is_valid"):
        issues = validation.get("issues", [])
        return {
            "success": False,
            "message": "Validation failed: " + "; ".join(issues),
        }

    sanitized = validation["sanitized_data"]

    # Step 2: Generate unique ticket ID
    try:
        ticket_id = _generate_unique_ticket_id(supabase)
    except RuntimeError as e:
        return {"success": False, "message": str(e)}

    # Step 3: Insert into Supabase
    record = {
        "ticket_id": ticket_id,
        "name": sanitized["name"],
        "phone": sanitized["phone"],
        "email": sanitized["email"],
        "location": sanitized["location"],
        "issue_description": sanitized["issue_description"],
        "priority": sanitized["priority"],
        # status and created_at use DB defaults
    }

    try:
        result = supabase.table("tickets").insert(record).execute()
        if result.data:
            return {"success": True, "ticket_id": ticket_id}
        else:
            return {"success": False, "message": "Insert returned no data. Please try again."}
    except Exception as e:
        error_msg = str(e)
        # Handle unique constraint violation (rare collision slip-through)
        if "duplicate" in error_msg.lower() or "unique" in error_msg.lower():
            try:
                ticket_id = _generate_unique_ticket_id(supabase)
                record["ticket_id"] = ticket_id
                result = supabase.table("tickets").insert(record).execute()
                if result.data:
                    return {"success": True, "ticket_id": ticket_id}
            except Exception as retry_e:
                return {"success": False, "message": f"Database error on retry: {str(retry_e)}"}
        return {"success": False, "message": f"Database error: {error_msg}"}
