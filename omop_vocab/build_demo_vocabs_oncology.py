"""Oncology demo vocabularies (real codes, partial, NOT clinically reviewed). Built separately from build_demo_vocabs.py so the earlier lists stay untouched.

  snomed_procedures_demo.csv  SNOMED CT procedures: surgery plus the specimen-collection procedures that Detroit sites enter in 'Surgical Procedure'
                              (real data: 'Blood draw' 8,275, 'CSF Collection' 5,670, 'Resection', 'Lobectomy', 'Panhysterectomy' ...)
  snomed_body_demo.csv        SNOMED CT body structures at organ/region level (real data: Lung, Breast, Colon, Uterus, Stomach, Rectum ...), plus
                              the specimen-type substances Detroit also enters as a location (Blood, Cerebrospinal fluid, Urine, Saliva)
  icdo3_topography_demo.csv   ICD-O-3 site groups from the official SEER Site/Type list (code = the SEER site recode range)
Run: .venv/bin/python3 omop_vocab/build_demo_vocabs_oncology.py   (needs internet; no keys)
"""
import csv, json, os, re, time, urllib.parse, urllib.request
OUT = os.path.dirname(os.path.abspath(__file__))
UA = {'User-Agent': 'bioivt-demo-vocab-builder', 'Accept': 'application/json'}
WHOLE = 'https://tx.fhir.org/r4/ValueSet/$expand?url=' + urllib.parse.quote('http://snomed.info/sct?fhir_vs', safe='')

def get(u, tries=4):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=90) as r: return json.loads(r.read().decode('utf-8'))
        except Exception:
            if i == tries - 1: return {}
            time.sleep(1.5 * (i + 1))

def search(term, n):
    x = get(WHOLE + '&filter=' + urllib.parse.quote(term) + f'&count={n}&includeDesignations=true')
    for c in x.get('expansion', {}).get('contains', []):
        fsn = next((d['value'] for d in c.get('designation', []) if (d.get('use') or {}).get('code') == '900000000000003001'), '')
        yield c['code'], (c.get('display') or '').strip(), fsn

def write(name, rows, other='Other (not in list)'):
    with open(os.path.join(OUT, name), 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f); w.writerow(['name', 'label']); w.writerow(['OTHER', other])
        for c, l in rows: w.writerow([c, l])
    print(f"  {name}: {len(rows)} entries (+ OTHER)")

CLEAN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ,\-/'()]{2,80}$")
PROC = """venipuncture|collection of blood specimen|blood sampling|phlebotomy|lumbar puncture|collection of cerebrospinal fluid|cerebrospinal fluid specimen|biopsy|needle biopsy|core needle biopsy|fine needle aspiration|excisional biopsy|incisional biopsy|punch biopsy|bone marrow biopsy|bone marrow aspiration|lymph node biopsy|sentinel lymph node biopsy|excision|excision of tumor|resection|wedge resection|segmental resection|lobectomy|pneumonectomy|lung resection|mastectomy|lumpectomy|partial mastectomy|radical mastectomy|colectomy|hemicolectomy|sigmoidectomy|low anterior resection|abdominoperineal resection|proctectomy|gastrectomy|esophagectomy|hepatectomy|liver resection|pancreatectomy|pancreaticoduodenectomy|splenectomy|cholecystectomy|nephrectomy|radical nephrectomy|partial nephrectomy|cystectomy|prostatectomy|radical prostatectomy|transurethral resection|orchiectomy|hysterectomy|total abdominal hysterectomy|salpingo-oophorectomy|oophorectomy|thyroidectomy|adrenalectomy|craniotomy|laparotomy|laparoscopy|thoracotomy|thoracoscopy|mediastinoscopy|bronchoscopy|endoscopy|colonoscopy|polypectomy|lymphadenectomy|lymph node dissection|axillary dissection|debulking|amputation|transplantation|stem cell transplant|apheresis|paracentesis|thoracentesis|tracheostomy|colostomy|ileostomy|ablation|radiofrequency ablation|embolization|mohs|wide local excision|cone biopsy|dilation and curettage|urine specimen collection|saliva specimen collection|collection of specimen""".split('|')
proc = {}
for t in PROC:
    for code, disp, fsn in search(t, 60):
        if fsn.endswith('(procedure)') and CLEAN.match(disp) and len(disp) <= 70: proc[code] = disp
    time.sleep(0.2)
write('snomed_procedures_demo.csv', sorted(proc.items(), key=lambda kv: kv[1].lower()))

ORGANS = """lung|breast|colon|rectum|stomach|esophagus|liver|pancreas|gallbladder|bile duct|small intestine|duodenum|jejunum|ileum|appendix|anus|kidney|renal pelvis|ureter|urinary bladder|urethra|prostate|testis|penis|scrotum|ovary|uterus|cervix uteri|endometrium|myometrium|vagina|vulva|fallopian tube|thyroid gland|parathyroid gland|adrenal gland|pituitary gland|brain|cerebrum|cerebellum|brain stem|spinal cord|meninges|eye|orbit|ear|nose|nasal cavity|paranasal sinus|larynx|pharynx|nasopharynx|oropharynx|tongue|mouth|lip|gum|palate|tonsil|salivary gland|parotid gland|trachea|bronchus|pleura|mediastinum|thymus|heart|aorta|vena cava|bone marrow|lymph node|spleen|skin|subcutaneous tissue|skeletal muscle|soft tissue|bone|rib|vertebra|pelvis|femur|humerus|tibia|skull|mandible|chest wall|abdominal wall|peritoneum|omentum|retroperitoneum|abdomen|thorax|head|neck|axilla|groin|upper limb|lower limb|hand|foot|knee|hip|shoulder|large intestine|colon sigmoid|cecum|ascending colon|transverse colon|descending colon|rectosigmoid|lacrimal gland|thoracic duct|diaphragm|uterine structure|ovarian structure|testicular structure|prostatic structure|renal structure|hepatic structure|gastric structure|cardiac structure|colonic structure|rectal structure|esophageal structure|pancreatic structure|splenic structure|cerebral structure|bronchial structure|vesical structure|cervical structure|thyroid structure|mammary structure|nodal structure""".split('|')
body = {}
for t in ORGANS:
    for code, disp, fsn in search(t, 20):
        if fsn.endswith('(body structure)') and CLEAN.match(disp) and len(disp) <= 55 and t.split()[0].lower() in disp.lower(): body[code] = disp
    time.sleep(0.2)
for t in ('Blood', 'Cerebrospinal fluid', 'Urine', 'Saliva', 'Serum', 'Plasma'):          # specimen types the data also enters as a location
    for code, disp, fsn in search(t, 8):
        if fsn.lower() == f"{t.lower()} (substance)" or fsn.lower() == f"{t.lower()} (body substance)": body[code] = disp
    time.sleep(0.2)
write('snomed_body_demo.csv', sorted(body.items(), key=lambda kv: kv[1].lower()))

import openpyxl
src = os.path.join(OUT, '..', 'tmp', 'icdo3_sitetype.xlsx')
ws = openpyxl.load_workbook(src, read_only=True, data_only=True).active
sites = {}
for r in ws.iter_rows(min_row=2, values_only=True):
    if r[0] and r[1]: sites[str(r[0]).strip()] = str(r[1]).strip()
def nice(d): return d[0] + d[1:].lower() if d.isupper() else d
write('icdo3_topography_demo.csv', sorted(((c, f"{nice(d)} ({c})") for c, d in sites.items()), key=lambda kv: kv[1].lower()))
print('done')
