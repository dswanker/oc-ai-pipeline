// src/api/monday.js
// All Monday.com API calls for the mapping review UI

const MONDAY_API = "https://api.monday.com/v2";
const MONDAY_FILE_API = "https://api.monday.com/v2/file";

// Column IDs on the Services Study AI Hub board
export const COLS = {
  spec_json:    "file_mm2gefht",   // Study Spec JSON upload column
  ai_status:    "color_mm2h9g3m",  // AI Status (Send to AI / All Complete / etc.)
  source_system:"dropdown_mm382w7d",
};

export const STATUS_TRIGGER = "Send to AI";

/**
 * Fetch the study spec JSON file content from a Monday item.
 * Returns parsed spec object or throws.
 */
export async function fetchSpec(itemId, token) {
  // Step 1: get asset ID from the file column
  const q = `query($ids:[ID!]!) {
    items(ids:$ids) {
      id name
      column_values(ids:["${COLS.spec_json}","${COLS.source_system}"]) {
        id
        ... on FileValue { files { asset_id } }
        ... on DropdownValue { values { label } }
      }
    }
  }`;

  const res = await mondayQuery(q, { ids: [itemId] }, token);
  const item = res?.data?.items?.[0];
  if (!item) throw new Error(`Item ${itemId} not found`);

  const fileCol = item.column_values.find(c => c.id === COLS.spec_json);
  const files = fileCol?.files;
  if (!files?.length) throw new Error("No Study Spec JSON found on this item. Run the pipeline first.");

  const assetId = files[files.length - 1].asset_id; // newest file

  // Step 2: get download URL
  const assetQuery = `query($ids:[ID!]!) {
    assets(ids:$ids) { id name public_url }
  }`;
  const assetRes = await mondayQuery(assetQuery, { ids: [assetId] }, token);
  const url = assetRes?.data?.assets?.[0]?.public_url;
  if (!url) throw new Error("Could not get download URL for spec file");

  // Step 3: download the JSON
  const jsonRes = await fetch(url);
  if (!jsonRes.ok) throw new Error(`Failed to download spec: ${jsonRes.status}`);
  const spec = await jsonRes.json();

  // Attach item metadata
  spec._item_id = itemId;
  spec._item_name = item.name;

  return spec;
}

/**
 * Save the edited spec JSON back to Monday and optionally trigger the build.
 */
export async function saveSpec(spec, itemId, boardId, token, triggerBuild = true) {
  const blob = new Blob(
    [JSON.stringify(spec, null, 2)],
    { type: "application/json" }
  );
  const filename = `${spec.study_name || "study"}_Study_Specification_reviewed.json`;

  const formData = new FormData();
  formData.append(
    "query",
    `mutation ($file: File!) {
      add_file_to_column(
        item_id: ${itemId},
        column_id: "${COLS.spec_json}",
        file: $file
      ) { id }
    }`
  );
  formData.append("variables[file]", blob, filename);

  const fileRes = await fetch(MONDAY_FILE_API, {
    method: "POST",
    headers: { Authorization: token },
    body: formData,
  });
  const fileData = await fileRes.json();
  if (fileData.errors) throw new Error(fileData.errors[0].message);

  // Optionally flip status to trigger build
  if (triggerBuild && boardId) {
    await mondayQuery(
      `mutation { change_column_value(
          board_id: ${boardId},
          item_id: ${itemId},
          column_id: "${COLS.ai_status}",
          value: "{\\"label\\":\\"${STATUS_TRIGGER}\\"}"
        ) { id } }`,
      {},
      token
    );
  }

  return fileData?.data?.add_file_to_column?.id;
}

// ── Low-level helper ──────────────────────────────────────────────────────────
async function mondayQuery(query, variables = {}, token) {
  const res = await fetch(MONDAY_API, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: token,
    },
    body: JSON.stringify({ query, variables }),
  });
  const data = await res.json();
  if (data.errors) throw new Error(data.errors.map(e => e.message).join("; "));
  return data;
}
