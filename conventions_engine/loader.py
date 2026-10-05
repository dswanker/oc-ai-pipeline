"""Filesystem + JSON Schema validation for the conventions store."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

from . import LoadError, SchemaValidationError

# Current expected schema version. Bumped when conventions/schema/* breaks.
CURRENT_SCHEMA_VERSION = 1


def _load_schema(repo_root: Path) -> Dict[str, Any]:
    schema_path = repo_root / "conventions" / "schema" / "convention.schema.json"
    with open(schema_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _check_version(repo_root: Path) -> None:
    version_path = repo_root / "conventions" / "schema" / "version.txt"
    if not version_path.exists():
        return  # tolerate missing file; treat as fresh store
    raw = version_path.read_text(encoding="utf-8").strip()
    try:
        version = int(raw)
    except ValueError:
        raise SchemaValidationError(
            f"conventions/schema/version.txt is not an integer: {raw!r}"
        )
    if version != CURRENT_SCHEMA_VERSION:
        raise SchemaValidationError(
            f"Conventions store version {version} does not match "
            f"engine version {CURRENT_SCHEMA_VERSION}. Run a migration "
            f"before loading."
        )


def _validate_convention(record: Dict[str, Any], schema: Dict[str, Any]) -> str:
    """Return empty string if valid, otherwise a human-readable error."""
    try:
        import jsonschema
    except ImportError:
        # If jsonschema isn't installed, fall back to a minimal manual check.
        # Production should have it (add to requirements.txt).
        required = ["id", "title", "kind", "scope", "status", "natural_key",
                    "description", "target", "created_at", "created_by", "source"]
        missing = [f for f in required if f not in record]
        if missing:
            return f"missing required fields: {missing}"
        return ""
    try:
        jsonschema.validate(instance=record, schema=schema)
        return ""
    except jsonschema.ValidationError as e:
        return f"{e.message} (at {list(e.path)})"


def _load_scope_dir(scope_dir: Path, schema: Dict[str, Any]
                    ) -> Tuple[List[Dict[str, Any]], List[LoadError]]:
    """Load every .json file in `scope_dir`. Validate. Drop inactive."""
    records: List[Dict[str, Any]] = []
    errors: List[LoadError] = []

    if not scope_dir.exists():
        return records, errors

    for path in sorted(scope_dir.glob("*.json")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                record = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            errors.append(LoadError(path=str(path), reason=f"parse error: {e}"))
            continue

        err = _validate_convention(record, schema)
        if err:
            errors.append(LoadError(path=str(path), reason=f"schema: {err}"))
            continue

        if record.get("status") != "active":
            # Silently skip proposed and archived — engine only applies active.
            continue

        records.append(record)

    return records, errors


def load_scope(repo_root: Path, scope: str, scope_id: str = ""
               ) -> Tuple[List[Dict[str, Any]], List[LoadError]]:
    """Load active conventions for a single scope."""
    _check_version(repo_root)
    schema = _load_schema(repo_root)

    if scope == "global":
        scope_dir = repo_root / "conventions" / "global"
    elif scope == "customer":
        if not scope_id:
            return [], []
        scope_dir = repo_root / "conventions" / "customers" / scope_id
    elif scope == "vendor":
        # Vendor slug (e.g. "redcap", "castor"). Empty slug means
        # non-migration build — vendor scope returns empty silently.
        if not scope_id:
            return [], []
        scope_dir = repo_root / "conventions" / "vendors" / scope_id
    elif scope == "study":
        if not scope_id:
            return [], []
        scope_dir = repo_root / "conventions" / "studies" / scope_id
    else:
        raise ValueError(f"Unknown scope: {scope!r}")

    return _load_scope_dir(scope_dir, schema)


def load_canonical_lists(repo_root: Path) -> Tuple[Dict[str, Any], List[LoadError]]:
    """Load the shared canonical choice-list library.

    Distinct from the four convention scopes (global/customer/vendor/
    study) — these are plain value-set data files, not conventions,
    and carry no cascade/precedence semantics of their own. A field
    either references one via the `use_canonical_list` effect
    directive or it doesn't; there's no "customer overrides canonical
    list" concept (a customer wanting different values for e.g. race
    would instead define its own list and reference that name).

    Each file at conventions/canonical_lists/<name>.json must have:
        {"list_name": "<xlsform list_name>", "choices": [...],
         "multi_select": bool (optional, default false)}

    Returns a dict keyed by filename stem (the name a convention's
    `use_canonical_list` payload refers to), plus any load errors —
    malformed files are skipped and surfaced as review_flags the same
    way convention load errors are, rather than crashing the build.
    """
    lists_dir = repo_root / "conventions" / "canonical_lists"
    out: Dict[str, Any] = {}
    errors: List[LoadError] = []

    if not lists_dir.exists():
        return out, errors

    for path in sorted(lists_dir.glob("*.json")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                record = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            errors.append(LoadError(path=str(path), reason=f"parse error: {e}"))
            continue

        missing = [k for k in ("list_name", "choices") if k not in record]
        if missing:
            errors.append(LoadError(path=str(path), reason=f"missing required fields: {missing}"))
            continue
        if not isinstance(record["choices"], list) or not record["choices"]:
            errors.append(LoadError(path=str(path), reason="'choices' must be a non-empty list"))
            continue

        out[path.stem] = record

    return out, errors


def resolve_customer(repo_root: Path, customer_subdomain: str, client_name: str = "") -> str:
    """Which conventions/customers/<folder> applies to this item?

    The tenant subdomain wins when it has a folder of its own. Names are matched ignoring case and spaces, so 'bioivt' (the
    registry spelling) and 'bioIVT' (the folder) are the same customer. When the subdomain has no folder, the item's Client
    name is tried instead, so a customer's conventions follow the customer even when a build is rehearsed in another tenant
    (for example cust1). With neither, the subdomain is returned unchanged (no customer conventions load)."""
    base = Path(repo_root) / "conventions" / "customers"
    try:
        folders = {p.name.replace(" ", "").lower(): p.name for p in base.iterdir() if p.is_dir()}
    except OSError:
        return customer_subdomain
    for candidate in (customer_subdomain, client_name):
        key = (candidate or "").replace(" ", "").lower()
        if key and key in folders:
            return folders[key]
    return customer_subdomain


def load_all(repo_root: Path, customer_subdomain: str, study_id: str,
             migration_source: str = ""
             ) -> Dict[str, Any]:
    """Load all four scopes plus aggregated errors.

    `migration_source` is the vendor slug (e.g. "redcap", "castor")
    from monday column `dropdown_mm382w7d`. Empty string means
    non-migration build — vendor scope returns empty.
    """
    global_recs, global_errs = load_scope(repo_root, "global")
    customer_recs, customer_errs = load_scope(repo_root, "customer", customer_subdomain)
    vendor_recs, vendor_errs = load_scope(repo_root, "vendor", migration_source)
    study_recs, study_errs = load_scope(repo_root, "study", study_id)

    return {
        "global": global_recs,
        "customer": customer_recs,
        "vendor": vendor_recs,
        "study": study_recs,
        "errors": [*global_errs, *customer_errs, *vendor_errs, *study_errs],
    }
