"""
email_change_intake.py
Email Change Intake Skill — implementation script

Monitors each PS team member's Gmail inbox for inbound customer emails
containing study design change requests. Classifies each email, then
routes based on the team member's mode (Automated/Gatekeeper/Off):

  Automated  + change_request  → post [DESIGN_CHANGE] to AI Hub directly
  Gatekeeper + change_request  → create review item on Change Requests board
  Any mode   + needs_review    → always create review item
  Any mode   + not_a_change_request → silently skip

Runs hourly via Monday.com automation → POST /admin/run-email-intake.

Entry point: run_email_change_intake(member_id=None) -> summary_dict
Also exports: handle_review_decision(item_id, decision_label) -> dict
"""

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone

import httpx

# ── Board / column constants ──────────────────────────────────────────────────

OC_STAFF_BOARD   = "7663638790"
CHANGE_REQ_BOARD = "18395557554"
CHANGE_REQ_GROUP = "group_mm3zj7yj"   # "Email Change Requests (AI)"
AI_HUB_BOARD     = "18409146946"

COL_STAFF_MODE         = "color_mm3zazg"
COL_STAFF_LAST_CHECKED = "date_mm3zvx6q"

COL_CR_ASSIGNED  = "project_owner"
COL_CR_STATUS    = "project_status"
COL_CR_PRIORITY  = "project_priority"
COL_CR_COMPANY   = "text_mkzkeygb"
COL_CR_EMAIL_BODY= "long_text_mm3zvw2q"
COL_CR_FROM      = "text_mm3zej1p"
COL_CR_AI_SUMMARY= "long_text_mm3z80v1"
COL_CR_PROPOSED  = "long_text_mm3z9m21"
COL_CR_DECISION  = "color_mm3zkh2y"
COL_CR_STUDY     = "text_mm3zkmkw"

COL_HUB_PROTOCOL    = "text_mm2hcfre"
COL_HUB_TRANSCRIPTS = "file_mm3tntz9"
COL_HUB_ASSIGNEE    = "dup__of_requester__1"

MONDAY_API_URL  = "https://api.monday.com/v2"
MONDAY_FILE_URL = "https://api.monday.com/v2/file"


# ── Monday helpers ────────────────────────────────────────────────────────────

def _token():
    return os.environ.get("MONDAY_API_TOKEN", "").strip()

def _headers():
    return {
        "Authorization": _token(),
        "Content-Type": "application/json",
        "API-Version": "2024-01",
    }

async def _gql(query: str, variables: dict = None) -> dict:
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(MONDAY_API_URL, headers=_headers(), json=payload)
    r.raise_for_status()
    return r.json()

async def _upload_file(item_id: str, col_id: str,
                       filename: str, content: bytes) -> bool:
    mutation = f"""
    mutation ($file: File!) {{
        add_file_to_column(item_id: {item_id}, column_id: "{col_id}",
                           file: $file) {{ id }}
    }}
    """
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(
            MONDAY_FILE_URL,
            headers={"Authorization": _token(), "API-Version": "2023-10"},
            files={
                "query":     (None, mutation),
                "variables": (None, '{"file": null}'),
                "map":       (None, '{"file": ["variables.file"]}'),
                "file":      (filename, content, "application/octet-stream"),
            },
        )
    return r.status_code == 200

async def _post_update(item_id: str, body: str):
    q = """mutation($id: ID!, $b: String!) {
        create_update(item_id: $id, body: $b) { id }
    }"""
    await _gql(q, {"id": str(item_id), "b": body})

async def _bell(user_id: str, item_id: str, text: str):
    q = """mutation($u: ID!, $t: ID!, $tx: String!) {
        create_notification(user_id: $u, target_id: $t,
                            text: $tx, target_type: Project) { text }
    }"""
    try:
        await _gql(q, {"u": str(user_id), "t": str(item_id), "tx": text})
    except Exception as e:
        print(f"Bell notification failed: {e}", flush=True)


# ── Step 1: Load active PS team members ──────────────────────────────────────

async def _load_members(member_id: str = None) -> list:
    q = """
    query {
        boards(ids: [7663638790]) {
            items_page(limit: 100) {
                items {
                    id name
                    column_values(ids: ["person", "status__1",
                                        "color_mm3zazg", "date_mm3zvx6q",
                                        "email"]) {
                        id text value
                    }
                }
            }
        }
    }
    """
    resp = await _gql(q)
    items = (resp.get("data", {})
                 .get("boards", [{}])[0]
                 .get("items_page", {})
                 .get("items", []))

    members = []
    for item in items:
        cv = {c["id"]: c for c in item.get("column_values", [])}

        mode_text = (cv.get("color_mm3zazg", {}).get("text") or "").strip()
        if not mode_text or mode_text.lower() == "off":
            continue

        status_text = (cv.get("status__1", {}).get("text") or "").strip()
        if status_text.lower() not in ("active", ""):
            continue

        person_val = cv.get("person", {}).get("value") or "{}"
        try:
            person_data = json.loads(person_val)
            persons = person_data.get("personsAndTeams", [])
            monday_user_id = str(persons[0]["id"]) if persons else None
        except Exception:
            monday_user_id = None

        if not monday_user_id:
            continue

        last_checked_val = cv.get("date_mm3zvx6q", {}).get("value") or "{}"
        try:
            lc_data = json.loads(last_checked_val)
            last_checked = lc_data.get("date")
        except Exception:
            last_checked = None

        email_text = (cv.get("email", {}).get("text") or "").strip()

        m = {
            "staff_item_id":  item["id"],
            "name":           item["name"],
            "monday_user_id": monday_user_id,
            "mode":           mode_text,
            "last_checked":   last_checked,
            "email":          email_text,
        }

        if member_id and monday_user_id != str(member_id):
            continue

        members.append(m)

    return members


# ── Step 2: Update last_checked ───────────────────────────────────────────────

async def _update_last_checked(staff_item_id: str):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    col_val = json.dumps({"date": today})
    q = """mutation($id: ID!, $col: JSON!) {
        change_column_value(item_id: $id, board_id: 7663638790,
                            column_id: "date_mm3zvx6q",
                            value: $col) { id }
    }"""
    try:
        await _gql(q, {"id": str(staff_item_id), "col": col_val})
    except Exception as e:
        print(f"update_last_checked failed: {e}", flush=True)


# ── Step 3: Fetch unread emails via Gmail MCP ─────────────────────────────────

class GmailAuthRequired(Exception):
    pass


async def _fetch_emails(member: dict) -> list:
    """
    Fetches unread emails for a team member using the Gmail OAuth2 token
    stored at /data/gmail_sessions/{monday_user_id}.json on Railway volume.
    Uses gmail_oauth.fetch_unread_emails() which handles token refresh.
    Raises GmailAuthRequired if token is missing or refresh fails.
    """
    import sys as _sys, os as _os
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(
        _os.path.dirname(_os.path.abspath(__file__)))))
    from gmail_oauth import fetch_unread_emails, GmailAuthRequired as _GmailAuthRequired

    try:
        emails = await fetch_unread_emails(
            monday_user_id=member["monday_user_id"],
            after_date=member.get("last_checked"),
            max_results=50,
        )
        return emails
    except _GmailAuthRequired as e:
        raise GmailAuthRequired(str(e))


# ── Step 4: Classify email via Claude ────────────────────────────────────────

async def _classify_email(subject: str, body: str) -> dict:
    system = """You are an assistant for OpenClinica Professional Services.
Classify this inbound customer email into one of three categories:

change_request: Customer explicitly requesting specific changes to their
study build. Must include at least one concrete ask — add/remove/change a
field, form, visit, validation, logic, choice list, or label. Phrases like
"please add", "can you remove", "we need to change", "update the X field"
qualify. A vague "can we discuss changing X" does NOT qualify.

needs_review: Email mentions a study or build but intent is ambiguous.
Could be a question, complaint, partial request, or unclear whether action
is needed. When in doubt use needs_review over change_request.

not_a_change_request: Routine communication with no study change action
needed. Thank you notes, meeting confirmations, status check-ins, OOO
replies, invoicing/billing, general questions not about the build.

Also extract:
- protocol_id: study/protocol identifier if mentioned or null
- customer_name: customer company name if mentioned or null
- summary: one sentence describing what the email is about
- changes_mentioned: list of specific changes mentioned (empty if none)

Return ONLY valid JSON — no markdown, no preamble:
{
  "classification": "change_request|needs_review|not_a_change_request",
  "protocol_id": "string or null",
  "customer_name": "string or null",
  "summary": "string",
  "changes_mentioned": ["string"]
}"""

    user_msg = f"Subject: {subject}\n\nBody:\n{body[:3000]}"
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 500,
                "system": system,
                "messages": [{"role": "user", "content": user_msg}],
            },
        )

    data = r.json()
    raw = "".join(b.get("text", "") for b in data.get("content", [])
                  if b.get("type") == "text")
    clean = raw.replace("```json", "").replace("```", "").strip()
    return json.loads(clean)


# ── Step 5: Find AI Hub row ───────────────────────────────────────────────────

async def _find_hub_row(protocol_id: str) -> dict:
    if not protocol_id:
        return {}
    q = """
    query {
        boards(ids: [18409146946]) {
            items_page(limit: 200) {
                items {
                    id name updated_at
                    column_values(ids: ["text_mm2hcfre",
                                        "dup__of_requester__1",
                                        "text7__1"]) {
                        id text value
                    }
                }
            }
        }
    }
    """
    resp = await _gql(q)
    items = (resp.get("data", {})
                 .get("boards", [{}])[0]
                 .get("items_page", {})
                 .get("items", []))

    pid = protocol_id.lower().replace("-", "").replace(" ", "")
    matches = []
    for item in items:
        for cv in item.get("column_values", []):
            if cv["id"] == "text_mm2hcfre":
                val = (cv.get("text") or "").lower().replace(
                    "-", "").replace(" ", "")
                if pid in val or val in pid:
                    matches.append(item)
                    break

    if not matches:
        return {}
    matches.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
    best = matches[0]
    cv_map = {c["id"]: c for c in best.get("column_values", [])}
    return {
        "item_id":        best["id"],
        "assignee_value": cv_map.get("dup__of_requester__1", {}).get("value"),
        "customer_name":  cv_map.get("text7__1", {}).get("text", ""),
    }


# ── Step 6: Post to AI Hub (Automated path) ───────────────────────────────────

async def _post_to_ai_hub(hub_row: dict, email: dict,
                           classification: dict, timestamp: str) -> bool:
    item_id     = hub_row["item_id"]
    protocol_id = classification.get("protocol_id") or "UNKNOWN"

    update_body = (
        f"[DESIGN_CHANGE] [SOURCE_TYPE:email] [PROTOCOL:{protocol_id}]\n\n"
        f"From: {email['from_name']} <{email['from_email']}>\n"
        f"Subject: {email['subject']}\n\n"
        f"{email['body']}"
    )

    try:
        await _post_update(item_id, update_body)
    except Exception as e:
        print(f"Failed to post [DESIGN_CHANGE] update: {e}", flush=True)
        return False

    # Save email as transcript
    ts_short = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    filename = f"{protocol_id}_EmailRequest_{ts_short}.txt"
    content = (
        f"Inbound Customer Email — Design Change Request\n"
        f"==============================================\n"
        f"From    : {email['from_name']} <{email['from_email']}>\n"
        f"Subject : {email['subject']}\n"
        f"Received: {email['received_at']}\n"
        f"Protocol: {protocol_id}\n\n"
        f"--- EMAIL BODY ---\n{email['body']}\n"
    )
    try:
        await _upload_file(item_id, COL_HUB_TRANSCRIPTS,
                           filename, content.encode("utf-8"))
    except Exception as e:
        print(f"Transcript upload failed: {e}", flush=True)

    return True


# ── Step 7: Create review item (Gatekeeper / needs_review) ───────────────────

async def _create_review_item(member: dict, email: dict,
                               classification: dict,
                               needs_review: bool) -> str | None:
    protocol_id   = classification.get("protocol_id") or "Not identified"
    customer_name = (classification.get("customer_name")
                     or email.get("from_name") or "")
    ai_summary    = classification.get("summary", "")

    proposed_update = (
        f"[DESIGN_CHANGE] [SOURCE_TYPE:email] [PROTOCOL:{protocol_id}]\n\n"
        f"From: {email['from_name']} <{email['from_email']}>\n"
        f"Subject: {email['subject']}\n\n"
        f"{email['body']}"
    )

    priority_label = "High" if needs_review else "Normal"
    item_name = (
        f"[EMAIL] {customer_name or email['from_name']} "
        f"— {email['subject'][:55]}"
    )

    col_values = json.dumps({
        COL_CR_ASSIGNED: {
            "personsAndTeams": [{
                "id": int(member["monday_user_id"]), "kind": "person"
            }]
        },
        COL_CR_STATUS:    {"label": "Ready To Start"},
        COL_CR_PRIORITY:  {"label": priority_label},
        COL_CR_COMPANY:   customer_name,
        COL_CR_EMAIL_BODY:{"text": email["body"]},
        COL_CR_FROM:      f"{email['from_name']} <{email['from_email']}>",
        COL_CR_AI_SUMMARY:{"text": ai_summary},
        COL_CR_PROPOSED:  {"text": proposed_update},
        COL_CR_DECISION:  {"label": "Awaiting Review"},
        COL_CR_STUDY:     protocol_id,
    })

    q = """
    mutation($name: String!, $col: JSON!) {
        create_item(
            board_id: 18395557554,
            group_id: "group_mm3zj7yj",
            item_name: $name,
            column_values: $col
        ) { id }
    }
    """
    resp = await _gql(q, {"name": item_name, "col": col_values})
    new_id = (resp.get("data", {})
                  .get("create_item", {})
                  .get("id"))

    if not new_id:
        return None

    notif_type = "needs human review" if needs_review else "awaiting your approval"
    await _bell(
        member["monday_user_id"], new_id,
        f"Email change request from {email['from_name']} ({customer_name}) "
        f"{notif_type}. Study: {protocol_id}. Summary: {ai_summary[:100]}"
    )

    await _post_update(new_id, (
        f"New email change request detected.\n\n"
        f"From: {email['from_name']} <{email['from_email']}>\n"
        f"Study: {protocol_id}\n"
        f"Summary: {ai_summary}\n\n"
        + ("⚠️ Marked as needs review — intent is ambiguous.\n\n"
           if needs_review else "")
        + "Set Review Decision to Approve or Dismiss to action this request."
    ))

    return new_id


# ── Step 8: Handle review decision (called from main.py webhook) ─────────────

async def handle_review_decision(item_id: str,
                                  decision_label: str) -> dict:
    """
    Called when Review Decision column changes on Change Requests board.
    Approve → post [DESIGN_CHANGE] to AI Hub, save transcript, close item.
    Dismiss → close item, no AI Hub action.
    """
    if decision_label not in ("Approve", "Dismiss"):
        return {"status": "ignored", "label": decision_label}

    q = """
    query($id: [ID!]) {
        items(ids: $id) {
            id name
            column_values(ids: ["long_text_mm3z9m21", "text_mm3zkmkw",
                                  "long_text_mm3zvw2q", "text_mm3zej1p",
                                  "project_owner"]) {
                id text value
            }
        }
    }
    """
    resp = await _gql(q, {"id": [str(item_id)]})
    items = resp.get("data", {}).get("items", [])
    if not items:
        return {"status": "error",
                "message": f"Item {item_id} not found"}

    cv_map = {c["id"]: c
              for c in items[0].get("column_values", [])}
    proposed_update = cv_map.get("long_text_mm3z9m21", {}).get("text", "")
    protocol_id     = cv_map.get("text_mm3zkmkw", {}).get("text", "")
    email_body      = cv_map.get("long_text_mm3zvw2q", {}).get("text", "")
    from_text       = cv_map.get("text_mm3zej1p", {}).get("text", "")

    if decision_label == "Dismiss":
        await _post_update(item_id,
                           "Dismissed — no action taken on AI Hub.")
        await _set_cr_status(item_id, "Done")
        return {"status": "dismissed", "item_id": item_id}

    # Approve path
    hub_row = await _find_hub_row(protocol_id)
    if not hub_row:
        await _post_update(
            item_id,
            f"⚠️ Could not find AI Hub row for protocol '{protocol_id}'. "
            f"Please post the update manually."
        )
        return {"status": "error",
                "message": f"AI Hub row not found for {protocol_id}"}

    hub_item_id = hub_row["item_id"]
    ts_short    = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")

    try:
        await _post_update(hub_item_id, proposed_update)
    except Exception as e:
        return {"status": "error",
                "message": f"AI Hub update failed: {e}"}

    # Save transcript to AI Hub row
    filename = f"{protocol_id}_EmailRequest_{ts_short}.txt"
    content = (
        f"Inbound Customer Email — Design Change Request\n"
        f"==============================================\n"
        f"From    : {from_text}\n"
        f"Protocol: {protocol_id}\n"
        f"Approved via Change Requests board item {item_id}\n\n"
        f"--- EMAIL BODY ---\n{email_body}\n"
    ).encode("utf-8")
    try:
        await _upload_file(hub_item_id, COL_HUB_TRANSCRIPTS,
                           filename, content)
    except Exception as e:
        print(f"Transcript upload failed: {e}", flush=True)

    await _post_update(
        item_id,
        f"Approved. [DESIGN_CHANGE] update posted to AI Hub for "
        f"{protocol_id}. Pipeline is processing the spec update."
    )
    await _set_cr_status(item_id, "Done")

    return {
        "status":      "approved",
        "item_id":     item_id,
        "hub_item_id": hub_item_id,
        "protocol_id": protocol_id,
    }


async def _set_cr_status(item_id: str, label: str):
    col_val = json.dumps({"label": label})
    q = """mutation($id: ID!, $col: JSON!) {
        change_column_value(item_id: $id, board_id: 18395557554,
                            column_id: "project_status",
                            value: $col) { id }
    }"""
    try:
        await _gql(q, {"id": str(item_id), "col": col_val})
    except Exception as e:
        print(f"Status update failed: {e}", flush=True)


# ── Main entry point ──────────────────────────────────────────────────────────

async def run_email_change_intake(member_id: str = None) -> dict:
    """
    Main hourly polling loop.
    member_id: optional Monday user ID — runs for that member only if provided.
    """
    run_id    = str(uuid.uuid4())[:8]
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    summary = {
        "run_id":                run_id,
        "timestamp":             timestamp,
        "members_checked":       0,
        "emails_scanned":        0,
        "change_requests_found": 0,
        "needs_review_found":    0,
        "auto_posted":           0,
        "review_items_created":  0,
        "skipped":               0,
        "errors":                [],
    }

    try:
        members = await _load_members(member_id)
    except Exception as e:
        summary["errors"].append(f"load_members failed: {e}")
        return summary

    summary["members_checked"] = len(members)

    for member in members:
        print(f"EMAIL_INTAKE: {member['name']} mode={member['mode']}",
              flush=True)

        await _update_last_checked(member["staff_item_id"])

        try:
            emails = await _fetch_emails(member)
        except GmailAuthRequired as e:
            print(f"  Auth required: {e}", flush=True)
            summary["errors"].append(
                f"{member['name']}: Gmail not connected")
            # Notify team member to connect their Gmail
            try:
                import sys as _sys, os as _os
                _sys.path.insert(0, _os.path.dirname(_os.path.dirname(
                    _os.path.dirname(_os.path.abspath(__file__)))))
                from pipeline import generate_gmail_auth_link
                await generate_gmail_auth_link(
                    member["monday_user_id"],
                    member["name"],
                    member["staff_item_id"],
                )
            except Exception as _e:
                print(f"  Could not send auth link: {_e}", flush=True)
            continue
        except Exception as e:
            print(f"  Fetch failed: {e}", flush=True)
            summary["errors"].append(
                f"{member['name']}: fetch failed: {e}")
            continue

        summary["emails_scanned"] += len(emails)

        for email in emails:
            try:
                try:
                    cls = await _classify_email(email["subject"],
                                                email["body"])
                except Exception as e:
                    print(f"  Classification failed: {e}", flush=True)
                    cls = {
                        "classification":  "needs_review",
                        "protocol_id":     None,
                        "customer_name":   None,
                        "summary":         f"Classification failed: {e}",
                        "changes_mentioned": [],
                    }

                classification = cls.get("classification",
                                         "not_a_change_request")
                needs_review   = classification == "needs_review"

                if classification == "not_a_change_request":
                    summary["skipped"] += 1
                    continue

                if classification == "change_request":
                    summary["change_requests_found"] += 1
                else:
                    summary["needs_review_found"] += 1

                protocol_id = cls.get("protocol_id")
                hub_row = {}
                if protocol_id:
                    hub_row = await _find_hub_row(protocol_id)

                if (classification == "change_request"
                        and member["mode"].lower() == "automated"
                        and hub_row):
                    ok = await _post_to_ai_hub(
                        hub_row, email, cls, timestamp)
                    if ok:
                        summary["auto_posted"] += 1
                    else:
                        new_id = await _create_review_item(
                            member, email, cls, needs_review=False)
                        if new_id:
                            summary["review_items_created"] += 1
                else:
                    new_id = await _create_review_item(
                        member, email, cls, needs_review=needs_review)
                    if new_id:
                        summary["review_items_created"] += 1

            except Exception as e:
                print(f"  Email error: {e}", flush=True)
                summary["errors"].append(
                    f"{member['name']}/{email.get('subject','?')}: {e}")

    print(f"EMAIL_INTAKE done: {summary}", flush=True)
    return summary


# ── CLI / pipeline entry point ────────────────────────────────────────────────

if __name__ == "__main__":
    import sys, base64
    payload = (json.loads(sys.stdin.read())
               if not sys.stdin.isatty() else {})
    result = asyncio.run(
        run_email_change_intake(payload.get("member_id")))
    out = base64.standard_b64encode(
        json.dumps(result).encode()).decode()
    print(f"===JSON_START===\n{out}\n===JSON_END===")
