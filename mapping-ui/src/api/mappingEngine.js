// src/api/mappingEngine.js
// Core mapping relationship logic

export const MAPPING_TYPES = {
  ONE_TO_ONE:  "1:1",
  MANY_TO_ONE: "many:1",
  ONE_TO_MANY: "1:many",
  UNMAPPED:    "unmapped",  // source field with no target
  NEW:         "new",       // target field with no source
};

export const EXPR_MODES = {
  TEMPLATE: "template",
  XPATH:    "xpath",
};

/**
 * Create a default 1:1 mapping from a source item OID to a target field name.
 */
export function createMapping(type, sourceOids = [], expression = "", mode = EXPR_MODES.TEMPLATE, notes = "") {
  return {
    type,
    sources: sourceOids,          // array of source item OIDs
    expression,                    // template string or XPath
    expression_mode: mode,
    notes,
    reviewed: false,
    reviewed_by: "",
    reviewed_at: "",
  };
}

/**
 * Build initial mappings by matching source item names/OIDs to target field names.
 * Called when ODM is loaded alongside an existing spec.
 * Returns a mappings object keyed by target field name:
 * { [targetFieldName]: MappingObject }
 */
export function buildInitialMappings(sourceTree, spec) {
  const mappings = {};
  if (!sourceTree || !spec) return mappings;

  // Build TWO lookups:
  //   - perFormNameToOid: maps formName.upper → { fieldName.upper: oid }
  //     so SUBJID in target form DM resolves to DM.SUBJID, not VS.SUBJID
  //   - globalNameToOid: cross-form fallback when no same-form match exists.
  //     First-wins so behavior is stable regardless of FormDef parse order.
  const perFormNameToOid = {};
  const globalNameToOid  = {};
  (sourceTree.forms || []).forEach(form => {
    const formKey = (form.name || "").toUpperCase();
    if (!perFormNameToOid[formKey]) perFormNameToOid[formKey] = {};
    (form.item_groups || []).forEach(group => {
      (group.items || []).forEach(item => {
        const nameKey = item.name.toUpperCase();
        perFormNameToOid[formKey][nameKey] = item.oid;
        if (!(nameKey in globalNameToOid)) globalNameToOid[nameKey] = item.oid;
        if (item.cdashAlias) {
          const aliasKey = item.cdashAlias.toUpperCase();
          perFormNameToOid[formKey][aliasKey] = item.oid;
          if (!(aliasKey in globalNameToOid)) globalNameToOid[aliasKey] = item.oid;
        }
      });
    });
  });

  (spec.forms || []).forEach(form => {
    const targetFormKey = (form.form_id || "").toUpperCase();
    const sameFormMap   = perFormNameToOid[targetFormKey] || {};
    (form.survey || []).forEach(row => {
      const targetName  = row.name;
      const sourceField = row.source_field || "";
      const formId      = form.form_id;

      // Same-form match takes priority; fall back to any-form match.
      const matchedOid =
        sameFormMap[sourceField.toUpperCase()]   ||
        sameFormMap[targetName.toUpperCase()]    ||
        globalNameToOid[sourceField.toUpperCase()] ||
        globalNameToOid[targetName.toUpperCase()]  ||
        null;

      // Detect many-to-one split-date pattern
      const splitDateTargets = { AESTDAT: ["AE.AESTDAT_YR","AE.AESTDAT_MON","AE.AESTDAT_DAY"], AEENDAT: ["AE.AEENDAT_YR","AE.AEENDAT_MON","AE.AEENDAT_DAY"] };
      const splitSources = splitDateTargets[`${targetName}`] || null;

      let mapping;
      if (splitSources) {
        mapping = createMapping(
          MAPPING_TYPES.MANY_TO_ONE,
          splitSources,
          "concat(lpad({AESTDAT_YR},4,'0'), '-', lpad({AESTDAT_MON},2,'0'), '-', lpad({AESTDAT_DAY},2,'0'))",
          EXPR_MODES.TEMPLATE,
          "Source stores this date as three separate integer fields (year/month/day). OC4 expects ISO 8601."
        );
        // Add AI-proposed transforms
        mapping.transformations = [
          {
            id: `t_${formId.toLowerCase()}_${targetName.toLowerCase()}_1`,
            type: "split_date_combine",
            proposed_by: "RULE",
            confidence: 0.98,
            status: "PENDING",
            config: {
              year_field: targetName + "_YR",
              month_field: targetName + "_MON",
              day_field: targetName + "_DAY",
              output_format: "YYYY-MM-DD",
              unk_token: "UNK",
              unknown_year_action: "blank_record",
              unknown_month_action: "use_partial",
              unknown_day_action: "use_partial",
              pad_month: true, pad_day: true,
            },
            rationale: "Source stores this date as three separate integer fields (" + targetName + "_YR, " + targetName + "_MON, " + targetName + "_DAY). OC4 expects a single ISO 8601 date. The pipeline will combine these as YYYY-MM-DD. Values of 'UNK' in any component will apply partial date logic — unknown year halts, unknown month/day produces a partial date.",
            exception_action: "HALT",
            exception_default: "",
            dm_note: "",
            applies_to_migration: true,
            applies_to_build: false,
          }
        ];
      } else if (matchedOid) {
        mapping = createMapping(MAPPING_TYPES.ONE_TO_ONE, [matchedOid], `{${sourceField || targetName}}`, EXPR_MODES.TEMPLATE, "");
        // Add date transforms for date fields
        if (row.type === "date") {
          mapping.transformations = [
            {
              id: `t_${formId.toLowerCase()}_${targetName.toLowerCase()}_1`,
              type: "partial_date",
              proposed_by: "RULE",
              confidence: 1.0,
              status: "PENDING",
              config: { vendor: "medidata_rave", unk_token: "UNK", unknown_year_action: "blank_record", unknown_month_action: "use_partial", unknown_day_action: "use_partial", output_format: "YYYY-MM-DD" },
              rationale: "Medidata Rave allows 'UNK' in date components. OC4 requires ISO 8601 (YYYY-MM-DD) or blank. Records with unknown year cannot be automatically resolved — DM decision required. Unknown month or day will produce a partial date (e.g. 2024 or 2024-03).",
              exception_action: "HALT", exception_default: "", dm_note: "", applies_to_migration: true, applies_to_build: false,
            },
            {
              id: `t_${formId.toLowerCase()}_${targetName.toLowerCase()}_2`,
              type: "date_format",
              proposed_by: "RULE",
              confidence: 0.95,
              status: "PENDING",
              config: { from_formats: ["DD-MON-YYYY", "YYYY-MM-DD", "DD/MM/YYYY"], to_format: "YYYY-MM-DD", try_multiple_formats: true },
              rationale: "Source date format (DD-MON-YYYY) may differ from OC4 target format (YYYY-MM-DD). All date values will be parsed and reformatted. Values that cannot be parsed will halt.",
              exception_action: "HALT", exception_default: "", dm_note: "", applies_to_migration: true, applies_to_build: false,
            }
          ];
        }
        // Add codelist transforms for select fields with mismatched values
        if (row.type === "select" && (targetName === "AESEV" || targetName === "CMDOSU")) {
          mapping.transformations = [
            {
              id: `t_${formId.toLowerCase()}_${targetName.toLowerCase()}_1`,
              type: "codelist_map",
              proposed_by: "AI",
              confidence: targetName === "AESEV" ? 0.95 : 0.45,
              status: "PENDING",
              config: {
                mappings: targetName === "AESEV" ? { MILD: "MILD", MODERATE: "MODERATE", SEVERE: "SEVERE" } : {},
                unmapped_source_values: targetName === "CMDOSU" ? ["mg", "mcg", "g", "mL", "IU"] : [],
                unmapped_action: "HALT",
                source_codelist: targetName === "AESEV" ? [{code:"MILD",label:"Mild"},{code:"MODERATE",label:"Moderate"},{code:"SEVERE",label:"Severe"}] : [],
                target_codelist: targetName === "AESEV" ? [{code:"MILD",label:"Mild"},{code:"MODERATE",label:"Moderate"},{code:"SEVERE",label:"Severe"}] : [],
              },
              rationale: targetName === "AESEV"
                ? "3/3 source codelist values automatically matched to OC4. All values matched. Review the mapping table to confirm each match."
                : "Target OC4 field has no codelist defined. Source codelist has 5 values. Passthrough assumed but DM must define or confirm the OC4 codelist before migration can run.",
              exception_action: "HALT", exception_default: "", dm_note: "", applies_to_migration: true, applies_to_build: false,
            }
          ];
        }
      } else if (row.completion_status === "PLACEHOLDER" || !sourceField) {
        mapping = createMapping(MAPPING_TYPES.NEW, [], "", EXPR_MODES.TEMPLATE, "No source field identified — will be entered manually in OC4");
      } else {
        mapping = createMapping(MAPPING_TYPES.ONE_TO_ONE, [], "", EXPR_MODES.TEMPLATE, `Source field: ${sourceField}`);
      }

      mappings[`${form.form_id}::${targetName}`] = mapping;
    });
  });

  return mappings;
}

/**
 * Generate a template expression string from source OIDs.
 * e.g. [{name:"YEAR"}, {name:"MONTH"}] → "{YEAR}-{MONTH}"
 */
export function buildTemplateFromSources(sourceItems) {
  if (!sourceItems?.length) return "";
  if (sourceItems.length === 1) return `{${sourceItems[0].name}}`;
  return sourceItems.map(i => `{${i.name}}`).join(" + ");
}

/**
 * Render a template expression with actual values for preview.
 * e.g. "{YEAR}-{MONTH}-{DAY}" with {YEAR:"2024", MONTH:"01", DAY:"15"} → "2024-01-15"
 */
export function renderTemplate(expression, values = {}) {
  return expression.replace(/\{(\w+)\}/g, (_, name) => values[name] ?? `[${name}]`);
}

/**
 * Validate a mapping object. Returns array of error strings (empty = valid).
 */
export function validateMapping(mapping) {
  const errors = [];
  if (!mapping) return ["No mapping defined"];

  if (mapping.type === MAPPING_TYPES.ONE_TO_ONE) {
    if (!mapping.sources?.length) errors.push("No source field selected");
  }

  if (mapping.type === MAPPING_TYPES.MANY_TO_ONE) {
    if (!mapping.sources?.length || mapping.sources.length < 2)
      errors.push("Many-to-one requires at least 2 source fields");
    if (!mapping.expression?.trim())
      errors.push("Expression is required for many-to-one mapping");
  }

  if (mapping.type === MAPPING_TYPES.ONE_TO_MANY) {
    if (!mapping.sources?.length) errors.push("No source field selected");
    if (!mapping.expression?.trim())
      errors.push("Parse expression is required for one-to-many mapping");
  }

  return errors;
}

/**
 * Count mapping stats across all forms.
 */
export function getMappingStats(mappings) {
  const stats = {
    total: 0,
    oneToOne: 0,
    manyToOne: 0,
    oneToMany: 0,
    unmapped: 0,
    newField: 0,
    reviewed: 0,
    unreviewed: 0,
  };

  Object.values(mappings || {}).forEach(m => {
    stats.total++;
    if (m.type === MAPPING_TYPES.ONE_TO_ONE)  stats.oneToOne++;
    if (m.type === MAPPING_TYPES.MANY_TO_ONE) stats.manyToOne++;
    if (m.type === MAPPING_TYPES.ONE_TO_MANY) stats.oneToMany++;
    if (m.type === MAPPING_TYPES.UNMAPPED)    stats.unmapped++;
    if (m.type === MAPPING_TYPES.NEW)         stats.newField++;
    if (m.reviewed) stats.reviewed++;
    else stats.unreviewed++;
  });

  return stats;
}

/**
 * Merge mappings back into the spec so each survey row carries its mapping.
 * Returns updated spec with mapping objects on each row.
 */
export function mergeIntoSpec(spec, mappings) {
  const next = JSON.parse(JSON.stringify(spec));
  next.forms.forEach(form => {
    form.survey.forEach(row => {
      const key = `${form.form_id}::${row.name}`;
      if (mappings[key]) {
        row.mapping = mappings[key];
      }
    });
  });
  return next;
}
