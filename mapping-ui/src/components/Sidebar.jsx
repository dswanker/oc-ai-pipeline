// src/components/Sidebar.jsx
export function Sidebar({ forms, activeForm, onSelect }) {
  return (
    <aside className="sidebar">
      <div className="sidebar-label">Forms ({forms.length})</div>
      {forms.map((f, i) => {
        const fl = f.survey.filter(r => r.completion_status === "FLAGGED").length;
        const ph = f.survey.filter(r => r.completion_status === "PLACEHOLDER").length;
        return (
          <button
            key={f.form_id}
            className={`form-btn ${i === activeForm ? "active" : ""}`}
            onClick={() => onSelect(i)}
          >
            <div className="form-btn-id">
              {f.form_id}
              <span>
                {fl > 0 && <span className="badge-f">⚑{fl}</span>}
                {ph > 0 && <span className="badge-p">⊘{ph}</span>}
              </span>
            </div>
            <div className="form-btn-name">{f.form_title}</div>
            <div className="form-btn-meta">
              {f.survey.length}f · {f.has_repeating_group ? "Repeating" : "Single"}
            </div>
          </button>
        );
      })}
    </aside>
  );
}
export default Sidebar;
