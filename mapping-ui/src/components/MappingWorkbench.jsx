// src/components/MappingWorkbench.jsx
// Two-panel source ↔ target mapping workbench
import { useState, useMemo, useCallback } from "react";
import {
  MAPPING_TYPES, EXPR_MODES,
  createMapping, buildTemplateFromSources,
  validateMapping, getMappingStats,
} from "../api/mappingEngine";
import ExpressionEditor from "./ExpressionEditor";
import TransformPanel   from "./TransformPanel";

const TYPE_LABELS = {
  [MAPPING_TYPES.ONE_TO_ONE]:  "1:1",
  [MAPPING_TYPES.MANY_TO_ONE]: "many:1",
  [MAPPING_TYPES.ONE_TO_MANY]: "1:many",
  [MAPPING_TYPES.UNMAPPED]:    "unmapped",
  [MAPPING_TYPES.NEW]:         "new",
};

const TYPE_COLORS = {
  [MAPPING_TYPES.ONE_TO_ONE]:  "#0073B1",  // --oc-blue
  [MAPPING_TYPES.MANY_TO_ONE]: "#7C3AED",  // --oc-purple
  [MAPPING_TYPES.ONE_TO_MANY]: "#D97706",  // --oc-amber
  [MAPPING_TYPES.UNMAPPED]:    "#C0392B",  // --oc-red
  [MAPPING_TYPES.NEW]:         "#6B7A8F",  // --text-muted
};

export default function MappingWorkbench({
  spec,
  formIdx,
  sourceTree,
  mappings,
  onUpdateMapping,
  onUpdateRow,
  showToast,
}) {
  const form        = spec.forms[formIdx];
  const [selectedTarget, setSelectedTarget] = useState(null); // "FORM_ID::fieldName"
  const [selectedSources, setSelectedSources] = useState(new Set()); // source item OIDs
  const [sourceFormIdx, setSourceFormIdx]   = useState(0);
  const [sourceSearch,  setSourceSearch]    = useState("");
  const [targetSearch,  setTargetSearch]    = useState("");
  const [targetFilter,  setTargetFilter]    = useState("ALL"); // ALL | MAPPED | UNMAPPED | ISSUES
  const [showExpr, setShowExpr] = useState(false);

  const stats = useMemo(() => getMappingStats(mappings), [mappings]);

  // ── Source tree helpers ───────────────────────────────────────────────────
  const sourceForms  = sourceTree?.forms || [];
  const sourceForm   = sourceForms[sourceFormIdx] || null;

  // Flat list of all source items across all groups in selected form
  const sourceItems = useMemo(() => {
    if (!sourceForm) return [];
    return sourceForm.item_groups.flatMap(g =>
      g.items.map(item => ({ ...item, groupName: g.name, groupOid: g.oid }))
    );
  }, [sourceForm]);

  const filteredSourceItems = useMemo(() => {
    if (!sourceSearch) return sourceItems;
    const q = sourceSearch.toLowerCase();
    return sourceItems.filter(i =>
      i.name.toLowerCase().includes(q) ||
      i.label.toLowerCase().includes(q) ||
      (i.cdashAlias || "").toLowerCase().includes(q)
    );
  }, [sourceItems, sourceSearch]);

  // ── Target helpers ───────────────────────────────────────────────────────
  const targetRows = useMemo(() => {
    return form.survey.filter(row => {
      const key = `${form.form_id}::${row.name}`;
      const m   = mappings[key];
      if (targetFilter === "MAPPED"   && (!m || m.type === MAPPING_TYPES.NEW || !m.sources?.length)) return false;
      if (targetFilter === "UNMAPPED" && m?.sources?.length) return false;
      if (targetFilter === "ISSUES"   && m?.type !== MAPPING_TYPES.UNMAPPED && validateMapping(m).length === 0) return false;
      if (targetSearch) {
        const q = targetSearch.toLowerCase();
        return row.name.toLowerCase().includes(q) || row.label.toLowerCase().includes(q) || (row.source_field||"").toLowerCase().includes(q);
      }
      return true;
    });
  }, [form, mappings, targetFilter, targetSearch]);

  // ── Selected target mapping ───────────────────────────────────────────────
  const activeMapping = selectedTarget ? mappings[selectedTarget] : null;
  const activeRow     = selectedTarget
    ? form.survey.find(r => `${form.form_id}::${r.name}` === selectedTarget)
    : null;

  // Source items selected in this mapping
  const activeSources = useMemo(() => {
    if (!activeMapping?.sources?.length || !sourceTree) return [];
    return activeMapping.sources.map(oid => {
      for (const f of sourceTree.forms) {
        for (const g of f.item_groups) {
          const found = g.items.find(i => i.oid === oid);
          if (found) return { ...found, groupName: g.name, formName: f.name };
        }
      }
      return { oid, name: oid, label: oid, groupName: "?", formName: "?" };
    });
  }, [activeMapping, sourceTree]);

  // ── Actions ──────────────────────────────────────────────────────────────
  function selectTarget(key) {
    setSelectedTarget(key);
    setSelectedSources(new Set());
    setShowExpr(false);
    // Pre-select source form that best matches this target's form_id
    if (sourceForms.length > 0) {
      const targetFormId = key.split("::")[0];
      const matchIdx = sourceForms.findIndex(f =>
        f.name.toUpperCase().includes(targetFormId) ||
        targetFormId.includes(f.name.toUpperCase().replace(/\s/g,""))
      );
      if (matchIdx >= 0) setSourceFormIdx(matchIdx);
    }
  }

  function toggleSourceSelect(oid) {
    setSelectedSources(prev => {
      const next = new Set(prev);
      next.has(oid) ? next.delete(oid) : next.add(oid);
      return next;
    });
  }

  function applyOneToOne(oid) {
    if (!selectedTarget) { showToast("Select a target field first", "error"); return; }
    const item = sourceItems.find(i => i.oid === oid);
    onUpdateMapping(selectedTarget, createMapping(
      MAPPING_TYPES.ONE_TO_ONE,
      [oid],
      `{${item?.name || oid}}`,
      EXPR_MODES.TEMPLATE,
      ""
    ));
    showToast(`Mapped → ${item?.name}`);
  }

  function applyManyToOne() {
    if (!selectedTarget) { showToast("Select a target field first", "error"); return; }
    if (selectedSources.size < 2) { showToast("Select at least 2 source fields for many-to-one", "error"); return; }
    const items = [...selectedSources].map(oid => sourceItems.find(i => i.oid === oid)).filter(Boolean);
    const expr  = buildTemplateFromSources(items);
    onUpdateMapping(selectedTarget, createMapping(
      MAPPING_TYPES.MANY_TO_ONE,
      [...selectedSources],
      expr,
      EXPR_MODES.TEMPLATE,
      ""
    ));
    setShowExpr(true);
    showToast(`Many-to-one mapping created — edit the expression below`);
  }

  function setMappingType(type) {
    if (!selectedTarget) return;
    const cur = mappings[selectedTarget] || {};
    onUpdateMapping(selectedTarget, createMapping(
      type,
      cur.sources || [],
      cur.expression || "",
      cur.expression_mode || EXPR_MODES.TEMPLATE,
      cur.notes || ""
    ));
  }

  function markReviewed() {
    if (!selectedTarget || !activeMapping) return;
    onUpdateMapping(selectedTarget, {
      ...activeMapping,
      reviewed: true,
      reviewed_by: "DM",
      reviewed_at: new Date().toISOString(),
    });
    showToast("Marked as reviewed ✓");
  }

  function clearMapping() {
    if (!selectedTarget) return;
    onUpdateMapping(selectedTarget, createMapping(MAPPING_TYPES.NEW, [], "", EXPR_MODES.TEMPLATE, ""));
    setSelectedSources(new Set());
  }

  function removeSource(oid) {
    if (!selectedTarget || !activeMapping) return;
    const newSources = (activeMapping.sources || []).filter(s => s !== oid);
    const newType = newSources.length === 0 ? MAPPING_TYPES.NEW :
                    newSources.length === 1 ? MAPPING_TYPES.ONE_TO_ONE :
                    MAPPING_TYPES.MANY_TO_ONE;
    onUpdateMapping(selectedTarget, { ...activeMapping, sources: newSources, type: newType });
  }

  const noSource = !sourceTree || sourceForms.length === 0;

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div style={S.root}>

      {/* ── Stats bar ─────────────────────────────────────────────────── */}
      <div style={S.statsBar}>
        <span style={S.statItem}>
          <span style={S.statDot("#2E7D5E")} />
          {stats.oneToOne} direct
        </span>
        <span style={S.statItem}>
          <span style={S.statDot("#7C3AED")} />
          {stats.manyToOne} combined
        </span>
        <span style={S.statItem}>
          <span style={S.statDot("#D97706")} />
          {stats.oneToMany} split
        </span>
        <span style={S.statItem}>
          <span style={S.statDot("#C0392B")} />
          {stats.unmapped} unmapped
        </span>
        <span style={S.statItem}>
          <span style={S.statDot("#6B7A8F")} />
          {stats.newField} new (no source)
        </span>
        <span style={{ ...S.statItem, marginLeft: "auto" }}>
          {stats.reviewed}/{stats.total} reviewed
        </span>
      </div>

      <div style={S.panels}>

        {/* ── LEFT: Source panel ──────────────────────────────────────── */}
        <div style={S.sourcePanel}>
          <div style={S.panelHeader}>
            <span style={S.panelTitle}>
              SOURCE
              {sourceTree && <span style={S.panelSub}> — {sourceTree.sourceSystem}</span>}
            </span>
            {noSource && (
              <span style={{ fontSize: 10, color: "var(--oc-amber)" }}>No ODM uploaded</span>
            )}
          </div>

          {noSource ? (
            <div style={S.noSource}>
              <div style={{ fontSize: 28, marginBottom: 8 }}>📄</div>
              <div style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 4 }}>No source ODM XML found</div>
              <div style={{ fontSize: 11, color: "var(--text-light)", textAlign: "center", lineHeight: 1.5 }}>
                Upload the competitor ODM XML to the Monday row's<br/>
                "Source EDC Export" column, then reload.
              </div>
            </div>
          ) : (
            <>
              {/* Source form selector */}
              <div style={S.sourceFormTabs}>
                {sourceForms.map((f, i) => (
                  <button
                    key={f.oid}
                    style={{
                      ...S.sourceFormTab,
                      background: i === sourceFormIdx ? "var(--oc-blue-light)" : "transparent",
                      color: i === sourceFormIdx ? "var(--oc-blue)" : "var(--text-muted)",
                      borderBottom: i === sourceFormIdx ? "2px solid var(--oc-blue)" : "2px solid transparent",
                    }}
                    onClick={() => setSourceFormIdx(i)}
                    title={f.name}
                  >
                    {f.name.length > 10 ? f.name.slice(0, 10) + "…" : f.name}
                    {f.repeating && <span style={{ fontSize: 8, color: "var(--oc-purple)", marginLeft: 3 }}>R</span>}
                  </button>
                ))}
              </div>

              {/* Search */}
              <div style={{ padding: "6px 10px", borderBottom: "1px solid var(--border)", background: "#fff" }}>
                <input
                  style={S.searchInput}
                  placeholder="Search source fields…"
                  value={sourceSearch}
                  onChange={e => setSourceSearch(e.target.value)}
                />
              </div>

              {/* Multi-select hint */}
              {selectedTarget && (
                <div style={S.selectionHint}>
                  {selectedSources.size === 0
                    ? "Click a field to map 1:1 · Shift-click multiple for many:1"
                    : `${selectedSources.size} selected — `}
                  {selectedSources.size >= 2 && (
                    <button style={S.applyBtn} onClick={applyManyToOne}>
                      Apply many:1 →
                    </button>
                  )}
                </div>
              )}

              {/* Source item list */}
              <div style={S.itemList}>
                {sourceForm?.item_groups.map(group => {
                  const groupItems = filteredSourceItems.filter(i => i.groupOid === group.oid);
                  if (!groupItems.length) return null;
                  return (
                    <div key={group.oid}>
                      <div style={S.groupHeader}>
                        {group.name}
                        {group.repeating && <span style={{ fontSize: 9, color: "var(--oc-purple)", marginLeft: 4 }}>REPEAT</span>}
                      </div>
                      {groupItems.map(item => {
                        const isUsed = Object.values(mappings).some(m => m.sources?.includes(item.oid));
                        const isSelected = selectedSources.has(item.oid);
                        return (
                          <div
                            key={item.oid}
                            style={{
                              ...S.sourceItem,
                              background: isSelected ? "var(--oc-blue-light)" : isUsed ? "var(--oc-blue-pale)" : "#fff",
                              borderLeft: isSelected ? "3px solid var(--oc-purple)" : isUsed ? "3px solid var(--oc-blue)" : "3px solid transparent",
                            }}
                            onClick={() => {
                              if (!selectedTarget) return;
                              if (selectedSources.size > 0) {
                                toggleSourceSelect(item.oid);
                              } else {
                                applyOneToOne(item.oid);
                              }
                            }}
                            onContextMenu={e => {
                              e.preventDefault();
                              toggleSourceSelect(item.oid);
                            }}
                          >
                            <div style={S.sourceItemName}>
                              {item.name}
                              {item.cdashAlias && item.cdashAlias !== item.name && (
                                <span style={{ fontSize: 9, color: "var(--oc-purple)", marginLeft: 5 }}>{item.cdashAlias}</span>
                              )}
                              {isUsed && !isSelected && (
                                <span style={{ fontSize: 9, color: "var(--oc-green)", marginLeft: 5 }}>✓</span>
                              )}
                            </div>
                            <div style={S.sourceItemLabel}>{item.label}</div>
                            <div style={S.sourceItemMeta}>
                              {item.dataType}
                              {item.length && ` · len:${item.length}`}
                              {item.mandatory && <span style={{ color: "var(--oc-red)", marginLeft: 4 }}>*</span>}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  );
                })}
              </div>
            </>
          )}
        </div>

        {/* ── CENTER: Mapping detail ───────────────────────────────────── */}
        <div style={S.centerPanel}>
          <div style={S.panelHeader}>
            <span style={S.panelTitle}>MAPPING</span>
          </div>

          {!selectedTarget ? (
            <div style={S.noSelection}>
              <div style={{ fontSize: 24, marginBottom: 8 }}>↔</div>
              <div style={{ fontSize: 12, color: "var(--text-muted)" }}>Select a target field</div>
            </div>
          ) : (
            <div style={S.mappingDetail}>
              {/* Target field info */}
              <div style={S.mappingTarget}>
                <div style={{ fontSize: 10, color: "var(--text-light)", marginBottom: 3, fontWeight: 600, letterSpacing: ".06em" }}>TARGET FIELD</div>
                <div style={{ fontFamily: "monospace", fontSize: 13, color: "var(--oc-blue)", fontWeight: 600 }}>
                  {activeRow?.name}
                </div>
                <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>{activeRow?.label}</div>
                <div style={{ fontSize: 10, color: "var(--text-light)", marginTop: 2 }}>
                  {activeRow?.type} · {activeRow?.bind__oc_itemgroup}
                </div>
              </div>

              {/* Mapping type selector */}
              <div style={{ padding: "10px 12px", borderBottom: "1px solid var(--border)" }}>
                <div style={{ fontSize: 10, color: "var(--text-light)", marginBottom: 6, fontWeight: 600, letterSpacing: ".06em" }}>RELATIONSHIP TYPE</div>
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                  {Object.entries(TYPE_LABELS).map(([type, label]) => (
                    <button
                      key={type}
                      style={{
                        ...S.typeBtn,
                        background: activeMapping?.type === type ? TYPE_COLORS[type] + "22" : "#fff",
                        borderColor: activeMapping?.type === type ? TYPE_COLORS[type] : "var(--border)",
                        color: activeMapping?.type === type ? TYPE_COLORS[type] : "var(--text-muted)",
                      }}
                      onClick={() => setMappingType(type)}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>

              {/* Source fields in this mapping */}
              <div style={{ padding: "10px 12px", borderBottom: "1px solid var(--border)" }}>
                <div style={{ fontSize: 10, color: "var(--text-light)", marginBottom: 6, fontWeight: 600, letterSpacing: ".06em" }}>
                  SOURCE FIELDS ({activeSources.length})
                </div>
                {activeSources.length === 0 ? (
                  <div style={{ fontSize: 11, color: "var(--text-light)", fontStyle: "italic" }}>
                    {noSource ? "No ODM loaded" : "Click a source field to assign"}
                  </div>
                ) : (
                  <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                    {activeSources.map((src, i) => (
                      <div key={src.oid} style={S.srcChip}>
                        <span style={{ fontFamily: "monospace", fontSize: 11, color: "var(--oc-blue)" }}>
                          {src.name}
                        </span>
                        <span style={{ fontSize: 10, color: "var(--text-muted)", marginLeft: 6 }}>
                          {src.formName} / {src.groupName}
                        </span>
                        <button style={S.removeBtn} onClick={() => removeSource(src.oid)}>✕</button>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Expression editor — shown for many:1, 1:many, or when forced */}
              {(activeMapping?.type === MAPPING_TYPES.MANY_TO_ONE ||
                activeMapping?.type === MAPPING_TYPES.ONE_TO_MANY ||
                showExpr) && (
                <ExpressionEditor
                  mapping={activeMapping}
                  sourceItems={activeSources}
                  onUpdate={updated => onUpdateMapping(selectedTarget, updated)}
                />
              )}

              {/* Show expression toggle for 1:1 */}
              {activeMapping?.type === MAPPING_TYPES.ONE_TO_ONE && !showExpr && (
                <div style={{ padding: "8px 12px", borderBottom: "1px solid var(--border)" }}>
                  <button style={S.linkBtn} onClick={() => setShowExpr(true)}>
                    + Add transformation expression
                  </button>
                </div>
              )}

              {/* Transform panel — always shown for active mapping */}
              {activeMapping && (
                <TransformPanel
                  mapping={activeMapping}
                  targetField={activeRow?.name || ""}
                  onUpdateMapping={updated => onUpdateMapping(selectedTarget, updated)}
                />
              )}

              {/* Notes */}
              <div style={{ padding: "10px 12px", borderBottom: "1px solid var(--border)" }}>
                <div style={{ fontSize: 10, color: "var(--text-light)", marginBottom: 4, fontWeight: 600, letterSpacing: ".06em" }}>NOTES</div>
                <textarea
                  style={S.notesInput}
                  value={activeMapping?.notes || ""}
                  onChange={e => onUpdateMapping(selectedTarget, { ...activeMapping, notes: e.target.value })}
                  placeholder="Mapping rationale, data issues, instructions for build…"
                  rows={3}
                />
              </div>

              {/* Validation errors */}
              {(() => {
                const errs = validateMapping(activeMapping);
                return errs.length > 0 ? (
                  <div style={{ padding: "8px 12px", borderBottom: "1px solid var(--border)" }}>
                    {errs.map((e, i) => (
                      <div key={i} style={{ fontSize: 10, color: "var(--oc-red)", marginBottom: 2 }}>⚠ {e}</div>
                    ))}
                  </div>
                ) : null;
              })()}

              {/* Actions */}
              <div style={{ padding: "10px 12px", display: "flex", gap: 8, flexWrap: "wrap" }}>
                <button
                  style={{ ...S.actionBtn, background: "var(--oc-green-light)", color: "var(--oc-green)", border: "1px solid var(--oc-green)" }}
                  onClick={markReviewed}
                >
                  {activeMapping?.reviewed ? "✓ Reviewed" : "Mark reviewed"}
                </button>
                <button
                  style={{ ...S.actionBtn, color: "var(--text-muted)", border: "1px solid var(--border)" }}
                  onClick={clearMapping}
                >
                  Clear mapping
                </button>
              </div>
            </div>
          )}
        </div>

        {/* ── RIGHT: Target panel ──────────────────────────────────────── */}
        <div style={S.targetPanel}>
          <div style={S.panelHeader}>
            <span style={S.panelTitle}>
              TARGET
              <span style={S.panelSub}> — OC4 / {form.form_id}</span>
            </span>
          </div>

          {/* Target toolbar */}
          <div style={{ padding: "6px 10px", borderBottom: "1px solid var(--border)", background: "#fff", display: "flex", gap: 6, flexWrap: "wrap" }}>
            <input
              style={{ ...S.searchInput, flex: 1 }}
              placeholder="Search target fields…"
              value={targetSearch}
              onChange={e => setTargetSearch(e.target.value)}
            />
            {["ALL","MAPPED","UNMAPPED","ISSUES"].map(f => (
              <button
                key={f}
                style={{
                  ...S.filterBtn,
                  background: targetFilter === f ? "var(--oc-blue-light)" : "#fff",
                  borderColor: targetFilter === f ? "var(--oc-blue)" : "var(--border)",
                  color: targetFilter === f ? "var(--oc-blue)" : "var(--text-muted)",
                }}
                onClick={() => setTargetFilter(f)}
              >
                {f}
              </button>
            ))}
          </div>

          {/* Target field list */}
          <div style={S.itemList}>
            {targetRows.map(row => {
              const key = `${form.form_id}::${row.name}`;
              const m   = mappings[key];
              const isActive = selectedTarget === key;
              const mType = m?.type || MAPPING_TYPES.NEW;
              const color = TYPE_COLORS[mType];
              const errs  = validateMapping(m);
              return (
                <div
                  key={key}
                  style={{
                    ...S.targetItem,
                    background: isActive ? "var(--oc-blue-light)" : "#fff",
                    borderLeft: `3px solid ${isActive ? "var(--oc-blue)" : color}`,
                  }}
                  onClick={() => selectTarget(key)}
                >
                  <div style={S.targetItemTop}>
                    <span style={{ fontFamily: "monospace", fontSize: 12, color: "var(--oc-blue)" }}>
                      {row.name}
                    </span>
                    <span style={{
                      fontSize: 9, fontWeight: 600, padding: "1px 5px", borderRadius: 4,
                      background: color + "22", color,
                    }}>
                      {TYPE_LABELS[mType]}
                    </span>
                  </div>
                  <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2 }}>{row.label}</div>
                  {/* Source fields summary */}
                  {m?.sources?.length > 0 && (
                    <div style={{ fontSize: 10, color: "var(--text-light)", marginTop: 3, fontFamily: "monospace" }}>
                      ← {m.sources.map(oid => {
                        // Find name for OID
                        for (const f of (sourceTree?.forms || [])) {
                          for (const g of f.item_groups) {
                            const found = g.items.find(i => i.oid === oid);
                            if (found) return found.name;
                          }
                        }
                        return oid;
                      }).join(", ")}
                    </div>
                  )}
                  {errs.length > 0 && (
                    <div style={{ fontSize: 9, color: "var(--oc-red)", marginTop: 2 }}>⚠ {errs[0]}</div>
                  )}
                  {m?.reviewed && (
                    <div style={{ fontSize: 9, color: "var(--oc-green)", marginTop: 2 }}>✓ reviewed</div>
                  )}
                </div>
              );
            })}
          </div>
        </div>

      </div>
    </div>
  );
}

// ── Styles ─────────────────────────────────────────────────────────────────
const S = {
  root: { display: "flex", flexDirection: "column", flex: 1, overflow: "hidden", minWidth: 0 },

  statsBar: {
    display: "flex", alignItems: "center", gap: 16,
    padding: "5px 14px", background: "var(--bg)",
    borderBottom: "1px solid var(--border)", flexShrink: 0, flexWrap: "wrap",
  },
  statItem: { display: "flex", alignItems: "center", gap: 5, fontSize: 11, color: "var(--text-muted)" },
  statDot: col => ({ width: 7, height: 7, borderRadius: "50%", background: col, flexShrink: 0 }),

  panels: { display: "flex", flex: 1, overflow: "hidden" },

  // ── Source panel
  sourcePanel: {
    width: 280, background: "var(--bg)",
    borderRight: "1px solid var(--border)", display: "flex",
    flexDirection: "column", overflow: "hidden", flexShrink: 0,
  },
  panelHeader: {
    padding: "8px 12px", background: "var(--bg)",
    borderBottom: "1px solid var(--border)", flexShrink: 0,
    display: "flex", alignItems: "center", justifyContent: "space-between",
  },
  panelTitle:  { fontSize: 10, fontWeight: 700, color: "var(--text-muted)", letterSpacing: ".08em", textTransform: "uppercase" },
  panelSub:    { fontSize: 10, color: "var(--oc-blue)", fontWeight: 400 },
  noSource: {
    flex: 1, display: "flex", flexDirection: "column", alignItems: "center",
    justifyContent: "center", padding: 24, textAlign: "center",
    background: "#fff",
  },
  sourceFormTabs: {
    display: "flex", overflow: "auto", borderBottom: "1px solid var(--border)",
    background: "#fff", flexShrink: 0,
  },
  sourceFormTab: {
    padding: "5px 9px", border: "none", cursor: "pointer",
    fontSize: 10, fontWeight: 500, whiteSpace: "nowrap", flexShrink: 0,
  },
  selectionHint: {
    padding: "4px 10px", background: "var(--oc-blue-pale)", fontSize: 10,
    color: "var(--text-muted)", borderBottom: "1px solid var(--border)", display: "flex",
    alignItems: "center", gap: 6, flexShrink: 0,
  },
  applyBtn: {
    padding: "2px 8px", borderRadius: 4, border: "1px solid var(--oc-purple)",
    background: "var(--oc-purple-light)", color: "var(--oc-purple)", fontSize: 10, cursor: "pointer", fontWeight: 600,
  },
  itemList: { flex: 1, overflow: "auto", background: "#fff" },
  groupHeader: {
    padding: "4px 10px", background: "var(--bg)", fontSize: 9,
    fontWeight: 700, color: "var(--text-muted)", letterSpacing: ".07em",
    textTransform: "uppercase", position: "sticky", top: 0, zIndex: 1,
    borderBottom: "1px solid var(--border)",
  },
  sourceItem: {
    padding: "7px 12px", cursor: "pointer", transition: "background .1s",
    borderBottom: "1px solid var(--border)",
  },
  sourceItemName:  { fontSize: 11, fontFamily: "monospace", color: "var(--oc-blue)", fontWeight: 500 },
  sourceItemLabel: { fontSize: 10, color: "var(--text-muted)", marginTop: 1 },
  sourceItemMeta:  { fontSize: 9,  color: "var(--text-light)", marginTop: 1 },

  // ── Center panel
  centerPanel: {
    width: 300, background: "#fff",
    borderRight: "1px solid var(--border)", display: "flex",
    flexDirection: "column", overflow: "hidden", flexShrink: 0,
  },
  noSelection: {
    flex: 1, display: "flex", flexDirection: "column", alignItems: "center",
    justifyContent: "center", color: "var(--text-muted)",
  },
  mappingDetail: { flex: 1, overflow: "auto" },
  mappingTarget: {
    padding: "10px 12px", background: "var(--bg)",
    borderBottom: "1px solid var(--border)",
  },
  typeBtn: {
    padding: "3px 9px", borderRadius: 4, border: "1px solid",
    fontSize: 10, fontWeight: 600, cursor: "pointer", letterSpacing: ".03em",
  },
  srcChip: {
    display: "flex", alignItems: "center", gap: 4,
    background: "var(--oc-blue-pale)", borderRadius: 4, padding: "4px 8px",
    border: "1px solid var(--border)",
  },
  removeBtn: {
    background: "transparent", border: "none", color: "var(--oc-red)",
    cursor: "pointer", fontSize: 11, padding: "0 2px", marginLeft: "auto",
  },
  notesInput: {
    width: "100%", background: "#fff", border: "1px solid var(--border)",
    borderRadius: 4, color: "var(--text)", fontSize: 11, padding: "6px 8px",
    resize: "vertical", fontFamily: "inherit",
  },
  actionBtn: {
    padding: "5px 12px", borderRadius: 5, background: "transparent",
    cursor: "pointer", fontSize: 11, fontWeight: 500,
  },
  linkBtn: {
    background: "transparent", border: "none", color: "var(--oc-blue)",
    fontSize: 11, cursor: "pointer", padding: 0, textDecoration: "underline",
  },

  // ── Target panel
  targetPanel: { flex: 1, background: "#fff", display: "flex", flexDirection: "column", overflow: "hidden" },
  targetItem: {
    padding: "8px 12px", cursor: "pointer", borderBottom: "1px solid var(--border)",
    transition: "background .1s",
  },
  targetItemTop: { display: "flex", alignItems: "center", justifyContent: "space-between" },

  // ── Shared
  searchInput: {
    width: "100%", padding: "4px 8px", borderRadius: 4, border: "1px solid var(--border)",
    background: "#fff", color: "var(--text)", fontSize: 11, outline: "none",
  },
  filterBtn: {
    padding: "2px 7px", borderRadius: 4, border: "1px solid",
    fontSize: 9, fontWeight: 600, cursor: "pointer",
  },
};
