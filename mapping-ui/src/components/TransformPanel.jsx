// src/components/TransformPanel.jsx
// Shows AI/rule-proposed transformations for the active mapping.
// DM can approve, override, reject, or add new transforms.
import { useState } from "react";

const TRANSFORM_TYPES = [
  { value: "partial_date",       label: "Partial date (UNK handling)" },
  { value: "date_format",        label: "Date format conversion" },
  { value: "value_remap",        label: "Value remap (specific values)" },
  { value: "codelist_map",       label: "Codelist mapping" },
  { value: "split_date_combine", label: "Split date combine (YR+MON+DAY)" },
  { value: "numeric_cast",       label: "Numeric cast / type conversion" },
  { value: "string_transform",   label: "String transform (trim/case)" },
  { value: "regex_replace",      label: "Regex replace" },
  { value: "conditional_default",label: "Conditional default" },
  { value: "truncate",           label: "Truncate to length" },
  { value: "custom",             label: "Custom (manual)" },
];

const EXCEPTION_ACTIONS = [
  { value: "HALT",    label: "Halt — flag for DM review",      color: "var(--oc-red)" },
  { value: "BLANK",   label: "Blank — write empty value",      color: "var(--oc-amber)" },
  { value: "DEFAULT", label: "Default — use fallback value",   color: "var(--oc-blue)" },
  { value: "SKIP",    label: "Skip — pass original value through", color: "var(--oc-green)" },
];

const STATUS_STYLES = {
  PENDING:    { bg: "var(--oc-amber-light)",  border: "var(--oc-amber)",  text: "var(--oc-amber)",  label: "⏳ Pending DM review" },
  APPROVED:   { bg: "var(--oc-green-light)",  border: "var(--oc-green)",  text: "var(--oc-green)",  label: "✓ Approved" },
  OVERRIDDEN: { bg: "var(--oc-purple-light)", border: "var(--oc-purple)", text: "var(--oc-purple)", label: "✎ Overridden by DM" },
  REJECTED:   { bg: "var(--oc-red-light)",    border: "var(--oc-red)",    text: "var(--oc-red)",    label: "✕ Rejected" },
};

const SOURCE_STYLES = {
  RULE: { color: "var(--oc-blue)",   label: "RULE" },
  AI:   { color: "var(--oc-purple)", label: "AI"   },
  DM:   { color: "var(--oc-green)",  label: "DM"   },
};

export default function TransformPanel({ mapping, targetField, onUpdateMapping }) {
  const transforms = mapping?.transformations || [];
  const [expanded, setExpanded]   = useState(new Set([0])); // first one open by default
  const [addingNew, setAddingNew] = useState(false);
  const [newType, setNewType]     = useState("value_remap");

  function toggleExpanded(i) {
    setExpanded(prev => {
      const next = new Set(prev);
      next.has(i) ? next.delete(i) : next.add(i);
      return next;
    });
  }

  function updateTransform(i, updates) {
    const next = transforms.map((t, idx) => idx === i ? { ...t, ...updates } : t);
    onUpdateMapping({ ...mapping, transformations: next });
  }

  function updateConfig(i, configUpdates) {
    const next = transforms.map((t, idx) =>
      idx === i ? { ...t, config: { ...t.config, ...configUpdates } } : t
    );
    onUpdateMapping({ ...mapping, transformations: next });
  }

  function setStatus(i, status) {
    updateTransform(i, { status, proposed_by: transforms[i].proposed_by === "DM" ? "DM" : (status === "OVERRIDDEN" ? "DM" : transforms[i].proposed_by) });
  }

  function addTransform() {
    const newT = {
      id: `t_dm_${targetField}_${transforms.length + 1}`,
      type: newType,
      proposed_by: "DM",
      confidence: 1.0,
      status: "APPROVED",
      config: defaultConfigForType(newType),
      rationale: "",
      exception_action: "HALT",
      exception_default: "",
      dm_note: "",
      applies_to_migration: true,
      applies_to_build: false,
    };
    onUpdateMapping({ ...mapping, transformations: [...transforms, newT] });
    setExpanded(prev => new Set([...prev, transforms.length]));
    setAddingNew(false);
  }

  function removeTransform(i) {
    const next = transforms.filter((_, idx) => idx !== i);
    onUpdateMapping({ ...mapping, transformations: next });
  }

  function moveTransform(i, dir) {
    const next = [...transforms];
    const j = i + dir;
    if (j < 0 || j >= next.length) return;
    [next[i], next[j]] = [next[j], next[i]];
    onUpdateMapping({ ...mapping, transformations: next });
  }

  const pendingCount  = transforms.filter(t => t.status === "PENDING").length;
  const approvedCount = transforms.filter(t => t.status === "APPROVED").length;

  return (
    <div style={S.root}>
      {/* Header */}
      <div style={S.header}>
        <span style={S.headerTitle}>TRANSFORMATIONS</span>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {pendingCount > 0 && (
            <span style={S.pendingBadge}>⏳ {pendingCount} pending</span>
          )}
          {approvedCount > 0 && (
            <span style={S.approvedBadge}>✓ {approvedCount} approved</span>
          )}
          <button style={S.addBtn} onClick={() => setAddingNew(!addingNew)}>+ Add</button>
        </div>
      </div>

      {transforms.length === 0 && !addingNew && (
        <div style={S.empty}>
          No transformations proposed.{" "}
          <button style={S.linkBtn} onClick={() => setAddingNew(true)}>Add one manually</button>
        </div>
      )}

      {/* Add new transform row */}
      {addingNew && (
        <div style={S.addRow}>
          <select
            style={S.typeSelect}
            value={newType}
            onChange={e => setNewType(e.target.value)}
          >
            {TRANSFORM_TYPES.map(t => (
              <option key={t.value} value={t.value}>{t.label}</option>
            ))}
          </select>
          <button style={{ ...S.actionBtn, background: "var(--oc-green-light)", color: "var(--oc-green)" }} onClick={addTransform}>Add</button>
          <button style={{ ...S.actionBtn, color: "var(--text-muted)" }} onClick={() => setAddingNew(false)}>Cancel</button>
        </div>
      )}

      {/* Transform list */}
      {transforms.map((t, i) => {
        const ss = STATUS_STYLES[t.status] || STATUS_STYLES.PENDING;
        const src = SOURCE_STYLES[t.proposed_by] || SOURCE_STYLES.DM;
        const isOpen = expanded.has(i);
        const conf = Math.round((t.confidence || 0) * 100);

        return (
          <div key={t.id || i} style={{ ...S.transformCard, borderColor: ss.border, background: ss.bg }}>
            {/* Card header */}
            <div style={S.cardHeader} onClick={() => toggleExpanded(i)}>
              <div style={S.cardHeaderLeft}>
                <span style={{ ...S.srcBadge, color: src.color, borderColor: src.color }}>
                  {src.label}
                </span>
                <span style={S.tType}>{t.type}</span>
                <span style={{ fontSize: 10, color: "var(--text-muted)" }}>{conf}% confidence</span>
              </div>
              <div style={S.cardHeaderRight}>
                <span style={{ fontSize: 10, color: ss.text, fontWeight: 600 }}>{ss.label}</span>
                <span style={S.chevron}>{isOpen ? "▲" : "▼"}</span>
              </div>
            </div>

            {isOpen && (
              <div style={S.cardBody}>
                {/* Rationale */}
                {t.rationale && (
                  <div style={S.rationale}>
                    <span style={{ fontSize: 9, color: "var(--text-muted)", display: "block", marginBottom: 3 }}>
                      RATIONALE
                    </span>
                    {t.rationale}
                  </div>
                )}

                {/* Config editor for this transform type */}
                <ConfigEditor type={t.type} config={t.config || {}} onUpdate={cfg => updateConfig(i, cfg)} />

                {/* Exception action */}
                <div style={S.exceptionRow}>
                  <span style={{ fontSize: 10, color: "var(--text-muted)" }}>On failure:</span>
                  <select
                    style={S.typeSelect}
                    value={t.exception_action || "HALT"}
                    onChange={e => updateTransform(i, { exception_action: e.target.value })}
                  >
                    {EXCEPTION_ACTIONS.map(a => (
                      <option key={a.value} value={a.value}>{a.label}</option>
                    ))}
                  </select>
                  {t.exception_action === "DEFAULT" && (
                    <input
                      style={S.defaultInput}
                      value={t.exception_default || ""}
                      onChange={e => updateTransform(i, { exception_default: e.target.value })}
                      placeholder="Default value…"
                    />
                  )}
                </div>

                {/* DM note */}
                <div style={{ marginBottom: 8 }}>
                  <input
                    style={S.noteInput}
                    value={t.dm_note || ""}
                    onChange={e => updateTransform(i, { dm_note: e.target.value })}
                    placeholder="DM note (optional)…"
                  />
                </div>

                {/* Actions */}
                <div style={S.actionRow}>
                  {t.status !== "APPROVED" && (
                    <button
                      style={{ ...S.actionBtn, background: "var(--oc-green-light)", color: "var(--oc-green)", border: "1px solid var(--oc-green)" }}
                      onClick={() => setStatus(i, "APPROVED")}
                    >
                      Approve
                    </button>
                  )}
                  {t.status !== "OVERRIDDEN" && t.proposed_by !== "DM" && (
                    <button
                      style={{ ...S.actionBtn, color: "var(--oc-purple)", border: "1px solid var(--oc-purple)" }}
                      onClick={() => setStatus(i, "OVERRIDDEN")}
                    >
                      Override
                    </button>
                  )}
                  {t.status !== "REJECTED" && (
                    <button
                      style={{ ...S.actionBtn, color: "var(--oc-red)", border: "1px solid var(--oc-red)" }}
                      onClick={() => setStatus(i, "REJECTED")}
                    >
                      Reject
                    </button>
                  )}
                  {t.status === "APPROVED" && (
                    <button
                      style={{ ...S.actionBtn, color: "var(--oc-amber)", border: "1px solid var(--oc-amber)" }}
                      onClick={() => setStatus(i, "PENDING")}
                    >
                      Reopen
                    </button>
                  )}
                  <div style={{ marginLeft: "auto", display: "flex", gap: 4 }}>
                    <button style={S.moveBtn} onClick={() => moveTransform(i, -1)} title="Move up" disabled={i === 0}>↑</button>
                    <button style={S.moveBtn} onClick={() => moveTransform(i,  1)} title="Move down" disabled={i === transforms.length - 1}>↓</button>
                    {t.proposed_by === "DM" && (
                      <button style={{ ...S.moveBtn, color: "var(--oc-red)" }} onClick={() => removeTransform(i)} title="Remove">✕</button>
                    )}
                  </div>
                </div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ── Config editors per transform type ─────────────────────────────────────────
function ConfigEditor({ type, config, onUpdate }) {
  switch(type) {

    case "value_remap": {
      const mappings = config.mappings || {};
      const entries = Object.entries(mappings);
      return (
        <div style={S.configSection}>
          <div style={S.configLabel}>VALUE MAPPINGS  <span style={{ color:"var(--text-muted)", fontWeight:400 }}>(source → target)</span></div>
          {entries.map(([src, tgt], i) => (
            <div key={i} style={S.mappingRow}>
              <input style={S.mapInput} value={src} readOnly />
              <span style={{ color:"var(--text-muted)", fontSize:12 }}>→</span>
              <input
                style={S.mapInput}
                value={tgt}
                onChange={e => {
                  const next = { ...mappings, [src]: e.target.value };
                  onUpdate({ mappings: next });
                }}
              />
              <button style={S.removeBtn} onClick={() => {
                const next = { ...mappings };
                delete next[src];
                onUpdate({ mappings: next });
              }}>✕</button>
            </div>
          ))}
          <button style={S.addMappingBtn} onClick={() => {
            const next = { ...mappings, "NEW_VALUE": "" };
            onUpdate({ mappings: next });
          }}>+ Add value</button>
        </div>
      );
    }

    case "codelist_map": {
      const mappings    = config.mappings || {};
      const unmapped    = config.unmapped_source_values || [];
      const allEntries  = { ...mappings };
      unmapped.forEach(v => { if (!(v in allEntries)) allEntries[v] = ""; });

      return (
        <div style={S.configSection}>
          <div style={S.configLabel}>CODELIST MAPPINGS</div>
          {unmapped.length > 0 && (
            <div style={S.unmappedWarning}>
              ⚠ {unmapped.length} value{unmapped.length > 1 ? "s" : ""} need{unmapped.length === 1 ? "s" : ""} manual mapping
            </div>
          )}
          <div style={{ maxHeight: 200, overflow: "auto" }}>
            {Object.entries(allEntries).map(([src, tgt], i) => {
              const isUnmapped = !tgt;
              return (
                <div key={i} style={{ ...S.mappingRow, background: isUnmapped ? "var(--oc-red-light)" : "transparent" }}>
                  <input style={{ ...S.mapInput, color: isUnmapped ? "var(--oc-red)" : "var(--oc-blue)" }} value={src} readOnly />
                  <span style={{ color: "var(--text-muted)", fontSize: 12 }}>→</span>
                  <input
                    style={{ ...S.mapInput, borderColor: isUnmapped ? "var(--oc-red)" : "var(--border)" }}
                    value={tgt}
                    placeholder="OC4 value…"
                    onChange={e => {
                      const next = { ...mappings, [src]: e.target.value };
                      onUpdate({ mappings: next, unmapped_source_values: unmapped.filter(v => v !== src || !e.target.value) });
                    }}
                  />
                </div>
              );
            })}
          </div>
        </div>
      );
    }

    case "partial_date":
    case "split_date_combine": {
      const unkActions = [
        { value: "blank_record", label: "Halt — flag for DM review" },
        { value: "use_partial",  label: "Use partial date (e.g. YYYY or YYYY-MM)" },
        { value: "use_default",  label: "Use a default value" },
      ];
      return (
        <div style={S.configSection}>
          <div style={S.configLabel}>PARTIAL DATE HANDLING</div>
          <div style={S.configRow}>
            <label style={S.configRowLabel}>Unknown year:</label>
            <select style={S.typeSelect} value={config.unknown_year_action || "blank_record"}
              onChange={e => onUpdate({ unknown_year_action: e.target.value })}>
              {unkActions.map(a => <option key={a.value} value={a.value}>{a.label}</option>)}
            </select>
          </div>
          <div style={S.configRow}>
            <label style={S.configRowLabel}>Unknown month:</label>
            <select style={S.typeSelect} value={config.unknown_month_action || "use_partial"}
              onChange={e => onUpdate({ unknown_month_action: e.target.value })}>
              {unkActions.map(a => <option key={a.value} value={a.value}>{a.label}</option>)}
            </select>
          </div>
          <div style={S.configRow}>
            <label style={S.configRowLabel}>Unknown day:</label>
            <select style={S.typeSelect} value={config.unknown_day_action || "use_partial"}
              onChange={e => onUpdate({ unknown_day_action: e.target.value })}>
              {unkActions.map(a => <option key={a.value} value={a.value}>{a.label}</option>)}
            </select>
          </div>
          <div style={S.configRow}>
            <label style={S.configRowLabel}>UNK token:</label>
            <input style={{ ...S.mapInput, width: 80 }} value={config.unk_token || "UNK"}
              onChange={e => onUpdate({ unk_token: e.target.value })} />
          </div>
        </div>
      );
    }

    case "date_format": {
      return (
        <div style={S.configSection}>
          <div style={S.configLabel}>DATE FORMAT</div>
          <div style={S.configRow}>
            <label style={S.configRowLabel}>Source format(s):</label>
            <input style={S.mapInput}
              value={(config.from_formats || []).join(", ")}
              onChange={e => onUpdate({ from_formats: e.target.value.split(",").map(s => s.trim()) })}
              placeholder="DD-MON-YYYY, DD/MM/YYYY"
            />
          </div>
          <div style={S.configRow}>
            <label style={S.configRowLabel}>Target format:</label>
            <input style={S.mapInput} value={config.to_format || "YYYY-MM-DD"}
              onChange={e => onUpdate({ to_format: e.target.value })} />
          </div>
        </div>
      );
    }

    case "numeric_cast": {
      return (
        <div style={S.configSection}>
          <div style={S.configLabel}>NUMERIC CAST</div>
          <div style={S.configRow}>
            <label style={S.configRowLabel}>Target type:</label>
            <select style={S.typeSelect} value={config.target_type || "float"}
              onChange={e => onUpdate({ target_type: e.target.value })}>
              <option value="float">float</option>
              <option value="integer">integer</option>
              <option value="text">text (string)</option>
            </select>
          </div>
          <div style={S.configRow}>
            <label style={S.configRowLabel}>Rounding:</label>
            <select style={S.typeSelect} value={config.rounding || "none"}
              onChange={e => onUpdate({ rounding: e.target.value })}>
              <option value="none">None</option>
              <option value="round">Round to nearest</option>
              <option value="truncate">Truncate (floor)</option>
              <option value="ceiling">Ceiling</option>
            </select>
          </div>
          <div style={S.configRow}>
            <label style={S.configRowLabel}>Strip unit strings:</label>
            <input type="checkbox" checked={!!config.strip_units}
              onChange={e => onUpdate({ strip_units: e.target.checked })} />
            <span style={{ fontSize: 10, color: "var(--text-muted)", marginLeft: 4 }}>e.g. "5 mg" → 5</span>
          </div>
        </div>
      );
    }

    case "conditional_default": {
      return (
        <div style={S.configSection}>
          <div style={S.configLabel}>CONDITIONAL DEFAULT</div>
          <div style={S.configRow}>
            <label style={S.configRowLabel}>Condition:</label>
            <select style={S.typeSelect} value={config.condition || "empty"}
              onChange={e => onUpdate({ condition: e.target.value })}>
              <option value="empty">Value is empty / blank</option>
              <option value="null">Value is NULL / NA / N/A</option>
              <option value="custom">Custom pattern (regex)</option>
            </select>
          </div>
          {config.condition === "custom" && (
            <div style={S.configRow}>
              <label style={S.configRowLabel}>Pattern:</label>
              <input style={S.mapInput} value={config.condition_pattern || ""}
                onChange={e => onUpdate({ condition_pattern: e.target.value })}
                placeholder="regex pattern…" />
            </div>
          )}
          <div style={S.configRow}>
            <label style={S.configRowLabel}>Default value:</label>
            <input style={S.mapInput} value={config.default_value || ""}
              onChange={e => onUpdate({ default_value: e.target.value })}
              placeholder="Value to use when condition is met…" />
          </div>
        </div>
      );
    }

    case "regex_replace": {
      return (
        <div style={S.configSection}>
          <div style={S.configLabel}>REGEX REPLACE</div>
          <div style={S.configRow}>
            <label style={S.configRowLabel}>Pattern:</label>
            <input style={S.mapInput} value={config.pattern || ""}
              onChange={e => onUpdate({ pattern: e.target.value })} placeholder="regex…" />
          </div>
          <div style={S.configRow}>
            <label style={S.configRowLabel}>Replacement:</label>
            <input style={S.mapInput} value={config.replacement || ""}
              onChange={e => onUpdate({ replacement: e.target.value })} placeholder="replacement…" />
          </div>
        </div>
      );
    }

    case "truncate": {
      return (
        <div style={S.configSection}>
          <div style={S.configLabel}>TRUNCATE</div>
          <div style={S.configRow}>
            <label style={S.configRowLabel}>Max length:</label>
            <input type="number" style={{ ...S.mapInput, width: 80 }}
              value={config.max_length || 4000}
              onChange={e => onUpdate({ max_length: parseInt(e.target.value) || 4000 })} />
          </div>
          <div style={S.configRow}>
            <label style={S.configRowLabel}>Indicator:</label>
            <input style={{ ...S.mapInput, width: 80 }} value={config.truncation_indicator || ""}
              onChange={e => onUpdate({ truncation_indicator: e.target.value })} placeholder="e.g. …" />
          </div>
        </div>
      );
    }

    default:
      return (
        <div style={S.configSection}>
          <div style={S.configLabel}>CONFIGURATION (JSON)</div>
          <textarea
            style={{ ...S.mapInput, width: "100%", height: 80, fontFamily: "monospace", fontSize: 10 }}
            value={JSON.stringify(config, null, 2)}
            onChange={e => { try { onUpdate(JSON.parse(e.target.value)); } catch(_) {} }}
          />
        </div>
      );
  }
}

function defaultConfigForType(type) {
  switch(type) {
    case "value_remap":        return { mappings: { "OLD_VALUE": "NEW_VALUE" }, case_insensitive: true, unmapped_action: "HALT" };
    case "codelist_map":       return { mappings: {}, unmapped_source_values: [], unmapped_action: "HALT" };
    case "partial_date":       return { unk_token: "UNK", unknown_year_action: "blank_record", unknown_month_action: "use_partial", unknown_day_action: "use_partial", output_format: "YYYY-MM-DD" };
    case "split_date_combine": return { year_field: null, month_field: null, day_field: null, output_format: "YYYY-MM-DD", unk_token: "UNK", unknown_year_action: "blank_record", unknown_month_action: "use_partial", unknown_day_action: "use_partial" };
    case "date_format":        return { from_formats: ["DD-MON-YYYY"], to_format: "YYYY-MM-DD", try_multiple_formats: true };
    case "numeric_cast":       return { target_type: "float", rounding: "none", strip_units: false, empty_to_blank: true };
    case "string_transform":   return { trim: true, upper: false, lower: false };
    case "regex_replace":      return { pattern: "", replacement: "", case_insensitive: false };
    case "conditional_default":return { condition: "empty", default_value: "" };
    case "truncate":           return { max_length: 4000, truncation_indicator: "", flag_truncated_records: true };
    default:                   return {};
  }
}

// ── Styles ─────────────────────────────────────────────────────────────────────
const S = {
  root: { borderTop: "1px solid var(--border)" },
  header: {
    display: "flex", alignItems: "center", justifyContent: "space-between",
    padding: "8px 12px", background: "var(--bg)", borderBottom: "1px solid var(--border)",
  },
  headerTitle: { fontSize: 9, fontWeight: 700, color: "var(--text-light)", letterSpacing: ".08em" },
  pendingBadge: { fontSize: 9, padding: "1px 6px", borderRadius: 4, background: "var(--oc-amber-light)", color: "var(--oc-amber)", border: "1px solid var(--oc-amber)" },
  approvedBadge:{ fontSize: 9, padding: "1px 6px", borderRadius: 4, background: "var(--oc-green-light)", color: "var(--oc-green)", border: "1px solid var(--oc-green)" },
  addBtn: { padding: "2px 8px", borderRadius: 4, border: "1px solid var(--oc-blue)", background: "#fff", color: "var(--oc-blue)", fontSize: 10, cursor: "pointer", fontWeight: 500 },
  empty: { padding: "12px", fontSize: 11, color: "var(--text-muted)", fontStyle: "italic" },
  linkBtn: { background: "transparent", border: "none", color: "var(--oc-blue)", fontSize: 11, cursor: "pointer", textDecoration: "underline", padding: 0 },
  addRow: { display: "flex", gap: 8, alignItems: "center", padding: "8px 12px", borderBottom: "1px solid var(--border)", background: "var(--bg)" },
  transformCard: { border: "1px solid", margin: "6px", borderRadius: 5, overflow: "hidden" },
  cardHeader: { display: "flex", alignItems: "center", justifyContent: "space-between", padding: "7px 10px", cursor: "pointer", userSelect: "none" },
  cardHeaderLeft: { display: "flex", alignItems: "center", gap: 8 },
  cardHeaderRight:{ display: "flex", alignItems: "center", gap: 8 },
  srcBadge: { fontSize: 8, fontWeight: 700, padding: "1px 5px", borderRadius: 3, border: "1px solid", letterSpacing: ".05em" },
  tType: { fontSize: 11, fontFamily: "monospace", color: "var(--text)" },
  chevron:{ fontSize: 9, color: "var(--text-muted)" },
  cardBody: { padding: "10px", borderTop: "1px solid var(--border)", background: "#fff" },
  rationale: { fontSize: 10, color: "var(--text-muted)", background: "var(--bg)", border: "1px solid var(--border)", borderRadius: 4, padding: "6px 8px", marginBottom: 8, lineHeight: 1.5 },
  exceptionRow: { display: "flex", alignItems: "center", gap: 8, marginBottom: 8 },
  noteInput: { width: "100%", padding: "3px 7px", borderRadius: 4, border: "1px solid var(--border)", background: "#fff", color: "var(--text)", fontSize: 11, outline: "none", fontFamily: "inherit" },
  defaultInput:{ padding: "2px 6px", borderRadius: 3, border: "1px solid var(--border)", background: "#fff", color: "var(--text)", fontSize: 11, outline: "none", width: 120 },
  actionRow: { display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" },
  actionBtn: { padding: "3px 10px", borderRadius: 4, border: "1px solid var(--border)", background: "transparent", cursor: "pointer", fontSize: 10, fontWeight: 500 },
  moveBtn:   { padding: "2px 6px", borderRadius: 3, border: "1px solid var(--border)", background: "transparent", color: "var(--text-muted)", cursor: "pointer", fontSize: 11 },
  configSection: { background: "var(--bg)", border: "1px solid var(--border)", borderRadius: 4, padding: "8px", marginBottom: 8 },
  configLabel:   { fontSize: 9, fontWeight: 700, color: "var(--text-light)", letterSpacing: ".07em", marginBottom: 6 },
  configRow:     { display: "flex", alignItems: "center", gap: 8, marginBottom: 5 },
  configRowLabel:{ fontSize: 10, color: "var(--text-muted)", minWidth: 110, flexShrink: 0 },
  mappingRow:    { display: "flex", alignItems: "center", gap: 6, marginBottom: 4, padding: "2px 0" },
  mapInput:      { padding: "3px 6px", borderRadius: 3, border: "1px solid var(--border)", background: "#fff", color: "var(--text)", fontSize: 11, outline: "none", flex: 1, minWidth: 0, fontFamily: "monospace" },
  removeBtn:     { background: "transparent", border: "none", color: "var(--oc-red)", cursor: "pointer", fontSize: 11, padding: "0 3px", flexShrink: 0 },
  addMappingBtn: { fontSize: 10, padding: "2px 8px", borderRadius: 3, border: "1px solid var(--border)", background: "transparent", color: "var(--oc-blue)", cursor: "pointer", marginTop: 4 },
  unmappedWarning:{ fontSize: 10, color: "var(--oc-amber)", background: "var(--oc-amber-light)", border: "1px solid var(--oc-amber)", borderRadius: 3, padding: "3px 7px", marginBottom: 6 },
  typeSelect:    { padding: "2px 5px", borderRadius: 3, border: "1px solid var(--border)", background: "#fff", color: "var(--text)", fontSize: 10, outline: "none", cursor: "pointer" },
};
