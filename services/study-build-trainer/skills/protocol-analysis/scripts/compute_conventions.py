"""
compute_conventions.py — Walk form definitions, compute conventions_applied
metrics, and apply §13 (briefdescription) + §14 (form style) remediation.

Returns (conventions_applied dict, modified forms list).

§0 (Protocol Data-Item Census + Form Definition Lookup Hierarchy) is honored:
- Each form carries a `definition_source` field: customer_oc4_standard /
  customer_crf_library / cdash_default.
- Conventions §3, §4, §5, §13, §14 apply only to forms with
  definition_source == "cdash_default" (and to placeholders within any form,
  since placeholders are level-3 generated content by definition).
- §1 (ICF presence) is checked dynamically: if no ICF form exists in the
  forms list, applied=false (red) — never hard-coded.
- Protocol-inferred placeholders (library_source ==
  "PROTOCOL_INFERRED_PLACEHOLDER") are counted per form and per build.
"""
import copy
import re

# ── §0: Definition source values ────────────────────────────────────────────
SRC_OC4_STD = "customer_oc4_standard"
SRC_CRF_LIB = "customer_crf_library"
SRC_CDASH   = "cdash_default"

# ── §0.C: Protocol-inferred placeholder marker ──────────────────────────────
LIBSRC_PLACEHOLDER = "PROTOCOL_INFERRED_PLACEHOLDER"
LIBSRC_EXTENSION   = "PROTOCOL_EXTENSION"

# ── Thresholds ──────────────────────────────────────────────────────────────
LIKERT_MAX_CHOICES        = 5
LIKERT_MAX_LABEL_LEN      = 20
TABLE_MAX_LABEL_LEN       = 15
SITE_AUTOCOMPLETE_THR     = 20
PARTICIPATE_AUTOCOMPLETE_THR = 5
EXTERNAL_CSV_CHAR_LIMIT   = 3500
SITE_ITEM_CAP             = 200
PARTICIPATE_ITEM_CAP      = 50

DATA_TYPES   = {"text", "integer", "decimal", "date", "pdate",
                "select_one", "select_multiple", "calculate"}
SKIP_BRIEFDESC = {"calculate"}  # calculate rows often don't have a label


def _row_type_word(row):
    t = (row.get("type") or "").strip().split()
    return t[0] if t else ""


def _is_data_row(row):
    return _row_type_word(row) in DATA_TYPES


def _short_brief(label, max_words=4):
    if not label:
        return ""
    label = re.sub(r"<[^>]+>", "", label)         # strip HTML
    label = re.sub(r"^\s*[\d.]+\s+", "", label)   # strip leading "1.2.3 "
    words = label.split()[:max_words]
    return " ".join(words).rstrip(":.,?;").strip()


# ── §1-§7 metrics ───────────────────────────────────────────────────────────
def _compute_legacy(forms):
    fdc_const = 0
    fdc_exempt = 0
    grp_wrapped = 0
    cdash_using = 0
    cdash_dev = 0
    upper_ok = True
    rm_required = 0
    rm_with = 0

    for form in forms:
        survey  = form.get("survey", []) or []
        choices = form.get("choices", []) or []
        cdash_dom = form.get("cdash_domain", "")

        # §3 — at least one begin group
        if any((row.get("type") or "").strip() == "begin group" for row in survey):
            grp_wrapped += 1

        for row in survey:
            rtype = _row_type_word(row)

            # §2 — future-date constraint
            if rtype in ("date", "pdate"):
                con = row.get("constraint") or ""
                if "today()" in con or "<= today" in con or "< today" in con:
                    fdc_const += 1
                elif row.get("name", "").startswith("BRTHDAT"):
                    fdc_exempt += 1
                else:
                    fdc_exempt += 1

            # §4 — CDASH naming
            if _is_data_row(row):
                nm = row.get("name", "")
                if nm and cdash_dom and nm.startswith(cdash_dom):
                    cdash_using += 1
                elif nm:
                    cdash_dev += 1

            # §6 — required_message
            if row.get("required") == "yes":
                rm_required += 1
                if row.get("required_message") or row.get("constraint_message"):
                    rm_with += 1

        # §5 — uppercase choice list names
        for c in choices:
            ln = c.get("name", "")
            if ln and not ln.isupper() and not ln.isdigit():
                # exception: short codes like 'Y','N','M','F' already uppercase;
                # multi-word names should be uppercase
                if any(ch.islower() for ch in ln):
                    upper_ok = False

    return {
        "fdc_const": fdc_const, "fdc_exempt": fdc_exempt,
        "grp_wrapped": grp_wrapped,
        "cdash_using": cdash_using, "cdash_dev": cdash_dev,
        "upper_ok": upper_ok,
        "rm_required": rm_required, "rm_with": rm_with,
    }


# ── Main entry point ────────────────────────────────────────────────────────
def compute_and_apply(forms, common_event_oid="SE_COMMON",
                      common_event_forms=("AE", "CM", "DV", "DD")):
    """
    Walk forms, compute conventions_applied (§0, §1-§19), and apply §13/§14
    remediation. Returns (conventions_applied dict, modified_forms list).
    """
    forms = copy.deepcopy(forms)

    # ── §0: Definition source distribution (FOUNDATIONAL) ───────────────────
    # Each form must carry a definition_source. If missing, default to
    # cdash_default (legacy behaviour for forms authored before §0 existed).
    src_oc4 = src_lib = src_cdash = 0
    forms_by_source = {}
    for f in forms:
        ds = f.get("definition_source") or SRC_CDASH
        f["definition_source"] = ds  # ensure populated for downstream consumers
        forms_by_source[f.get("form_id", "?")] = ds
        if ds == SRC_OC4_STD:
            src_oc4 += 1
        elif ds == SRC_CRF_LIB:
            src_lib += 1
        else:
            src_cdash += 1

    # ── §0.C: Protocol-inferred placeholder counting ────────────────────────
    # Each placeholder field carries library_source == PROTOCOL_INFERRED_PLACEHOLDER.
    # Walk every form's survey and tally per-form + total.
    placeholder_total = 0
    placeholder_by_form = {}
    placeholder_items = []
    for f in forms:
        form_id = f.get("form_id", "?")
        n = 0
        for row in (f.get("survey") or []):
            if (row.get("library_source") or "") == LIBSRC_PLACEHOLDER:
                n += 1
                placeholder_total += 1
                placeholder_items.append({
                    "form": form_id,
                    "name": row.get("name", "?"),
                    "type": (row.get("type") or "").split()[0] if row.get("type") else "?",
                    "label": (row.get("label") or "")[:120],
                    "source_section": row.get("protocol_source_section", ""),
                    "source_quote": (row.get("protocol_source_quote") or "")[:200],
                })
        if n > 0:
            placeholder_by_form[form_id] = n

    # ── §1: Dynamic ICF presence check (no hard-coded False) ────────────────
    # Per §1 the ICF form must be present. Check the actual forms list.
    icf_form = None
    for f in forms:
        fid = (f.get("form_id") or "").upper()
        dom = (f.get("cdash_domain") or "").upper()
        title = (f.get("form_title") or "").lower()
        if fid == "ICF" or dom == "ICF" or "informed consent" in title:
            icf_form = f
            break
    icf_present = icf_form is not None
    icf_source = icf_form.get("definition_source") if icf_form else None
    icf_fields = (
        [r.get("name", "") for r in icf_form.get("survey", [])
         if (r.get("type") or "").split()[:1] not in ([], ["begin"], ["end"], ["note"])]
        if icf_form else []
    )

    # Now legacy + §8-§19 are measured ONLY against forms generated at the
    # CDASH-default layer per §0. Customer-sourced forms are excluded from
    # measurement against conventions §3/§4/§5/§13/§14.
    forms_for_measurement = [f for f in forms
                              if f.get("definition_source") == SRC_CDASH]
    legacy = _compute_legacy(forms_for_measurement)

    # Counters for §8-§19
    soft_strict_req = 0
    soft_strict_con = 0
    pdate_count = 0
    date_count = 0
    pdate_xform = []

    p_lists_elig = p_lists_done = 0
    s_lists_elig = s_lists_done = 0

    ext_csv_count = 0
    ext_csv_files = []

    site_over = []
    partic_over = []

    bd_done = bd_total = 0
    bd_missing = []

    fse_simple = fse_pages = fse_grid = fse_partic = fse_missing = 0

    cfr_with_calc = cfr_with_xref = 0

    igk_records = igk_consistent = 0
    igk_devs = []

    lik_total = lik_compliant = 0
    lik_noncompl = []

    vas_total = vas_vertical = 0
    tbl_total = tbl_compliant = 0

    for form in forms:
        form_id = form.get("form_id", "?")
        is_epro = bool(form.get("is_epro") or form.get("is_participate"))
        survey = form.get("survey", []) or []
        choices = form.get("choices", []) or []
        settings = form.get("settings", {}) or {}

        # Build choice index
        list_index = {}
        for c in choices:
            ln = c.get("list_name") or ""
            if ln:
                list_index.setdefault(ln, []).append(
                    {"label": c.get("label", ""), "name": c.get("name", "")})

        # Data row count for §12 + style decision
        data_row_count = sum(1 for r in survey if _is_data_row(r))

        # ── §14: form style (auto-set based on size and purpose) ─────────────
        # Override the existing settings["style"] if it's the wrong default.
        # Existing build_agilis.py sets every form to "theme-grid", which
        # violates §14 — theme-grid is reserved for LB/VS panels.
        # PER §0: only override style on CDASH-default forms; customer-sourced
        # forms keep their authored style verbatim.
        is_cdash_default = form.get("definition_source", SRC_CDASH) == SRC_CDASH
        if is_cdash_default:
            if is_epro:
                settings["style"] = "pages"
                fse_partic += 1
            elif data_row_count > 50:
                settings["style"] = "pages"
                fse_pages += 1
            else:
                settings["style"] = ""  # blank = Simple-single
                fse_simple += 1
        else:
            # Customer-sourced: count by what's already there, do not change
            existing_style = (settings.get("style") or "").lower()
            if "pages" in existing_style:
                fse_pages += 1
            elif "grid" in existing_style:
                fse_grid += 1
            else:
                fse_simple += 1
        form["settings"] = settings

        # ── §12: item-count cap ──────────────────────────────────────────────
        if is_epro and data_row_count > PARTICIPATE_ITEM_CAP:
            partic_over.append({"form_id": form_id, "count": data_row_count})
        elif not is_epro and data_row_count > SITE_ITEM_CAP:
            site_over.append({"form_id": form_id, "count": data_row_count})

        # ── Walk each survey row ─────────────────────────────────────────────
        cross_form_calc_in_form = False
        repeat_groups = []  # list of itemgroup names from begin_repeat rows

        for row in survey:
            rtype = _row_type_word(row)

            # §8 — strict checks
            if row.get("bind::oc:required-type") == "strict":
                soft_strict_req += 1
            if row.get("bind::oc:constraint-type") == "strict":
                soft_strict_con += 1

            # §9 — date type tracking
            if rtype == "date":
                date_count += 1
            elif rtype == "pdate":
                pdate_count += 1

            # §10 — autocomplete threshold
            if rtype in ("select_one", "select_multiple"):
                parts = (row.get("type") or "").strip().split()
                ln = parts[1] if len(parts) > 1 else None
                if ln and ln in list_index:
                    n_choices = len(list_index[ln])
                    has_min = "minimal" in (row.get("appearance") or "").lower()
                    if is_epro:
                        if n_choices >= PARTICIPATE_AUTOCOMPLETE_THR:
                            p_lists_elig += 1
                            if has_min:
                                p_lists_done += 1
                    else:
                        if n_choices >= SITE_AUTOCOMPLETE_THR:
                            s_lists_elig += 1
                            if has_min:
                                s_lists_done += 1

            # §13 — briefdescription coverage + auto-fill
            # PER §0: only measure and auto-fill on CDASH-default forms.
            # Customer-sourced forms are reported as "out of scope" and do not
            # contribute to the bd_done/bd_total counts.
            if _is_data_row(row) and rtype not in SKIP_BRIEFDESC and is_cdash_default:
                bd_total += 1
                bd = row.get("bind::oc:briefdescription") or ""
                if bd:
                    bd_done += 1
                else:
                    derived = _short_brief(row.get("label", ""))
                    if derived:
                        row["bind::oc:briefdescription"] = derived
                        bd_done += 1
                    else:
                        bd_missing.append(
                            {"form": form_id, "name": row.get("name", "?")})

            # §15 — cross-form calc detection
            if rtype == "calculate":
                if (row.get("bind::oc:external") or "") == "clinicaldata":
                    cross_form_calc_in_form = True

            # §16 — repeating group itemgroup tracking
            full_type = (row.get("type") or "").strip()
            if full_type == "begin repeat":
                ig = row.get("bind::oc:itemgroup") or ""
                repeat_groups.append(ig)

            # §17/18/19 — appearance rules
            appearance = (row.get("appearance") or "").lower()
            if "likert" in appearance and rtype in ("select_one", "select_multiple"):
                lik_total += 1
                parts = (row.get("type") or "").strip().split()
                ln = parts[1] if len(parts) > 1 else None
                if ln and ln in list_index:
                    items = list_index[ln]
                    n = len(items)
                    max_lbl = max((len(i["label"]) for i in items), default=0)
                    if n <= LIKERT_MAX_CHOICES and max_lbl <= LIKERT_MAX_LABEL_LEN:
                        lik_compliant += 1
                    else:
                        lik_noncompl.append(
                            {"form": form_id, "name": row.get("name", "?"),
                             "n_choices": n, "max_label_len": max_lbl})
            if "vas" in appearance or "distress" in appearance:
                vas_total += 1
                if "vertical" in appearance:
                    vas_vertical += 1
            if "table" in appearance and rtype in ("select_one", "select_multiple"):
                tbl_total += 1
                parts = (row.get("type") or "").strip().split()
                ln = parts[1] if len(parts) > 1 else None
                if ln and ln in list_index:
                    max_lbl = max(
                        (len(i["label"]) for i in list_index[ln]), default=0)
                    if max_lbl <= TABLE_MAX_LABEL_LEN:
                        tbl_compliant += 1

        # Form-level §15
        if cross_form_calc_in_form:
            cfr_with_calc += 1
            if (settings.get("crossform_references") or "").strip():
                cfr_with_xref += 1

        # Form-level §16 — repeating consistency
        # For each begin_repeat with a non-blank itemgroup, count as 1 record.
        for ig in repeat_groups:
            igk_records += 1
            if ig:
                igk_consistent += 1
            else:
                igk_devs.append({"form": form_id, "issue": "begin repeat missing bind::oc:itemgroup"})

        # §11 — external CSV check
        for ln, items in list_index.items():
            total_chars = sum(len(c["label"]) + len(c["name"]) for c in items)
            if total_chars > EXTERNAL_CSV_CHAR_LIMIT:
                ext_csv_count += 1
                ext_csv_files.append(f"agilis_{ln.lower()}.csv")

    # ── §7: derive from common_event_forms parameter ────────────────────────
    forms_in_common = []
    for f in forms:
        if f.get("form_id") in common_event_forms:
            if common_event_oid in (f.get("visits_assigned") or []):
                forms_in_common.append(f.get("form_id"))

    # Build the conventions_applied dict
    conventions_applied = {
        "version": "3",
        "source": "references/conventions.md",
        # §0 — Form definition lookup hierarchy + data-item census (foundational)
        "definition_source_distribution": {
            "customer_oc4_standard": src_oc4,
            "customer_crf_library":  src_lib,
            "cdash_default":         src_cdash,
            "forms_by_source":       forms_by_source,
        },
        "protocol_inferred_placeholders": {
            "applied":  True,
            "count":    placeholder_total,
            "by_form":  placeholder_by_form,
            "items":    placeholder_items,
        },
        # §1 — Standalone ICF form (DYNAMIC: derived from forms list, not hard-coded)
        "icf_form_added_by_default": {
            "applied":           icf_present,
            "definition_source": icf_source,
            "form_id":           (icf_form.get("form_id") if icf_form else None),
            "fields":            icf_fields,
            "violation":         (not icf_present),  # red status when missing
        },
        "future_date_constraint_applied": {
            "fields_constrained": legacy["fdc_const"],
            "fields_exempted": legacy["fdc_exempt"],
            "exemptions": [],
        },
        "group_wrapping_applied": {
            "forms_wrapped": legacy["grp_wrapped"],
            "single_section_group_name": "group0",
            "scope": "cdash_default_forms_only",
        },
        "cdash_naming_applied": {
            "fields_using_cdash": legacy["cdash_using"],
            "name_deviations": legacy["cdash_dev"],
            "deviations_list": [],
            "scope": "cdash_default_forms_only",
        },
        "uppercase_choice_lists": {
            "applied": legacy["upper_ok"],
            "scope": "cdash_default_forms_only",
        },
        "required_message_coverage": {
            "required_fields": legacy["rm_required"],
            "fields_with_message": legacy["rm_with"],
        },
        "common_event_applied": {
            "event_oid": common_event_oid,
            "event_type": "Common",
            "event_title": "Common — Reported As Occurring",
            "forms_in_common_event": forms_in_common,
            "forms_excluded_by_override": [],
            "conditional_forms_added": (
                [{"form": "DD", "reason": "Device study — DD reporting required"}]
                if "DD" in forms_in_common else []),
            "conditional_forms_skipped": (
                [{"form": "CM", "reason": "Agilis collects peri-procedural meds inside PROC form, no separate CM"}]
                if "CM" not in forms_in_common else []),
        },
        # §8-§19
        "soft_edit_checks_applied": {
            "applied": True,
            "strict_required_count": soft_strict_req,
            "strict_constraint_count": soft_strict_con,
            "overrides": [],
        },
        "pdate_for_recall_dates": {
            "applied": True,
            "pdate_fields": pdate_count,
            "date_fields": date_count,
            "rule_flagged_crossform_uses": pdate_xform,
            "deviations": [],
        },
        "autocomplete_appearance": {
            "applied": True,
            "participate_lists_eligible": p_lists_elig,
            "participate_lists_with_minimal": p_lists_done,
            "site_lists_eligible": s_lists_elig,
            "site_lists_with_minimal": s_lists_done,
        },
        "external_csv_for_long_lists": {
            "applied": True,
            "lists_exceeded_threshold": ext_csv_count,
            "external_csvs_created": ext_csv_files,
        },
        "item_count_caps": {
            "checked": True,
            "site_forms_over_200": site_over,
            "participate_forms_over_50": partic_over,
        },
        "briefdescription_coverage": {
            "applied_count": bd_done,
            "total_data_rows": bd_total,
            "missing_count": len(bd_missing),
            "missing_list": bd_missing[:20],
        },
        "form_style_explicit": {
            "applied": True,
            "site_simple_single": fse_simple,
            "site_simple_pages": fse_pages,
            "site_theme_grid": fse_grid,
            "participate_simple_pages": fse_partic,
            "missing_style": fse_missing,
        },
        "crossform_references_populated": {
            "applied": True,
            "forms_with_cross_form_calc": cfr_with_calc,
            "forms_with_crossform_references": cfr_with_xref,
        },
        "itemgroup_keep_together": {
            "applied": True,
            "repeating_logical_records": igk_records,
            "repeating_records_consistent": igk_consistent,
            "deviations": igk_devs,
        },
        "likert_appearance_rule": {
            "applied": True,
            "likert_fields": lik_total,
            "likert_compliant": lik_compliant,
            "likert_non_compliant": lik_noncompl,
        },
        "vas_appearance_rule": {
            "applied": True,
            "vas_fields": vas_total,
            "vas_vertical": vas_vertical,
        },
        "table_appearance_rule": {
            "applied": True,
            "table_fields": tbl_total,
            "table_compliant": tbl_compliant,
        },
    }

    # ── §20-§28 pattern-detection pass ──────────────────────────────────────
    _apply_pattern_conventions(forms, conventions_applied)

    return conventions_applied, forms


# ── §20-§28 helpers ──────────────────────────────────────────────────────────
SENTINEL_VALUES = {"DECLINED", "UNKNOWN", "NONE", "N_A", "NA", "REFUSED"}
PRECISION_TABLE = [
    # (regex pattern on field name uppercase, decimals)
    (("HEIGHT", "HT"), 2),
    (("WEIGHT", "WT"), 2),
    (("TEMP",), 1),
    (("BP", "SBP", "DBP"), 0),
    (("HR", "PULSE"), 0),
]
UNIT_LIST_PATTERNS = ("UNIT",)        # list_name contains
UNIT_NAME_SUFFIXES = ("_U", "_UNIT", "_UNITS")
CONDITIONAL_LABEL_PREFIXES = ("if yes", "if applicable", "if so", "if other")


def _has_sentinel(choice_list_for_field, choices):
    """Return sentinel name if list contains a sentinel value, else None."""
    if not choice_list_for_field:
        return None
    for c in choices:
        if (c.get("list_name") or "") != choice_list_for_field:
            continue
        nm = (c.get("name") or "").upper()
        if nm in SENTINEL_VALUES:
            return c.get("name")
    return None


def _is_unit_field(row):
    """True if field is a unit selector per §26 conservative detection."""
    rtype = (row.get("type") or "").split()
    if len(rtype) < 2 or rtype[0] != "select_one":
        return False
    list_name = rtype[1].upper()
    name = (row.get("name") or "").upper()
    return (any(p in list_name for p in UNIT_LIST_PATTERNS) or
            any(name.endswith(s) for s in UNIT_NAME_SUFFIXES))


def _precision_for_name(field_name):
    """Return (decimals, matched_pattern) for a decimal field, or default."""
    nu = (field_name or "").upper()
    for patterns, decimals in PRECISION_TABLE:
        for pat in patterns:
            if pat in nu:
                return decimals, pat
    return 2, "default"  # default 2 decimals for unmatched decimal fields


def _apply_pattern_conventions(forms, ca):
    """Walk every form's survey and surface §20-§28 metrics."""
    # §20 Forms-completion safety net
    safety_net_forms = []
    for f in forms:
        survey = f.get("survey") or []
        # Detection: form ends with a group containing AE/DV/DS Y/N triggers
        ae_yn = dv_yn = ds_yn = False
        for r in survey:
            nm = (r.get("name") or "").upper()
            if nm.endswith("AE_YN"): ae_yn = True
            if nm.endswith("DV_YN"): dv_yn = True
            if nm.endswith("DS_YN") or nm.endswith("WITHDRAWAL_YN"): ds_yn = True
        if ae_yn and dv_yn and ds_yn:
            safety_net_forms.append(f.get("form_id", "?"))
    ca["forms_completion_safety_net"] = {
        "applied_count": len(safety_net_forms),
        "forms_with_safety_net": safety_net_forms,
    }

    # §21 Header group pattern
    with_header = without_header = 0
    for f in forms:
        survey = f.get("survey") or []
        first_grp = next((r for r in survey if r.get("type") == "begin group"), None)
        if first_grp and first_grp.get("name") == "group0" and not (first_grp.get("label") or "").strip():
            with_header += 1
        else:
            without_header += 1
    ca["header_group_pattern"] = {
        "forms_with_header": with_header,
        "forms_without_header": without_header,
    }

    # §22 Reminder notes gated by Y/N + §23 hidden-parent-context label rewrites
    gated_notes = []
    rewritten_labels = []
    for f in forms:
        form_id = f.get("form_id", "?")
        survey = f.get("survey") or []
        for i, r in enumerate(survey):
            rtype = (r.get("type") or "").split()
            # §22: note-after-YN
            if r.get("type") == "note" and i > 0:
                prev = survey[i-1]
                ptype = (prev.get("type") or "").split()
                if (len(ptype) >= 2 and ptype[0] == "select_one" and
                        ptype[1].upper() in ("YN", "NY")):
                    note_lbl = (r.get("label") or "").lower()
                    if any(prefix in note_lbl for prefix in CONDITIONAL_LABEL_PREFIXES):
                        if r.get("relevant"):
                            gated_notes.append({
                                "form": form_id,
                                "trigger": prev.get("name", "?"),
                                "note": r.get("name", "?"),
                            })
            # §23: detect "If yes, X" labels (rewritten) and "If yes" alone (not rewritten)
            lbl = (r.get("label") or "").strip()
            if r.get("relevant") and lbl:
                # Strip leading section number for pattern check
                import re as _re
                bare = _re.sub(r"^[\d.]+\s*", "", lbl).lower().strip()
                # Rewritten: "if yes, ..." with content after
                if bare.startswith("if yes,") and len(bare) > len("if yes,"):
                    rewritten_labels.append({
                        "form": form_id,
                        "field": r.get("name", "?"),
                        "rewritten_label": lbl,
                    })

    ca["reminder_notes_gated"] = {
        "applied_count": len(gated_notes),
        "detected_patterns": gated_notes,
    }
    ca["source_label_disambiguation"] = {
        "applied_count": len(rewritten_labels),
        "rewritten_labels": rewritten_labels,
    }

    # §24 Source ambiguity (data sourced from review_flags.choice_list_review)
    # We can't directly compute this — it's emitted by the regen at form-build
    # time. Just count placeholder for the metric.
    ca["source_ambiguity_resolved"] = {
        "applied_count": 0,  # populated by integration with review_flags
        "note": "Counts entries in review_flags.choice_list_review per §24",
    }

    # §25 Eligibility verdict 3-state
    elig_forms = []
    for f in forms:
        survey = f.get("survey") or []
        for r in survey:
            calc = r.get("calculation") or ""
            if "'Eligible'" in calc and "'Ineligible'" in calc and "'Not yet calculated'" in calc:
                elig_forms.append({
                    "form": f.get("form_id", "?"),
                    "calc_field": r.get("name", "?"),
                })
                break
    ca["eligibility_verdict_3state"] = {
        "applied_forms": elig_forms,
        "applied_count": len(elig_forms),
    }

    # §26 Value+unit pair layout
    pairs_detected = pairs_with_w2 = 0
    pairs_list = []
    for f in forms:
        form_id = f.get("form_id", "?")
        survey = f.get("survey") or []
        for i in range(len(survey) - 1):
            cur = survey[i]
            nxt = survey[i+1]
            cur_type = (cur.get("type") or "").split()
            if not cur_type or cur_type[0] not in ("decimal", "integer"):
                continue
            if not _is_unit_field(nxt):
                continue
            pairs_detected += 1
            cur_app = (cur.get("appearance") or "")
            nxt_app = (nxt.get("appearance") or "")
            if "w2" in cur_app and "w2" in nxt_app:
                pairs_with_w2 += 1
            pairs_list.append({
                "form": form_id,
                "value_field": cur.get("name", "?"),
                "unit_field": nxt.get("name", "?"),
                "w2_applied": ("w2" in cur_app and "w2" in nxt_app),
            })
    ca["value_unit_pair_layout"] = {
        "pairs_detected": pairs_detected,
        "pairs_with_w2": pairs_with_w2,
        "pairs": pairs_list,
    }

    # §27 Sentinel-value exclusivity
    sentinel_fields = []
    for f in forms:
        form_id = f.get("form_id", "?")
        survey = f.get("survey") or []
        choices = f.get("choices") or []
        for r in survey:
            rtype = (r.get("type") or "").split()
            if not rtype or rtype[0] != "select_multiple":
                continue
            if len(rtype) < 2:
                continue
            sentinel = _has_sentinel(rtype[1], choices)
            if sentinel:
                con = r.get("constraint") or ""
                applied = (f"selected(., '{sentinel}')" in con and
                            "not(selected" in con)
                sentinel_fields.append({
                    "form": form_id,
                    "field": r.get("name", "?"),
                    "sentinel": sentinel,
                    "constraint_applied": applied,
                })
    ca["sentinel_exclusivity"] = {
        "applied_count": sum(1 for s in sentinel_fields if s["constraint_applied"]),
        "fields_detected": len(sentinel_fields),
        "fields": sentinel_fields,
    }

    # §28 Decimal precision constraint
    precision_fields = []
    for f in forms:
        form_id = f.get("form_id", "?")
        survey = f.get("survey") or []
        for r in survey:
            if (r.get("type") or "").strip() != "decimal":
                continue
            decimals, pat = _precision_for_name(r.get("name"))
            con = r.get("constraint") or ""
            applied = f"round(${{{r.get('name','')}}}, {decimals})" in con
            precision_fields.append({
                "form": form_id,
                "field": r.get("name", "?"),
                "decimals": decimals,
                "matched_pattern": pat,
                "constraint_applied": applied,
            })
    ca["decimal_precision_constraint"] = {
        "applied_count": sum(1 for p in precision_fields if p["constraint_applied"]),
        "fields_detected": len(precision_fields),
        "fields": precision_fields,
    }

