#!/usr/bin/env python3
"""
One-shot setup script for the Build Preview output.

What it does (all idempotent — safe to run multiple times):

 1. Confirms board 18409146946 is reachable with the provided MONDAY_API_TOKEN.
 2. Confirms the dropdown_mm2nc7d4 column contains a "Build Preview" label.
    If missing, prints exact instructions to add it manually (the API does not
    support adding labels to non-managed dropdown columns reliably).
 3. Checks whether a "Build Preview" file column already exists. If yes, prints
    its column ID. If no, creates it via create_column mutation.
 4. Prints the line you need to paste into monday_client.py.

Usage:
    export MONDAY_API_TOKEN="eyJ..."
    python scripts/create_build_preview_column.py

Requires: httpx (already in the project's deps).
"""
import json
import os
import sys

import httpx

BOARD_ID = "18409146946"
DROPDOWN_COL_ID = "dropdown_mm2nc7d4"
NEW_COL_TITLE = "Build Preview"
NEW_COL_TYPE = "file"
EXPECTED_DROPDOWN_LABEL = "Build Preview"
MONDAY_API_URL = "https://api.monday.com/v2"


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


def main():
    token = (os.environ.get("MONDAY_API_TOKEN") or "").strip()
    if not token:
        sys.exit("ERROR: MONDAY_API_TOKEN environment variable is not set.")

    print(f"Talking to Monday board {BOARD_ID}...")

    # Step 1 — read all columns on the board
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
    print(f"  Board: {board['name']}")
    cols_by_id = {c["id"]: c for c in board["columns"]}

    # Step 2 — verify the dropdown has a "Build Preview" label
    dropdown = cols_by_id.get(DROPDOWN_COL_ID)
    if not dropdown:
        print(f"\nWARNING: Column {DROPDOWN_COL_ID} does not exist on this board.")
        print("This is the dropdown the pipeline reads to decide whether to run")
        print("the Build Preview branch. Without it, the new path will never fire.")
        print("Add it manually before deploying.")
    else:
        settings = json.loads(dropdown.get("settings_str") or "{}")
        labels = settings.get("labels") or []
        # `labels` is a list of objects (managed dropdown) or a name->id map (legacy).
        label_texts = []
        if isinstance(labels, list):
            label_texts = [str(l.get("name", "")) for l in labels if isinstance(l, dict)]
        elif isinstance(labels, dict):
            label_texts = list(labels.values())
        has_build_preview = any(
            t.strip().lower() == EXPECTED_DROPDOWN_LABEL.lower() for t in label_texts
        )
        if has_build_preview:
            print(f"  Dropdown {DROPDOWN_COL_ID}: 'Build Preview' label present.")
        else:
            print(
                f"\nWARNING: Dropdown {DROPDOWN_COL_ID} does not contain a "
                f"'Build Preview' label.\n"
                f"  Existing labels: {label_texts}\n"
                f"\nAdd 'Build Preview' to the dropdown manually:\n"
                f"  1. Open the board in monday.com\n"
                f"  2. Click the column header for the output-type dropdown\n"
                f"  3. Edit settings, add a new label exactly: Build Preview\n"
                f"  4. Save\n"
                f"\nThe Monday API can manage labels on 'managed' dropdowns only;\n"
                f"a regular dropdown column requires UI-side editing.\n"
            )

    # Step 3 — find or create the Build Preview file column
    existing = next(
        (c for c in board["columns"]
         if c["title"].strip().lower() == NEW_COL_TITLE.lower()
         and c["type"] == NEW_COL_TYPE),
        None,
    )
    if existing:
        new_col_id = existing["id"]
        print(f"\nBuild Preview column already exists: {new_col_id}")
    else:
        print(f"\nCreating new file column titled '{NEW_COL_TITLE}'...")
        created = gql(
            token,
            """
            mutation($b: ID!, $t: String!) {
              create_column(board_id: $b, title: $t, column_type: file) { id title type }
            }
            """,
            {"b": BOARD_ID, "t": NEW_COL_TITLE},
        )
        col = created["create_column"]
        new_col_id = col["id"]
        print(f"  Created column: {col}")

    print()
    print("=" * 70)
    print("DONE — next step: edit monday_client.py")
    print("=" * 70)
    print()
    print("Replace this line in monday_client.py:")
    print('    "build_preview": "REPLACE_WITH_NEW_COLUMN_ID",')
    print("with:")
    print(f'    "build_preview": "{new_col_id}",')
    print()
    print("Then commit and push to trigger a Railway redeploy.")


if __name__ == "__main__":
    main()
