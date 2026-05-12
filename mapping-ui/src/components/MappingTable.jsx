// src/components/MappingTable.jsx
import { useState, useRef, useEffect } from "react";

const TYPES = ["text","integer","float","date","datetime","boolean","select","multi-select","file","calculated","note"];
const APPEARANCES = ["","w1","w2","w3","w4","w5","w6","minimal","compact","multiline"];
const LIB_SOURCES = ["CDASH_DEFAULT","CDASH_EXTENDED","CUSTOMER_LIBRARY","AI_GENERATED","MANUAL"];
const STATUSES = ["COMPLETE","FLAGGED","PLACEHOLDER"];

function statusClass(s) {
  if (s === "FLAGGED")     return "s-flagged";
  if (s === "PLACEHOLDER") return "s-placeholder";
  return "s-complete";
}
function statusDot(s) {
  if (s === "FLAGGED")     return "#f59e0b";
  if (s === "PLACEHOLDER") return "#ef4444";
  return "#10b981";
}
function libClass(s) {
  if (!s) return "lib-manual";
  if (s.startsWith("CDASH"))    return "lib-cdash";
  if (s === "CUSTOMER_LIBRARY") return "lib-cust";
  if (s === "AI_GENERATED")     return "lib-ai";
  return "lib-manual";
}

export default function MappingTable({ spec, formIdx, onUpdateRow, onDeleteRow, onAddRow, onOpenChoices }) {
  const form = spec.forms[formIdx];

  const [filter,  setFilter]  = useState("ALL");
  const [search,  setSearch]  = useState("");
  const [editing, setEditing] = useState(null); // {rowIdx, field}
  const inputRef = useRef(null);

  useEffect(() => { setFilter("ALL"); setSearch(""); setEditing(null); }, [formIdx]);
  useEffect(() => { if (editing && inputRef.current) { inputRef.current.focus(); inputRef.current.select(); } }, [editing]);

  function commit(value) {
    if (!editing) return;
    const { rowIdx, field } = editing;
    onUpdateRow(formIdx, rowIdx, field, value);
    setEditing(null);
  }

  function handleKey(e) {
    if (e.key === "Enter") commit(e.target.value);
    if (e.key === "Escape") setEditing(null);
  }

  function toggleBool(rowIdx, field) {
    const cur = form.survey[rowIdx][field];
    onUpdateRow(formIdx, rowIdx, field, cur ? "" : "yes");
  }

  function cycleStatus(rowIdx) {
    const cur = form.survey[rowIdx].completion_status;
    const next = STATUSES[(STATUSES.indexOf(cur) + 1) % STATUSES.length];
    onUpdateRow(formIdx, rowIdx, "completion_status", next);
  }

  // Filter + search
  const q = search.toLowerCase();
  const rows = form.survey
    .map((r, i) => ({ r, i }))
    .filter(({ r }) => {
      if (filter === "FLAGGED"     && r.completion_status !== "FLAGGED")     return false;
      if (filter === "PLACEHOLDER" && r.completion_status !== "PLACEHOLDER") return false;
      if (q) return (
        (r.name || "").toLowerCase().includes(q) ||
        (r.label || "").toLowerCase().includes(q) ||
        (r.source_field || "").toLowerCase().includes(q) ||
        (r.bind__oc_itemgroup || "").toLowerCase().includes(q)
      );
      return true;
    });

  // Inline cell renderer
  function Cell({ rowIdx, field, value, className, mono, placeholder }) {
    const isActive = editing?.rowIdx === rowIdx && editing?.field === field;
    if (isActive) {
      return (
        <input
          ref={inputRef}
          className={`cell-input${mono ? " mono" : ""}`}
          defaultValue={value || ""}
          onBlur={e => commit(e.target.value)}
          onKeyDown={handleKey}
        />
      );
    }
    return (
      <span
        className={className}
        title={value || placeholder || "Double-click to edit"}
        onDoubleClick={() => setEditing({ rowIdx, field })}
      >
        {value || <span style={{ color: "var(--text3)", fontStyle: "italic" }}>{placeholder || "—"}</span>}
      </span>
    );
  }

  return (
    <>
      {/* Toolbar */}
      <div className="toolbar">
        <div className="toolbar-left">
          <span className="form-title">{form.form_id} — {form.form_title}</span>
          {form.has_repeating_group && <span className="repeat-badge">REPEATING</span>}
        </div>
        <div className="toolbar-right">
          <input
            className="search-input"
            placeholder="Search fields…"
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
          {["ALL","FLAGGED","PLACEHOLDER"].map(f => (
            <button
              key={f}
              className={`filter-btn ${filter === f ? "on" : ""}`}
              onClick={() => setFilter(f)}
            >
              {f === "ALL" ? "All" : f === "FLAGGED" ? "Flagged" : "Placeholder"}
            </button>
          ))}
          <button className="btn-add" onClick={() => onAddRow(formIdx)}>+ Field</button>
        </div>
      </div>

      {/* Table */}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th style={{ width: 22 }}>#</th>
              {/* Source columns — shaded */}
              <th className="src-col" style={{ width: 110 }}>Source field</th>
              <th className="src-col" style={{ width: 80 }}>Source group</th>
              {/* OC4 Identity */}
              <th style={{ width: 130 }}>OC4 name</th>
              <th style={{ width: 160 }}>Label</th>
              <th style={{ width: 80 }}>IG group</th>
              {/* Type & display */}
              <th style={{ width: 100 }}>Type</th>
              <th style={{ width: 90 }}>Appearance</th>
              {/* Flags */}
              <th style={{ width: 55, textAlign: "center" }}>Mand.</th>
              <th style={{ width: 55, textAlign: "center" }}>Readonly</th>
              {/* Logic */}
              <th style={{ width: 170 }}>Constraint</th>
              <th style={{ width: 140 }}>Relevant</th>
              <th style={{ width: 170 }}>Calculation</th>
              {/* Help text */}
              <th style={{ width: 130 }}>Hint</th>
              <th style={{ width: 140 }}>Brief desc.</th>
              <th style={{ width: 150 }}>Description</th>
              {/* Choices */}
              <th style={{ width: 70 }}>Choices</th>
              {/* Provenance */}
              <th style={{ width: 80 }}>Lib source</th>
              <th style={{ width: 100, textAlign: "center" }}>Status</th>
              <th style={{ minWidth: 180 }}>Flag reason</th>
              <th style={{ width: 24 }}></th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ r, i }) => (
              <tr key={i}>
                <td className="row-num">{i + 1}</td>

                {/* Source (read-only) */}
                <td><span className="src-chip" title={r.source_field}>{r.source_field || "—"}</span></td>
                <td><span className="src-chip" title={r.source_group}>{r.source_group || "—"}</span></td>

                {/* OC4 name */}
                <td onDoubleClick={() => setEditing({ rowIdx: i, field: "name" })}>
                  <Cell rowIdx={i} field="name" value={r.name} className="oc4-name" mono />
                </td>

                {/* Label */}
                <td onDoubleClick={() => setEditing({ rowIdx: i, field: "label" })}>
                  <Cell rowIdx={i} field="label" value={r.label} className="lbl-text" />
                </td>

                {/* IG group */}
                <td onDoubleClick={() => setEditing({ rowIdx: i, field: "bind__oc_itemgroup" })}>
                  <Cell rowIdx={i} field="bind__oc_itemgroup" value={r.bind__oc_itemgroup} className="src-chip" mono />
                </td>

                {/* Type */}
                <td>
                  <select
                    className="type-select"
                    value={r.type || "text"}
                    onChange={e => onUpdateRow(formIdx, i, "type", e.target.value)}
                  >
                    {TYPES.map(t => <option key={t} value={t}>{t}</option>)}
                  </select>
                </td>

                {/* Appearance */}
                <td>
                  <select
                    className="type-select"
                    value={r.appearance || ""}
                    onChange={e => onUpdateRow(formIdx, i, "appearance", e.target.value)}
                  >
                    {APPEARANCES.map(a => (
                      <option key={a} value={a}>{a || "(default)"}</option>
                    ))}
                  </select>
                </td>

                {/* Mandatory */}
                <td style={{ textAlign: "center" }}>
                  <button
                    className={`toggle-btn ${r.required ? "toggle-yes" : "toggle-no"}`}
                    onClick={() => toggleBool(i, "required")}
                  >
                    {r.required ? "YES" : "NO"}
                  </button>
                </td>

                {/* Readonly */}
                <td style={{ textAlign: "center" }}>
                  <button
                    className={`toggle-btn ${r.readonly ? "toggle-yes" : "toggle-no"}`}
                    onClick={() => toggleBool(i, "readonly")}
                  >
                    {r.readonly ? "YES" : "NO"}
                  </button>
                </td>

                {/* Constraint */}
                <td onDoubleClick={() => setEditing({ rowIdx: i, field: "constraint" })}>
                  <Cell rowIdx={i} field="constraint" value={r.constraint} className="expr-text" mono placeholder="Double-click to add" />
                </td>

                {/* Relevant */}
                <td onDoubleClick={() => setEditing({ rowIdx: i, field: "relevant" })}>
                  <Cell rowIdx={i} field="relevant" value={r.relevant} className="expr-text" mono placeholder="Double-click to add" />
                </td>

                {/* Calculation */}
                <td onDoubleClick={() => setEditing({ rowIdx: i, field: "calculation" })}>
                  <Cell rowIdx={i} field="calculation" value={r.calculation} className="expr-text" mono placeholder="Double-click to add" />
                </td>

                {/* Hint */}
                <td onDoubleClick={() => setEditing({ rowIdx: i, field: "hint" })}>
                  <Cell rowIdx={i} field="hint" value={r.hint} className="hint-text" placeholder="Double-click to add" />
                </td>

                {/* Brief description */}
                <td onDoubleClick={() => setEditing({ rowIdx: i, field: "bind__oc_briefdescription" })}>
                  <Cell rowIdx={i} field="bind__oc_briefdescription" value={r.bind__oc_briefdescription} className="hint-text" placeholder="Double-click to add" />
                </td>

                {/* Description */}
                <td onDoubleClick={() => setEditing({ rowIdx: i, field: "bind__oc_description" })}>
                  <Cell rowIdx={i} field="bind__oc_description" value={r.bind__oc_description} className="hint-text" placeholder="Double-click to add" />
                </td>

                {/* Choices (for select types) */}
                <td>
                  {(r.type === "select" || r.type === "multi-select") ? (
                    <button className="choices-link" onClick={() => onOpenChoices(i)}>
                      Edit choices
                    </button>
                  ) : (
                    <span style={{ color: "var(--text3)", fontSize: 10 }}>—</span>
                  )}
                </td>

                {/* Library source */}
                <td>
                  <select
                    className="type-select"
                    value={r.library_source || "MANUAL"}
                    onChange={e => onUpdateRow(formIdx, i, "library_source", e.target.value)}
                    style={{ maxWidth: 80 }}
                  >
                    {LIB_SOURCES.map(l => <option key={l} value={l}>{l.replace(/_/g, " ")}</option>)}
                  </select>
                </td>

                {/* Status */}
                <td style={{ textAlign: "center" }}>
                  <button
                    className={`status-pill ${statusClass(r.completion_status)}`}
                    onClick={() => cycleStatus(i)}
                    title="Click to cycle status"
                  >
                    <span className="s-dot" style={{ background: statusDot(r.completion_status) }} />
                    {r.completion_status}
                  </button>
                </td>

                {/* Flag reason */}
                <td onDoubleClick={() => setEditing({ rowIdx: i, field: "flag_reason" })}>
                  <Cell
                    rowIdx={i} field="flag_reason"
                    value={r.flag_reason}
                    className={`flag-text${r.flag_reason ? " has-flag" : ""}`}
                    placeholder="Double-click to add"
                  />
                </td>

                {/* Delete */}
                <td>
                  <button className="del-btn" onClick={() => onDeleteRow(formIdx, i)} title="Remove field">✕</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length === 0 && (
          <div className="empty-state">No fields match the current filter.</div>
        )}
      </div>

      <div className="hint-bar">
        Double-click any cell to edit · Click status pill to cycle · Click Mand./Readonly to toggle · Blue columns = source EDC (read-only)
      </div>
    </>
  );
}
