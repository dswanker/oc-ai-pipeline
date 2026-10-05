"""Did the BioIVT canonical-list conventions drop any answer option that existed before?
For every field whose list changed, compare old option labels to new ones (case-insensitive)."""
import copy, json, sys
sys.path.insert(0, '.')
from conventions_engine import apply_conventions

def lists(form):
    d = {}
    for c in form.get('choices', []):
        d.setdefault(c['list_name'], []).append(c)
    return d

for study, path, sid in (('Detroit', 'tmp/Detroit_DS_Study_Specification_CORRECTED_v8.json', 'DETROITD_NEW_V3'),
                         ('Precision', 'tmp/PrecisionMed_Study_Specification_CORRECTED_v6.json', 'PRECISIO_NEW_V3')):
    with open(path) as f:
        raw = json.load(f)
    plain = apply_conventions(copy.deepcopy(raw), study_id=sid, customer_subdomain='')
    bio = apply_conventions(copy.deepcopy(raw), study_id=sid, customer_subdomain='bioIVT')
    print(f"\n===== {study}: options that existed before and are missing now =====")
    n_changed = n_dropped = 0
    bio_by = {f['form_id']: f for f in bio['forms']}          # match forms by id: conventions may add forms
    for fa in plain['forms']:
        fb = bio_by.get(fa['form_id'])
        if fb is None:
            continue
        la, lb = lists(fa), lists(fb)
        rb = {r['name']: r for r in fb.get('survey', []) if r.get('name')}
        for r in fa.get('survey', []):
            t = str(r.get('type', ''))
            if not t.startswith('select') or r['name'] not in rb:
                continue
            t2 = str(rb[r['name']].get('type', ''))
            if t == t2:
                continue
            n_changed += 1
            old = {str(c['label']).strip().casefold(): c for c in la.get(t.split(' ', 1)[1], [])}
            new = {str(c['label']).strip().casefold() for c in lb.get(t2.split(' ', 1)[1], [])}
            missing = [c['label'] for k, c in old.items() if k not in new]
            if missing:
                n_dropped += 1
                print(f"  {fa['form_id']}.{r['name']}: {t} -> {t2}   MISSING: {missing}")
    print(f"  ({n_changed} fields switched lists; {n_dropped} lost at least one option label)")
