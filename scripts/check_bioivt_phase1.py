"""Phase 1 acceptance check: build the Detroit spec with and without the Phase 1 conventions and verify the result.
Run from the repo root with the project venv. Prints PASS/FAIL per check; exits 1 if any FAIL."""
import collections, contextlib, copy, csv, io, json, os, re, shutil, sys, tempfile, types
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT); sys.path.insert(0, str(ROOT))
stub = types.ModuleType('auth_manager')
class AuthManager: pass
stub.AuthManager = AuthManager; sys.modules['auth_manager'] = stub
import conventions_engine
with contextlib.redirect_stdout(io.StringIO()):
    from pipeline import (_enforce_common_visit, _backfill_migration_fields, _sanitize_form_titles, _ensure_required_forms,
                          run_study_spec_files, _build_board_json)
    from omop_coding import _apply_omop_coding

PHASE1 = ['speccol_form', 'death_details_form', 'therapy_frequency_list', 'therapy_dose_unit_list', 'therapy_route_list',
          'therapy_dose_number', 'therapy_other_specify_rows']
SPEC_PATH = 'tmp/Detroit_DS_Study_Specification_CORRECTED_v8.json'

def build(raw, name):
    s = copy.deepcopy(raw)
    with contextlib.redirect_stdout(io.StringIO()):
        s = _enforce_common_visit(s); s = _backfill_migration_fields(s); s = _sanitize_form_titles(s); s = _ensure_required_forms(s, name)
        run_study_spec_files(s, customer_subdomain='bioIVT'); s = _apply_omop_coding(s, 'OMOP_CDM')
    return s

raw = json.load(open(SPEC_PATH))
# Phase 2 conventions also change some of the same forms, so compare Phase 1 alone: 'after' = Phase 1 present and Phase 2 absent.
PHASE2 = ['vocab_rxnorm_single', 'vocab_rxnorm_multi', 'vocab_snomed_diagnoses', 'vocab_loinc_tests', 'vocab_units', 'lab_values_numeric', 'lab_unit_rows',
          'vocab_other_specify', 'vocab_other_specify_multi', 'din_max_length', 'autofill_demographics', 'calc_months_disease_free', 'calc_months_survived',
          'calc_months_survived_death_lookup', 'vocab_snomed_procedures', 'vocab_snomed_body_structure', 'vocab_icdo3_topography']
def root_without(slugs):
    r = Path(tempfile.mkdtemp()); shutil.copytree(ROOT / 'conventions_engine' / 'conventions', r / 'conventions')
    for slug in slugs:
        f = r / 'conventions' / 'customers' / 'bioIVT' / f'{slug}.json'
        if f.exists(): f.unlink()
    return r
real_root = conventions_engine._default_data_root
conventions_engine._default_data_root = (lambda r=root_without(PHASE2): r)
after = build(raw, 'after')
conventions_engine._default_data_root = (lambda r=root_without(PHASE1 + PHASE2): r)
before = build(raw, 'before')
conventions_engine._default_data_root = real_root

results = []
def check(name, ok, detail=''):
    results.append(ok); print(('PASS  ' if ok else 'FAIL  ') + name + (f'   [{detail}]' if detail and not ok else ''))

FB = {f['form_id']: f for f in before['forms']}; FA = {f['form_id']: f for f in after['forms']}
rows = lambda f: {r['name']: r for r in f['survey'] if r.get('name') and r.get('type') not in ('begin group', 'end group')}

check('baseline build really has no Phase 1 forms (so the comparison is meaningful)', 'SPECCOL' not in FB and 'DD' not in FB)
check('after build has 21 forms, before has 19', (len(FB), len(FA)) == (19, 21), f'{len(FB)} -> {len(FA)}')

EXPECT_REMOVED = {'SHORTFORM': {'KIT_NO', 'COLLDT', 'COLLTM', 'PM_Y', 'DOD', 'TOD', 'COD'}, 'FOLLOWUP': {'CANCERDTH', 'DTDTH', 'CAUSDTH'}, 'OUTCOME': {'DATE_OF_DEATH'}}
for fid, exp in EXPECT_REMOVED.items():
    rb, ra = rows(FB[fid]), rows(FA[fid])
    gone = set(rb) - set(ra)
    if fid in ('PSORIASIS',): continue
    check(f'{fid}: exactly the planned questions left the form', gone == exp and not (set(ra) - set(rb)), f'removed={sorted(gone)} added={sorted(set(ra)-set(rb))}')
    check(f'{fid}: every remaining question is byte-for-byte unchanged', all(rb[n] == ra[n] for n in ra), [n for n in ra if rb[n] != ra[n]])

# --- Specimen Collection
sc = FA['SPECCOL']; rsc = rows(sc); src = rows(FB['SHORTFORM'])
check('SPECCOL sits on exactly the same 7 events as the Short Form', sc['visits_assigned'] == FB['SHORTFORM']['visits_assigned'] and len(sc['visits_assigned']) == 7, sc['visits_assigned'])
check('SPECCOL has the 4 planned questions and the hidden helper', [n for n in rsc if rsc[n]['type'] != 'calculate'] == ['KIT_NO', 'COLLDT', 'COLLTM', 'PM_Y'] and 'HUMAN_CF' in rsc)
def same_but(a, b, *keys): return {k: v for k, v in a.items() if k not in keys} == {k: v for k, v in b.items() if k not in keys}
check('SPECCOL questions are copies of the Short Form originals (only item group and show/hide rule differ)', all(same_but(rsc[n], src[n], 'bind__oc_itemgroup', 'relevant') for n in ('KIT_NO', 'COLLDT', 'COLLTM', 'PM_Y')))
check("kit number and post-mortem hide for animals with the fail-safe rule; date/time have no rule", rsc['KIT_NO'].get('relevant') == "${HUMAN_CF}!='No'" and rsc['PM_Y'].get('relevant') == "${HUMAN_CF}!='No'" and not rsc['COLLDT'].get('relevant') and not rsc['COLLTM'].get('relevant'))
h = rsc['HUMAN_CF']
check('helper reads the gate answer from the Short Form on the CURRENT event, flagged for OID confirmation', h['bind__oc_external'] == 'clinicaldata' and "@OpenClinica:Current='Yes'" in h['calculation'] and "IS_IT_A_VETERINARY_FORM" in h['calculation'] and h['completion_status'] == 'FLAGGED')
gate_names = {c['name'] for c in FA['SHORTFORM']['choices'] if c['list_name'] == rows(FA['SHORTFORM'])['IS_IT_A_VETERINARY_FORM']['type'].split(' ', 1)[1]}
check("the fail-safe rule compares against a real choice value ('No')", 'No' in gate_names, gate_names)
check('SPECCOL form settings: id/title set, crossform_references lists its 7 events', sc['settings']['form_id'] == 'SPECCOL' and sc['settings']['crossform_references'] == ','.join(sc['visits_assigned']))
check('SPECCOL carries the post-mortem choice list', any(c['list_name'] == 'pm' for c in sc['choices']))

# --- Death Details
dd = FA['DD']; rdd = rows(dd)
check('DD sits on the common event only (add-when-needed)', dd['visits_assigned'] == ['SE_COMMON'], dd['visits_assigned'])
check('DD has exactly the 4 planned questions', list(rdd) == ['DTHDAT', 'DTHTIM', 'PRCDTH', 'CANCERDTH'], list(rdd))
check('date of death is a date and keeps the STRICTER date rule from the Short Form', rdd['DTHDAT']['type'] == 'date' and rdd['DTHDAT'].get('constraint') == src['DOD']['constraint'] == ". <= today() and . >= '1900-01-01'")
check('time of death keeps its HH:MM rule', rdd['DTHTIM'].get('constraint') == src['TOD']['constraint'])
check('cause of death is text; none of the four has a show/hide rule or is required', rdd['PRCDTH']['type'] == 'text' and all(not r.get('relevant') and r.get('required') != 'yes' for r in rdd.values()))
fl = FB['FOLLOWUP']; ln = rows(fl)['CANCERDTH']['type'].split(' ', 1)[1]
check("'died of the cancer?' keeps its list and all of its choices", rdd['CANCERDTH']['type'] == f'select_one {ln}' and {c['name'] for c in dd['choices'] if c['list_name'] == ln} == {c['name'] for c in fl['choices'] if c['list_name'] == ln} and len([c for c in dd['choices'] if c['list_name'] == ln]) == 3)
check('every DD question lives in the DD item group', all(r.get('bind__oc_itemgroup') == 'DD' for r in rdd.values()))

# --- Lineage: nothing deleted without a home
EXP_LIN = {('SHORTFORM.KIT_NO', 'SPECCOL.KIT_NO'), ('SHORTFORM.COLLDT', 'SPECCOL.COLLDT'), ('SHORTFORM.COLLTM', 'SPECCOL.COLLTM'), ('SHORTFORM.PM_Y', 'SPECCOL.PM_Y'),
           ('SHORTFORM.DOD', 'DD.DTHDAT'), ('FOLLOWUP.DTDTH', 'DD.DTHDAT'), ('OUTCOME.DATE_OF_DEATH', 'DD.DTHDAT'), ('SHORTFORM.TOD', 'DD.DTHTIM'),
           ('SHORTFORM.COD', 'DD.PRCDTH'), ('FOLLOWUP.CAUSDTH', 'DD.PRCDTH'), ('FOLLOWUP.CANCERDTH', 'DD.CANCERDTH')}
lin = after['study_meta']['field_lineage']
check('lineage records all 11 old questions with their new home', {(l['old'], l['new']) for l in lin} == EXP_LIN and len(lin) == 11, len(lin))
exists_after = lambda ref: ref.split('.')[1] in rows(FA[ref.split('.')[0]])
check('every lineage target exists in the built forms; every old question is gone from its old form', all(exists_after(l['new']) and not exists_after(l['old']) and l['old'].split('.')[1] in rows(FB[l['old'].split('.')[0]]) for l in lin))

# --- therapy lists
ps, on = rows(FA['PSORIASIS']), rows(FA['ONCOLOGY'])
cl = lambda f, ln: {c['name'] for c in f['choices'] if c['list_name'] == ln}
check('psoriasis frequencies and unit are pick lists; dose is a number', all(ps[n]['type'] == 'select_one canon_cm_freq' for n in ('ORALFREQ', 'TOPFREQ', 'OTHRFREQ')) and ps['ORALUNIT']['type'] == 'select_one canon_cm_dose_unit' and ps['ORALDOSE']['type'] == 'decimal')
check('oncology administration is the route pick list', on['THERADMIN']['type'] == 'select_one canon_cm_route')
spec5 = [(FA['PSORIASIS'], ps, n, 'canon_cm_freq') for n in ('ORALFREQ', 'TOPFREQ', 'OTHRFREQ')] + [(FA['PSORIASIS'], ps, 'ORALUNIT', 'canon_cm_dose_unit'), (FA['ONCOLOGY'], on, 'THERADMIN', 'canon_cm_route')]
check("each of the 5 has exactly one 'Other, specify' row shown only for Other, and 'other' exists in its list",
      all(f['survey'].count(r[n + '_OTH']) == 1 and r[n + '_OTH']['relevant'] == "${" + n + "}='other'" and 'other' in cl(f, lst) and r[n + '_OTH']['type'] == 'text' for f, r, n, lst in spec5))
check('the new Other rows sit directly after their question', all([x.get('name') for x in f['survey']].index(n + '_OTH') == [x.get('name') for x in f['survey']].index(n) + 1 for f, r, n, lst in spec5))

# --- board
def board_cards(spec):
    b = _build_board_json(spec); ev = {l['_id']: l['eventOcoid'] for l in b['lists']}
    return b, collections.Counter((c['formOcoid'], ev[c['listId']]) for c in b['cards'])
bb, cb = board_cards(before); ba, ca = board_cards(after)
added, removed = ca - cb, cb - ca
check('board: 69 cards before, 77 after', (len(bb['cards']), len(ba['cards'])) == (69, 77), (len(bb['cards']), len(ba['cards'])))
check('board: the ONLY added cards are SPECCOL x7 (its events) and DD x1 on the common event; none removed',
      set(added) == {('F_SPECCOL', e) for e in FA['SPECCOL']['visits_assigned']} | {('F_DD', 'SE_COMMON')} and not removed and all(v == 1 for v in ca.values()), (sorted(added)[:3], dict(removed)))
check('board: same 10 events, every card points at a real event', len(ba['lists']) == len(bb['lists']) == 10 and {c['listId'] for c in ba['cards']} <= {l['_id'] for l in ba['lists']})

# --- dangling ${references}
def dangling(spec):
    out = set()
    for f in spec['forms']:
        names = {r.get('name') for r in f['survey']}
        for r in f['survey']:
            for k, v in r.items():
                if isinstance(v, str):
                    for ref in re.findall(r"\$\{(\w+)\}", v):
                        if ref not in names: out.add((f['form_id'], r.get('name'), k, ref))
    return out
db, da = dangling(before), dangling(after)
check('no new dangling ${references} in any form (a moved question nobody still points at)', not (da - db), sorted(da - db))
print(f'      (pre-existing dangling references in the baseline: {len(db)})')
check('no convention load errors, no skipped assemblies', not after.get('review_flags', {}).get('convention_load_errors') and not after.get('review_flags', {}).get('assemble_form_skipped'))

# --- lineage file for the record
with open('docs/BIOIVT_FIELD_LINEAGE.csv', 'w', newline='') as fh:
    w = csv.writer(fh); w.writerow(['old_form.field', 'old_label', 'new_form.field', 'new_label', 'how'])
    for l in lin:
        of, on_ = l['old'].split('.'); nf, nn = l['new'].split('.')
        w.writerow([l['old'], rows(FB[of])[on_]['label'], l['new'], rows(FA[nf])[nn]['label'], l['how']])
print(f"\n{sum(results)} of {len(results)} checks passed" + ("" if all(results) else "   <-- FAILURES ABOVE"))
sys.exit(0 if all(results) else 1)
