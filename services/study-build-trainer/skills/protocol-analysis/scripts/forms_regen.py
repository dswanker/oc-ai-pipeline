"""
forms_regen.py — Fresh regeneration of ICF, IE, DM_BL, MH from
Agilis_RF_TSP_EFS_CIP_vA.pdf + Agilis_RF_TSP_EFS_CRF_Case_Book_vA.pdf.

Applies all 30 decisions from form-by-form walkthrough (IE-1..11, MH-1..5,
DM-2..9). Decision list lives in walkthrough notes; conventions §20-§28
codify the patterns where applicable.

Key behaviours embedded:
- §3 group naming: sequential (group0/1/2/...) — IE-1a / MH / DM
- §3 group label: drop section-number prefixes — IE-10
- §4 field naming: CDASH-prefix — IE-2/MH-5/DM-4/DM-6
- §5 choice list naming: UPPERCASE, Yes-first ordering for YN list — IE-3
- §13 briefdescription: auto-fill universal — IE-6
- date constraint: no space (. <=today()) — IE-4
- date cmsg: "Future dates are not allowed." — IE-5
- itemgroup: form_id default — MH-1a
- §22 note `relevant` gating: auto for note-after-YN — MH-3
- §23 hidden-parent-context label rewrite: `If yes` → `If yes, <context>` — MH-4b
- §24 multi-select ambiguity: clinical-reasoning default + flag — MH-2
- §25 eligibility verdict: 3-state, no-space calc, `\n`-formatted — IE-9
- §26 w2 width class for value+unit pairs — DM-3
- §27 sentinel-value exclusivity constraint — DM-7
- §28 decimal precision constraint — DM-8
- BMI calc: 4-combination + rounding — DM-9
- AGE constraint: hybrid eligibility + sanity — DM-2
"""
import sys
sys.path.insert(0, "/home/claude")
from build_agilis import (settings_block, srow, begin_grp, end_grp, note,
                          CL_SEX, CL_ETHNIC)

DATE_CONS = ". <=today()"  # IE-4: no space
DATE_CONS_MSG = "Future dates are not allowed."  # IE-5

LIBSRC_PLACEHOLDER = "PROTOCOL_INFERRED_PLACEHOLDER"

# ── Review flags emitted by the regen ────────────────────────────────────────
# Surfaces in spec_data["review_flags"][bucket] when the build runs.
REVIEW_FLAGS = {
    "choice_list_review": [
        # MH-2: Case Book ambiguity at 2.5.1 (o symbol — radio or checkbox?)
        {
            "form": "MH",
            "field": "MHSURGDEV_TYPE",
            "case_book_section": "2.5.1",
            "issue": ("Case Book renders 2.5.1 as a list of items each "
                      "preceded by 'o', which is ambiguous between radio "
                      "(single-select) and checkbox (multi-select)."),
            "skill_decision": ("Defaulted to multi-select on clinical "
                                "reasoning grounds — subjects can plausibly "
                                "have multiple implanted intracardiac devices "
                                "simultaneously (e.g., pacemaker + "
                                "CardioMEMS)."),
            "action_required": ("Verify with sponsor that multi-select "
                                 "matches their intent. If single-select is "
                                 "preferred, change type to "
                                 "'select_one DEVTYPE'."),
        },
    ],
    "protocol_ambiguous": [
        # MH-4b: source label rewrite for hidden-parent-context disambiguation
        {
            "form": "MH",
            "field": "MHARRVT_TYPE",
            "case_book_section": "1.5.1",
            "case_book_label": "If yes",
            "skill_rewrite": "If yes, type of VT",
            "rationale": ("Case Book label 'If yes' depends on hidden "
                           "parent question context (1.5 Ventricular "
                           "Tachycardia) which is not visible when the "
                           "field renders. Skill rewrote label to be "
                           "self-contained so site users see meaningful "
                           "wording when the gated field appears."),
            "action_required": ("Verify rewrite matches sponsor intent for "
                                 "this question."),
        },
    ],
    "placeholders_for_human_completion": [],  # populated by §0.C reconciliation
}


def _brief(label, max_words=4):
    import re
    if not label:
        return ""
    label = re.sub(r"<[^>]+>", "", label)
    label = re.sub(r"^\s*[\d.]+\s+", "", label)
    return " ".join(label.split()[:max_words]).rstrip(":.,?;").strip()


def _placeholder(name, label, type_, source_section, source_quote,
                  itemgroup, brief, **kw):
    """§0.C placeholder field row."""
    return srow(type=type_, name=name, label=label,
                required="yes", appearance="horizontal",
                completion_status="FLAGGED",
                library_source=LIBSRC_PLACEHOLDER,
                **{"bind::oc:itemgroup": itemgroup,
                   "bind::oc:briefdescription": brief,
                   "protocol_source_section": source_section,
                   "protocol_source_quote": source_quote},
                **kw)


# ── §0.C placeholders for ICF (4) and IE (1) populate review_flags ──────────
def _record_placeholder_flag(form_id, field_name, source_section,
                              source_quote):
    REVIEW_FLAGS["placeholders_for_human_completion"].append({
        "form": form_id, "name": field_name,
        "source_section": source_section,
        "source_quote": source_quote,
        "reason": ("Protocol implies field but no hierarchy source provided "
                    "encoding."),
    })


# ── YN choice list (UPPERCASE, Yes first per IE-3) ──────────────────────────
CL_YN_NEW = [
    {"list_name": "YN", "label": "Yes", "name": "Y"},
    {"list_name": "YN", "label": "No",  "name": "N"},
]
# NYU is NYU (No-Yes-Unknown) — kept since its name explicitly encodes the
# 3-state ordering, AND the customer reviewed it as such on MH already.
# Customer's actual ordering on MH: Yes-No-Unknown ('ynuk'). For consistency
# with IE-3b (Yes-first) we use the YNU pattern.
CL_YNU_NEW = [
    {"list_name": "YNU", "label": "Yes",     "name": "Y"},
    {"list_name": "YNU", "label": "No",      "name": "N"},
    {"list_name": "YNU", "label": "Unknown", "name": "U"},
]


# ════════════════════════════════════════════════════════════════════════════
# ICF — level 3 base + 4 placeholders per §0.C
# ════════════════════════════════════════════════════════════════════════════
icf_survey = [
    begin_grp("group0", ""),
    srow(type="date", name="RFICDAT", label="Date of Informed Consent",
         required="yes",
         constraint=DATE_CONS, constraint_message=DATE_CONS_MSG,
         **{"bind::oc:itemgroup": "ICF",
            "bind::oc:briefdescription": "Date of Informed Consent"}),
    _placeholder(name="RFICLANG_TBD",
                 label="Consent language",
                 type_="select_one ",
                 source_section="§5.2 Informed Consent",
                 source_quote="language … understandable to the patient",
                 itemgroup="ICF",
                 brief="Consent language (placeholder)"),
    _placeholder(name="RFICSITV_TBD",
                 label="Site IRB/EC consent version",
                 type_="select_one ",
                 source_section="§5.2 Informed Consent",
                 source_quote="approved by the center's IRB/EC",
                 itemgroup="ICF",
                 brief="Site IRB/EC version (placeholder)"),
    _placeholder(name="RFICPRTV_TBD",
                 label="Protocol version subject was consented to",
                 type_="select_one ",
                 source_section="protocol header",
                 source_quote="ABT-CIP-10601 Ver. A",
                 itemgroup="ICF",
                 brief="Protocol version (placeholder)"),
    _placeholder(name="RFICPRIOR_TBD",
                 label="Subject signed Informed Consent prior to any "
                       "clinical investigation-specific procedures",
                 type_="select_one YN",
                 source_section="§5.2 Informed Consent",
                 source_quote=("must sign and date the Informed Consent "
                                "form … prior to any clinical "
                                "investigation-specific procedures"),
                 itemgroup="ICF",
                 brief="Pre-procedure consent flag (placeholder)"),
    end_grp(),
]
for ph in ("RFICLANG_TBD","RFICSITV_TBD","RFICPRTV_TBD","RFICPRIOR_TBD"):
    _record_placeholder_flag("ICF", ph, "§5.2 / header", "see field")

ICF_FORM = {
    "form_id": "ICF", "form_title": "Informed Consent",
    "form_category": "INFRASTRUCTURE", "cdash_domain": "RF",
    "visits_assigned": ["SE_BASELINE"],
    "definition_source": "cdash_default",
    "library_source": "CDASH_DEFAULT_NO_LIBRARY_MATCH",
    "settings": settings_block("Informed Consent", "ICF"),
    "survey": icf_survey,
    "choices": list(CL_YN_NEW),
    "cross_form_dependencies": [],
}


# ════════════════════════════════════════════════════════════════════════════
# IE — level 2 (Case Book pp. 1-2)
# ════════════════════════════════════════════════════════════════════════════
IE_INC = [
    ("IEINC1",
     "1.1. Patient must provide written informed consent prior to any "
     "clinical investigation-related procedure."),
    ("IEINC2",
     "1.2. Plans to undergo an ablation procedure in LA, LV, LAAO device "
     "implantation or concomitant procedure with ablation and LAAO device "
     "implantation requiring transseptal puncture."),
    ("IEINC3", "1.3. Patient is at least 18 years of age."),
    ("IEINC4", "1.4. Able and willing to comply with all study requirements."),
]
IE_EXC = [
    ("IEEXC1",
     "2.1. Currently participating in another clinical trial or has "
     "participated in a clinical trial within 30 days prior to screening "
     "that may interfere with this clinical trial without pre-approval "
     "from this study Sponsor."),
    ("IEEXC2",  "2.2. Pregnant or nursing."),
    ("IEEXC3",  "2.3. Known presence of intracardiac thrombus"),
    ("IEEXC4",  "2.4. Known existing circumferential pericardial effusion (>2 mm)"),
    ("IEEXC5",
     "2.5. Previous interatrial septal patch or prosthetic atrial septal "
     "defect closure device"),
    ("IEEXC6",  "2.6. Any previous thromboembolic event within the last 6 months"),
    ("IEEXC7",  "2.7. Known or suspected left atrial myxoma"),
    ("IEEXC8",  "2.8. Known or suspected myocardial infarction within the last "
                "two weeks"),
    ("IEEXC9",  "2.9. Unstable angina"),
    ("IEEXC10", "2.10. Recent (within the last 3 months) cerebral vascular "
                "accident (CVA)"),
    ("IEEXC11", "2.11. Patients with an active systemic infection"),
    ("IEEXC12", "2.12. Patients who do not tolerate anticoagulation therapy"),
    ("IEEXC13",
     "2.13. Presence of other anatomic or comorbid conditions, or other "
     "medical, social, or psychological conditions that, in the "
     "investigator's opinion, could limit the subject's ability to "
     "participate in the clinical investigation or to comply with "
     "follow-up requirements, or impact the scientific soundness of "
     "the clinical investigation results"),
]

ie_survey = [begin_grp("group0", "")]
ie_survey.append(srow(type="date", name="IEDAT",
    label="Date of Informed Consent", required="yes",
    constraint=DATE_CONS, constraint_message=DATE_CONS_MSG,
    **{"bind::oc:itemgroup": "IE",
       "bind::oc:briefdescription": "Date of Informed Consent",
       "library_source": "CUSTOMER_CRF_EXACT"}))
ie_survey.append(srow(type="text", name="IEPHYNAM",
    label="Physician Name", required="yes",
    **{"bind::oc:itemgroup": "IE",
       "bind::oc:briefdescription": "Physician Name",
       "library_source": "CUSTOMER_CRF_EXACT"}))
ie_survey.append(end_grp())

# IE-10: drop section-number prefix from group labels
ie_survey.append(begin_grp("group1", "Inclusion Criteria"))
for nm, lbl in IE_INC:
    ie_survey.append(srow(type="select_one YN", name=nm, label=lbl,
        required="yes", appearance="horizontal",
        **{"bind::oc:itemgroup": "IE",
           "bind::oc:briefdescription": _brief(lbl),
           "library_source": "CUSTOMER_CRF_EXACT"}))
ie_survey.append(end_grp())

ie_survey.append(begin_grp("group2", "Exclusion Criteria"))
for nm, lbl in IE_EXC:
    ie_survey.append(srow(type="select_one YN", name=nm, label=lbl,
        required="yes", appearance="horizontal",
        **{"bind::oc:itemgroup": "IE",
           "bind::oc:briefdescription": _brief(lbl),
           "library_source": "CUSTOMER_CRF_EXACT"}))
ie_survey.append(end_grp())

# IE-8: HIPAA placeholder per §5.2.2.1
ie_survey.append(begin_grp("group3", "HIPAA Authorization"))
ie_survey.append(_placeholder(name="IEHIPAA_TBD",
    label="HIPAA authorization obtained from the subject (or legally "
          "acceptable representative)",
    type_="select_one YN",
    source_section="§5.2.2.1 Special Circumstances for Informed Consent",
    source_quote=("sites must obtain an authorization for use and "
                   "disclosure of the subject's protected health "
                   "information, in accordance with the Health "
                   "Insurance Portability and Accountability Act "
                   "(HIPAA), from the subject"),
    itemgroup="IE",
    brief="HIPAA authorization (placeholder)"))
ie_survey.append(end_grp())
_record_placeholder_flag("IE", "IEHIPAA_TBD",
    "§5.2.2.1 Special Circumstances for Informed Consent",
    "sites must obtain an authorization … HIPAA … from the subject")

# IE-9: 3-state eligibility verdict, no-space calc, `\n` between conditions
def _build_eligibility_calc():
    inc_clauses = [f"${{{nm}}}='Y'" for nm,_ in IE_INC]
    exc_clauses = [f"${{{nm}}}='N'" for nm,_ in IE_EXC]
    all_eligible = " and \n".join(inc_clauses + exc_clauses)
    inc_neg = [f"${{{nm}}}='N'" for nm,_ in IE_INC]
    exc_pos = [f"${{{nm}}}='Y'" for nm,_ in IE_EXC]
    any_disqualifier = " or \n".join(inc_neg + exc_pos)
    return (f"if({all_eligible}, 'Eligible',\n"
             f"if({any_disqualifier}, 'Ineligible', 'Not yet calculated'))")

ie_survey.append(begin_grp("group4", "Subject Eligibility"))
ie_survey.append(srow(type="calculate", name="IEELIG_CALC",
    calculation=_build_eligibility_calc(),
    completion_status="FLAGGED",
    library_source="PROTOCOL_EXTENSION",
    **{"bind::oc:itemgroup": "IE",
       "bind::oc:briefdescription": "Eligibility verdict (calc)"}))
ie_survey.append(srow(type="text", name="IEELIG",
    label="Subject Eligibility",
    calculation="${IEELIG_CALC}", readonly="yes",
    completion_status="FLAGGED",
    library_source="PROTOCOL_EXTENSION",
    **{"bind::oc:itemgroup": "IE",
       "bind::oc:briefdescription": "Eligibility verdict"}))
ie_survey.append(end_grp())

IE_FORM = {
    "form_id": "IE", "form_title": "Inclusion / Exclusion",
    "form_category": "CDASH_CLINICAL", "cdash_domain": "IE",
    "visits_assigned": ["SE_BASELINE"],
    "definition_source": "customer_crf_library",
    "library_source": "CUSTOMER_CRF_EXACT",
    "settings": settings_block("Inclusion / Exclusion", "IE"),
    "survey": ie_survey,
    "choices": list(CL_YN_NEW),
    "cross_form_dependencies": [],
}


# ════════════════════════════════════════════════════════════════════════════
# DM_BL — level 2 (Case Book pp. 3-4)
# ════════════════════════════════════════════════════════════════════════════
CL_RACE = [
    {"list_name":"RACE","label":"Declined","name":"DECLINED"},
    {"list_name":"RACE","label":"American Indian or Alaska Native","name":"AIAN"},
    {"list_name":"RACE","label":"Asian","name":"ASIAN"},
    {"list_name":"RACE","label":"Black or African American","name":"BLACK"},
    {"list_name":"RACE","label":"Native Hawaiian or Other Pacific Islander",
     "name":"NHPI"},
    {"list_name":"RACE","label":"White","name":"WHITE"},
]
CL_ASIAN = [
    {"list_name":"ASIAN_SUB","label":"South Asian (India, Pakistan, "
     "Bangladesh, Sri Lanka etc.)","name":"SOUTH_ASIAN"},
    {"list_name":"ASIAN_SUB","label":"Chinese","name":"CHINESE"},
    {"list_name":"ASIAN_SUB","label":"Filipino","name":"FILIPINO"},
    {"list_name":"ASIAN_SUB","label":"Japanese","name":"JAPANESE"},
    {"list_name":"ASIAN_SUB","label":"Korean","name":"KOREAN"},
    {"list_name":"ASIAN_SUB","label":"Vietnamese","name":"VIETNAMESE"},
    {"list_name":"ASIAN_SUB","label":"Other Asian","name":"OTHER_ASIAN"},
]
CL_HEIGHT_U = [
    {"list_name":"HEIGHT_U","label":"in","name":"IN"},
    {"list_name":"HEIGHT_U","label":"cm","name":"CM"},
]
CL_WEIGHT_U = [
    {"list_name":"WEIGHT_U","label":"lbs","name":"LBS"},
    {"list_name":"WEIGHT_U","label":"kg","name":"KG"},
]

# DM-7: §27 sentinel-value exclusivity constraint for RACE
def _build_race_constraint():
    sentinel = "DECLINED"
    others = ["AIAN","ASIAN","BLACK","NHPI","WHITE"]
    others_or = " or ".join([f"selected(., '{v}')" for v in others])
    return f"not(selected(., '{sentinel}') and ({others_or}))"

dm_survey = [begin_grp("group0", "")]
dm_survey.append(srow(type="date", name="VISDAT",
    label="Date of Assessment", required="yes",
    constraint=DATE_CONS, constraint_message=DATE_CONS_MSG,
    **{"bind::oc:itemgroup":"DM",
       "bind::oc:briefdescription":"Date of Assessment",
       "library_source":"CUSTOMER_CRF_EXACT"}))
dm_survey.append(srow(type="text", name="DMPHYNAM",
    label="Physician Name", required="yes",
    **{"bind::oc:itemgroup":"DM",
       "bind::oc:briefdescription":"Physician Name",
       "library_source":"CUSTOMER_CRF_EXACT"}))
dm_survey.append(end_grp())

# DM-2: hybrid AGE constraint
dm_survey.append(begin_grp("group1", "Subject Demographics"))
dm_survey.append(srow(type="integer", name="AGE",
    label="1.1. Subject's Age at the time of consent (years)", required="yes",
    constraint=". >=18 and . <120",
    constraint_message="Age must be at least 18 years and less than 120.",
    **{"bind::oc:itemgroup":"DM",
       "bind::oc:briefdescription":"Subject's Age",
       "library_source":"CUSTOMER_CRF_EXACT"}))
# DM-4: DMSEX
dm_survey.append(srow(type="select_one SEX", name="DMSEX",
    label="1.2. Subject's Sex at birth", required="yes", appearance="horizontal",
    **{"bind::oc:itemgroup":"DM",
       "bind::oc:briefdescription":"Sex at birth",
       "library_source":"CUSTOMER_CRF_EXACT"}))
dm_survey.append(srow(type="select_one ETHNIC", name="ETHNIC",
    label="1.3. Subject's ethnicity", required="yes", appearance="horizontal",
    **{"bind::oc:itemgroup":"DM",
       "bind::oc:briefdescription":"Ethnicity",
       "library_source":"CUSTOMER_CRF_EXACT"}))
# DM-7: RACE with sentinel exclusivity constraint
dm_survey.append(srow(type="select_multiple RACE", name="RACE",
    label="1.4. Subject's Race (select all that apply)", required="yes",
    constraint=_build_race_constraint(),
    constraint_message="Cannot select 'Declined' and other options. "
                        "Please correct or clarify.",
    **{"bind::oc:itemgroup":"DM",
       "bind::oc:briefdescription":"Race",
       "library_source":"CUSTOMER_CRF_EXACT"}))
# DM-6: DMRACE_ASIAN_SPECIFY
dm_survey.append(srow(type="select_multiple ASIAN_SUB",
    name="DMRACE_ASIAN_SPECIFY",
    label="If Asian, select all that apply",
    relevant="selected(${RACE},'ASIAN')",
    **{"bind::oc:itemgroup":"DM",
       "bind::oc:briefdescription":"Asian sub-category",
       "library_source":"CUSTOMER_CRF_EXACT"}))
dm_survey.append(end_grp())

# DM-3: w2 width class for value+unit pairs
# DM-8: precision constraint on HEIGHT/WEIGHT
dm_survey.append(begin_grp("group2", "Physical Examination"))
dm_survey.append(srow(type="decimal", name="HEIGHT",
    label="2.1. Subject height", required="yes",
    appearance="w2",
    constraint=". >0 and . =round(${HEIGHT}, 2)",
    constraint_message="Value must be positive, with no more than 2 decimal places.",
    **{"bind::oc:itemgroup":"DM",
       "bind::oc:briefdescription":"Subject height",
       "library_source":"CUSTOMER_CRF_EXACT"}))
dm_survey.append(srow(type="select_one HEIGHT_U", name="HEIGHT_U",
    label="Height units", required="yes",
    appearance="horizontal w2",
    **{"bind::oc:itemgroup":"DM",
       "bind::oc:briefdescription":"Height units",
       "library_source":"CUSTOMER_CRF_EXACT"}))
dm_survey.append(srow(type="decimal", name="WEIGHT",
    label="2.2. Subject weight", required="yes",
    appearance="w2",
    constraint=". >0 and . =round(${WEIGHT}, 2)",
    constraint_message="Value must be positive, with no more than 2 decimal places.",
    **{"bind::oc:itemgroup":"DM",
       "bind::oc:briefdescription":"Subject weight",
       "library_source":"CUSTOMER_CRF_EXACT"}))
dm_survey.append(srow(type="select_one WEIGHT_U", name="WEIGHT_U",
    label="Weight units", required="yes",
    appearance="horizontal w2",
    **{"bind::oc:itemgroup":"DM",
       "bind::oc:briefdescription":"Weight units",
       "library_source":"CUSTOMER_CRF_EXACT"}))
# DM-9: 4-combination BMI calc with rounding
BMI_CALC = (
    "if(${HEIGHT}>0 and ${WEIGHT}>0,\n"
    "if(${WEIGHT_U}='LBS' and ${HEIGHT_U}='IN',\n"
    "round((${WEIGHT} div (${HEIGHT} * ${HEIGHT})) * 703, 2),\n"
    "if(${WEIGHT_U}='KG' and ${HEIGHT_U}='CM',\n"
    "round((${WEIGHT} div (${HEIGHT} * ${HEIGHT})) * 10000, 2),\n"
    "if(${WEIGHT_U}='KG' and ${HEIGHT_U}='IN',\n"
    "round((${WEIGHT} div (${HEIGHT} * ${HEIGHT})) * 1550.003, 2),\n"
    "if(${WEIGHT_U}='LBS' and ${HEIGHT_U}='CM',\n"
    "round((${WEIGHT} div (${HEIGHT} * ${HEIGHT})) * 4535.924, 2),\n"
    "'')))),\n"
    "'')"
)
dm_survey.append(srow(type="decimal", name="BMI",
    label="2.3. BMI (kg/m²) — derived",
    calculation=BMI_CALC,
    readonly="yes", completion_status="FLAGGED",
    **{"bind::oc:itemgroup":"DM",
       "bind::oc:briefdescription":"BMI derived",
       "library_source":"PROTOCOL_EXTENSION"}))
dm_survey.append(end_grp())

dm_survey.append(begin_grp("group3", "Additional Data and Forms"))
dm_survey.append(note("DM_NOTE",
    '<span style="color:red">**Required: complete the Medical History CRF. '
    'As applicable: Adverse Event, Deviation, Withdrawal CRFs.**</span>'))
dm_survey.append(end_grp())

DM_FORM = {
    "form_id": "DM_BL", "form_title": "Baseline Demographics and Physical Exam",
    "form_category": "CDASH_CLINICAL", "cdash_domain": "DM",
    "visits_assigned": ["SE_BASELINE"],
    "definition_source": "customer_crf_library",
    "library_source": "CUSTOMER_CRF_EXACT",
    "settings": settings_block("Baseline Demographics and Physical Exam",
                                "DM_BL"),
    "survey": dm_survey,
    "choices": (list(CL_SEX) + list(CL_ETHNIC) + list(CL_RACE) +
                list(CL_ASIAN) + list(CL_HEIGHT_U) + list(CL_WEIGHT_U)),
    "cross_form_dependencies": [],
}


# ════════════════════════════════════════════════════════════════════════════
# MH — level 2 (Case Book pp. 24-25)
# ════════════════════════════════════════════════════════════════════════════
CL_AFL_TYPE = [
    {"list_name":"AFL_TYPE","label":"Typical","name":"TYPICAL"},
    {"list_name":"AFL_TYPE","label":"Atypical","name":"ATYPICAL"},
]
CL_VT_TYPE = [
    {"list_name":"VT_TYPE","label":"Ischemic VT","name":"ISCHEMIC"},
    {"list_name":"VT_TYPE","label":"Non-ischemic VT","name":"NONISCHEMIC"},
    {"list_name":"VT_TYPE","label":"Unknown","name":"U"},
]
CL_DEVTYPE = [
    {"list_name":"DEVTYPE","label":"Pacemaker","name":"PACEMAKER"},
    {"list_name":"DEVTYPE","label":"Implantable Cardioverter Defibrillators "
     "(ICD)","name":"ICD"},
    {"list_name":"DEVTYPE","label":"Cardiac Loop Recorders","name":"LOOP"},
    {"list_name":"DEVTYPE","label":"Ventricular Assist Devices (VADs)",
     "name":"VAD"},
    {"list_name":"DEVTYPE","label":"CardioMEMS","name":"CARDIOMEMS"},
    {"list_name":"DEVTYPE","label":"Other","name":"OTHER"},
]
CL_NYHA = [
    {"list_name":"NYHA","label":"I","name":"I"},
    {"list_name":"NYHA","label":"II","name":"II"},
    {"list_name":"NYHA","label":"III","name":"III"},
    {"list_name":"NYHA","label":"IV","name":"IV"},
    {"list_name":"NYHA","label":"Unknown","name":"U"},
]
CL_SHD = [
    {"list_name":"SHD","label":"Atrial septal defect","name":"ASD"},
    {"list_name":"SHD","label":"Mitral valve prolapse","name":"MVP"},
    {"list_name":"SHD","label":"Patent foramen ovale (PFO)","name":"PFO"},
    {"list_name":"SHD","label":"Leaking valve","name":"LEAKING_VALVE"},
    {"list_name":"SHD","label":"Aortic stenosis","name":"AS"},
    {"list_name":"SHD","label":"Regurgitation","name":"REGURGITATION"},
    {"list_name":"SHD","label":"Narrowing of the aortic valve",
     "name":"AORTIC_NARROW"},
    {"list_name":"SHD","label":"Valve stiffening","name":"VALVE_STIFF"},
    {"list_name":"SHD","label":"Ischemic cardiomyopathy",
     "name":"ISCHEMIC_CM"},
    {"list_name":"SHD","label":"Non-ischemic cardiomyopathy",
     "name":"NONISCHEMIC_CM"},
    {"list_name":"SHD","label":"Left ventricular hypertrophy","name":"LVH"},
    {"list_name":"SHD","label":"Other","name":"OTHER"},
]

mh_survey = [begin_grp("group0", "")]
mh_survey.append(srow(type="date", name="MHDAT",
    label="Date of Procedure", required="yes",
    constraint=DATE_CONS, constraint_message=DATE_CONS_MSG,
    **{"bind::oc:itemgroup":"MH",
       "bind::oc:briefdescription":"Date of Procedure",
       "library_source":"CUSTOMER_CRF_EXACT"}))
mh_survey.append(end_grp())

mh_survey.append(begin_grp("group1", "Arrhythmia History"))
ARRHY = [
    ("MHARRPAF",  "1.1. Persistent AF"),
    ("MHARRPRF",  "1.2. Paroxysmal Atrial Fibrillation"),
    ("MHARRAFL",  "1.3. Atrial Flutter (AFL)"),
]
for nm, lbl in ARRHY:
    mh_survey.append(srow(type="select_one YNU", name=nm, label=lbl,
        required="yes", appearance="horizontal",
        **{"bind::oc:itemgroup":"MH",
           "bind::oc:briefdescription":_brief(lbl),
           "library_source":"CUSTOMER_CRF_EXACT"}))
mh_survey.append(srow(type="select_one AFL_TYPE", name="MHARRAFL_TYPE",
    label="1.3.1. Type of Atrial Flutter", appearance="horizontal",
    relevant="${MHARRAFL}='Y'",
    **{"bind::oc:itemgroup":"MH",
       "bind::oc:briefdescription":"Type of AFL",
       "library_source":"CUSTOMER_CRF_EXACT"}))
mh_survey.append(srow(type="select_one YNU", name="MHARRAT",
    label="1.4. Atrial Tachycardia", required="yes", appearance="horizontal",
    **{"bind::oc:itemgroup":"MH",
       "bind::oc:briefdescription":"Atrial Tachycardia",
       "library_source":"CUSTOMER_CRF_EXACT"}))
mh_survey.append(srow(type="select_one YNU", name="MHARRVT",
    label="1.5. Ventricular Tachycardia", required="yes", appearance="horizontal",
    **{"bind::oc:itemgroup":"MH",
       "bind::oc:briefdescription":"Ventricular Tachycardia",
       "library_source":"CUSTOMER_CRF_EXACT"}))
# MH-4b: §23 source-label disambiguation — "If yes" → "If yes, type of VT"
mh_survey.append(srow(type="select_one VT_TYPE", name="MHARRVT_TYPE",
    label="1.5.1. If yes, type of VT", appearance="horizontal",
    relevant="${MHARRVT}='Y'",
    **{"bind::oc:itemgroup":"MH",
       "bind::oc:briefdescription":"VT type",
       "library_source":"CUSTOMER_CRF_EXACT"}))
ARRHY2 = [
    ("MHARRBRDY","1.6. Bradycardia"),
    ("MHARRSVT", "1.7. SVT"),
    ("MHARRPVC", "1.8. PVC"),
    ("MHARRHB",  "1.9. Heart Block"),
    ("MHARRAVN", "1.10. AV Nodal Dysfunction"),
]
for nm, lbl in ARRHY2:
    mh_survey.append(srow(type="select_one YNU", name=nm, label=lbl,
        required="yes", appearance="horizontal",
        **{"bind::oc:itemgroup":"MH",
           "bind::oc:briefdescription":_brief(lbl),
           "library_source":"CUSTOMER_CRF_EXACT"}))
mh_survey.append(end_grp())

mh_survey.append(begin_grp("group2", "Surgical History"))
SURG = [
    ("MHSURGABL",  "2.1. Has the subject had a previous ablation?"),
    ("MHSURGLA",   "2.2. Has the subject had a previous procedure in which "
                    "access to LA was obtained (e.g., previous transseptal "
                    "access, incision from LAAO)?"),
    ("MHSURGATR",  "2.3. Did the subject have an atriotomy or ventriculotomy "
                    "in the past 4 weeks?"),
    ("MHSURGVALV", "2.4. Did the subject have a valve repair or other valve "
                    "procedure?"),
    ("MHSURGDEV",  "2.5. Does the subject have any implanted intracardiac "
                    "devices?"),
]
for nm, lbl in SURG:
    mh_survey.append(srow(type="select_one YNU", name=nm, label=lbl,
        required="yes", appearance="horizontal",
        **{"bind::oc:itemgroup":"MH",
           "bind::oc:briefdescription":_brief(lbl),
           "library_source":"CUSTOMER_CRF_EXACT"}))
# MH-2: multi-select per §24 + flag in REVIEW_FLAGS
mh_survey.append(srow(type="select_multiple DEVTYPE", name="MHSURGDEV_TYPE",
    label="2.5.1. If yes, what devices?",
    relevant="${MHSURGDEV}='Y'",
    **{"bind::oc:itemgroup":"MH",
       "bind::oc:briefdescription":"Implanted devices",
       "library_source":"CUSTOMER_CRF_EXACT"}))
mh_survey.append(srow(type="text", name="MHSURGDEV_OTH",
    label="If other, please specify",
    relevant="selected(${MHSURGDEV_TYPE},'OTHER')",
    **{"bind::oc:itemgroup":"MH",
       "bind::oc:briefdescription":"Other device",
       "library_source":"CUSTOMER_CRF_EXACT"}))
mh_survey.append(end_grp())

mh_survey.append(begin_grp("group3", "Disease History"))
DIS = [
    ("MHDISCAD", "3.1. Coronary artery disease"),
    ("MHDISCABG","3.2. Has the subject had coronary artery bypass graft "
                  "(CABG) surgery?"),
    ("MHDISMI",  "3.3. Myocardial infarction (MI)"),
]
for nm, lbl in DIS:
    mh_survey.append(srow(type="select_one YNU", name=nm, label=lbl,
        required="yes", appearance="horizontal",
        **{"bind::oc:itemgroup":"MH",
           "bind::oc:briefdescription":_brief(lbl),
           "library_source":"CUSTOMER_CRF_EXACT"}))
mh_survey.append(srow(type="date", name="MHDISMI_DAT",
    label="3.3.1. If yes, Date of occurrence",
    relevant="${MHDISMI}='Y'",
    constraint=DATE_CONS, constraint_message=DATE_CONS_MSG,
    **{"bind::oc:itemgroup":"MH",
       "bind::oc:briefdescription":"MI Date",
       "library_source":"CUSTOMER_CRF_EXACT"}))
mh_survey.append(srow(type="select_one YNU", name="MHDISDM",
    label="3.4. Diabetes", required="yes", appearance="horizontal",
    **{"bind::oc:itemgroup":"MH",
       "bind::oc:briefdescription":"Diabetes",
       "library_source":"CUSTOMER_CRF_EXACT"}))
mh_survey.append(srow(type="select_one YNU", name="MHDISHF",
    label="3.5. Heart failure", required="yes", appearance="horizontal",
    **{"bind::oc:itemgroup":"MH",
       "bind::oc:briefdescription":"Heart failure",
       "library_source":"CUSTOMER_CRF_EXACT"}))
mh_survey.append(srow(type="select_one NYHA", name="MHDISHF_NYHA",
    label="3.5.1. If yes, subject's most recent NYHA classification",
    appearance="horizontal", relevant="${MHDISHF}='Y'",
    **{"bind::oc:itemgroup":"MH",
       "bind::oc:briefdescription":"NYHA class",
       "library_source":"CUSTOMER_CRF_EXACT"}))
mh_survey.append(srow(type="select_one YNU", name="MHDISHTN",
    label="3.6. Hypertension", required="yes", appearance="horizontal",
    **{"bind::oc:itemgroup":"MH",
       "bind::oc:briefdescription":"Hypertension",
       "library_source":"CUSTOMER_CRF_EXACT"}))
mh_survey.append(srow(type="select_one YNU", name="MHDISSHD",
    label="3.7. Structural heart disease", required="yes", appearance="horizontal",
    **{"bind::oc:itemgroup":"MH",
       "bind::oc:briefdescription":"Structural heart disease",
       "library_source":"CUSTOMER_CRF_EXACT"}))
mh_survey.append(srow(type="select_multiple SHD", name="MHDISSHD_TYPE",
    label="3.7.1. Check all that apply",
    relevant="${MHDISSHD}='Y'",
    **{"bind::oc:itemgroup":"MH",
       "bind::oc:briefdescription":"SHD types",
       "library_source":"CUSTOMER_CRF_EXACT"}))
mh_survey.append(srow(type="text", name="MHDISSHD_OTH",
    label="If other, please specify",
    relevant="selected(${MHDISSHD_TYPE},'OTHER')",
    **{"bind::oc:itemgroup":"MH",
       "bind::oc:briefdescription":"Other SHD",
       "library_source":"CUSTOMER_CRF_EXACT"}))
mh_survey.append(end_grp())

mh_survey.append(begin_grp("group4", "Other Disease History"))
mh_survey.append(srow(type="select_one YNU", name="MHOTH",
    label="4.1. Other cardiovascular disease history not indicated above "
          "that could impact heart morphology?",
    required="yes", appearance="horizontal",
    **{"bind::oc:itemgroup":"MH",
       "bind::oc:briefdescription":"Other CV disease",
       "library_source":"CUSTOMER_CRF_EXACT"}))
mh_survey.append(srow(type="text", name="MHOTH_SPEC",
    label="4.1.1. If yes, specify condition",
    relevant="${MHOTH}='Y'",
    **{"bind::oc:itemgroup":"MH",
       "bind::oc:briefdescription":"Specify condition",
       "library_source":"CUSTOMER_CRF_EXACT"}))
mh_survey.append(end_grp())

# §22 Reminder Notes Gated by Y/N Trigger
mh_survey.append(begin_grp("group5", "Additional Data and Forms Check"))
mh_survey.append(srow(type="select_one YN", name="MHAE_YN",
    label="Have there been any adverse events up to this point?",
    required="yes", appearance="horizontal",
    **{"bind::oc:itemgroup":"MH",
       "bind::oc:briefdescription":"AE check",
       "library_source":"PROTOCOL_EXTENSION"}))
mh_survey.append(note("MHAE_NOTE",
    '<span style="color:red">**If yes, please be sure to complete the '
    'Adverse Event form.**</span>',
    relevant="${MHAE_YN}='Y'"))
mh_survey.append(srow(type="select_one YN", name="MHDV_YN",
    label="Have there been any deviations up to this point?",
    required="yes", appearance="horizontal",
    **{"bind::oc:itemgroup":"MH",
       "bind::oc:briefdescription":"DV check",
       "library_source":"PROTOCOL_EXTENSION"}))
mh_survey.append(note("MHDV_NOTE",
    '<span style="color:red">**If yes, please be sure to complete the '
    'Deviation form.**</span>',
    relevant="${MHDV_YN}='Y'"))
mh_survey.append(srow(type="select_one YN", name="MHDS_YN",
    label="Does a withdrawal form need to be completed?",
    required="yes", appearance="horizontal",
    **{"bind::oc:itemgroup":"MH",
       "bind::oc:briefdescription":"DS check",
       "library_source":"PROTOCOL_EXTENSION"}))
mh_survey.append(note("MHDS_NOTE",
    '<span style="color:red">**If yes, please be sure to complete the '
    'Withdrawal form.**</span>',
    relevant="${MHDS_YN}='Y'"))
mh_survey.append(end_grp())

MH_FORM = {
    "form_id": "MH", "form_title": "Medical History",
    "form_category": "CDASH_CLINICAL", "cdash_domain": "MH",
    "visits_assigned": ["SE_BASELINE"],
    "definition_source": "customer_crf_library",
    "library_source": "CUSTOMER_CRF_EXACT",
    "settings": settings_block("Medical History", "MH"),
    "survey": mh_survey,
    "choices": (list(CL_YNU_NEW) + list(CL_YN_NEW) + list(CL_AFL_TYPE) +
                list(CL_VT_TYPE) + list(CL_DEVTYPE) + list(CL_NYHA) +
                list(CL_SHD)),
    "cross_form_dependencies": [],
}


REGEN_FORMS = [ICF_FORM, IE_FORM, DM_FORM, MH_FORM]

if __name__ == "__main__":
    for f in REGEN_FORMS:
        n_ph = sum(1 for r in f["survey"]
                    if r.get("library_source") == LIBSRC_PLACEHOLDER)
        print(f"  {f['form_id']:8s} ({f['definition_source']:22s}) "
              f"survey={len(f['survey']):3d}  choices={len(f['choices']):3d}  "
              f"placeholders={n_ph}")
    print()
    print(f"  REVIEW_FLAGS:")
    for bucket, items in REVIEW_FLAGS.items():
        print(f"    {bucket}: {len(items)} entries")
