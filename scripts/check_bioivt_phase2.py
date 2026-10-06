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
SPECS = {'Detroit': 'tmp/Detroit_DS_Study_Specification_CORRECTED_v8.json', 'Precision': 'tmp/PrecisionMed_Study_Specification_CORRECTED_v6.json'}
def build(raw, name):
    s = copy.deepcopy(raw)
    with contextlib.redirect_stdout(io.StringIO()):
        s = _enforce_common_visit(s); s = _backfill_migration_fields(s); s = _sanitize_form_titles(s); s = _ensure_required_forms(s, name)
        run_study_spec_files(s, customer_subdomain='bioIVT'); s = _apply_omop_coding(s, 'OMOP_CDM')
    return s
r = Path(tempfile.mkdtemp()); shutil.copytree(ROOT / 'conventions_engine' / 'conventions', r / 'conventions')
for slug in PHASE2: (r / 'conventions' / 'customers' / 'bioIVT' / f'{slug}.json').unlink()
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

# ---------------------------------------------------------------- vocabulary files
V = {n: list(csv.DictReader(open(f'omop_vocab/{n}.csv'))) for n in ('rxnorm_demo', 'snomed_demo', 'loinc_demo', 'ucum_units_demo', 'snomed_procedures_demo', 'snomed_body_demo', 'icdo3_topography_demo')}
for n, rr in V.items():
    codes = [x['name'] for x in rr]
    check(f'{n}.csv: header name,label; first row OTHER; no duplicate codes; {len(rr)-1} entries', list(rr[0].keys()) == ['name', 'label'] and codes[0] == 'OTHER' and len(codes) == len(set(codes)))
check('RxNorm and SNOMED codes are numeric; LOINC codes look like 1234-5 (real code shapes)', all(x['name'].isdigit() for x in V['rxnorm_demo'][1:] + V['snomed_demo'][1:]) and all(re.fullmatch(r'\d+-\d', x['name']) for x in V['loinc_demo'][1:]))
known = {'rxnorm_demo': {'5640': 'Ibuprofen', '8640': 'Prednisone'}, 'snomed_demo': {'69896004': 'Rheumatoid arthritis', '55464009': 'Systemic lupus erythematosus', '9014002': 'Psoriasis'},
         'loinc_demo': {'1988-5': 'C reactive protein', '2160-0': 'Creatinine', '718-7': 'Hemoglobin'}}
check('known real codes map to their real terms', all(any(x['name'] == c and t.lower() in x['label'].lower() for x in V[n]) for n, d in known.items() for c, t in d.items()))
check('list sizes within the planned range (RxNorm<=10k, SNOMED<=8k, LOINC<=4k)', len(V['rxnorm_demo']) <= 10000 and len(V['snomed_demo']) <= 8000 and len(V['loinc_demo']) <= 4000)

# ---------------------------------------------------------------- Detroit: the lookups
D, DB = after['Detroit'], before['Detroit']
EXPECT = {'rxnorm_demo.csv': [('PSORIASIS', 'ORALSTER'), ('PSORIASIS', 'TOP'), ('PSORIASIS', 'OTHR'), ('CM', 'CMTRT')],
          'snomed_demo.csv': [('INCEXC', 'DONOR_DIAGNOSIS'), ('FOLLOWUP', 'SPECDIAG'), ('OUTCOME', 'SPECDIAG'), ('ONCOLOGY', 'SPECIDIAG'), ('BIOIVT' + 'ONC', 'DIAG_CONF'), ('MH', 'DONDIAG'), ('CM', 'CMINDIC'), ('DD', 'PRCDTH')],
          'loinc_demo.csv': [('ONCOLOGY', 'OTHTEST'), ('TESTRES', 'OTHTEST'), ('ONCOLOGY', 'MARKOTH')],
          'ucum_units_demo.csv': [('ONCOLOGY', 'BLDUNIT'), ('ONCOLOGY', 'MARKUNIT'), ('TESTRES', 'BLDUNIT')],
          'snomed_procedures_demo.csv': [('ONCOLOGY', 'SURGIPROC'), ('FOLLOWUP', 'SURGPROC'), ('OUTCOME', 'SURGPROC')],
          'snomed_body_demo.csv': [('ONCOLOGY', 'ANALOC'), ('BIOIVTONC', 'ANATLOC'), ('FOLLOWUP', 'ANATLOC'), ('OUTCOME', 'ANATLOC')],
          'icdo3_topography_demo.csv': [('ONCOLOGY', 'TUMLOCA'), ('BIOIVTONC', 'TUM_LOC_CONF')]}
for f, fl in EXPECT.items():
    check(f'{f}: all {len(fl)} planned fields are type-ahead lookups', all(rows(D, fid)[n]['type'] == f'select_one_from_file {f}' and rows(D, fid)[n].get('appearance') == 'minimal autocomplete' for fid, n in fl), [(a, b, rows(D, a)[b]['type']) for a, b in fl if rows(D, a)[b]['type'] != f'select_one_from_file {f}'])
MULTI = [('OUTCOME', n) for n in ('CHEMO_AGENTS', 'MTT_AGENTS', 'IMMUNO_AGENTS', 'HORMONE_AGENTS')]
check('the 4 therapy-agent questions are multi-select lookups', all(rows(D, a)[n]['type'] == 'select_multiple_from_file rxnorm_demo.csv' and rows(D, a)[n]['appearance'] == 'minimal' for a, n in MULTI))
UNIT_ROWS = [('RA', 'ESR_RES'), ('RA', 'CRP_RES'), ('LUPUS', 'RESRD1B'), ('LUPUS', 'RESHD2'), ('LUPUS', 'RESHD3'), ('LUPUS', 'RESHD4')]
check('6 lab values that had no unit question gain a unit lookup right after them, inheriting their show/hide rule', all(order(D, a)[order(D, a).index(n) + 1] == n + '_U' and rows(D, a)[n + '_U']['type'] == 'select_one_from_file ucum_units_demo.csv' and rows(D, a)[n + '_U'].get('relevant') == rows(D, a)[n].get('relevant') for a, n in UNIT_ROWS))
NUM = [('ONCOLOGY', 'BLDVALUE'), ('ONCOLOGY', 'MARKVALUE'), ('TESTRES', 'BLDVALUE'), ('RA', 'SERRHEUM_UNIT1'), ('RA', 'SERRHEUM_UNIT2'), ('RA', 'ESR_RES'), ('RA', 'CRP_RES'), ('LUPUS', 'RESRD1B'), ('LUPUS', 'RESHD2'), ('LUPUS', 'RESHD3'), ('LUPUS', 'RESHD4')]
check('11 lab result values are decimal numbers', all(rows(D, a)[n]['type'] == 'decimal' for a, n in NUM))
SINGLE_OTH = [x for fl in EXPECT.values() for x in fl]
def oth_ok(a, n, multi=False):
    o = rows(D, a).get(n + '_OTH'); ords = order(D, a)
    want = f"selected(${{{n}}}, 'OTHER')" if multi else f"${{{n}}}='OTHER'"
    return bool(o) and o['type'] == 'text' and o['relevant'] == want and ords[ords.index(n) + 1] == n + '_OTH' and ords.count(n + '_OTH') == 1
check("every lookup has exactly one 'Other (not in list), specify' row right after it (single: ='OTHER'; multi: selected())", all(oth_ok(a, n) for a, n in SINGLE_OTH) and all(oth_ok(a, n, True) for a, n in MULTI))
check("'OTHER' exists as a real choice in every list those rows test", all(V[f.split('.')[0]][0]['name'] == 'OTHER' for f in EXPECT))

# ---------------------------------------------------------------- every file referenced is registered; per-form attachments
refs = collections.defaultdict(set)
for f in D['forms']:
    for r in f['survey']:
        m = re.search(r'select_(?:one|multiple)_from_file\s+(\S+\.csv)', str(r.get('type', '')))
        if m: refs[f['form_id']].add(m.group(1))
allref = set().union(*refs.values())
check('every list a form references is registered in the spec so the builder writes it', allref <= set(D['_omop_vocab_files']) and allref == {'rxnorm_demo.csv', 'snomed_demo.csv', 'loinc_demo.csv', 'ucum_units_demo.csv', 'snomed_procedures_demo.csv', 'snomed_body_demo.csv', 'icdo3_topography_demo.csv'}, allref)
old_total = sum(len(v.encode()) for v in D['_omop_vocab_files'].values()) * len(D['forms'])
new_total = sum(len(D['_omop_vocab_files'][c].encode()) for fid in refs for c in refs[fid])
print(f'      (upload volume of list files per run: old behaviour {old_total/1e6:.1f} MB across {len(D["forms"])} forms -> now {new_total/1e6:.1f} MB across {len(refs)} forms that use them)')
check('lists are attached only to forms that use them (new volume is a fraction of the old)', new_total < old_total / 3 and set(refs) == {'CM', 'MH', 'PSORIASIS', 'INCEXC', 'FOLLOWUP', 'OUTCOME', 'ONCOLOGY', 'BIOIVTONC', 'TESTRES', 'RA', 'LUPUS', 'DD'}, sorted(refs))

# ---------------------------------------------------------------- DIN
for st in ('Detroit', 'Precision'):
    din = [(f['form_id'], r) for f in after[st]['forms'] for r in f['survey'] if r.get('name') in ('DIN', 'FOLLOW_DIN')]
    check(f'{st}: all {len(din)} DIN questions are text with the same rule (<=30 characters) and nothing else', din and all(r['type'] == 'text' and r.get('constraint') == 'string-length(.) <= 30' and r.get('constraint_message') for _, r in din), [(f, r['name'], r.get('constraint')) for f, r in din if r.get('constraint') != 'string-length(.) <= 30'])

# ---------------------------------------------------------------- autofill
SF = rows(D, 'SHORTFORM'); sf_events = next(f for f in D['forms'] if f['form_id'] == 'SHORTFORM')['visits_assigned']
AUTO = {'FOLLOWUP': ['DOB', 'GENDER', 'RACE', 'ETH'], 'OUTCOME': ['DIN', 'DOB', 'GENDER', 'RACE', 'ETH']}
ok = True; why = []
for fid, names in AUTO.items():
    R, RB, form = rows(D, fid), rows(DB, fid), next(f for f in D['forms'] if f['form_id'] == fid)
    for n in names:
        h, m, o = R.get(n + '_CF'), R.get(n + '_SF'), R[n]
        good = (h and h['type'] == 'calculate' and h['bind__oc_external'] == 'clinicaldata' and "FormOID='F_SHORTFORM'" in h['calculation'] and f"ItemGroupName='{SF[n]['bind__oc_itemgroup']}'" in h['calculation'] and f"ItemName='{n}'" in h['calculation']
                and m and m['readonly'] == 'yes' and m['calculation'] == f'${{{n}_CF}}' and m['relevant'] == f"${{{n}_CF}}!=''" and m['bind__oc_itemgroup'] == o['bind__oc_itemgroup']
                and o['relevant'].endswith(f"${{{n}_CF}}=''") and (RB[n].get('relevant') is None or o['relevant'] == f"({RB[n]['relevant']}) and ${{{n}_CF}}=''")
                and order(D, fid)[order(D, fid).index(n) + 1:order(D, fid).index(n) + 3] == [n + '_CF', n + '_SF'] and o['type'] == RB[n]['type'])
        if not good: ok = False; why.append((fid, n))
check('autofill: all 9 questions keep their type, hide only while the lookup is blank, and get a hidden lookup + read-only line', ok, why)
check("autofill: forms may read the Short Form's 7 events (crossform_references) and document each lookup", all(set(next(f for f in D['forms'] if f['form_id'] == fid)['settings']['crossform_references'].split(',')) >= set(sf_events) and len(next(f for f in D['forms'] if f['form_id'] == fid)['cross_form_dependencies']) >= len(AUTO[fid]) for fid in AUTO))

# ---------------------------------------------------------------- calculated months
fu = rows(D, 'FOLLOWUP')
check('months disease-free: read-only calculation from remission/therapy-end to recurrence/follow-up date; still shown only when status is Disease free',
      fu['MONDISFREE']['readonly'] == 'yes' and 'REMISSDT' in fu['MONDISFREE']['calculation'] and 'REOCCURDT' in fu['MONDISFREE']['calculation'] and 'THER_ENDDT' in fu['MONDISFREE']['calculation'] and 'DTFUPROV' in fu['MONDISFREE']['calculation'] and fu['MONDISFREE']['relevant'] == "${STATUS}='1'" and fu['MONDISFREE']['type'] == 'integer')
c = fu['DTHDAT_CF']['calculation']
check('months survived: read-only; looks up DD date of death on the common event; falls back to the follow-up date', fu['MONSURV']['readonly'] == 'yes' and 'DTHDAT_CF' in fu['MONSURV']['calculation'] and 'DIAGDT' in fu['MONSURV']['calculation'] and "StudyEventOID='SE_COMMON'" in c and "F_DD" in c and "ItemGroupName='DD'" in c and 'SE_COMMON' in next(f for f in D['forms'] if f['form_id'] == 'FOLLOWUP')['settings']['crossform_references'])

# ---------------------------------------------------------------- nothing else changed
ch, add, rem = rowdiff(DB, D)
exp_changed = set(SINGLE_OTH) | set(MULTI) | set(NUM) | {(f, n) for f, ns in AUTO.items() for n in ns} | {('FOLLOWUP', 'MONDISFREE'), ('FOLLOWUP', 'MONSURV')} | {(f, 'DIN') for f in ('SHORTFORM', 'OUTCOME')}
exp_added = {(a, n + '_OTH') for a, n in SINGLE_OTH + MULTI} | {(a, n + '_U') for a, n in UNIT_ROWS} | {(f, n + s) for f, ns in AUTO.items() for n in ns for s in ('_CF', '_SF')} | {('FOLLOWUP', 'DTHDAT_CF')}
exp_changed -= {('CM', 'CMTRT'), ('MH', 'DONDIAG')}   # these two already use the real lists in BOTH builds (the code registry, not a convention); only their Other row is new
check('DETROIT: exactly the expected questions changed or were added, none removed, nothing else touched', ch == exp_changed and add == exp_added and not rem, (sorted(ch ^ exp_changed)[:6], sorted(add ^ exp_added)[:6], sorted(rem)[:3]))
chp, addp, remp = rowdiff(before['Precision'], after['Precision'])
check("PRECISION: only the DIN rule (3 questions), the medication and indication lookups on CM, and the hospitalisation-reason lookup on MH changed (each with its Other row)",
      chp == {('DM', 'DIN'), ('DM', 'FOLLOW_DIN'), ('DMONC', 'DIN'), ('CM', 'CMINDIC'), ('MH', 'MHHOSPREASON')} | ({('CM', 'CMTRT')} if before['Precision'] and rows(before['Precision'], 'CM')['CMTRT']['type'] != rows(after['Precision'], 'CM')['CMTRT']['type'] else set())
      and addp == {('CM', 'CMTRT_OTH'), ('CM', 'CMINDIC_OTH'), ('MH', 'MHHOSPREASON_OTH')} and not remp, (sorted(chp), sorted(addp)))

# ---------------------------------------------------------------- structure
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
for st in ('Detroit', 'Precision'):
    check(f'{st}: no new dangling ${{references}}', not (dangling(after[st]) - dangling(before[st])), sorted(dangling(after[st]) - dangling(before[st]))[:4])
    names_all = [[r.get('name') for r in f['survey'] if r.get('name')] for f in after[st]['forms']]
    check(f'{st}: no duplicate question names inside any form that were not already there', all(len(set(n)) == len(n) for n in names_all) or all(len(set(n)) == len(n) for n in [[r.get('name') for r in f['survey'] if r.get('name')] for f in before[st]['forms']]))
check('board unchanged by Phase 2 (still 77 cards on the same events)', len(_build_board_json(after['Detroit'])['cards']) == len(_build_board_json(before['Detroit'])['cards']) == 77)
flags = {k for k, v in after['Detroit'].get('review_flags', {}).items() if v and k in ('vocabulary_missing', 'lookup_skipped', 'assemble_form_skipped', 'convention_load_errors')}
check('no convention load errors, no skipped lookups or assemblies', not flags, flags)
print(f"\n{sum(results)} of {len(results)} checks passed" + ("" if all(results) else "   <-- FAILURES ABOVE"))
sys.exit(0 if all(results) else 1)
