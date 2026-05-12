// src/components/ChoicesPanel.jsx
import { useState, useEffect } from "react";

export default function ChoicesPanel({ spec, formIdx, focusField, onUpdateChoices }) {
  const form    = spec.forms[formIdx];
  const choices = form.choices || [];
  const survey  = form.survey || [];

  // Get unique codelist names used by select fields in this form
  const selectFields = survey.filter(r => r.type === "select" || r.type === "multi-select");
  const listsInUse   = [...new Set(selectFields.map(r => r.list_name).filter(Boolean))];

  // Also gather all list names that appear in choices
  const listsInChoices = [...new Set(choices.map(c => c.list_name))];
  const allLists = [...new Set([...listsInUse, ...listsInChoices])];

  // Focused list (from focusField rowIdx → list_name)
  const [activeList, setActiveList] = useState(null);

  useEffect(() => {
    if (focusField != null) {
      const row = survey[focusField];
      if (row?.list_name) setActiveList(row.list_name);
    }
  }, [focusField, formIdx]);

  function choicesForList(listName) {
    return choices.filter(c => c.list_name === listName);
  }

  function updateChoice(listName, choiceIdx, field, value) {
    const next = [...choices];
    // Find actual index in choices array
    let count = 0;
    for (let i = 0; i < next.length; i++) {
      if (next[i].list_name === listName) {
        if (count === choiceIdx) { next[i] = { ...next[i], [field]: value }; break; }
        count++;
      }
    }
    onUpdateChoices(formIdx, next);
  }

  function addChoice(listName) {
    const existing = choicesForList(listName);
    const next = [
      ...choices,
      { list_name: listName, name: `OPT_${existing.length + 1}`, label: "New Option", source: "MANUAL" }
    ];
    onUpdateChoices(formIdx, next);
  }

  function removeChoice(listName, choiceIdx) {
    let count = 0;
    const next = choices.filter((c, i) => {
      if (c.list_name !== listName) return true;
      const keep = count !== choiceIdx;
      count++;
      return keep;
    });
    onUpdateChoices(formIdx, next);
  }

  function addNewList() {
    const name = prompt("New codelist name (e.g. CL_YESNO):");
    if (!name) return;
    const next = [...choices, { list_name: name.toUpperCase(), name: "Y", label: "Yes", source: "MANUAL" }];
    onUpdateChoices(formIdx, next);
    setActiveList(name.toUpperCase());
  }

  if (allLists.length === 0) {
    return (
      <div className="choices-panel">
        <p style={{ fontSize: 12, color: "var(--text3)", marginTop: 8 }}>
          No codelists defined for this form. Add a select-type field and assign a list_name to it, or create a new codelist below.
        </p>
        <button className="btn-add-choice" style={{ marginTop: 12 }} onClick={addNewList}>+ New Codelist</button>
      </div>
    );
  }

  return (
    <div className="choices-panel">
      {/* List selector */}
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 16, flexWrap: "wrap" }}>
        <span style={{ fontSize: 11, color: "var(--text3)" }}>Codelist:</span>
        {allLists.map(l => (
          <button
            key={l}
            style={{
              padding: "3px 10px", borderRadius: 4, border: "1px solid",
              fontSize: 11, fontFamily: "monospace", cursor: "pointer",
              background: activeList === l ? "var(--bg4)" : "transparent",
              borderColor: activeList === l ? "var(--teal2)" : "var(--border)",
              color: activeList === l ? "var(--teal2)" : "var(--text3)",
            }}
            onClick={() => setActiveList(l)}
          >
            {l}
            <span style={{ marginLeft: 5, fontSize: 10, color: "var(--text3)" }}>
              ({choicesForList(l).length})
            </span>
          </button>
        ))}
        <button className="btn-add-choice" onClick={addNewList}>+ New</button>
      </div>

      {/* Active codelist table */}
      {activeList && (() => {
        const listChoices = choicesForList(activeList);
        const usedBy = selectFields.filter(r => r.list_name === activeList).map(r => r.name);
        return (
          <div className="choices-section">
            <div className="choices-head">
              <span>{activeList}</span>
              {usedBy.length > 0 && (
                <span style={{ fontSize: 10, color: "var(--text3)" }}>
                  Used by: {usedBy.join(", ")}
                </span>
              )}
            </div>

            <table className="choices-table">
              <thead>
                <tr>
                  <th style={{ width: 22 }}>#</th>
                  <th style={{ width: 150 }}>Code (name)</th>
                  <th>Display label</th>
                  <th style={{ width: 100 }}>Source</th>
                  <th style={{ width: 24 }}></th>
                </tr>
              </thead>
              <tbody>
                {listChoices.map((c, ci) => (
                  <tr key={ci}>
                    <td style={{ color: "var(--text3)", textAlign: "center", fontSize: 10 }}>{ci + 1}</td>
                    <td>
                      <input
                        className="choice-input"
                        style={{ fontFamily: "monospace" }}
                        value={c.name || ""}
                        onChange={e => updateChoice(activeList, ci, "name", e.target.value)}
                        placeholder="Code value"
                      />
                    </td>
                    <td>
                      <input
                        className="choice-input"
                        value={c.label || ""}
                        onChange={e => updateChoice(activeList, ci, "label", e.target.value)}
                        placeholder="Display label"
                      />
                    </td>
                    <td>
                      <span style={{ fontSize: 10, color: "var(--text3)" }}>
                        {c.source || "ODM"}
                      </span>
                    </td>
                    <td>
                      <button className="del-btn" onClick={() => removeChoice(activeList, ci)}>✕</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <button className="btn-add-choice" onClick={() => addChoice(activeList)}>
              + Add option
            </button>
          </div>
        );
      })()}
    </div>
  );
}
