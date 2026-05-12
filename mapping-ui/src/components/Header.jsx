// src/components/Header.jsx
export default function Header({ spec, dirty, saving, isDemo, onSave }) {
  const forms  = spec?.forms || [];
  const counts = forms.reduce(
    (acc, f) => {
      f.survey.forEach(r => {
        if (r.completion_status === "FLAGGED")     acc.fl++;
        else if (r.completion_status === "PLACEHOLDER") acc.ph++;
        else acc.ok++;
      });
      return acc;
    },
    { ok: 0, fl: 0, ph: 0 }
  );

  return (
    <header className="header">
      <div className="header-left">
        <img className="logo" src="/oc-swoosh.svg" alt="OpenClinica" />
        <div>
          <div className="header-title">
            Syndeo
            {isDemo && <span className="demo-badge" style={{ marginLeft: 8 }}>DEMO</span>}
          </div>
          <div className="header-sub">
            {spec?.study_name} · {spec?.source_system} · {spec?.version}
          </div>
        </div>
      </div>

      <div className="header-right">
        <div className="stat-pill">
          <span className="stat-dot" style={{ background: "#10b981" }} />
          <span className="stat-label">Complete</span>
          <strong className="stat-count">{counts.ok}</strong>
        </div>
        <div className="stat-pill">
          <span className="stat-dot" style={{ background: "#f59e0b" }} />
          <span className="stat-label">Flagged</span>
          <strong className="stat-count">{counts.fl}</strong>
        </div>
        <div className="stat-pill">
          <span className="stat-dot" style={{ background: "#ef4444" }} />
          <span className="stat-label">Placeholder</span>
          <strong className="stat-count">{counts.ph}</strong>
        </div>

        {dirty && <span className="dirty-dot" title="Unsaved changes" />}

        <button
          className="btn-save-only"
          onClick={() => onSave(false)}
          disabled={saving}
          title="Save spec without triggering build"
        >
          Save only
        </button>
        <button
          className={`btn-save ${saving ? "" : ""}`}
          onClick={() => onSave(true)}
          disabled={saving}
          title="Save spec and trigger OC4 build"
        >
          {saving ? "Saving…" : "Save & Build ▶"}
        </button>
      </div>
    </header>
  );
}
