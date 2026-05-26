// src/api/mappingEngine.js
//
// Helpers for the GapAnalysisReport schema produced by
// migration/gap_analysis.py and served from main.py's
// GET /api/gap-report/{item_id} endpoint.
//
// Schema reference (see migration/gap_analysis.py for the canonical
// definition):
//   {
//     report_id, generated_at, source_system,
//     source_study_oid, target_study_oid,
//     arm_analysis_available, warnings, summary,
//     mappings: [{
//       sources: [FieldDescriptor], targets: [FieldDescriptor],
//       mapping_type: "1:1" | "1:many" | "many:1" | "unmapped",
//       confidence:   "High" | "Medium" | "Low" | "Unmappable",
//       risk:         "Clean" | "Warning" | "Data Loss Risk" | "Blocking",
//       reason,
//       reviewer_decision, reviewer_note, override_mapping,
//     }]
//   }

const API_BASE = process.env.REACT_APP_API_URL || "";

/** Risk levels in descending severity. Defines sort order (blocking
 *  first) and the canonical ordering for UI filter chips. */
export const RISK_ORDER = ["Blocking", "Data Loss Risk", "Warning", "Clean"];

/** Numeric weight per risk for `sortByRisk` — lower number = higher
 *  severity = renders first. Anything not in RISK_ORDER falls to the
 *  bottom. */
const RISK_WEIGHT = RISK_ORDER.reduce(
  (acc, r, i) => ({ ...acc, [r]: i }),
  { __unknown__: RISK_ORDER.length },
);

/** Tailwind/CSS-ready colors per risk. Pair foreground + background so
 *  the badge is legible on either theme. */
export const RISK_STYLES = {
  Blocking:         { bg: "#FEE2E2", fg: "#991B1B", border: "#FCA5A5" },
  "Data Loss Risk": { bg: "#FFEDD5", fg: "#9A3412", border: "#FDBA74" },
  Warning:          { bg: "#FEF9C3", fg: "#854D0E", border: "#FDE68A" },
  Clean:            { bg: "#DCFCE7", fg: "#166534", border: "#86EFAC" },
};

/** Mapping cardinality colors — distinct from risk so reviewers can
 *  distinguish "what kind of mapping" from "how risky is it". */
export const TYPE_STYLES = {
  "1:1":      { bg: "#DBEAFE", fg: "#1E40AF" },
  "1:many":   { bg: "#FFEDD5", fg: "#9A3412" },
  "many:1":   { bg: "#EDE9FE", fg: "#5B21B6" },
  unmapped:   { bg: "#FEE2E2", fg: "#991B1B" },
};

/** Reviewer decision options — the values written into
 *  mapping.reviewer_decision on the in-memory report. */
export const REVIEWER_DECISIONS = {
  PENDING:  null,         // initial state, no review yet
  APPROVED: "approved",   // 1-click approval, no override
  OVERRIDE: "override",   // reviewer wrote a free-text note, mapping needs change
};

// ── HTTP ─────────────────────────────────────────────────────────────────────

/** GET /api/gap-report/{item_id}. Resolves the API origin from
 *  REACT_APP_API_URL at build time (CRA bakes env into the bundle).
 *  Throws with a message the caller can render directly. */
export async function fetchGapReport(itemId) {
  if (!itemId) {
    throw new Error("Missing ?item= URL parameter");
  }
  const url = `${API_BASE}/api/gap-report/${encodeURIComponent(itemId)}`;
  const res = await fetch(url, {
    method: "GET",
    headers: { "Accept": "application/json" },
  });
  if (!res.ok) {
    let body = "";
    try { body = await res.text(); } catch { /* ignore */ }
    throw new Error(
      `GET ${url} → ${res.status} ${res.statusText}` +
      (body ? `: ${body.slice(0, 300)}` : ""),
    );
  }
  return res.json();
}

// ── Pure helpers ─────────────────────────────────────────────────────────────

/** Sort an array of mapping rows by risk descending. Stable
 *  (`Array.prototype.sort` is stable in modern engines). Falls back on
 *  mapping_type alpha order as a tiebreaker so otherwise-equal rows
 *  group consistently between renders. */
export function sortByRisk(mappings) {
  const rows = [...(mappings || [])];
  rows.sort((a, b) => {
    const ra = RISK_WEIGHT[a.risk] ?? RISK_WEIGHT.__unknown__;
    const rb = RISK_WEIGHT[b.risk] ?? RISK_WEIGHT.__unknown__;
    if (ra !== rb) return ra - rb;
    return String(a.mapping_type || "").localeCompare(
      String(b.mapping_type || ""),
    );
  });
  return rows;
}

/** Apply a reviewer decision to one mapping row in-place on a NEW
 *  array (immutable update — React-state friendly). Returns the new
 *  array. `mappingId` is the row's index in the original report's
 *  mappings array; we use the index as the stable key because the
 *  GapAnalysisReport schema has no per-row uuid. */
export function applyReviewerDecision(
  mappings, mappingIndex, decision, note,
) {
  if (mappingIndex < 0 || mappingIndex >= (mappings?.length ?? 0)) {
    return mappings;
  }
  const next = mappings.slice();
  next[mappingIndex] = {
    ...next[mappingIndex],
    reviewer_decision: decision,
    reviewer_note: note ?? null,
  };
  return next;
}

/** Format a FieldDescriptor as a short identifier ("FORM.OID") for
 *  table cells. Returns "—" when the descriptor is null/empty (the
 *  "unmapped" case has targets: []). */
export function formatField(field) {
  if (!field) return "—";
  const form = field.form || "";
  const oid  = field.oid || "";
  if (form && oid) return `${form}.${oid}`;
  return oid || form || "—";
}

/** One-line summary of a field for the tooltip / row detail. */
export function describeField(field) {
  if (!field) return "—";
  const bits = [
    `label: ${field.label || "—"}`,
    `type: ${field.type || "—"}`,
  ];
  if (field.length != null)         bits.push(`length: ${field.length}`);
  if (field.required)               bits.push("required");
  if ((field.coded_list || []).length) {
    bits.push(`codes: ${field.coded_list.length}`);
  }
  return bits.join(" · ");
}

/** Filter rows by risk. `enabledRisks` is a Set of risk labels to keep.
 *  Returns a new array. Passing an empty set keeps nothing. */
export function filterByRisk(mappings, enabledRisks) {
  if (!enabledRisks || enabledRisks.size === 0) return [];
  return (mappings || []).filter(m => enabledRisks.has(m.risk));
}

/** Free-text search across source/target OID, label, and reason. */
export function filterBySearch(mappings, query) {
  const q = (query || "").trim().toLowerCase();
  if (!q) return mappings || [];
  return (mappings || []).filter(m => {
    const haystack = [
      m.reason || "",
      ...(m.sources || []).flatMap(s => [s.oid, s.label, s.form]),
      ...(m.targets || []).flatMap(t => [t.oid, t.label, t.form]),
    ].filter(Boolean).join(" ").toLowerCase();
    return haystack.includes(q);
  });
}
