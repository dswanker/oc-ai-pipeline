"""Builds the DEMONSTRATION vocabularies (real codes, partial, NOT clinically reviewed) used for type-ahead lookups.

Sources (all open, no key):
  RxNorm : NLM RxNav REST API  (https://rxnav.nlm.nih.gov)            prescribable ingredients + brand names, real RxCUIs
  SNOMED : HL7 public terminology server (https://tx.fhir.org)       real SNOMED CT concept ids for chosen disease families
  LOINC  : NLM Clinical Tables (https://clinicaltables.nlm.nih.gov)   real LOINC codes, common tests only
  UCUM   : curated list of common unit codes (https://ucum.org)

Output: omop_vocab/{rxnorm,snomed,loinc}_demo.csv and ucum_units_demo.csv with columns name,label where
name = the real code (this is what OpenClinica stores) and label = the term staff see and search.
Every file starts with an OTHER row so nothing becomes un-enterable.
Run: .venv/bin/python3 omop_vocab/build_demo_vocabs.py
"""
import csv, json, os, re, sys, time, urllib.parse, urllib.request
OUT = os.path.dirname(os.path.abspath(__file__))
UA = {'User-Agent': 'bioivt-demo-vocab-builder', 'Accept': 'application/json'}

def get(url, tries=4, wait=1.5):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=90) as r:
                return json.loads(r.read().decode('utf-8'))
        except Exception as e:
            if i == tries - 1: raise
            time.sleep(wait * (i + 1))

def write(name, rows, other_label='Other (not in list)'):
    with open(os.path.join(OUT, name), 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f); w.writerow(['name', 'label']); w.writerow(['OTHER', other_label])
        for code, label in rows: w.writerow([code, label])
    print(f"  {name}: {len(rows)} entries (+ OTHER)")

# ---------------------------------------------------------------- RxNorm
print('RxNorm ...')
CLEAN = re.compile(r"[A-Za-z][A-Za-z0-9 \-/']{2,44}")
rx = {}
for tty, suffix in (('IN', ''), ('BN', ' (brand)')):
    for c in get(f'https://rxnav.nlm.nih.gov/REST/Prescribe/allconcepts.json?tty={tty}')['minConceptGroup']['minConcept']:
        n = c['name'].strip()
        if CLEAN.fullmatch(n) and not re.search(r'\d{3,}', n):
            rx[c['rxcui']] = (n[0].upper() + n[1:]) + suffix
rows = sorted(rx.items(), key=lambda kv: kv[1].lower())
write('rxnorm_demo.csv', rows)

# ---------------------------------------------------------------- SNOMED CT
print('SNOMED CT ...')
ROOTS = {  # root concept id -> max entries to take
    '85828009': 600, '69896004': 300, '55464009': 200, '200936003': 200, '9014002': 200, '3723001': 300, '396275006': 150,
    '363346000': 2500, '840539006': 150, '26929004': 150, '52448006': 300, '24700007': 150, '86044005': 100, '49049000': 150,
    '73211009': 300, '38341003': 150, '195967001': 150, '13645005': 100, '56265001': 400, '90708001': 300, '235856003': 300,
    '128241005': 200, '86406008': 100, '56717001': 100, '414916001': 100, '35489007': 150, '84757009': 150, '230690007': 150,
    '271737000': 200, '14304000': 200, '34000006': 100, '64766004': 100, '396331005': 60, '83901003': 40, '89155008': 100,
    '40733004': 600,  # infectious disease
}
sn = {}
for root, cap in ROOTS.items():
    got, off = 0, 0
    while got < cap:
        u = ('https://tx.fhir.org/r4/ValueSet/$expand?url=' + urllib.parse.quote(f'http://snomed.info/sct?fhir_vs=isa/{root}', safe='')
             + f'&count=500&offset={off}')
        try: exp = get(u).get('expansion', {})
        except Exception as e:
            print(f'   root {root} offset {off} failed: {str(e)[:60]}'); break
        items = exp.get('contains', [])
        for it in items:
            d = (it.get('display') or '').strip()
            if d and len(d) <= 80 and not re.search(r'[\^<>\[\]{}]', d) and it['code'] not in sn:
                sn[it['code']] = d; got += 1
                if got >= cap: break
        if len(items) < 500: break
        off += 500; time.sleep(0.4)
    time.sleep(0.4)
rows = sorted(sn.items(), key=lambda kv: kv[1].lower())
write('snomed_demo.csv', rows)

# ---------------------------------------------------------------- LOINC
print('LOINC ...')
KEYWORDS = """glucose creatinine hemoglobin hematocrit leukocytes platelets erythrocytes neutrophils lymphocytes monocytes eosinophils basophils
sodium potassium chloride bicarbonate calcium magnesium phosphate urea nitrogen albumin protein bilirubin alkaline phosphatase alanine aspartate
gamma glutamyl lactate dehydrogenase amylase lipase cholesterol triglyceride lipoprotein troponin natriuretic creatine kinase myoglobin
ferritin iron transferrin vitamin folate thyrotropin thyroxine triiodothyronine cortisol insulin prolactin testosterone estradiol progesterone
antigen prostate carcinoembryonic cancer alpha fetoprotein chorionic gonadotropin interleukin tumor necrosis factor reactive protein sedimentation
rheumatoid antinuclear antibody double stranded DNA complement smith ribonucleoprotein cardiolipin anticoagulant HLA cyclic citrullinated
hepatitis HIV cytomegalovirus Epstein syphilis treponema SARS-CoV-2 influenza respiratory syncytial herpes toxoplasma rubella measles
prothrombin INR thromboplastin fibrinogen dimer hemoglobin A1c microalbumin urinalysis urine specific gravity pH ketones nitrite
culture gram stain blood gas oxygen carbon dioxide ammonia uric acid osmolality lithium digoxin vancomycin
beta amyloid tau neurofilament oligoclonal immunoglobulin electrophoresis kappa lambda free light chains""".split()
KEYWORDS = sorted(set(KEYWORDS))
SPEC = re.compile(r' in (Serum|Plasma|Blood|Urine|Serum or Plasma|Venous blood|Arterial blood|Capillary blood|Cerebral spinal fluid|Saliva|Stool|Red Blood Cells|Platelets|24 hour Urine)\b', re.I)
BAD = re.compile(r'panel|challenge|survey|report|questionnaire|estimated|calculated|ratio of|during|tolerance|observed|reported|^deprecated|^discouraged|^trial', re.I)
cand = {}
for kw in KEYWORDS:
    try: d = get('https://clinicaltables.nlm.nih.gov/api/loinc_items/v3/search?terms=' + urllib.parse.quote(kw) + '&maxList=500&df=LOINC_NUM,LONG_COMMON_NAME')
    except Exception as e:
        print(f'   {kw} failed: {str(e)[:50]}'); continue
    for code, label in d[3]:
        if '[' in label and ']' in label and SPEC.search(label) and not BAD.search(label) and len(label) <= 90:
            cand[code] = label
    time.sleep(0.25)
ranked = sorted(cand.items(), key=lambda kv: (' by ' in kv[1], len(kv[1])))[:3500]
rows = sorted(ranked, key=lambda kv: kv[1].lower())
write('loinc_demo.csv', rows)

# ---------------------------------------------------------------- UCUM units (curated common subset)
UNITS = [('mg/dL', 'mg/dL'), ('g/dL', 'g/dL'), ('g/L', 'g/L'), ('mg/L', 'mg/L'), ('ug/L', 'ug/L'), ('ng/mL', 'ng/mL'), ('pg/mL', 'pg/mL'), ('ug/mL', 'ug/mL'),
         ('ug/dL', 'ug/dL'), ('ng/dL', 'ng/dL'), ('pg/dL', 'pg/dL'), ('mmol/L', 'mmol/L'), ('umol/L', 'umol/L'), ('nmol/L', 'nmol/L'), ('pmol/L', 'pmol/L'),
         ('mEq/L', 'mEq/L'), ('U/L', 'U/L'), ('U/mL', 'U/mL'), ('[IU]/L', 'IU/L'), ('[IU]/mL', 'IU/mL'), ('m[IU]/L', 'mIU/L'), ('u[IU]/mL', 'uIU/mL'),
         ('10*3/uL', '10^3/uL (K/uL)'), ('10*6/uL', '10^6/uL (M/uL)'), ('10*9/L', '10^9/L'), ('10*12/L', '10^12/L'), ('/uL', '/uL'), ('/L', '/L'), ('/mL', '/mL'),
         ('%', '%'), ('fL', 'fL'), ('pg', 'pg'), ('g', 'g'), ('mg', 'mg'), ('ug', 'ug (mcg)'), ('ng', 'ng'), ('kg', 'kg'), ('mL', 'mL'), ('L', 'L'), ('dL', 'dL'),
         ('mm/h', 'mm/h'), ('s', 'seconds'), ('min', 'minutes'), ('h', 'hours'), ('d', 'days'), ('mo', 'months'), ('a', 'years'), ('cm', 'cm'), ('mm', 'mm'),
         ('mm[Hg]', 'mmHg'), ('Cel', 'degrees Celsius'), ('[degF]', 'degrees Fahrenheit'), ('{ratio}', 'ratio'), ('{titer}', 'titer'), ('{index_val}', 'index value'),
         ('mL/min/{1.73_m2}', 'mL/min/1.73 m2'), ('mL/min', 'mL/min'), ('mosm/kg', 'mOsm/kg'), ('g/24.h', 'g/24 h'), ('mg/24.h', 'mg/24 h'), ('mg/g', 'mg/g'),
         ('ug/g', 'ug/g'), ('mg/mmol', 'mg/mmol'), ('{copies}/mL', 'copies/mL'), ('[arb\'U]/mL', 'arbitrary units/mL'), ('{S_CO_ratio}', 'S/CO ratio'), ('kU/L', 'kU/L')]
print('UCUM units ...'); write('ucum_units_demo.csv', UNITS, 'Other unit (not in list)')
print('done')
