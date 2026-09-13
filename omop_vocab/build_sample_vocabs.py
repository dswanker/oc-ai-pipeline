"""
Sample/test-only vocabulary generator for OMOP_CDM demo mode.

NOT VERIFIED REAL RXNORM/SNOMED DATA. Codes are placeholder test IDs
(RXTEST-#####, SNOMEDTEST-#####), not looked up against a live
vocabulary API. Purpose: give BioIVT a realistic-scale searchable
picklist (select_one_from_file + appearance=minimal) so they can see
how the search UX feels with real-world-sized lists, using real,
therapeutic-area-relevant drug/diagnosis NAMES. Do not treat the
codes as clinically valid -- swap in real RxNorm/SNOMED CUIs before
any real study goes live with real patient data.
"""
import csv

# ---- Carlsbad (Neurology: AD/MCI, MS, ALS, PD, PSP, FTD + Oncology/Balkan arm) ----
carlsbad_meds = [
    # AD/MCI/dementia
    "Donepezil","Rivastigmine","Galantamine","Memantine","Memantine ER",
    "Aducanumab","Lecanemab","Donanemab",
    # MS
    "Interferon beta-1a","Interferon beta-1b","Glatiramer acetate","Natalizumab",
    "Fingolimod","Siponimod","Ozanimod","Ponesimod","Dimethyl fumarate",
    "Diroximel fumarate","Monomethyl fumarate","Teriflunomide","Cladribine",
    "Alemtuzumab","Ocrelizumab","Ofatumumab","Rituximab",
    # ALS
    "Riluzole","Edaravone","Sodium phenylbutyrate/taurursodiol","Tofersen",
    # Parkinson's / PSP
    "Levodopa/carbidopa","Levodopa/carbidopa/entacapone","Pramipexole",
    "Ropinirole","Rotigotine","Apomorphine","Selegiline","Rasagiline","Safinamide",
    "Amantadine","Trihexyphenidyl","Benztropine","Entacapone","Opicapone",
    "Istradefylline",
    # FTD (symptomatic/off-label)
    "Sertraline","Citalopram","Trazodone","Quetiapine",
    # General / neuro-supportive
    "Aspirin","Clopidogrel","Atorvastatin","Rosuvastatin","Simvastatin",
    "Lisinopril","Losartan","Amlodipine","Metoprolol","Metformin",
    "Levothyroxine","Omeprazole","Pantoprazole","Gabapentin","Pregabalin",
    "Baclofen","Tizanidine","Diazepam","Clonazepam","Lorazepam","Alprazolam",
    "Sertraline","Escitalopram","Duloxetine","Venlafaxine","Bupropion",
    "Melatonin","Zolpidem","Trazodone","Mirtazapine","Vitamin D3","Vitamin B12",
    "Folic acid","Calcium carbonate","Multivitamin","Docusate","Senna",
    "Polyethylene glycol","Loperamide","Famotidine","Acetaminophen","Ibuprofen",
    "Naproxen","Tramadol","Oxycodone","Hydrocodone/acetaminophen","Morphine ER",
    "Fentanyl patch","Warfarin","Apixaban","Rivaroxaban","Dabigatran",
    "Furosemide","Hydrochlorothiazide","Spironolactone","Carvedilol","Digoxin",
    "Insulin glargine","Insulin lispro","Glipizide","Sitagliptin","Empagliflozin",
    "Albuterol","Fluticasone/salmeterol","Montelukast","Prednisone","Prednisolone",
    "Methylprednisolone","Hydroxychloroquine","Methotrexate","Azathioprine",
    "Mycophenolate mofetil","Cyclophosphamide","Tacrolimus","Cyclosporine",
    "Allopurinol","Colchicine","Cetirizine","Loratadine","Diphenhydramine",
    "Ranitidine","Sucralfate","Metoclopramide","Ondansetron","Prochlorperazine",
    "Promethazine","Meclizine","Sumatriptan","Topiramate","Valproate",
    "Lamotrigine","Levetiracetam","Phenytoin","Carbamazepine","Oxcarbazepine",
    "Zonisamide","Lacosamide",
    # Oncology (Balkan arm)
    "Carboplatin","Cisplatin","Oxaliplatin","Paclitaxel","Docetaxel",
    "Doxorubicin","Cyclophosphamide (oncology)","Gemcitabine","Fluorouracil",
    "Leucovorin","Irinotecan","Etoposide","Vincristine","Vinblastine",
    "Bleomycin","Methotrexate (oncology)","Pemetrexed","Bevacizumab",
    "Trastuzumab","Pertuzumab","Rituximab (oncology)","Pembrolizumab",
    "Nivolumab","Atezolizumab","Durvalumab","Ipilimumab","Tamoxifen",
    "Letrozole","Anastrozole","Exemestane","Fulvestrant","Enzalutamide",
    "Abiraterone","Bicalutamide","Leuprolide","Goserelin","Imatinib",
    "Dasatinib","Nilotinib","Erlotinib","Gefitinib","Osimertinib",
    "Sunitinib","Sorafenib","Pazopanib","Lenvatinib","Regorafenib",
    "Palbociclib","Ribociclib","Abemaciclib","Olaparib","Niraparib",
]

carlsbad_diags = [
    # AD/MCI
    "Alzheimer disease, early onset","Alzheimer disease, late onset",
    "Mild cognitive impairment","Vascular dementia","Mixed dementia",
    "Dementia with Lewy bodies","Subjective cognitive decline",
    # MS
    "Multiple sclerosis, relapsing-remitting","Multiple sclerosis, secondary progressive",
    "Multiple sclerosis, primary progressive","Clinically isolated syndrome",
    "Neuromyelitis optica spectrum disorder","Optic neuritis",
    # ALS / motor neuron
    "Amyotrophic lateral sclerosis","Progressive muscular atrophy",
    "Primary lateral sclerosis","Progressive bulbar palsy",
    # Parkinsonism
    "Parkinson disease","Progressive supranuclear palsy",
    "Multiple system atrophy","Corticobasal degeneration",
    "Drug-induced parkinsonism","Essential tremor",
    # FTD
    "Frontotemporal dementia, behavioral variant",
    "Primary progressive aphasia, semantic variant",
    "Primary progressive aphasia, nonfluent variant",
    "Corticobasal syndrome",
    # General comorbidities
    "Essential hypertension","Type 2 diabetes mellitus","Type 1 diabetes mellitus",
    "Hyperlipidemia","Hypothyroidism","Hyperthyroidism","Obesity",
    "Chronic kidney disease, stage 3","Chronic kidney disease, stage 4",
    "Coronary artery disease","Congestive heart failure","Atrial fibrillation",
    "Chronic obstructive pulmonary disease","Asthma","Obstructive sleep apnea",
    "Major depressive disorder","Generalized anxiety disorder","Insomnia disorder",
    "Osteoarthritis","Osteoporosis","Gastroesophageal reflux disease",
    "Peptic ulcer disease","Irritable bowel syndrome","Chronic constipation",
    "Anemia, unspecified","Iron deficiency anemia","Vitamin B12 deficiency",
    "Vitamin D deficiency","Peripheral neuropathy","Diabetic neuropathy",
    "Migraine without aura","Migraine with aura","Tension-type headache",
    "Epilepsy, focal","Epilepsy, generalized","Seizure disorder, unspecified",
    "Urinary tract infection","Benign prostatic hyperplasia","Urinary incontinence",
    "Deep vein thrombosis","Pulmonary embolism","Stroke, ischemic",
    "Stroke, hemorrhagic","Transient ischemic attack","Traumatic brain injury",
    # Oncology (Balkan arm)
    "Breast cancer, invasive ductal carcinoma","Breast cancer, invasive lobular carcinoma",
    "Prostate cancer, adenocarcinoma","Non-small cell lung cancer, adenocarcinoma",
    "Non-small cell lung cancer, squamous cell","Small cell lung cancer",
    "Colorectal cancer, adenocarcinoma","Pancreatic adenocarcinoma",
    "Ovarian cancer, high-grade serous","Endometrial cancer","Cervical cancer",
    "Bladder cancer, urothelial carcinoma","Renal cell carcinoma",
    "Hepatocellular carcinoma","Cholangiocarcinoma","Gastric adenocarcinoma",
    "Esophageal adenocarcinoma","Head and neck squamous cell carcinoma",
    "Melanoma, cutaneous","Non-Hodgkin lymphoma, diffuse large B-cell",
    "Non-Hodgkin lymphoma, follicular","Hodgkin lymphoma","Multiple myeloma",
    "Chronic lymphocytic leukemia","Acute myeloid leukemia","Glioblastoma",
    "Thyroid cancer, papillary",
]

# ---- Detroit (Lupus, RA, Psoriasis, Oncology, Covid-19, Veterinary, General) ----
detroit_meds = [
    # Lupus
    "Hydroxychloroquine","Prednisone","Methylprednisolone","Azathioprine",
    "Mycophenolate mofetil","Cyclophosphamide","Belimumab","Anifrolumab",
    "Rituximab","Methotrexate (lupus)","Voclosporin","Tacrolimus",
    # Rheumatoid Arthritis
    "Methotrexate","Leflunomide","Sulfasalazine","Hydroxychloroquine (RA)",
    "Etanercept","Adalimumab","Infliximab","Golimumab","Certolizumab pegol",
    "Abatacept","Tocilizumab","Sarilumab","Rituximab (RA)","Tofacitinib",
    "Baricitinib","Upadacitinib","Prednisone (RA)",
    # Psoriasis
    "Topical corticosteroids","Calcipotriene","Tazarotene","Methotrexate (psoriasis)",
    "Cyclosporine (psoriasis)","Acitretin","Apremilast","Adalimumab (psoriasis)",
    "Etanercept (psoriasis)","Infliximab (psoriasis)","Ustekinumab",
    "Secukinumab","Ixekizumab","Brodalumab","Guselkumab","Risankizumab",
    "Tildrakizumab",
    # Oncology
    "Carboplatin","Cisplatin","Paclitaxel","Docetaxel","Doxorubicin",
    "Cyclophosphamide (oncology)","Gemcitabine","Fluorouracil","Leucovorin",
    "Oxaliplatin","Irinotecan","Etoposide","Bevacizumab","Trastuzumab",
    "Pertuzumab","Pembrolizumab","Nivolumab","Atezolizumab","Ipilimumab",
    "Tamoxifen","Letrozole","Anastrozole","Enzalutamide","Abiraterone",
    "Leuprolide","Imatinib","Erlotinib","Osimertinib","Sunitinib","Sorafenib",
    "Palbociclib","Olaparib","Rituximab (oncology)","Vincristine","Bleomycin",
    # Covid-19
    "Remdesivir","Nirmatrelvir/ritonavir","Molnupiravir","Dexamethasone (covid)",
    "Tocilizumab (covid)","Baricitinib (covid)","Bamlanivimab","Casirivimab/imdevimab",
    "Sotrovimab","Tixagevimab/cilgavimab","Heparin","Enoxaparin",
    # General
    "Aspirin","Clopidogrel","Atorvastatin","Rosuvastatin","Lisinopril",
    "Losartan","Amlodipine","Metoprolol","Carvedilol","Furosemide",
    "Hydrochlorothiazide","Spironolactone","Metformin","Insulin glargine",
    "Insulin lispro","Glipizide","Sitagliptin","Empagliflozin","Levothyroxine",
    "Omeprazole","Pantoprazole","Famotidine","Sucralfate","Gabapentin",
    "Pregabalin","Duloxetine","Sertraline","Escitalopram","Trazodone",
    "Mirtazapine","Melatonin","Zolpidem","Alprazolam","Lorazepam","Clonazepam",
    "Acetaminophen","Ibuprofen","Naproxen","Tramadol","Oxycodone",
    "Hydrocodone/acetaminophen","Morphine ER","Fentanyl patch","Warfarin",
    "Apixaban","Rivaroxaban","Vitamin D3","Vitamin B12","Folic acid",
    "Calcium carbonate","Multivitamin","Docusate","Senna","Polyethylene glycol",
    "Loperamide","Cetirizine","Loratadine","Diphenhydramine","Albuterol",
    "Fluticasone/salmeterol","Montelukast","Prednisone (general)","Allopurinol",
    "Colchicine","Ondansetron","Metoclopramide","Meclizine","Sumatriptan",
    "Topiramate","Levetiracetam","Phenytoin",
    # Veterinary
    "Carprofen (canine)","Meloxicam (veterinary)","Gabapentin (veterinary)",
    "Trazodone (veterinary)","Tramadol (veterinary)","Prednisolone (veterinary)",
    "Amoxicillin/clavulanate (veterinary)","Cephalexin (veterinary)",
    "Enrofloxacin (veterinary)","Metronidazole (veterinary)",
    "Furosemide (veterinary)","Pimobendan","Doxycycline (veterinary)",
]

detroit_diags = [
    # Lupus
    "Systemic lupus erythematosus","Lupus nephritis","Cutaneous lupus erythematosus",
    "Drug-induced lupus","Antiphospholipid syndrome",
    # RA
    "Rheumatoid arthritis, seropositive","Rheumatoid arthritis, seronegative",
    "Rheumatoid nodules","Felty syndrome",
    # Psoriasis
    "Plaque psoriasis","Guttate psoriasis","Psoriatic arthritis",
    "Inverse psoriasis","Pustular psoriasis","Erythrodermic psoriasis",
    # Oncology
    "Breast cancer, invasive ductal carcinoma","Prostate cancer, adenocarcinoma",
    "Non-small cell lung cancer, adenocarcinoma","Colorectal cancer, adenocarcinoma",
    "Pancreatic adenocarcinoma","Ovarian cancer, high-grade serous",
    "Bladder cancer, urothelial carcinoma","Renal cell carcinoma",
    "Hepatocellular carcinoma","Non-Hodgkin lymphoma, diffuse large B-cell",
    "Multiple myeloma","Melanoma, cutaneous","Thyroid cancer, papillary",
    "Head and neck squamous cell carcinoma","Gastric adenocarcinoma",
    # Covid-19
    "COVID-19, confirmed","COVID-19, suspected","Post-acute sequelae of COVID-19",
    "COVID-19 pneumonia","Acute respiratory distress syndrome",
    # General
    "Essential hypertension","Type 2 diabetes mellitus","Type 1 diabetes mellitus",
    "Hyperlipidemia","Hypothyroidism","Obesity","Chronic kidney disease, stage 3",
    "Coronary artery disease","Congestive heart failure","Atrial fibrillation",
    "Chronic obstructive pulmonary disease","Asthma","Major depressive disorder",
    "Generalized anxiety disorder","Osteoarthritis","Osteoporosis",
    "Gastroesophageal reflux disease","Anemia, unspecified","Iron deficiency anemia",
    "Peripheral neuropathy","Migraine without aura","Urinary tract infection",
    "Deep vein thrombosis","Pulmonary embolism","Stroke, ischemic",
    # Veterinary
    "Canine hip dysplasia","Canine osteoarthritis","Feline chronic kidney disease",
    "Canine congestive heart failure","Feline hyperthyroidism","Canine diabetes mellitus",
    "Feline diabetes mellitus","Canine atopic dermatitis","Feline asthma",
    "Canine lymphoma","Feline lymphoma","Canine mast cell tumor",
]

def write_vocab(filename, names, prefix):
    with open(filename, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["name", "label"])
        for i, n in enumerate(names, 1):
            w.writerow([f"{prefix}-{i:05d}", n])
    print(f"Wrote {filename}: {len(names)} rows")

write_vocab("rxnorm_cm_carlsbad_sample.csv", carlsbad_meds, "RXTEST")
write_vocab("snomed_diagnoses_carlsbad_sample.csv", carlsbad_diags, "SNOMEDTEST")
write_vocab("rxnorm_cm_detroit_sample.csv", detroit_meds, "RXTEST")
write_vocab("snomed_diagnoses_detroit_sample.csv", detroit_diags, "SNOMEDTEST")
