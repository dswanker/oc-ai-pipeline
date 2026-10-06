"""Phase 2 acceptance check (vocabulary lookups, lab values/units, DIN rule, autofill, calculated months).
Compares the real build with a build that has Phase 1 but not Phase 2. Run from the repo root with the project venv."""
import collections, contextlib, copy, csv, io, json, os, re, shutil, sys, tempfile, types
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT); sys.path.insert(0, str(ROOT))
stub = types.ModuleType('auth_manager')
class AuthManager: pass
stub.AuthManager = AuthManager; sys.modules['auth_manager'] = stub
import conventions_engine
with contextlib.redirect_stdout(io.StringIO()):
    from pipeline import (_enforce_common_visit, _backfill_migration_fields, _sanitize_form_titles, _ensure_required_forms, run_study_spec_files, _build_board_json)
    from omop_coding import _apply_omop_coding
PHASE2 = ['vocab_rxnorm_single', 'vocab_rxnorm_multi', 'vocab_snomed_diagnoses', 'vocab_loinc_tests', 'vocab_units', 'lab_values_numeric', 'lab_unit_rows',
          'vocab_other_specify', 'vocab_other_specify_multi', 'din_max_length', 'autofill_demographics', 'calc_months_disease_free', 'calc_months_survived',
          'calc_months_survived_death_lookup', 'vocab_snomed_procedures', 'vocab_snomed_body_structure', 'vocab_icdo3_topography']
PHASE3 = ['stage_group_list', 'stage_labels', 'tnm_format_check', 'therapy_outcome_label', 'therapy_s1_status_row', 'therapy_s3_response_row', 'therapy_s2_status_other', 'therapy_s4_response_other', 'therapy_s5_status_list', 'therapy_s6_response_list', 'stage_other_rows']
SPECS = {'Detroit': 'tmp/Detroit_DS_Study_Specification_CORRECTED_v9.json', 'Precision': 'tmp/PrecisionMed_Study_Specification_CORRECTED_v7.json'}
def build(raw, name):
    s = copy.deepcopy(raw)
    with contextlib.redirect_stdout(io.StringIO()):
        s = _enforce_common_visit(s); s = _backfill_migration_fields(s); s = _sanitize_form_titles(s); s = _ensure_required_forms(s, name)
        run_study_spec_files(s, customer_subdomain='bioIVT'); s = _apply_omop_coding(s, 'OMOP_CDM')
    return s
r = Path(tempfile.mkdtemp()); shutil.copytree(ROOT / 'conventions_engine' / 'conventions', r / 'conventions')
r3 = Path(tempfile.mkdtemp()); shutil.copytree(ROOT / 'conventions_engine' / 'conventions', r3 / 'conventions')   # root without the later (phase 3) conventions
for slug in PHASE3: (r3 / 'conventions' / 'customers' / 'bioIVT' / f'{slug}.json').unlink()
for slug in PHASE3: (r / 'conventions' / 'customers' / 'bioIVT' / f'{slug}.json').unlink()
raws = {k: json.load(open(v)) for k, v in SPECS.items()}
after = {k: build(v, k) for k, v in raws.items()}
real = conventions_engine._default_data_root; conventions_engine._default_data_root = lambda: r
# the registry (omop_coding) now points at real lists even in the 'before' build; fine, the registry is not a convention
before = {k: build(v, k) for k, v in raws.items()}; conventions_engine._default_data_root = real
results = []
def check(name, ok, detail=''):
    results.append(ok); print(('PASS  ' if ok else 'FAIL  ') + name + (f'   [{detail}]' if detail and not ok else ''))
def rows(spec, fid): return {r['name']: r for r in next(f for f in spec['forms'] if f['form_id'] == fid)['survey'] if r.get('name')}
def order(spec, fid): return [r.get('name') for r in next(f for f in spec['forms'] if f['form_id'] == fid)['survey']]
def rowdiff(b, a):
    ch, add, rem = set(), set(), set()
    for f in a['forms']:
        fb = next((x for x in b['forms'] if x['form_id'] == f['form_id']), None)
        if fb is None: add |= {(f['form_id'], r.get('name')) for r in f['survey']}; continue
        rb, ra = {r['name']: r for r in fb['survey'] if r.get('name')}, {r['name']: r for r in f['survey'] if r.get('name')}
        add |= {(f['form_id'], n) for n in set(ra) - set(rb)}; rem |= {(f['form_id'], n) for n in set(rb) - set(ra)}
        ch |= {(f['form_id'], n) for n in set(ra) & set(rb) if ra[n] != rb[n]}
    return ch, add, rem


dd, pr, db, pb = after['Detroit'], after['Precision'], before['Detroit'], before['Precision']
ch, add, rem = rowdiff(db, dd)
exp_changed = {('ONCOLOGY', 'TUMSTGE'), ('ONCOLOGY', 'UICCSTGE'), ('ONCOLOGY', 'THEROUTC'), ('BIOIVTONC', 'CONFSTGROUP'), ('BIOIVTONC', 'CONFTNMSTG'), ('FOLLOWUP', 'TNMSTG'), ('OUTCOME', 'AJCC_TNM_DLC')}
exp_added = {('ONCOLOGY', n) for n in ('THER_STATUS', 'THER_STATUS_OTH', 'THER_RESP', 'THER_RESP_OTH', 'TUMSTGE_OTH')} | {('BIOIVTONC', 'CONFSTGROUP_OTH')}
check("DETROIT: exactly the planned questions changed and were added, none removed", ch == exp_changed and add == exp_added and not rem, (sorted(ch ^ exp_changed), sorted(add ^ exp_added), sorted(rem)))
pch, padd, prem = rowdiff(pb, pr)
check("PRECISION: not touched at all", not (pch or padd or prem), (sorted(pch), sorted(padd), sorted(prem)))
o = order(dd, 'ONCOLOGY'); i = o.index('THEROUTC')
check("ONCOLOGY order: Therapy Outcome, Therapy status, its Other box, Response, its Other box", o[i:i + 5] == ['THEROUTC', 'THER_STATUS', 'THER_STATUS_OTH', 'THER_RESP', 'THER_RESP_OTH'], o[i:i + 6])
onc = rows(dd, 'ONCOLOGY'); bio = rows(dd, 'BIOIVTONC')
check("the original Therapy Outcome question is kept (never deleted), still free text", onc['THEROUTC']['type'] == 'text' and 'original free text' in onc['THEROUTC']['label'])
check("Stage group is a pick list on Oncology and on the BioIVT oncology form", onc['TUMSTGE']['type'] == 'select_one canon_bioivt_stage_group' and bio['CONFSTGROUP']['type'] == 'select_one canon_bioivt_stage_group')
check("Therapy status and Response are pick lists", onc['THER_STATUS']['type'] == 'select_one canon_bioivt_therapy_status' and onc['THER_RESP']['type'] == 'select_one canon_bioivt_therapy_response')
check("the labels say what each stage question holds", onc['UICCSTGE']['label'].startswith('TNM classification') and onc['TUMSTGE']['label'] == 'Stage group', (onc['UICCSTGE']['label'], onc['TUMSTGE']['label']))
oth = [(f, n, "${" + n.replace('_OTH', '') + "}='other'") for f, n in (('ONCOLOGY', 'TUMSTGE_OTH'), ('ONCOLOGY', 'THER_STATUS_OTH'), ('ONCOLOGY', 'THER_RESP_OTH'), ('BIOIVTONC', 'CONFSTGROUP_OTH'))]
check("every Other box appears only when 'other' is chosen", all(rows(dd, f)[n].get('relevant') == rel for f, n, rel in oth), [(n, rows(dd, f)[n].get('relevant')) for f, n, rel in oth])
cho = {c.get('list_name') for c in next(f for f in dd['forms'] if f['form_id'] == 'ONCOLOGY').get('choices', [])}
check("the three lists are in the Oncology form's choices", {'canon_bioivt_stage_group', 'canon_bioivt_therapy_status', 'canon_bioivt_therapy_response'} <= cho, sorted(cho)[:12])
sg = [c['name'] for c in next(f for f in dd['forms'] if f['form_id'] == 'ONCOLOGY').get('choices', []) if c.get('list_name') == 'canon_bioivt_stage_group']
check("the stage group list is exactly the approved values plus Other", sg == ['0', 'I', 'IA', 'IB', 'II', 'IIA', 'IIB', 'III', 'IIIA', 'IIIB', 'IIIC', 'IV', 'other'], sg)
tn = [(f, n) for f, n in (('ONCOLOGY', 'UICCSTGE'), ('BIOIVTONC', 'CONFTNMSTG'), ('FOLLOWUP', 'TNMSTG'), ('OUTCOME', 'AJCC_TNM_DLC')) if 'regex(' in str(rows(dd, f)[n].get('constraint'))]
check("all four TNM questions carry the format check, and it is soft (not strict)", len(tn) == 4 and all(rows(dd, f)[n].get('bind__oc_constraint_type') in (None, 'soft') for f, n in tn), tn)
import re as _re
rx = rows(dd, 'ONCOLOGY')['UICCSTGE']['constraint']; pat = _re.search(r"regex\(\., '(.*)'\)", rx).group(1)
good = ['T2N0M0', 'T3N0M0', 'T1bN0M0', 'pT2 pN1 cM0', 'TisN0M0', 'TXNXMX', 'T2N1M0', 'ypT0N0M0', 'T4aN2bM1c']; bad = ['stage II', 'T5N0M0', 'T2N0', '', 'N0M0', 'T2N4M0', 'II']
check("the TNM check accepts real strings and rejects non-TNM text", all(_re.fullmatch(pat, g) for g in good) and not any(_re.fullmatch(pat, b) for b in bad),
      ([g for g in good if not _re.fullmatch(pat, g)], [b for b in bad if _re.fullmatch(pat, b)]))
# other customers must not be affected
other = copy.deepcopy(raws['Detroit'])
with contextlib.redirect_stdout(io.StringIO()):
    other = _enforce_common_visit(other); other = _backfill_migration_fields(other); other = _sanitize_form_titles(other); other = _ensure_required_forms(other, 'Detroit')
    run_study_spec_files(other, customer_subdomain='acme'); other = _apply_omop_coding(other, 'OMOP_CDM')
oo = rows(other, 'ONCOLOGY')
check("another customer's build is unaffected (no new questions, Stage still free text, old labels)", 'THER_STATUS' not in oo and oo['TUMSTGE']['type'] == 'text' and oo['TUMSTGE']['label'] == 'Stage' and oo['UICCSTGE']['label'] == 'UICC Stage', (oo['TUMSTGE']['type'], oo['TUMSTGE']['label']))
print(f"\n{sum(results)} of {len(results)} checks passed"); sys.exit(0 if all(results) else 1)
