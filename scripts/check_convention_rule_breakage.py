"""Which form rules break because of the BioIVT canonical-list conventions?
Compares the same spec with and without the bioIVT customer conventions, and checks every
relevant/constraint/calculation expression that compares a select field to a literal value
against the field's actual choice names."""
import copy, json, re, sys
sys.path.insert(0, '.')
from conventions_engine import apply_conventions

PATS = [
    re.compile(r"\$\{(\w+)\}\s*(?:!=|=)\s*'([^']*)'"),
    re.compile(r'\$\{(\w+)\}\s*(?:!=|=)\s*"([^"]*)"'),
    re.compile(r"selected\(\s*\$\{(\w+)\}\s*,\s*'([^']*)'\s*\)"),
    re.compile(r'selected\(\s*\$\{(\w+)\}\s*,\s*"([^"]*)"\s*\)'),
    re.compile(r"\$\{(\w+)\}\s*(?:!=|=)\s*(\d+)(?![\w'\"])"),
]

def mismatches(spec):
    found = {}
    for form in spec['forms']:
        fields = {r['name']: r for r in form.get('survey', []) if r.get('name')}
        names_by_list = {}
        for c in form.get('choices', []):
            names_by_list.setdefault(c['list_name'], set()).add(str(c['name']))
        for r in form.get('survey', []):
            for key, val in r.items():
                if not isinstance(val, str) or '${' not in val:
                    continue
                for pat in PATS:
                    for m in pat.finditer(val):
                        ref, lit = m.group(1), m.group(2)
                        if lit == '':
                            continue
                        tgt = fields.get(ref)
                        if not tgt or not str(tgt.get('type', '')).startswith('select'):
                            continue
                        ln = tgt['type'].split(' ', 1)[1]
                        if lit not in names_by_list.get(ln, set()):
                            found[(form['form_id'], r.get('name'), key, ref, lit)] = (ln, sorted(names_by_list.get(ln, set()))[:12])
    return found

for study, path, sid in (('Detroit', 'tmp/Detroit_DS_Study_Specification_CORRECTED_v8.json', 'DETROITD_NEW_V3'),
                         ('Precision', 'tmp/PrecisionMed_Study_Specification_CORRECTED_v6.json', 'PRECISIO_NEW_V3')):
    with open(path) as f:
        raw = json.load(f)
    plain = apply_conventions(copy.deepcopy(raw), study_id=sid, customer_subdomain='')
    bio = apply_conventions(copy.deepcopy(raw), study_id=sid, customer_subdomain='bioIVT')
    before, after = mismatches(plain), mismatches(bio)
    new = {k: v for k, v in after.items() if k not in before}
    print(f"\n===== {study} =====")
    print(f"rules pointing at a value that doesn't exist: before bioIVT conventions={len(before)}, after={len(after)}, NEWLY BROKEN={len(new)}")
    for (fid, fld, key, ref, lit), (ln, names) in sorted(new.items()):
        print(f"  NEW  {fid}.{fld}  [{key}]  {ref} = '{lit}'   but {ln} has: {names}")
    pre = {k: v for k, v in before.items()}
    for (fid, fld, key, ref, lit), (ln, names) in sorted(pre.items()):
        print(f"  OLD  {fid}.{fld}  [{key}]  {ref} = '{lit}'   but {ln} has: {names}")
