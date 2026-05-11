#!/usr/bin/env python3
"""
One-shot setup script for the EDC-migration input columns on the AI Hub board.

What it does (all idempotent — safe to run multiple times):

 1. Confirms board 18409146946 is reachable with the provided MONDAY_API_TOKEN.
 2. Finds-or-creates a file column titled "Source EDC Export" (single file;
    accepts ODM XML or a ZIP containing ODM XML — the unzip happens at
    runtime in migration_pipeline.py).
 3. Finds-or-creates a dropdown column titled "Source EDC System" pre-populated
    with the vendor list that odm_reader._detect_vendor can return.
 4. Prints the two column IDs and the exact lines to paste into monday_client.COL.

Usage:
    export MONDAY_API_TOKEN="..."   # Professional Services account token
    python scripts/create_migration_columns.py
"""
import json
import os
import sys

import httpx

BOARD_ID = "18409146946"
MONDAY_API_URL = "https://api.monday.com/v2"

EXPORT_COL_TITLE = "Source EDC Export"
EXPORT_COL_TYPE = "file"
EXPORT_COL_DESC = (
    "Source EDC metadata export for migration. Upload an ODM 1.3.x XML file, "
    "or a ZIP containing one. The migration pipeline parses this into the "
    "OC4 Study Spec JSON, replacing the protocol-PDF path."
)

SYSTEM_COL_TITLE = "Source EDC System"
SYSTEM_COL_TYPE = "dropdown"
SYSTEM_COL_DESC = (
    "Source EDC vendor (auto-detected from the uploaded export when possible; "
    "may be overridden manually)."
)

# Vendor labels — must stay in sync with odm_reader._detect_vendor return values.
VENDOR_LABELS = [
    "Medidata Rave",
    "Oracle InForm",
    "Viedoc",
    "Castor EDC",
    "REDCap",
    "OpenClinica 3",
    "OpenClinica 4",
    "Zelta (Merative)",
    "Medrio",
    "Veeva Vault CDMS",
    "Other",
]


def headers(token):
    return {
        "Authorization": token,
        "Content-Type": "application/json",
        "API-Version": "2024-01",
    }


def gql(token, query, variables=None):
    body = {"query": query}
    if variables:
        body["variables"] = variables
    r = httpx.post(MONDAY_API_URL, headers=headers(token), json=body, timeout=30)
    if r.status_code != 200:
        sys.exit(f"\nMonday API HTTP {r.status_code}: {r.text}")
    data = r.json()
    if "errors" in data:
        sys.exit(f"\nMonday API GraphQL errors: {json.dumps(data['errors'], indent=2)}")
    return data["data"]


def find_column(board, title, col_type):
    return next(
        (c for c in board["columns"]
         if (c["title"] or "").strip().lower() == title.lower()
         and c["type"] == col_type),
        None,
    )


def create_file_column(token, title, description):
    print(f"Creating file column '{title}'...")
    created = gql(
        token,
        """
        mutation($b: ID!, $t: String!, $d: String) {
          create_column(board_id: $b, title: $t, description: $d, column_type: file) {
            id title type
          }
        }
        """,
        {"b": BOARD_ID, "t": title, "d": description},
    )
    col = created["create_column"]
    print(f"  Created: {col}")
    return col["id"]


def create_dropdown_column(token, title, description, labels):
    print(f"Creating dropdown column '{title}' with {len(labels)} labels...")
    defaults = json.dumps({"settings": {"labels": [{"id": i + 1, "name": name}
                                                     for i, name in enumerate(labels)]}})
    # Monday accepts a `defaults` JSON-encoded string in create_column for
    # dropdown columns. Format below matches what get_column_type_info returns
    # under "schema" for the dropdown type.
    created = gql(
        token,
        """
        mutation($b: ID!, $t: String!, $d: String, $def: JSON) {
          create_column(board_id: $b, title: $t, description: $d,
                        column_type: dropdown, defaults: $def) {
            id title type settings_str
          }
        }
        """,
        {"b": BOARD_ID, "t": title, "d": description, "def": defaults},
    )
    col = created["create_column"]
    print(f"  Created: id={col['id']} title={col['title']}")
    settings = json.loads(col.get("settings_str") or "{}")
    created_labels = [l.get("name") for l in (settings.get("labels") or [])]
    print(f"  Labels: {created_labels}")
    return col["id"]


def verify_dropdown_labels(col, want_labels):
    settings = json.loads(col.get("settings_str") or "{}")
    have = settings.get("labels") or []
    have_names = []
    if isinstance(have, list):
        have_names = [l.get("name") for l in have if isinstance(l, dict)]
    elif isinstance(have, dict):
        have_names = list(have.values())
    missing = [n for n in want_labels if n not in have_names]
    return missing


def main():
    token = (os.environ.get("MONDAY_API_TOKEN") or "").strip()
    if not token:
        sys.exit("ERROR: MONDAY_API_TOKEN environment variable is not set.")

    print(f"Talking to Monday board {BOARD_ID}...")
    board_data = gql(
        token,
        """
        query($b: [ID!]) {
          boards(ids: $b) {
            id
            name
            columns { id title type settings_str }
          }
        }
        """,
        {"b": [BOARD_ID]},
    )
    boards = board_data.get("boards") or []
    if not boards:
        sys.exit(f"ERROR: Board {BOARD_ID} not found or token lacks access.")
    board = boards[0]
    print(f"  Board: {board['name']}\n")

    # Export column
    existing_export = find_column(board, EXPORT_COL_TITLE, EXPORT_COL_TYPE)
    if existing_export:
        export_id = existing_export["id"]
        print(f"'{EXPORT_COL_TITLE}' already exists: {export_id}")
    else:
        export_id = create_file_column(token, EXPORT_COL_TITLE, EXPORT_COL_DESC)
    print()

    # System dropdown column
    existing_system = find_column(board, SYSTEM_COL_TITLE, SYSTEM_COL_TYPE)
    if existing_system:
        system_id = existing_system["id"]
        print(f"'{SYSTEM_COL_TITLE}' already exists: {system_id}")
        missing = verify_dropdown_labels(existing_system, VENDOR_LABELS)
        if missing:
            print(f"  WARNING: dropdown is missing labels: {missing}")
            print(f"  Add them manually in the Monday UI, or delete the column and re-run.")
        else:
            print(f"  All {len(VENDOR_LABELS)} vendor labels present.")
    else:
        system_id = create_dropdown_column(
            token, SYSTEM_COL_TITLE, SYSTEM_COL_DESC, VENDOR_LABELS,
        )

    print()
    print("=" * 70)
    print("DONE — next step: edit monday_client.py")
    print("=" * 70)
    print()
    print("Add these entries to the COL dict in monday_client.py:")
    print()
    print(f'    # EDC migration input')
    print(f'    "source_edc_export": "{export_id}",')
    print(f'    "source_edc_system": "{system_id}",')
    print()


if __name__ == "__main__":
    main()
