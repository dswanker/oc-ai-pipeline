// src/components/FormPanel.jsx
const CATEGORIES   = ["ADMINISTRATIVE","SAFETY","EFFICACY","PHARMACOKINETIC","DISPOSITION","DEMOGRAPHICS","CONCOMITANT","LABORATORY"];
const STYLES       = ["theme-grid","theme-table","theme-list"];
const COMPLEXITIES = ["simple","moderate","complex"];
const ARM_OPTIONS  = ["ALL","ARM_A","ARM_B","ARM_C"];
const ALL_VISITS   = [
  "SE_SCREEN","SE_BASELINE","SE_WEEK1","SE_WEEK2","SE_WEEK4","SE_WEEK8",
  "SE_WEEK12","SE_WEEK16","SE_WEEK24","SE_EOT","SE_FOLLOW","SE_UNSCHEDULED",
];

export default function FormPanel({ spec, formIdx, onUpdateForm, onUpdateSettings, onAddVisit, onRemoveVisit }) {
  const form = spec.forms[formIdx];
  const s    = form.settings || {};
  const lm   = form.library_match || {};

  function fld(label, content) {
    return (
      <div className="fp-field">
        <label>{label}</label>
        {content}
      </div>
    );
  }

  return (
    <div className="form-panel">
      {/* Identity */}
      <div className="fp-section">
        <div className="fp-section-head">Form Identity</div>
        <div className="fp-grid">
          {fld("Form ID (OC4 — read-only)", <div className="fp-val">{form.form_id}</div>)}
          {fld("XLSForm ID (settings.form_id)", <div className="fp-val">{s.form_id || `F_${form.form_id}_1`}</div>)}
          {fld("Form Title", (
            <input
              value={form.form_title || ""}
              onChange={e => {
                onUpdateForm(formIdx, "form_title", e.target.value);
                onUpdateSettings(formIdx, "form_title", e.target.value);
              }}
            />
          ))}
          {fld("CDASH Domain", (
            <input
              value={form.cdash_domain || ""}
              onChange={e => onUpdateForm(formIdx, "cdash_domain", e.target.value)}
            />
          ))}
          {fld("Category", (
            <select value={form.form_category || ""} onChange={e => onUpdateForm(formIdx, "form_category", e.target.value)}>
              {CATEGORIES.map(c => <option key={c}>{c}</option>)}
            </select>
          ))}
          {fld("Style", (
            <select value={s.style || "theme-grid"} onChange={e => onUpdateSettings(formIdx, "style", e.target.value)}>
              {STYLES.map(st => <option key={st}>{st}</option>)}
            </select>
          ))}
          {fld("Version", (
            <input
              value={s.version || "1"}
              onChange={e => onUpdateSettings(formIdx, "version", e.target.value)}
            />
          ))}
          {fld("Complexity", (
            <select value={form.complexity || "simple"} onChange={e => onUpdateForm(formIdx, "complexity", e.target.value)}>
              {COMPLEXITIES.map(c => <option key={c}>{c}</option>)}
            </select>
          ))}
        </div>
      </div>

      {/* Flags */}
      <div className="fp-section">
        <div className="fp-section-head">Form Flags</div>
        <div className="fp-grid">
          {fld("Repeating group", (
            <select
              value={form.has_repeating_group ? "true" : "false"}
              onChange={e => onUpdateForm(formIdx, "has_repeating_group", e.target.value === "true")}
            >
              <option value="false">No</option>
              <option value="true">Yes</option>
            </select>
          ))}
          {fld("ePRO form", (
            <select
              value={form.is_epro ? "true" : "false"}
              onChange={e => onUpdateForm(formIdx, "is_epro", e.target.value === "true")}
            >
              <option value="false">No</option>
              <option value="true">Yes</option>
            </select>
          ))}
          {fld("CRO accessible", (
            <select
              value={s.cro_accessible ? "true" : "false"}
              onChange={e => onUpdateSettings(formIdx, "cro_accessible", e.target.value === "true")}
            >
              <option value="false">No</option>
              <option value="true">Yes</option>
            </select>
          ))}
          {fld("Arm applicability", (
            <select
              value={form.arm_applicability || "ALL"}
              onChange={e => onUpdateForm(formIdx, "arm_applicability", e.target.value)}
            >
              {ARM_OPTIONS.map(a => <option key={a}>{a}</option>)}
            </select>
          ))}
          {fld("Reuse count", (
            <input
              type="number" min="1"
              value={form.reuse_count || 1}
              onChange={e => onUpdateForm(formIdx, "reuse_count", parseInt(e.target.value, 10))}
            />
          ))}
        </div>
      </div>

      {/* Visit assignment */}
      <div className="fp-section">
        <div className="fp-section-head">Visit Assignment</div>
        <div className="visit-chips">
          {(form.visits_assigned || []).map((v, vi) => (
            <span className="visit-chip" key={vi}>
              {v}
              <button onClick={() => onRemoveVisit(formIdx, vi)}>×</button>
            </span>
          ))}
          {(form.visits_assigned || []).length === 0 && (
            <span style={{ fontSize: 11, color: "var(--text3)" }}>No visits assigned</span>
          )}
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <select id={`vadd-${formIdx}`} style={{ fontSize: 11, padding: "3px 6px", borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg3)", color: "var(--text1)" }}>
            {ALL_VISITS.filter(v => !(form.visits_assigned || []).includes(v)).map(v => (
              <option key={v}>{v}</option>
            ))}
          </select>
          <button
            className="btn-add-visit"
            onClick={() => {
              const sel = document.getElementById(`vadd-${formIdx}`);
              if (sel?.value) onAddVisit(formIdx, sel.value);
            }}
          >
            + Add Visit
          </button>
        </div>
      </div>

      {/* Cross-form dependencies */}
      <div className="fp-section">
        <div className="fp-section-head">Cross-Form Dependencies ({(form.cross_form_dependencies || []).length})</div>
        {(form.cross_form_dependencies || []).length === 0 ? (
          <p style={{ fontSize: 11, color: "var(--text3)" }}>None defined.</p>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
            <thead>
              <tr>
                {["Source form","Source field","Target field","XPath"].map(h => (
                  <th key={h} style={{ padding: "4px 8px", textAlign: "left", borderBottom: "1px solid var(--border)", color: "var(--text3)", fontSize: 9, textTransform: "uppercase", letterSpacing: ".06em" }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(form.cross_form_dependencies || []).map((dep, di) => (
                <tr key={di}>
                  <td style={{ padding: "4px 8px", borderBottom: "1px solid var(--border)", color: "var(--teal2)", fontFamily: "monospace", fontSize: 10 }}>{dep.source_form}</td>
                  <td style={{ padding: "4px 8px", borderBottom: "1px solid var(--border)", fontFamily: "monospace", fontSize: 10 }}>{dep.source_field}</td>
                  <td style={{ padding: "4px 8px", borderBottom: "1px solid var(--border)", fontFamily: "monospace", fontSize: 10 }}>{dep.target_field}</td>
                  <td style={{ padding: "4px 8px", borderBottom: "1px solid var(--border)", fontFamily: "monospace", fontSize: 10, maxWidth: 300, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={dep.xpath_expression}>{dep.xpath_expression}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Library match info */}
      <div className="fp-section">
        <div className="fp-section-head">Library Match</div>
        <p style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 14, lineHeight: 1.5, maxWidth: 720 }}>
          Where the fields on this form came from. Every field has one
          origin: the customer's CRF library template, the protocol text
          (added on top of the library), or a CDASH industry-standard
          default when neither source defined it.
        </p>
        <div className="fp-grid">
          {fld("Match status",   <div className="fp-val">{lm.status || "—"}</div>)}
          {fld("Source type",    <div className="fp-val">{lm.source_type || "—"}</div>)}
          {fld("From library",   <div className="fp-val">{lm.fields_from_library ?? "—"}</div>)}
          {fld("From protocol",  <div className="fp-val">{lm.fields_extended_from_protocol ?? lm.fields_extended ?? "—"}</div>)}
          {fld("From CDASH default", <div className="fp-val">{lm.fields_from_cdash_default ?? "—"}</div>)}
        </div>
      </div>
    </div>
  );
}
