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

    # Run the conversion. xls2xform_convert needs an output XML path even
    # though we don't care about the converted XML — it's required.
    tmp_xml = None
    try:
        tmp = tempfile.NamedTemporaryFile(suffix=".xml", delete=False)
        tmp.close()
        tmp_xml = tmp.name

        warnings = xls2xform_convert(
            xlsform_path=xlsx_path,
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
