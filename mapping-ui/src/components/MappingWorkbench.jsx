// src/components/MappingWorkbench.jsx
//
// Gap-analysis report viewer. Reads ?item= from the URL, fetches the
// corresponding GapAnalysisReport via /api/gap-report/{item_id} on the
// pipeline backend, renders the per-field mappings sorted by risk
// (Blocking → Data Loss Risk → Warning → Clean), and exposes
// approve/override controls that write into the in-memory
// reviewer_decision / reviewer_note fields of each row.
//
// Persistence: edits live in component state only — no save endpoint
// exists yet. A POST companion can layer on top once the operator
// workflow firms up.

import { useEffect, useMemo, useState } from "react";
import {
  RISK_ORDER,
  RISK_STYLES,
  TYPE_STYLES,
  REVIEWER_DECISIONS,
  fetchGapReport,
  sortByRisk,
  applyReviewerDecision,
  formatField,
  describeField,
  filterByRisk,
  filterBySearch,
} from "../api/mappingEngine";

function getItemIdFromUrl() {
  const p = new URLSearchParams(window.location.search);
  return p.get("item_id") || p.get("item") || "";
}

const ALL_RISKS = new Set(RISK_ORDER);

export default function MappingWorkbench() {
  const itemId = getItemIdFromUrl();

  const [report, setReport]     = useState(null);
  const [loading, setLoading]   = useState(true);
  const [loadError, setLoadError] = useState("");

  // Controlled-view state.
  const [enabledRisks, setEnabledRisks] = useState(ALL_RISKS);
  const [search, setSearch] = useState("");
  const [overrideRow, setOverrideRow] = useState(null); // index | null
  const [overrideDraft, setOverrideDraft] = useState("");

  // ── Load on mount / when item changes ──────────────────────────────────
  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setLoadError("");
      try {
        const r = await fetchGapReport(itemId);
        if (!cancelled) setReport(r);
      } catch (e) {
        if (!cancelled) setLoadError(e.message || String(e));
      }
      if (!cancelled) setLoading(false);
    }
    load();
    return () => { cancelled = true; };
  }, [itemId]);

  // ── Derived view ──────────────────────────────────────────────────────
  const sortedMappings = useMemo(
    () => sortByRisk(report?.mappings || []),
    [report],
  );

  const visibleRows = useMemo(() => {
    const byRisk = filterByRisk(sortedMappings, enabledRisks);
    return filterBySearch(byRisk, search);
  }, [sortedMappings, enabledRisks, search]);

  // ── Reviewer actions ──────────────────────────────────────────────────
  function approveRow(mapping) {
    const idx = report.mappings.indexOf(mapping);
    setReport({
      ...report,
      mappings: applyReviewerDecision(
        report.mappings, idx, REVIEWER_DECISIONS.APPROVED, null,
      ),
    });
  }

  function startOverride(mapping) {
    const idx = report.mappings.indexOf(mapping);
    setOverrideRow(idx);
    setOverrideDraft(mapping.reviewer_note || "");
  }

  function commitOverride() {
    if (overrideRow == null) return;
    setReport({
      ...report,
      mappings: applyReviewerDecision(
        report.mappings, overrideRow,
        REVIEWER_DECISIONS.OVERRIDE, overrideDraft.trim() || null,
      ),
    });
    setOverrideRow(null);
    setOverrideDraft("");
  }

  function cancelOverride() {
    setOverrideRow(null);
    setOverrideDraft("");
  }

  function clearReview(mapping) {
    const idx = report.mappings.indexOf(mapping);
    setReport({
      ...report,
      mappings: applyReviewerDecision(
        report.mappings, idx, REVIEWER_DECISIONS.PENDING, null,
      ),
    });
  }

  function toggleRisk(risk) {
    setEnabledRisks(prev => {
      const next = new Set(prev);
      if (next.has(risk)) next.delete(risk);
      else next.add(risk);
      return next;
    });
  }

  // ── Render ────────────────────────────────────────────────────────────
  if (loading) {
    return (
      <div style={s.fullPage}>
        <p style={s.muted}>Loading gap report for item {itemId}…</p>
      </div>
    );
  }

  if (loadError) {
    return (
      <div style={s.fullPage}>
        <h2 style={s.headerTitle}>Couldn't load gap report</h2>
        <p style={s.errorBox}>{loadError}</p>
        <p style={s.muted}>
          The Syndeo URL points at a Migrations Hub item — make sure the
          migration pipeline has run for this study at least once.
        </p>
      </div>
    );
  }

  if (!report) return null;

  const { summary = {}, warnings = [] } = report;
  const reviewedCount = (report.mappings || []).filter(
    m => m.reviewer_decision != null,
  ).length;

  return (
    <div style={s.app}>
      {/* ── Header ──────────────────────────────────────────────── */}
      <header style={s.header}>
        <div>
          <h1 style={s.headerTitle}>Migration Gap Report</h1>
          <p style={s.headerSub}>
            <span><strong>Source:</strong> {report.source_system || "—"}</span>
            <span style={s.dot}>·</span>
            <span><strong>Study:</strong> {report.source_study_oid || "—"}
              {" → "}{report.target_study_oid || "—"}</span>
            <span style={s.dot}>·</span>
            <span style={s.muted}>generated {report.generated_at}</span>
          </p>
        </div>
        <div style={s.headerStats}>
          <SummaryChip label="Total"          value={summary.total ?? 0} />
          <SummaryChip label="Clean"          value={summary.clean ?? 0}          tone="Clean" />
          <SummaryChip label="Warning"        value={summary.warning ?? 0}        tone="Warning" />
          <SummaryChip label="Data Loss Risk" value={summary.data_loss_risk ?? 0} tone="Data Loss Risk" />
          <SummaryChip label="Blocking"       value={summary.blocking ?? 0}       tone="Blocking" />
          <SummaryChip label="Unmapped"       value={summary.unmapped ?? 0} />
          <SummaryChip label="Reviewed"       value={`${reviewedCount}/${summary.total ?? 0}`} />
        </div>
      </header>

      {/* ── Warnings strip ───────────────────────────────────────── */}
      {warnings.length > 0 && (
        <div style={s.warningStrip}>
          {warnings.map((w, i) => (
            <div key={i} style={s.warningRow}>
              <strong>⚠</strong> {w}
            </div>
          ))}
        </div>
      )}

      {/* ── Controls ─────────────────────────────────────────────── */}
      <div style={s.controls}>
        <div style={s.riskChips}>
          {RISK_ORDER.map(r => {
            const on = enabledRisks.has(r);
            const palette = RISK_STYLES[r];
            return (
              <button
                key={r}
                onClick={() => toggleRisk(r)}
                style={{
                  ...s.chip,
                  background: on ? palette.bg : "#F3F4F6",
                  color:      on ? palette.fg : "#6B7280",
                  borderColor: on ? palette.border : "#E5E7EB",
                  fontWeight: on ? 600 : 400,
                }}
              >
                {r}
              </button>
            );
          })}
        </div>
        <input
          type="text"
          placeholder="Search by OID, label, or reason…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          style={s.search}
        />
      </div>

      {/* ── Mappings table ───────────────────────────────────────── */}
      <table style={s.table}>
        <thead>
          <tr>
            <th style={s.th}>Risk</th>
            <th style={s.th}>Type</th>
            <th style={s.th}>Source</th>
            <th style={s.th}>Target</th>
            <th style={s.th}>Reason</th>
            <th style={s.th}>Review</th>
          </tr>
        </thead>
        <tbody>
          {visibleRows.length === 0 && (
            <tr>
              <td colSpan={6} style={s.empty}>
                No mappings match the current filters.
              </td>
            </tr>
          )}
          {visibleRows.map((m) => {
            const idx = report.mappings.indexOf(m);
            const inOverride = overrideRow === idx;
            const src = (m.sources || [])[0] || null;
            const tgt = (m.targets || [])[0] || null;
            return (
              <tr key={idx} style={s.tr}>
                <td style={s.td}>
                  <RiskBadge risk={m.risk} />
                </td>
                <td style={s.td}>
                  <TypeBadge type={m.mapping_type} />
                </td>
                <td style={s.td}>
                  <code>{formatField(src)}</code>
                  <div style={s.fieldDetail}>{describeField(src)}</div>
                </td>
                <td style={s.td}>
                  <code>{formatField(tgt)}</code>
                  <div style={s.fieldDetail}>{describeField(tgt)}</div>
                </td>
                <td style={s.td}>{m.reason}</td>
                <td style={s.td}>
                  {inOverride ? (
                    <div style={s.overrideForm}>
                      <textarea
                        autoFocus
                        rows={2}
                        value={overrideDraft}
                        onChange={e => setOverrideDraft(e.target.value)}
                        placeholder="Reviewer note explaining the override…"
                        style={s.overrideInput}
                      />
                      <div style={s.overrideButtons}>
                        <button onClick={commitOverride} style={s.btnPrimary}>
                          Save
                        </button>
                        <button onClick={cancelOverride} style={s.btnGhost}>
                          Cancel
                        </button>
                      </div>
                    </div>
                  ) : (
                    <ReviewControls
                      mapping={m}
                      onApprove={() => approveRow(m)}
                      onOverride={() => startOverride(m)}
                      onClear={() => clearReview(m)}
                    />
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ── Small presentational pieces ─────────────────────────────────────────

function RiskBadge({ risk }) {
  const palette = RISK_STYLES[risk] || RISK_STYLES.Clean;
  return (
    <span style={{
      ...s.badge,
      background: palette.bg,
      color:      palette.fg,
      border:     `1px solid ${palette.border}`,
    }}>
      {risk}
    </span>
  );
}

function TypeBadge({ type }) {
  const palette = TYPE_STYLES[type] || TYPE_STYLES["1:1"];
  return (
    <span style={{
      ...s.badge,
      background: palette.bg,
      color:      palette.fg,
      border: "1px solid transparent",
      fontFamily: "monospace",
    }}>
      {type}
    </span>
  );
}

function SummaryChip({ label, value, tone }) {
  const palette = tone ? RISK_STYLES[tone] : null;
  return (
    <div style={{
      ...s.summaryChip,
      background: palette ? palette.bg : "#F3F4F6",
      color:      palette ? palette.fg : "#374151",
    }}>
      <div style={s.summaryLabel}>{label}</div>
      <div style={s.summaryValue}>{value}</div>
    </div>
  );
}

function ReviewControls({ mapping, onApprove, onOverride, onClear }) {
  const decision = mapping.reviewer_decision;
  if (decision === REVIEWER_DECISIONS.APPROVED) {
    return (
      <div style={s.reviewState}>
        <span style={s.approved}>✓ Approved</span>
        <button onClick={onClear} style={s.btnLink}>clear</button>
      </div>
    );
  }
  if (decision === REVIEWER_DECISIONS.OVERRIDE) {
    return (
      <div style={s.reviewState}>
        <span style={s.overridden}>✎ Override</span>
        {mapping.reviewer_note && (
          <div style={s.reviewNote} title={mapping.reviewer_note}>
            "{mapping.reviewer_note}"
          </div>
        )}
        <button onClick={onClear} style={s.btnLink}>clear</button>
      </div>
    );
  }
  return (
    <div style={s.buttonRow}>
      <button onClick={onApprove}  style={s.btnPrimary}>Approve</button>
      <button onClick={onOverride} style={s.btnSecondary}>Override</button>
    </div>
  );
}

// ── Inline styles ──────────────────────────────────────────────────────
// Inline because the UI shipped as fixtures earlier and a global stylesheet
// hasn't been authored for the gap-report view yet. Easy to lift into a
// .css file as the design firms up.
const s = {
  app: {
    fontFamily: "system-ui, -apple-system, sans-serif",
    color: "#111827",
    padding: "16px 24px",
    maxWidth: 1400,
    margin: "0 auto",
  },
  fullPage: {
    padding: 40,
    fontFamily: "system-ui, sans-serif",
    color: "#374151",
  },
  header: {
    display: "flex",
    alignItems: "flex-start",
    justifyContent: "space-between",
    gap: 16,
    paddingBottom: 12,
    borderBottom: "1px solid #E5E7EB",
    marginBottom: 12,
    flexWrap: "wrap",
  },
  headerTitle: { fontSize: 22, fontWeight: 700, margin: 0 },
  headerSub:   { fontSize: 13, color: "#4B5563", margin: "4px 0 0" },
  headerStats: { display: "flex", gap: 8, flexWrap: "wrap" },
  dot: { margin: "0 8px", color: "#9CA3AF" },
  muted: { color: "#6B7280", fontSize: 13 },
  warningStrip: {
    background: "#FFFBEB",
    border: "1px solid #FDE68A",
    color: "#854D0E",
    padding: "8px 12px",
    borderRadius: 6,
    margin: "8px 0",
    fontSize: 13,
  },
  warningRow: { padding: "2px 0" },
  controls: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    gap: 12,
    margin: "12px 0",
    flexWrap: "wrap",
  },
  riskChips: { display: "flex", gap: 6, flexWrap: "wrap" },
  chip: {
    padding: "4px 12px",
    borderRadius: 16,
    border: "1px solid",
    cursor: "pointer",
    fontSize: 13,
  },
  search: {
    flex: "1 1 280px",
    minWidth: 240,
    padding: "8px 12px",
    border: "1px solid #D1D5DB",
    borderRadius: 6,
    fontSize: 14,
  },
  summaryChip: {
    minWidth: 80,
    padding: "6px 10px",
    borderRadius: 6,
    textAlign: "center",
  },
  summaryLabel: { fontSize: 11, opacity: 0.8, textTransform: "uppercase",
                  letterSpacing: 0.5 },
  summaryValue: { fontSize: 18, fontWeight: 700 },
  table: {
    width: "100%",
    borderCollapse: "collapse",
    fontSize: 13,
    background: "white",
  },
  th: {
    textAlign: "left",
    padding: "8px 10px",
    background: "#F9FAFB",
    borderBottom: "1px solid #E5E7EB",
    fontWeight: 600,
    color: "#374151",
    position: "sticky",
    top: 0,
  },
  tr: { borderBottom: "1px solid #F3F4F6" },
  td: { padding: "10px", verticalAlign: "top" },
  fieldDetail: {
    fontSize: 11,
    color: "#6B7280",
    marginTop: 2,
    maxWidth: 220,
    wordBreak: "break-word",
  },
  empty: { padding: 24, textAlign: "center", color: "#6B7280" },
  badge: {
    display: "inline-block",
    padding: "2px 8px",
    borderRadius: 12,
    fontSize: 11,
    fontWeight: 600,
    whiteSpace: "nowrap",
  },
  buttonRow: { display: "flex", gap: 6 },
  btnPrimary: {
    padding: "4px 10px",
    fontSize: 12,
    background: "#0073B1",
    color: "white",
    border: "none",
    borderRadius: 4,
    cursor: "pointer",
  },
  btnSecondary: {
    padding: "4px 10px",
    fontSize: 12,
    background: "white",
    color: "#0073B1",
    border: "1px solid #0073B1",
    borderRadius: 4,
    cursor: "pointer",
  },
  btnGhost: {
    padding: "4px 10px",
    fontSize: 12,
    background: "white",
    color: "#6B7280",
    border: "1px solid #D1D5DB",
    borderRadius: 4,
    cursor: "pointer",
  },
  btnLink: {
    padding: 0,
    background: "transparent",
    color: "#6B7280",
    border: "none",
    fontSize: 11,
    textDecoration: "underline",
    cursor: "pointer",
  },
  reviewState: { display: "flex", flexDirection: "column", gap: 2 },
  approved:   { color: "#166534", fontWeight: 600, fontSize: 12 },
  overridden: { color: "#9A3412", fontWeight: 600, fontSize: 12 },
  reviewNote: { fontSize: 11, color: "#6B7280", fontStyle: "italic",
                maxWidth: 220, wordBreak: "break-word" },
  overrideForm: { display: "flex", flexDirection: "column", gap: 6 },
  overrideInput: {
    width: "100%",
    minWidth: 200,
    padding: 6,
    border: "1px solid #D1D5DB",
    borderRadius: 4,
    fontSize: 12,
    fontFamily: "inherit",
  },
  overrideButtons: { display: "flex", gap: 6 },
  errorBox: {
    background: "#FEE2E2",
    border: "1px solid #FCA5A5",
    color: "#991B1B",
    padding: 12,
    borderRadius: 6,
    fontFamily: "monospace",
    fontSize: 13,
    whiteSpace: "pre-wrap",
  },
};
