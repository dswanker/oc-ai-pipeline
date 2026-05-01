"""
validate_form.py — XLSForm validation using pyxform

Validates a generated XLSForm by attempting to convert it to ODK XForm XML
using pyxform (the same engine that powers https://getodk.org/xlsform/).

Returns a structured result that callers can attach to build_log.

Phase 1 design (per TODO/TODO-xlsform-validation.md):
- validate=False (no Java/ODK Validate dependency)
- Behavior B: warn-but-proceed — caller decides whether to halt
- Per-form invocation; results aggregated by build_xlsforms.py

Output structure:
    {
        "form_id":  "AE",
        "is_valid": True | False,
        "errors":   ["..."],        # PyXFormError messages, prevent build
        "warnings": ["..."],        # pyxform warnings, do not prevent build
        "skipped":  False,          # True if pyxform unavailable
        "skip_reason": None,
    }
"""

import os
import tempfile
import logging

logger = logging.getLogger(__name__)


def _try_import_pyxform():
    """Lazy import so the rest of the pipeline runs even if pyxform missing."""
    try:
        from pyxform.xls2xform import xls2xform_convert
        from pyxform.errors import PyXFormError
        return xls2xform_convert, PyXFormError, None
    except ImportError as e:
        return None, None, f"pyxform not installed: {e}"


def _strip_oc8_phantom_end_groups(xlsx_path):
    """
    Return a temp xlsx path with OC-8 phantom end group rows removed.

    OpenClinica requires a non-standard XLSForm pattern for repeating forms:
        begin repeat NAME
        end group          ← "phantom" — no matching begin group
        end repeat

    pyxform rejects this because it cannot find a begin_group to match the
    end_group. OC's own validator accepts it. We strip the phantom rows before
    passing to pyxform so validation/conversion succeeds.

    Caller is responsible for deleting the returned temp file.
    Returns (temp_path, was_modified).
    """
    try:
        import openpyxl, tempfile, shutil
        wb = openpyxl.load_workbook(xlsx_path, data_only=True)
        if 'survey' not in wb.sheetnames:
            return xlsx_path, False

        ws = wb['survey']
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return xlsx_path, False

        headers = [str(h).strip() if h is not None else '' for h in rows[0]]
        try:
            type_col = headers.index('type')
        except ValueError:
            return xlsx_path, False

        # Stack-based scan — identify phantom end group rows
        # A phantom end group is an 'end group' row encountered when the
        # innermost open block is a repeat (not a group).
        stack = []          # 'group' or 'repeat'
        phantom_rows = set()
        for i, row in enumerate(rows[1:], start=1):
            t = str(row[type_col] or '').strip().lower() if type_col < len(row) else ''
            if t == 'begin group':
                stack.append('group')
            elif t == 'begin repeat':
                stack.append('repeat')
            elif t == 'end group':
                if stack and stack[-1] == 'repeat':
                    phantom_rows.add(i)   # OC-8 phantom — skip for pyxform
                elif stack:
                    stack.pop()
            elif t == 'end repeat':
                if stack and stack[-1] == 'repeat':
                    stack.pop()

        if not phantom_rows:
            return xlsx_path, False

        # Write a cleaned copy to a temp file
        tmp = tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False)
        tmp.close()
        shutil.copy2(xlsx_path, tmp.name)

        wb2 = openpyxl.load_workbook(tmp.name)
        ws2 = wb2['survey']
        # Delete rows in reverse order so indices stay valid
        data_rows = list(ws2.iter_rows())
        for row_idx in sorted(phantom_rows, reverse=True):
            # row_idx is 0-based in data_rows (row 0 = header row 1 in sheet)
            sheet_row = row_idx + 1   # openpyxl rows are 1-indexed
            ws2.delete_rows(sheet_row)
        wb2.save(tmp.name)
        return tmp.name, True

    except Exception:
        # If anything goes wrong, fall back to original file
        return xlsx_path, False


def validate_xlsform(xlsx_path, form_id=None):
    """
    Validate a single XLSForm at xlsx_path.

    Returns a dict (see module docstring for shape). Never raises;
    failures are captured in the dict.
    """
    if form_id is None:
        form_id = os.path.basename(xlsx_path).replace(".xlsx", "").replace(".xls", "")

    result = {
        "form_id":     form_id,
        "is_valid":    False,
        "errors":      [],
        "warnings":    [],
        "skipped":     False,
        "skip_reason": None,
    }

    # Lazy-import pyxform
    xls2xform_convert, PyXFormError, import_err = _try_import_pyxform()
    if xls2xform_convert is None:
        result["skipped"]     = True
        result["skip_reason"] = import_err
        result["is_valid"]    = True   # don't fail builds when validator unavailable
        logger.warning(f"validate_xlsform: skipped {form_id} — {import_err}")
        return result

    if not os.path.exists(xlsx_path):
        result["errors"].append(f"File does not exist: {xlsx_path}")
        return result

    # Strip OC-8 phantom end group rows before pyxform sees them.
    # pyxform rejects the OC-required phantom end group between begin repeat
    # and end repeat; stripping it lets pyxform convert the form correctly
    # while the real xlsx (sent to OC) retains the required structure.
    clean_path, was_stripped = _strip_oc8_phantom_end_groups(xlsx_path)

    # Run the conversion. xls2xform_convert needs an output XML path even
    # though we don't care about the converted XML — it's required.
    tmp_xml = None
    try:
        tmp = tempfile.NamedTemporaryFile(suffix=".xml", delete=False)
        tmp.close()
        tmp_xml = tmp.name

        warnings = xls2xform_convert(
            xlsform_path=clean_path,
            xform_path=tmp_xml,
            validate=False,        # Phase 1: no Java/ODK Validate dependency
            pretty_print=False,
            enketo=False,
        )

        # Success path — pyxform returns a list of warning strings
        result["is_valid"] = True
        if warnings:
            # Filter out empty strings just in case
            result["warnings"] = [w for w in warnings if w and str(w).strip()]

    except PyXFormError as e:
        # Form-structural error — pipeline should warn the user but not halt
        result["is_valid"] = False
        result["errors"].append(_clean_error_message(str(e)))

    except Exception as e:
        # Unexpected — log but don't fail the build
        result["is_valid"] = False
        result["errors"].append(f"Unexpected validation error ({type(e).__name__}): {e}")
        logger.exception(f"validate_xlsform: unexpected error on {form_id}")

    finally:
        # Clean up the stripped temp xlsx if we created one
        if was_stripped and clean_path != xlsx_path and os.path.exists(clean_path):
            try:
                os.unlink(clean_path)
            except OSError:
                pass
        # Always clean up the temp XML file
        if tmp_xml and os.path.exists(tmp_xml):
            try:
                os.unlink(tmp_xml)
            except OSError:
                pass
            # Also clean up any itemsets.csv pyxform may have written
            itemsets = os.path.join(os.path.dirname(tmp_xml), "itemsets.csv")
            if os.path.exists(itemsets):
                try:
                    os.unlink(itemsets)
                except OSError:
                    pass

    return result


def _clean_error_message(msg):
    """
    pyxform error messages can include long file paths and stack-trace-ish
    detail. Tighten them for human readability without losing meaning.
    """
    if not msg:
        return msg
    # Strip leading whitespace, collapse newlines into spaces for brevity
    cleaned = " ".join(line.strip() for line in msg.split("\n") if line.strip())
    # Cap length so it fits in a build report
    if len(cleaned) > 500:
        cleaned = cleaned[:497] + "..."
    return cleaned


def summarize_validation_results(per_form_results):
    """
    Given a list of per-form validation dicts, produce summary counts.

    Returns:
        {
            "total":     N,
            "valid":     N,
            "with_errors":   N,
            "with_warnings": N,
            "skipped":   N,
            "all_clean": bool,    # True iff every form passed with no warnings
        }
    """
    total = len(per_form_results)
    valid = sum(1 for r in per_form_results if r.get("is_valid"))
    with_errors   = sum(1 for r in per_form_results if r.get("errors"))
    with_warnings = sum(1 for r in per_form_results if r.get("warnings"))
    skipped = sum(1 for r in per_form_results if r.get("skipped"))
    return {
        "total":         total,
        "valid":         valid,
        "with_errors":   with_errors,
        "with_warnings": with_warnings,
        "skipped":       skipped,
        "all_clean":     (with_errors == 0 and with_warnings == 0 and skipped == 0),
    }
