// src/components/ExpressionEditor.jsx
// Template and XPath expression editor for many-to-one and one-to-many mappings
import { useState } from "react";
import { EXPR_MODES, renderTemplate } from "../api/mappingEngine";

// Common XPath functions relevant to clinical data
const XPATH_FUNCTIONS = [
  { fn: "concat(", desc: "Concatenate strings" },
  { fn: "substring(", desc: "Extract substring" },
  { fn: "string-length(", desc: "String length" },
  { fn: "normalize-space(", desc: "Trim whitespace" },
  { fn: "translate(", desc: "Replace characters" },
  { fn: "number(", desc: "Cast to number" },
  { fn: "string(", desc: "Cast to string" },
  { fn: "format-number(", desc: "Format number" },
  { fn: "if (", desc: "Conditional" },
  { fn: "coalesce(", desc: "First non-empty" },
];

// Common transformation templates for clinical data
const QUICK_TEMPLATES = [
  {
    label: "Date from parts (YMD)",
    template: "concat({YEAR}, '-', lpad({MONTH}, 2, '0'), '-', lpad({DAY}, 2, '0'))",
    desc: "Combine year/month/day into ISO date",
    sources: ["YEAR", "MONTH", "DAY"],
  },
  {
    label: "Date from parts (DMY)",
    template: "concat({YEAR}, '-', lpad({MONTH}, 2, '0'), '-', lpad({DAY}, 2, '0'))",
    desc: "Combine day/month/year into ISO date",
    sources: ["DAY", "MONTH", "YEAR"],
  },
  {
    label: "Concatenate with space",
    template: "concat({FIELD1}, ' ', {FIELD2})",
    desc: "Join two text fields with a space",
    sources: ["FIELD1", "FIELD2"],
  },
  {
    label: "First non-empty",
    template: "coalesce({FIELD1}, {FIELD2})",
    desc: "Use FIELD1 if populated, otherwise FIELD2",
    sources: ["FIELD1", "FIELD2"],
  },
  {
    label: "Conditional",
    template: "if ({CONDITION} = 'Y', {VALUE_IF_YES}, {VALUE_IF_NO})",
    desc: "Conditional expression",
    sources: ["CONDITION", "VALUE_IF_YES", "VALUE_IF_NO"],
  },
  {
    label: "Numeric sum",
    template: "number({FIELD1}) + number({FIELD2})",
    desc: "Add two numeric fields",
    sources: ["FIELD1", "FIELD2"],
  },
];

export default function ExpressionEditor({ mapping, sourceItems, onUpdate }) {
  const [mode, setMode] = useState(mapping?.expression_mode || EXPR_MODES.TEMPLATE);
  const [showFunctions, setShowFunctions] = useState(false);
  const [showTemplates, setShowTemplates] = useState(false);
  const [previewValues, setPreviewValues] = useState({});

  const expr = mapping?.expression || "";

  function updateExpr(newExpr) {
    onUpdate({ ...mapping, expression: newExpr, expression_mode: mode });
  }

  function switchMode(newMode) {
    setMode(newMode);
    onUpdate({ ...mapping, expression_mode: newMode });
  }

  function insertField(name) {
    updateExpr(expr + `{${name}}`);
  }

  function insertFn(fn) {
    updateExpr(expr + fn);
  }

  function applyQuickTemplate(tpl) {
    // Replace placeholder names with actual source field names where possible
    let applied = tpl.template;
    tpl.sources.forEach((placeholder, i) => {
      const actual = sourceItems[i]?.name || placeholder;
      applied = applied.replace(new RegExp(`\\{${placeholder}\\}`, "g"), `{${actual}}`);
    });
    updateExpr(applied);
    setShowTemplates(false);
  }

  // Live preview for template mode
  const preview = mode === EXPR_MODES.TEMPLATE
    ? renderTemplate(expr, Object.fromEntries(
        sourceItems.map(i => [i.name, previewValues[i.name] || `[${i.name}]`])
      ))
    : null;

  return (
    <div style={S.root}>
      <div style={S.header}>
        <span style={S.label}>EXPRESSION</span>
        {/* Mode toggle */}
        <div style={S.modeToggle}>
          <button
            style={{ ...S.modeBtn, ...(mode === EXPR_MODES.TEMPLATE ? S.modeBtnOn : {}) }}
            onClick={() => switchMode(EXPR_MODES.TEMPLATE)}
          >
            Template
          </button>
          <button
            style={{ ...S.modeBtn, ...(mode === EXPR_MODES.XPATH ? S.modeBtnOn : {}) }}
            onClick={() => switchMode(EXPR_MODES.XPATH)}
          >
            XPath
          </button>
        </div>
      </div>

      {/* Source field chips — click to insert */}
      {sourceItems.length > 0 && (
        <div style={S.fieldChips}>
          <span style={S.chipLabel}>Insert field:</span>
          {sourceItems.map(item => (
            <button
              key={item.oid}
              style={S.fieldChip}
              onClick={() => insertField(item.name)}
              title={`Insert {${item.name}}`}
            >
              {`{${item.name}}`}
            </button>
          ))}
        </div>
      )}

      {/* Quick template picker */}
      <div style={{ padding: "0 12px 6px", display: "flex", gap: 8 }}>
        <button style={S.quickBtn} onClick={() => setShowTemplates(!showTemplates)}>
          ⚡ Quick templates
        </button>
        {mode === EXPR_MODES.XPATH && (
          <button style={S.quickBtn} onClick={() => setShowFunctions(!showFunctions)}>
            ƒ Functions
          </button>
        )}
      </div>

      {/* Quick templates dropdown */}
      {showTemplates && (
        <div style={S.dropdown}>
          {QUICK_TEMPLATES.map((tpl, i) => (
            <button key={i} style={S.dropdownItem} onClick={() => applyQuickTemplate(tpl)}>
              <div style={{ fontSize: 11, fontWeight: 600, color: "#e2e8f0" }}>{tpl.label}</div>
              <div style={{ fontSize: 10, color: "#64748b", marginTop: 2 }}>{tpl.desc}</div>
              <div style={{ fontSize: 9, fontFamily: "monospace", color: "#7c3aed", marginTop: 2 }}>
                {tpl.template.slice(0, 60)}{tpl.template.length > 60 ? "…" : ""}
              </div>
            </button>
          ))}
        </div>
      )}

      {/* XPath functions dropdown */}
      {showFunctions && mode === EXPR_MODES.XPATH && (
        <div style={S.dropdown}>
          {XPATH_FUNCTIONS.map((fn, i) => (
            <button key={i} style={S.dropdownItem} onClick={() => { insertFn(fn.fn); setShowFunctions(false); }}>
              <span style={{ fontFamily: "monospace", fontSize: 11, color: "#7dd3fc" }}>{fn.fn}</span>
              <span style={{ fontSize: 10, color: "#64748b", marginLeft: 8 }}>{fn.desc}</span>
            </button>
          ))}
        </div>
      )}

      {/* Expression input */}
      <div style={{ padding: "0 12px 10px" }}>
        <textarea
          style={S.exprInput}
          value={expr}
          onChange={e => updateExpr(e.target.value)}
          placeholder={
            mode === EXPR_MODES.TEMPLATE
              ? 'e.g. {YEAR}-{MONTH}-{DAY}  or  concat({FIRST}, " ", {LAST})'
              : 'e.g. concat(string({YEAR_OID}), "-", lpad(string({MON_OID}), 2, "0"))'
          }
          rows={3}
          spellCheck={false}
        />
      </div>

      {/* Template mode: live preview with editable sample values */}
      {mode === EXPR_MODES.TEMPLATE && sourceItems.length > 0 && (
        <div style={S.previewSection}>
          <div style={S.label}>PREVIEW</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6, margin: "6px 0" }}>
            {sourceItems.map(item => (
              <div key={item.oid} style={S.previewField}>
                <span style={{ fontSize: 9, color: "#475569", display: "block" }}>{item.name}</span>
                <input
                  style={S.previewInput}
                  value={previewValues[item.name] || ""}
                  onChange={e => setPreviewValues(v => ({ ...v, [item.name]: e.target.value }))}
                  placeholder="sample"
                />
              </div>
            ))}
          </div>
          <div style={S.previewResult}>
            <span style={{ fontSize: 9, color: "#475569" }}>Result: </span>
            <span style={{ fontFamily: "monospace", fontSize: 11, color: "#7dd3fc" }}>
              {preview || <span style={{ color: "#334155" }}>enter sample values above</span>}
            </span>
          </div>
        </div>
      )}

      {/* XPath mode: helpful note */}
      {mode === EXPR_MODES.XPATH && (
        <div style={{ padding: "0 12px 10px" }}>
          <div style={S.xpathNote}>
            XPath expressions are evaluated against the OC4 form at runtime.
            Use field OIDs or names in curly braces: <code style={{ color: "#7c3aed" }}>{"{AETERM}"}</code>.
            Standard XPath 1.0 functions are supported plus <code style={{ color: "#7c3aed" }}>lpad()</code>, <code style={{ color: "#7c3aed" }}>coalesce()</code>.
          </div>
        </div>
      )}
    </div>
  );
}

const S = {
  root: { borderTop: "1px solid #1e3a5f", borderBottom: "1px solid #1e3a5f", background: "#091525" },
  header: {
    display: "flex", alignItems: "center", justifyContent: "space-between",
    padding: "8px 12px 4px",
  },
  label: { fontSize: 9, fontWeight: 700, color: "#334155", letterSpacing: ".08em" },
  modeToggle: { display: "flex", borderRadius: 4, overflow: "hidden", border: "1px solid #1e3a5f" },
  modeBtn: {
    padding: "2px 10px", border: "none", background: "transparent",
    fontSize: 10, color: "#475569", cursor: "pointer",
  },
  modeBtnOn: { background: "#1e3a5f", color: "#7dd3fc", fontWeight: 600 },

  fieldChips: {
    display: "flex", flexWrap: "wrap", gap: 4, padding: "4px 12px 6px",
    alignItems: "center",
  },
  chipLabel: { fontSize: 9, color: "#334155", marginRight: 2 },
  fieldChip: {
    padding: "2px 7px", borderRadius: 4, border: "1px solid #7c3aed",
    background: "rgba(124,58,237,.1)", color: "#a78bfa", fontSize: 10,
    fontFamily: "monospace", cursor: "pointer",
  },
  quickBtn: {
    padding: "2px 8px", borderRadius: 4, border: "1px solid #1e3a5f",
    background: "transparent", color: "#64748b", fontSize: 10, cursor: "pointer",
  },
  dropdown: {
    margin: "0 12px 8px", background: "#0d1b2a", border: "1px solid #1e3a5f",
    borderRadius: 6, overflow: "hidden", maxHeight: 220, overflow: "auto",
  },
  dropdownItem: {
    width: "100%", textAlign: "left", padding: "7px 10px",
    border: "none", borderBottom: "1px solid #1e3a5f",
    background: "transparent", cursor: "pointer", display: "block",
  },
  exprInput: {
    width: "100%", background: "#0a1826", border: "1px solid #1e3a5f",
    borderRadius: 4, color: "#e2e8f0", fontSize: 11, padding: "6px 8px",
    fontFamily: "monospace", resize: "vertical",
    outline: "none",
  },
  previewSection: { padding: "0 12px 10px" },
  previewField: { display: "flex", flexDirection: "column" },
  previewInput: {
    width: 70, padding: "2px 5px", borderRadius: 3, border: "1px solid #1e3a5f",
    background: "#0a1826", color: "#e2e8f0", fontSize: 10, outline: "none",
  },
  previewResult: {
    background: "#0d1b2a", border: "1px solid #1e3a5f", borderRadius: 4,
    padding: "6px 8px", marginTop: 6,
  },
  xpathNote: {
    fontSize: 10, color: "#475569", background: "#0a1826",
    border: "1px solid #1e3a5f", borderRadius: 4, padding: "6px 8px", lineHeight: 1.6,
  },
};
