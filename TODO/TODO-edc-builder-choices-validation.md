# TODO: EDC Builder — Auto-validate and auto-inject missing choices lists

**Status:** Not started  
**Priority:** Medium  
**Created:** 2026-05-31  
**Context:** Recurring build failures — `prepost`, `saecrit`, and others

---

## Problem

The EDC build pipeline hard-fails when Claude omits a `choices` list that is
referenced by a `select_one` or `select_multiple` row in the survey sheet.
The current fix is to add the missing list to the OC-10 rule in `prompts.py`
and re-run — which means burning a full Claude extraction run (~8 min, ~$0.50)
every time Claude forgets a list.

This has happened twice so far:
- `saecrit` (AESAE form, CDASH seriousness criteria) — fixed in commit `dc14655`
- `prepost` (NRS form, pre/post procedure timing) — fixed in commit `da1f2ed`

Root causes:
1. **Instruction dilution** — OC-10 has many rules; individual list definitions
   get less attention as the rule list grows
2. **No negative reinforcement** — Claude has never seen the build failure in
   its training data, so omitting choices doesn't feel "wrong"
3. **Study-specific lists** — `prepost` is not in Claude's training data at
   all; it invented the list name without including the definition

---

## Real fix

In `build_xlsforms.py` (or `build_package.py`), after Claude generates the
XLSForm JSON but BEFORE pyxform validation, add a pre-validate step that:

1. **Scans every form's survey sheet** for `select_one X` and
   `select_multiple X` references
2. **Cross-checks each list name** against the form's choices sheet
3. For **known boilerplate lists** (`yn`, `saecrit`, `prepost`, and any
   others we define in a registry), **auto-injects** the missing choices
   rather than hard-failing — same outcome as if Claude had included them
4. For **unknown/study-specific lists** (e.g. `dvcat`, `peres`, `sev`,
   `rel`), **hard-fail with a clear message** naming the form and list —
   these require Claude to actually define them since their values are
   protocol-specific

This turns Claude's omission of boilerplate lists into a **soft recoverable
error** rather than a build blocker, while still catching genuinely missing
study-specific lists that Claude must define.

---

## Implementation sketch

```python
# In build_xlsforms.py, after generating form JSON, before pyxform:

BOILERPLATE_CHOICES = {
    "yn": [
        {"list_name": "yn", "name": "Y", "label": "Yes"},
        {"list_name": "yn", "name": "N", "label": "No"},
    ],
    "saecrit": [
        {"list_name": "saecrit", "name": "DEATH",      "label": "Death"},
        {"list_name": "saecrit", "name": "LIFE",       "label": "Life-threatening"},
        {"list_name": "saecrit", "name": "HOSP",       "label": "Hospitalization or prolonged hospitalization"},
        {"list_name": "saecrit", "name": "DISABILITY", "label": "Persistent or significant disability/incapacity"},
        {"list_name": "saecrit", "name": "CONGENITAL", "label": "Congenital anomaly or birth defect"},
        {"list_name": "saecrit", "name": "MEDIMPT",    "label": "Medically significant or important medical event"},
    ],
    "prepost": [
        {"list_name": "prepost", "name": "pre",  "label": "Pre-procedure"},
        {"list_name": "prepost", "name": "post", "label": "Post-procedure"},
    ],
}

def auto_inject_boilerplate_choices(form_json):
    """
    Scan survey for select_one/select_multiple references.
    Auto-inject known boilerplate lists if missing from choices.
    Raise ValueError for unknown missing lists (study-specific).
    """
    referenced = set()
    for row in form_json.get("survey", []):
        t = row.get("type", "")
        if t.startswith("select_one ") or t.startswith("select_multiple "):
            list_name = t.split(" ", 1)[1].strip()
            referenced.add(list_name)

    defined = {r["list_name"] for r in form_json.get("choices", [])}
    missing = referenced - defined

    injected = []
    unknown = []
    for name in missing:
        if name in BOILERPLATE_CHOICES:
            form_json["choices"].extend(BOILERPLATE_CHOICES[name])
            injected.append(name)
        else:
            unknown.append(name)

    if injected:
        print(f"[build] Auto-injected boilerplate choices: {injected}")
    if unknown:
        raise ValueError(
            f"Missing choices lists (study-specific, Claude must define): {unknown}"
        )

    return form_json
```

---

## Notes

- `yn` is already auto-injected by existing pipeline code — this generalizes
  that pattern to cover all known boilerplate lists
- The boilerplate registry in code should mirror OC-10 in `prompts.py` so
  they stay in sync — consider a single source of truth (e.g. a JSON file
  read by both the prompt builder and the validator)
- Long-term, study-specific lists that appear repeatedly across studies could
  graduate into the boilerplate registry (e.g. `ynu` = Yes/No/Unknown)
