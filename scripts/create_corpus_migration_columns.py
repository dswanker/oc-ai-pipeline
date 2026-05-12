#!/usr/bin/env python3
"""
One-shot setup script for the Path-M (migration) columns on the trainer
corpus board.

What it does (all idempotent — safe to run multiple times):

 1. Confirms board 18410424473 is reachable with the provided MONDAY_API_TOKEN.
 2. Finds-or-creates a text column titled "Source System" (vendor label
    written by the migration pipeline — e.g. "Medidata Rave", "Viedoc").
 3. Finds-or-creates a status column titled "Path" with two labels:
    "Protocol (Path B)" and "Migration (Path M)".
 4. Finds-or-creates a file column titled "Source ODM XML" (the source EDC
    export the pipeline migrated from).
 5. Appends "Pending PS Review" to the existing "Ingest Status" status
    column when the label isn't already present.
 6. Prints the new column IDs and the exact lines to paste into
    services/study-build-trainer/core/monday_client.py's COL map.

Usage:
    export MONDAY_API_TOKEN="..."   # Professional Services account token
    python scripts/create_corpus_migration_columns.py
"""
import json
import os
import sys

import httpx

BOARD_ID = "18410424473"
MONDAY_API_URL = "https://api.monday.com/v2"

INGEST_STATUS_COLUMN_ID = "color_mm2t8mek"
NEW_INGEST_LABEL = "Pending PS Review"

SOURCE_SYSTEM_TITLE = "Source System"
SOURCE_SYSTEM_TYPE  = "text"
SOURCE_SYSTEM_DESC  = (
    "Vendor label from the source EDC, populated by the migration pipeline "
    "from odm_reader._detect_vendor (e.g. 'Medidata Rave', 'Viedoc'). "
    "Empty on Path B (protocol-PDF) rows."
)

PATH_TITLE  = "Path"
PATH_TYPE   = "status"
PATH_DESC   = (
    "Which ingest path produced this corpus row: Protocol (Path B) for "
    "protocol-PDF runs, Migration (Path M) for ODM-XML migration runs."
)
PATH_LABELS = ["Protocol (Path B)", "Migration (Path M)"]

SOURCE_ODM_TITLE = "Source ODM XML"
SOURCE_ODM_TYPE  = "file"
SOURCE_ODM_DESC  = (
    "Raw source EDC ODM XML uploaded for migration. Path M rows only. "
    "On Path B this column stays empty (the protocol PDF goes in the "
    "Protocol column instead)."
)


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


def create_text_column(token, title, description):
    print(f"Creating text column '{title}'...")
    created = gql(
        token,
        """
        mutation($b: ID!, $t: String!, $d: String) {
          create_column(board_id: $b, title: $t, description: $d, column_type: text) {
            id title type
          }
        }
        """,
        {"b": BOARD_ID, "t": title, "d": description},
    )
    col = created["create_column"]
    print(f"  Created: {col}")
    return col["id"]


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


def create_status_column(token, title, description, labels):
    print(f"Creating status column '{title}' with {len(labels)} labels...")
    # Monday status (color) column labels: {"labels": {"0": "L1", "1": "L2"}}
    defaults = json.dumps({
        "labels": {str(i): name for i, name in enumerate(labels)},
    })
    created = gql(
        token,
        """
        mutation($b: ID!, $t: String!, $d: String, $def: JSON) {
          create_column(board_id: $b, title: $t, description: $d,
                        column_type: status, defaults: $def) {
            id title type settings_str
          }
        }
        """,
        {"b": BOARD_ID, "t": title, "d": description, "def": defaults},
    )
    col = created["create_column"]
    print(f"  Created: id={col['id']} title={col['title']}")
    settings = json.loads(col.get("settings_str") or "{}")
    print(f"  Labels: {settings.get('labels')}")
    return col["id"]


def status_labels(col):
    """Return {index_str: label_name} for a status column."""
    settings = json.loads(col.get("settings_str") or "{}")
    raw = settings.get("labels") or {}
    if isinstance(raw, list):
        # Some boards expose labels as a list of {id, name} dicts.
        out = {}
        for i, entry in enumerate(raw):
            if isinstance(entry, dict):
                out[str(entry.get("id", i))] = entry.get("name", "")
            else:
                out[str(i)] = str(entry)
        return out
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    return {}


def append_status_label(token, board, column_id, label_name):
    """
    Idempotently add a label to an existing status column.

    Monday's GraphQL has no first-class "edit status labels" mutation:
    `change_column_metadata` does not accept `labels` as a column_property,
    and `defaults` is read-only after column creation. The supported
    workaround is `create_labels_if_missing: true` on a column-value set —
    Monday materialises the new label as a side effect of writing it.

    To avoid mutating a real corpus row, this function creates a transient
    placeholder item, sets the status on it (which registers the label),
    and then deletes the placeholder.
    """
    col = next((c for c in board["columns"] if c["id"] == column_id), None)
    if col is None:
        sys.exit(f"ERROR: status column {column_id} not found on board.")

    if label_name in status_labels(col).values():
        print(f"'{label_name}' label already present on {col.get('title','?')} "
              f"— skipping.")
        return

    print(f"Registering '{label_name}' on {col.get('title','?')} via "
          f"placeholder item...")
    placeholder_name = f"__label_seed__:{label_name} (delete me)"
    placeholder = gql(
        token,
        """
        mutation($b: ID!, $n: String!) {
          create_item(board_id: $b, item_name: $n,
                      create_labels_if_missing: true) { id }
        }
        """,
        {"b": BOARD_ID, "n": placeholder_name},
    )
    placeholder_id = placeholder["create_item"]["id"]

    try:
        gql(
            token,
            """
            mutation($b: ID!, $i: ID!, $c: String!, $v: String!) {
              change_simple_column_value(
                board_id: $b, item_id: $i, column_id: $c, value: $v,
                create_labels_if_missing: true
              ) { id }
            }
            """,
            {"b": BOARD_ID, "i": placeholder_id, "c": column_id, "v": label_name},
        )
    finally:
        # Always delete the placeholder even if the value-set failed, so we
        # don't leave seed rows lying around on a partial run.
        try:
            gql(
                token,
                "mutation($i: ID!){ delete_item(item_id: $i){ id } }",
                {"i": placeholder_id},
            )
        except SystemExit as del_exc:
            print(f"  WARNING: failed to delete placeholder {placeholder_id}: "
                  f"{del_exc}")

    print(f"  Added '{label_name}' (placeholder {placeholder_id} cleaned up).")


def main():
    token = (os.environ.get("MONDAY_API_TOKEN") or "").strip()
    if not token:
        sys.exit("ERROR: MONDAY_API_TOKEN environment variable is not set.")

    print(f"Talking to Monday corpus board {BOARD_ID}...")
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

    # 1. Source System (text)
    existing = find_column(board, SOURCE_SYSTEM_TITLE, SOURCE_SYSTEM_TYPE)
    if existing:
        source_system_id = existing["id"]
        print(f"'{SOURCE_SYSTEM_TITLE}' already exists: {source_system_id}")
    else:
        source_system_id = create_text_column(
            token, SOURCE_SYSTEM_TITLE, SOURCE_SYSTEM_DESC,
        )
    print()

    # 2. Path (status)
    existing = find_column(board, PATH_TITLE, PATH_TYPE)
    if existing:
        path_id = existing["id"]
        print(f"'{PATH_TITLE}' already exists: {path_id}")
        have = set(status_labels(existing).values())
        missing = [l for l in PATH_LABELS if l not in have]
        if missing:
            print(f"  WARNING: missing labels on existing column: {missing}")
            print(f"  Add them manually in Monday UI, or delete the column "
                  f"and re-run.")
        else:
            print(f"  All {len(PATH_LABELS)} path labels present.")
    else:
        path_id = create_status_column(
            token, PATH_TITLE, PATH_DESC, PATH_LABELS,
        )
    print()

    # 3. Source ODM XML (file)
    existing = find_column(board, SOURCE_ODM_TITLE, SOURCE_ODM_TYPE)
    if existing:
        source_odm_id = existing["id"]
        print(f"'{SOURCE_ODM_TITLE}' already exists: {source_odm_id}")
    else:
        source_odm_id = create_file_column(
            token, SOURCE_ODM_TITLE, SOURCE_ODM_DESC,
        )
    print()

    # 4. Append "Pending PS Review" to the existing Ingest Status column.
    append_status_label(token, board, INGEST_STATUS_COLUMN_ID, NEW_INGEST_LABEL)
    print()

    print("=" * 70)
    print("DONE — next step: edit services/study-build-trainer/core/monday_client.py")
    print("=" * 70)
    print()
    print("Add these entries to the COL dict:")
    print()
    print(f'    # Path-M (migration) columns')
    print(f'    "source_system":           "{source_system_id}",')
    print(f'    "path":                    "{path_id}",')
    print(f'    "source_odm_xml":          "{source_odm_id}",')
    print()
    print("Add this entry to INGEST_STATUS_LABELS:")
    print()
    print(f'    "pending_ps_review":        "{NEW_INGEST_LABEL}",')
    print()


if __name__ == "__main__":
    main()
