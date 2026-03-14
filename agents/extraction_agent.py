"""
Agent 1 — Extraction Agent
Parses the full conversation history and extracts structured complaint data.
Uses OpenAI Structured Outputs (Pydantic schema) to guarantee valid JSON.
"""

import os
import time
from typing import Literal, Optional

from dotenv import load_dotenv
from openai import OpenAI, RateLimitError
from pydantic import BaseModel

load_dotenv()

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _client


SYSTEM_PROMPT = """You are a data extraction specialist for a customer support system.

Given a conversation between a support chatbot and a user, extract whatever complaint fields you can find:
- name: The user's full name
- phone: The user's phone number (digits only, e.g. "9876543210")
- email: The user's email address
- location: The user's city, area, or address
- issue_description: A detailed description of the complaint or issue
- priority: The urgency level — must be exactly "Low", "Medium", or "High"

Rules:
1. Extract ONLY what was explicitly stated. Do NOT infer or guess.
2. Set success=True ONLY when ALL 6 fields are present and valid. Otherwise success=False.
3. Always populate the "data" object with whatever fields you DID find. Use null for fields not yet provided.
4. List all missing field names in missing_fields when success=False.
5. For priority: map "urgent"/"critical"/"asap"/"high" → "High"; "normal"/"medium" → "Medium"; "low"/"not urgent" → "Low". If not mentioned at all, leave it null.
6. For phone: extract digits only. Strip spaces, dashes, country codes (e.g. "+91").
7. For email: it must contain "@" and a domain (e.g. "name@example.com").
8. If a field appears multiple times in the conversation, use the most recent value.
9. issue_description should be as detailed as possible from what the user stated.
"""


class PartialComplaintData(BaseModel):
    """All fields optional — populated with whatever the agent could find so far."""
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    location: Optional[str] = None
    issue_description: Optional[str] = None
    priority: Optional[Literal["Low", "Medium", "High"]] = None


class ExtractionResult(BaseModel):
    success: bool
    data: PartialComplaintData  # Always returned — contains all found fields (nulls for missing)
    missing_fields: list[str]   # Names of fields not yet provided
    message: Optional[str] = None


def extract(chat_history: list[dict]) -> dict:
    """
    Parse the conversation history and extract complaint fields.

    Args:
        chat_history: List of {"role": "user"|"assistant", "content": "..."} dicts.

    Returns:
        {"success": True, "data": {...}} — all 6 fields found, or
        {"success": False, "data": {...}, "missing_fields": [...]} — partial data with what was found
    """
    client = _get_client()

    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + chat_history

    for attempt in range(3):
        try:
            response = client.beta.chat.completions.parse(
                model="gpt-4o-2024-08-06",
                messages=messages,
                response_format=ExtractionResult,
                temperature=0,
            )
            result: ExtractionResult = response.choices[0].message.parsed

            data_dict = result.data.model_dump()

            # Sanity check: if the model contradicts itself (success=False but
            # missing_fields is empty and all fields are non-null), trust the data.
            all_fields = ["name", "phone", "email", "location", "issue_description", "priority"]
            actual_missing = [f for f in all_fields if not str(data_dict.get(f) or "").strip()]
            resolved_success = len(actual_missing) == 0
            resolved_missing = actual_missing if actual_missing else result.missing_fields

            return {
                "success": resolved_success,
                "data": data_dict,
                "missing_fields": resolved_missing,
                "message": result.message or "",
            }

        except RateLimitError:
            if attempt < 2:
                time.sleep(2 ** attempt)
            else:
                return {
                    "success": False,
                    "data": {},
                    "missing_fields": [],
                    "message": "OpenAI rate limit reached. Please try again in a moment.",
                }
        except Exception as e:
            return {
                "success": False,
                "data": {},
                "missing_fields": [],
                "message": f"Extraction error: {str(e)}",
            }
