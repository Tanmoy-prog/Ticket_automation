import streamlit as st
import json
import ollama
import os
import re

TICKET_FILE = "tickets.json"
MEMORY_FILE = "memory.json"


# ---------- Load JSON ----------
def load_tickets():
    if not os.path.exists(TICKET_FILE):
        return []
    with open(TICKET_FILE, "r") as f:
        return json.load(f)

# ---------- Save JSON ----------
def save_tickets(tickets):
    with open(TICKET_FILE, "w") as f:
        json.dump(tickets, f, indent=2)

# ---------- Next Ticket Number ----------
def get_next_ticket_no(tickets):
    if not tickets:
        return "TICKET-0001"
    last = tickets[-1]["ticket_no"]
    number = int(last.split("-")[1])
    return f"TICKET-{number+1:04d}"

# ---------- FIELD SCORE ----------
def field_score(field_value: str, description: str) -> float:
    if not field_value or field_value.lower() == "unknown":
        return 0.3

    desc = description.lower()
    fv = field_value.lower()

    if re.search(r"\b" + re.escape(fv) + r"\b", desc):
        return 1.0  # exact match

    return 0.7  # inferred


# ---------- CONFIDENCE COMPUTATION ----------
def compute_confidence(issue_type, severity, affected_system, description):
    w_issue = 0.33
    w_sev = 0.33
    w_sys = 0.33

    s_issue = field_score(issue_type, description)
    s_sev = field_score(severity, description)
    s_sys = field_score(affected_system, description)

    conf = (s_issue * w_issue + s_sev * w_sev + s_sys * w_sys) * 100

    
    if (
        issue_type
        and affected_system
        and issue_type.lower() == affected_system.lower()
    ):
        conf = min(conf, 70)

    # Normalize
    if conf < 40:
        conf = 40
    if conf > 95:
        conf = 95

    return round(conf)


# ---------- GENERATE FIX ----------
def generate_proposed_fix(description):
    prompt = f"""
Generate a short practical fix for this issue. 
Return ONLY the fix sentence. No JSON.

Issue:
{description}
"""
    response = ollama.chat(
        model='gemma3:1b',
        messages=[{'role': 'user', 'content': prompt}]
    )
    return response["message"]["content"].strip()


# ---------- AI Analyzer ----------
def analyze_ticket(description):
    prompt = f"""
You are an information extraction system.

RULES:
1. Infer severity, issue_type, and affected_system from the text when possible.
2. If truly not identifiable, set the value to "unknown".

--------------- EXTRACTION RULES ---------------
1. Extract the following fields only from the given text:
   - issue_type: What type of problem is described?
   - severity: low, medium, high, critical (or "unknown" if not clearly stated)
   - affected_system: The system or component affected
   
Inference rules:
- Infer values only when the text clearly implies them.
- Do NOT guess. If unclear, set "unknown".
- If severity keyword is not available in the description make the severity portion output in json as "unknown"

Return JSON ONLY.

JSON FORMAT:
{{
  "issue_type": "",
  "severity": "",
  "affected_system": ""
}}

TEXT:
"{description}"
"""

    response = ollama.chat(
        model='gemma3:1b',
        messages=[{'role':'user','content':prompt}]
    )

    try:
        content = response["message"]["content"]
        json_start = content.find("{")
        json_end = content.rfind("}") + 1
        extracted = json.loads(content[json_start:json_end])
    except:
        extracted = {
            "issue_type": "unknown",
            "severity": "unknown",
            "affected_system": "unknown",
        }

    # --- Compute confidence using Python function ---
    conf = compute_confidence(
        extracted.get("issue_type", ""),
        extracted.get("severity", ""),
        extracted.get("affected_system", ""),
        description,
    )

    # Add confidence
    extracted["confidence"] = conf

    # Add propose fix if confidence ≥ 85
    if conf >= 85:
        extracted["propose_fix"] = generate_proposed_fix(description)
    else:
        extracted["propose_fix"] = "none"

    return extracted


# ---------- AUTO PROCESS OPEN TICKETS ----------
def auto_process_open_tickets():
    tickets = load_tickets()
    changed = False

    for t in tickets:
        if t["status"] == "open":
            analysis = analyze_ticket(t["description"])
            t["ai_analysis"] = analysis

            if analysis["confidence"] >= 85:
                t["status"] = "closed"
            else:
                t["status"] = "need review"

            changed = True

    if changed:
        save_tickets(tickets)

def parse_search_query(query):
    prompt = f"""
Extract ONLY these two fields from the search query:

1. status → one of: "open", "closed", "need review"
2. severity → one of: "low", "medium", "high", "critical"

RULES:
- If field is not mentioned, return "none".
- Do NOT infer anything.
- Do NOT extract any other field.
- Return ONLY the following JSON:

{{
  "status": "",
  "severity": ""
}}

QUERY:
"{query}"
"""
    response = ollama.chat(
        model="gemma3:1b",
        messages=[{"role": "user", "content": prompt}]
    )

    try:
        text = response["message"]["content"]
        js = json.loads(text[text.find("{"): text.rfind("}") + 1])
        return js
    except:
        return {"status": "none", "severity": "none"}


def filter_tickets(tickets, status, severity):
    results = []
    for t in tickets:
        ai = t.get("ai_analysis", {})
        if status != "none" and t["status"] != status:
            continue
        if severity != "none" and ai.get("severity") != severity:
            continue
        results.append(t)
    return results

def load_memory():
    if not os.path.exists(MEMORY_FILE):
        return []
    with open(MEMORY_FILE, "r") as f:
        return json.load(f)

def save_memory(memory):
    with open(MEMORY_FILE, "w") as f:
        json.dump(memory, f, indent=2)



# ---------- STREAMLIT UI ----------
st.title("Automated Ticketing System")

auto_process_open_tickets()

st.subheader("Create a New Ticket")
user_input = st.text_input("Enter new ticket description:")

if st.button("Create Ticket"):
    if not user_input.strip():
        st.error("Please enter a ticket description.")
    else:
        tickets = load_tickets()
        new_no = get_next_ticket_no(tickets)

        new_ticket = {
            "ticket_no": new_no,
            "description": user_input,
            "status": "open"
        }

        tickets.append(new_ticket)
        save_tickets(tickets)

        auto_process_open_tickets()

        st.success(f"Ticket {new_no} created and processed!")

st.subheader("Search Tickets (Natural Language Query)")

# ---- SESSION STATE INIT ----
if "search_results" not in st.session_state:
    st.session_state.search_results = []
if "selected_ticket" not in st.session_state:
    st.session_state.selected_ticket = None
if "search_performed" not in st.session_state:
    st.session_state.search_performed = False

search_text = st.text_input("Ask something like: 'show me need review medium severity tickets'")

# ---- SEARCH BUTTON ----
if st.button("Search"):
    if not search_text.strip():
        st.error("Please enter a query.")
    else:
        filters = parse_search_query(search_text)
        tickets = load_tickets()

        st.session_state.search_results = filter_tickets(
            tickets,
            filters["status"],
            filters["severity"]
        )
        st.session_state.selected_ticket = None
        st.session_state.search_performed = True   # <-- only now we allow results/warnings

# ---- DISPLAY RESULTS ----
results = st.session_state.search_results

if st.session_state.search_performed:
    if results:
        st.write("### Search Results")

        labels = [f"{t['ticket_no']}: {t['description'][:50]}..." for t in results]

        selected_label = st.radio(
            "Select a ticket to view details:",
            labels,
            key="ticket_radio"
        )

        ticket_no = selected_label.split(":")[0]

        st.session_state.selected_ticket = next(
            t for t in results if t["ticket_no"] == ticket_no
        )

        st.write("### Ticket Details")
        st.json(st.session_state.selected_ticket)

    else:
        st.warning("No tickets found matching your filters.")


#   HUMAN REVIEW & MANUAL CLOSURE

st.subheader("Manual Ticket Closure (Need Review Only)")

ticket_to_close = st.text_input("Enter Ticket Number to Close")
resolution_notes = st.text_area("Enter Human Resolution Notes")

if st.button("Close Ticket Manually"):
    if not ticket_to_close.strip():
        st.error("Please enter a ticket number.")
    elif not resolution_notes.strip():
        st.error("Please enter resolution notes.")
    else:
        tickets = load_tickets()
        found = False

        for t in tickets:
            if t["ticket_no"].strip().upper() == ticket_to_close.strip().upper():
                if t["status"] != "need review":
                    st.error("This ticket is not in 'need review' state.")
                    break

                t["status"] = "closed"
                t["human_resolution"] = resolution_notes
                found = True
                save_tickets(tickets)

                # Save to memory.json
                memory = load_memory()
                memory.append({
                    "ticket_no": ticket_to_close,
                    "resolution": resolution_notes,
                    "approved_by_human": True
                })
                save_memory(memory)

                st.success(f"Ticket {ticket_to_close} has been manually closed.")
                st.session_state["ticket_to_close"] = ""
                st.session_state["resolution_notes"] = ""

                # Clear selected ticket details
                st.session_state.selected_ticket = None
                
                break

        if not found:
            st.error("Ticket not found.")

