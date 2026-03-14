"""
Testing Agent
Runs 5 end-to-end test scenarios through the full pipeline:
  Extraction Agent → Database Agent → Query/Update Agent

Usage:
    python test_agent.py

Results are printed to the terminal. All test tickets are cleaned up from
Supabase at the end so they don't pollute your real data.
"""

import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from utils.supabase_client import get_supabase
import agents.extraction_agent as extraction_agent
import agents.database_agent as database_agent
import agents.query_agent as query_agent

# ── ANSI colour helpers ───────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def ok(msg):  print(f"  {GREEN}[OK] {msg}{RESET}")
def fail(msg):print(f"  {RED}[FAIL] {msg}{RESET}")
def info(msg):print(f"  {YELLOW}-> {msg}{RESET}")

# ── 5 Test Scenarios (realistic multi-turn conversations) ─────────────────────
TEST_SCENARIOS = [
    {
        "id": 1,
        "name": "Internet outage — all details in one message",
        "conversation": [
            {"role": "assistant", "content": "Hello! Do you have a complaint you'd like to raise today?"},
            {"role": "user",      "content": "Yes I have a complaint. My name is Rahul Sharma, phone 9876543210, email rahul.sharma@gmail.com, I live in Mumbai. My internet connection has been completely down for 3 days affecting my work from home. This is urgent — High priority."},
        ],
        "expected_fields": ["name", "phone", "email", "location", "issue_description", "priority"],
        "expected_priority": "High",
    },
    {
        "id": 2,
        "name": "Billing issue — details spread across turns",
        "conversation": [
            {"role": "assistant", "content": "Hello! Do you have a complaint you'd like to raise today?"},
            {"role": "user",      "content": "Yes I have a billing problem."},
            {"role": "assistant", "content": "I'm sorry to hear that. What is your full name?"},
            {"role": "user",      "content": "My name is Priya Menon."},
            {"role": "assistant", "content": "What is your phone number?"},
            {"role": "user",      "content": "My phone is 8800112233."},
            {"role": "assistant", "content": "What is your email address?"},
            {"role": "user",      "content": "priya.menon@yahoo.com"},
            {"role": "assistant", "content": "What is your location?"},
            {"role": "user",      "content": "I am in Bangalore, Koramangala area."},
            {"role": "assistant", "content": "Please describe your issue in detail."},
            {"role": "user",      "content": "I was charged twice for my subscription on 10th March. The amount of Rs 999 was debited twice from my account. I need a refund for the duplicate charge immediately."},
            {"role": "assistant", "content": "How urgent is this? Low, Medium, or High?"},
            {"role": "user",      "content": "Medium priority."},
        ],
        "expected_fields": ["name", "phone", "email", "location", "issue_description", "priority"],
        "expected_priority": "Medium",
    },
    {
        "id": 3,
        "name": "Delivery delay — user corrects email mid-conversation",
        "conversation": [
            {"role": "assistant", "content": "Hello! Do you have a complaint you'd like to raise today?"},
            {"role": "user",      "content": "Yes, my delivery is delayed. I'm Vikram Nair, 7701234567, vikram.nair@hotmail.com, Chennai."},
            {"role": "assistant", "content": "Please describe your issue."},
            {"role": "user",      "content": "Actually my email is vikramnair@outlook.com not hotmail. My order number ORD-445566 was supposed to arrive 5 days ago but there's no update from the courier. I've tried calling support but no one answers."},
            {"role": "assistant", "content": "How urgent is this?"},
            {"role": "user",      "content": "Low, it's okay if resolved this week."},
        ],
        "expected_fields": ["name", "phone", "email", "location", "issue_description", "priority"],
        "expected_priority": "Low",
    },
    {
        "id": 4,
        "name": "App crash — high priority technical issue",
        "conversation": [
            {"role": "assistant", "content": "Hello! Do you have a complaint you'd like to raise today?"},
            {"role": "user",      "content": "Yes, urgent complaint. Sneha Reddy here."},
            {"role": "assistant", "content": "What is your phone number?"},
            {"role": "user",      "content": "9988776655"},
            {"role": "assistant", "content": "What is your email address?"},
            {"role": "user",      "content": "sneha.reddy@gmail.com"},
            {"role": "assistant", "content": "What is your location?"},
            {"role": "user",      "content": "Hyderabad, Banjara Hills"},
            {"role": "assistant", "content": "Please describe your issue."},
            {"role": "user",      "content": "The mobile app crashes every time I try to open the payment screen. I've reinstalled it twice, cleared cache, tried on two different phones. I cannot make any payments. This is blocking my business operations completely."},
            {"role": "assistant", "content": "How urgent is this?"},
            {"role": "user",      "content": "Very high, critical issue!"},
        ],
        "expected_fields": ["name", "phone", "email", "location", "issue_description", "priority"],
        "expected_priority": "High",
    },
    {
        "id": 5,
        "name": "Rude customer service — medium priority",
        "conversation": [
            {"role": "assistant", "content": "Hello! Do you have a complaint you'd like to raise today?"},
            {"role": "user",      "content": "I want to complain about your customer service. I am Arjun Kapoor, arjun.kapoor@gmail.com, 9123456789, from Delhi, Dwarka."},
            {"role": "assistant", "content": "Please describe your issue in detail."},
            {"role": "user",      "content": "I called your helpline yesterday (13th March) regarding a product return. The agent named Ravi was extremely rude, cut the call twice and refused to process my return request saying 'it's not my problem'. I have the call recording. I want a formal apology and my return to be processed."},
            {"role": "assistant", "content": "How urgent is this?"},
            {"role": "user",      "content": "Medium."},
        ],
        "expected_fields": ["name", "phone", "email", "location", "issue_description", "priority"],
        "expected_priority": "Medium",
    },
]


# ── Test runner ───────────────────────────────────────────────────────────────

def run_tests():
    print(f"\n{BOLD}{CYAN}{'='*60}{RESET}")
    print(f"{BOLD}{CYAN}  Support Agent — End-to-End Test Suite{RESET}")
    print(f"{BOLD}{CYAN}{'='*60}{RESET}\n")

    # Connect to Supabase once
    try:
        supabase = get_supabase()
        ok("Supabase connection established")
    except Exception as e:
        fail(f"Supabase connection failed: {e}")
        sys.exit(1)

    created_ticket_ids = []
    results = []

    # ── Phase 1: Extraction + Database (all 5 scenarios) ─────────────────────
    print(f"\n{BOLD}Phase 1 — Extraction & Storage (5 scenarios){RESET}")
    print("─" * 50)

    for scenario in TEST_SCENARIOS:
        sid   = scenario["id"]
        sname = scenario["name"]
        passed = True
        ticket_id = None

        print(f"\n{BOLD}Test {sid}: {sname}{RESET}")

        # Step 1a: Extraction Agent
        try:
            extraction = extraction_agent.extract(scenario["conversation"])

            if extraction.get("success"):
                ok("Extraction Agent — all fields extracted")
                data = extraction["data"]

                # Verify priority
                if data.get("priority") == scenario["expected_priority"]:
                    ok(f"Priority correctly extracted as '{data['priority']}'")
                else:
                    fail(f"Priority mismatch: expected '{scenario['expected_priority']}', got '{data.get('priority')}'")
                    passed = False

                # Verify all expected fields present
                missing = [f for f in scenario["expected_fields"] if not data.get(f)]
                if missing:
                    fail(f"Missing fields after extraction: {missing}")
                    passed = False
                else:
                    ok(f"All fields present: name='{data['name']}', email='{data['email']}'")

            else:
                fail(f"Extraction Agent failed: {extraction.get('message')}")
                info(f"Missing fields: {extraction.get('missing_fields')}")
                passed = False
                data = None

        except Exception as e:
            fail(f"Extraction Agent raised exception: {e}")
            passed = False
            data = None

        # Step 1b: Database Agent (only if extraction passed)
        if passed and data:
            try:
                db_result = database_agent.store(data, supabase)

                if db_result.get("success"):
                    ticket_id = db_result["ticket_id"]
                    created_ticket_ids.append(ticket_id)
                    ok(f"Database Agent — ticket stored with ID: {ticket_id}")
                else:
                    fail(f"Database Agent failed: {db_result.get('message')}")
                    passed = False

            except Exception as e:
                fail(f"Database Agent raised exception: {e}")
                passed = False

        results.append({
            "id": sid,
            "name": sname,
            "passed": passed,
            "ticket_id": ticket_id,
        })

    # ── Phase 2: Query Agent tests ────────────────────────────────────────────
    print(f"\n{BOLD}Phase 2 — Query Agent Tests{RESET}")
    print("─" * 50)

    query_tests_passed = 0
    query_tests_total  = 3

    # Test Q1: Fetch all "In Progress" tickets
    print(f"\n{BOLD}Query Test 1: Fetch all In Progress tickets{RESET}")
    try:
        q1 = query_agent.handle("show me all pending tickets", supabase)
        if q1.get("success"):
            ok(f"Query Agent responded: {q1['response_text'][:120]}...")
            query_tests_passed += 1
        else:
            fail(f"Query Agent failed: {q1.get('response_text')}")
    except Exception as e:
        fail(f"Query Agent raised exception: {e}")

    # Test Q2: Fetch a specific ticket by ID
    if created_ticket_ids:
        target_id = created_ticket_ids[0]
        print(f"\n{BOLD}Query Test 2: Fetch ticket {target_id} by ID{RESET}")
        try:
            q2 = query_agent.handle(f"show me details of ticket {target_id}", supabase)
            if q2.get("success"):
                ok(f"Query Agent responded: {q2['response_text'][:120]}...")
                query_tests_passed += 1
            else:
                fail(f"Query Agent failed: {q2.get('response_text')}")
        except Exception as e:
            fail(f"Query Agent raised exception: {e}")

        # Test Q3: Update ticket status to "Completed"
        print(f"\n{BOLD}Query Test 3: Mark ticket {target_id} as Completed{RESET}")
        try:
            q3 = query_agent.handle(f"mark ticket {target_id} as completed", supabase)
            if q3.get("success"):
                # Verify the update in Supabase directly
                verify = supabase.table("tickets").select("status").eq("ticket_id", target_id).execute()
                if verify.data and verify.data[0]["status"] == "Completed":
                    ok(f"Status updated to 'Completed' in Supabase — confirmed")
                    query_tests_passed += 1
                else:
                    fail("Status update not reflected in Supabase")
            else:
                fail(f"Query Agent failed: {q3.get('response_text')}")
        except Exception as e:
            fail(f"Query Agent raised exception: {e}")
    else:
        fail("No tickets were created — skipping Query Tests 2 & 3")
        query_tests_total = 1

    # ── Phase 3: Cleanup ──────────────────────────────────────────────────────
    print(f"\n{BOLD}Phase 3 — Cleanup{RESET}")
    print("─" * 50)

    if created_ticket_ids:
        try:
            supabase.table("tickets").delete().in_("ticket_id", created_ticket_ids).execute()
            ok(f"Deleted {len(created_ticket_ids)} test ticket(s) from Supabase: {created_ticket_ids}")
        except Exception as e:
            info(f"Cleanup warning (non-fatal): {e}")
    else:
        info("No test tickets to clean up")

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{BOLD}{CYAN}{'='*60}{RESET}")
    print(f"{BOLD}  Test Summary{RESET}")
    print(f"{BOLD}{CYAN}{'='*60}{RESET}")

    extraction_passed = sum(1 for r in results if r["passed"])
    print(f"\n  Extraction + Storage : {extraction_passed}/{len(TEST_SCENARIOS)} passed")
    print(f"  Query Agent          : {query_tests_passed}/{query_tests_total} passed")

    print(f"\n  {'Scenario':<45} {'Result'}")
    print(f"  {'-'*45} {'------'}")
    for r in results:
        status = f"{GREEN}PASS{RESET}" if r["passed"] else f"{RED}FAIL{RESET}"
        tid    = f"  [{r['ticket_id']}]" if r["ticket_id"] else ""
        print(f"  {r['name']:<45} {status}{tid}")

    total = len(TEST_SCENARIOS) + query_tests_total
    passed = extraction_passed + query_tests_passed
    overall = f"{GREEN}ALL PASSED{RESET}" if passed == total else f"{RED}{total - passed} FAILED{RESET}"
    print(f"\n  Overall: {passed}/{total} — {overall}\n")


if __name__ == "__main__":
    run_tests()
