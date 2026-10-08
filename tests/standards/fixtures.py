"""Synthetic customer standards for the standards-matching tests (no customer content): XLSForm workbooks and an
ODM XML built in memory, plus a small protocol-analysed Study Spec."""
import copy, io, zipfile
import openpyxl

SURVEY_HDR = ["type", "name", "label", "bind::oc:itemgroup", "hint", "appearance", "relevant", "required",
              "constraint", "constraint_message", "calculation", "readonly", "bind::oc:external", "oc:custom_col"]

AE_SURVEY = [
    ["calculate", "EVT_CF", "", "", "", "", "", "", "", "", "instance('clinicaldata')/ODM/x/@Value", "", "clinicaldata", ""],
    ["begin group", "AEGRP", "Adverse Event", "", "", "field-list", "", "", "", "", "", "", "", ""],
    ["text", "AETERM", "What is the adverse event term?", "AEG", "Verbatim term", "w4", "", "yes", "", "", "", "", "", "keep-me"],
    ["date", "AESTDAT", "Start date", "AEG", "", "w2", "", "yes", ". <= today()", "Start date cannot be in the future!", "", "", "", ""],
    ["select_one NY", "AEONGO", "Ongoing?", "AEG", "", "w2 horizontal", "", "yes", "", "", "", "", "", ""],
    ["date", "AEENDAT", "End date", "AEG", "", "w2", "${AEONGO} = 'N'", "yes", "", "", "", "", "", ""],
    ["select_one SEV", "AESEV", "Severity", "AEG", "", "w2", "", "", "", "", "", "", "", ""],
    ["select_one NY", "AESER", "Serious?", "AEG", "", "w2", "", "yes", "", "", "", "", "", ""],
    ["text", "AESITE", "Site-specific note", "AEG", "", "w4", "${AESER} = 'Y'", "", "", "", "", "", "", ""],
    ["end group", "", "", "", "", "", "", "", "", "", "", "", "", ""],
]
AE_CHOICES = [["NY", "Yes", "Y"], ["NY", "No", "N"], ["SEV", "Mild (custom)", "1"], ["SEV", "Moderate (custom)", "2"],
              ["SEV", "Severe (custom)", "3"]]
AE_SETTINGS = {"form_title": "AE General", "form_id": "AEGEN", "version": "7", "style": "theme-grid",
               "namespaces": 'oc="http://openclinica.org/xforms" , OpenClinica="http://openclinica.com/odm"'}

VS_SURVEY = [
    ["date", "VSDAT", "Date of vital signs", "VS", "", "", "", "yes", "", "", "", "", "", ""],
    ["decimal", "SYSBP_VSORRES", "Systolic blood pressure", "VS", "", "", "", "", "", "", "", "", "", ""],
    ["decimal", "DIABP_VSORRES", "Diastolic blood pressure", "VS", "", "", "", "", "", "", "", "", "", ""],
]
VS_SETTINGS = {"form_title": "Vital Signs", "form_id": "VS", "version": "2", "style": "theme-grid"}


def xlsform_bytes(survey=AE_SURVEY, choices=AE_CHOICES, settings=AE_SETTINGS, with_survey=True):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "survey" if with_survey else "notes"
    ws.append(SURVEY_HDR)
    for r in survey:
        ws.append([v if v != "" else None for v in r])
    wc = wb.create_sheet("choices")
    wc.append(["list_name", "label", "name"])
    for r in choices:
        wc.append(r)
    wt = wb.create_sheet("settings")
    wt.append(list(settings))
    wt.append(list(settings.values()))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def zip_bytes(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def docx_bytes():
    return zip_bytes({"[Content_Types].xml": "<Types/>", "word/document.xml": "<w:document/>"})


ODM = """<?xml version="1.0" encoding="UTF-8"?>
<ODM xmlns="http://www.cdisc.org/ns/odm/v1.3" xmlns:OpenClinica="http://www.openclinica.org/ns/odm_ext_v130/v3.1"
     FileType="Snapshot" ODMVersion="1.3">
 <Study OID="S_SYNTH"><MetaDataVersion OID="v1" Name="v1">
  <FormDef OID="F_AE" Name="Adverse Events" Repeating="No"><ItemGroupRef ItemGroupOID="IG_AE_AE" Mandatory="Yes"/></FormDef>
  <FormDef OID="F_MEDH" Name="Medical History" Repeating="No"><ItemGroupRef ItemGroupOID="IG_MEDH_MH" Mandatory="Yes"/></FormDef>
  <ItemGroupDef OID="IG_AE_AE" Name="AE" Repeating="No">
   <ItemRef ItemOID="I_AE_AESTDAT" OrderNumber="2" Mandatory="Yes"/>
   <ItemRef ItemOID="I_AE_AETERM" OrderNumber="1" Mandatory="Yes"/>
   <ItemRef ItemOID="I_AE_AEENDAT" OrderNumber="3" Mandatory="No"/>
   <ItemRef ItemOID="I_AE_AESEV" OrderNumber="4" Mandatory="No"/>
   <ItemRef ItemOID="I_AE_AEACN" OrderNumber="5" Mandatory="No"/>
   <ItemRef ItemOID="I_AE_AEWT" OrderNumber="6" Mandatory="No"/>
  </ItemGroupDef>
  <ItemGroupDef OID="IG_MEDH_MH" Name="MH" Repeating="Yes">
   <ItemRef ItemOID="I_MEDH_MHTERM" OrderNumber="1" Mandatory="Yes"/>
   <ItemRef ItemOID="I_MEDH_MHSTDAT" OrderNumber="2" Mandatory="No"/>
   <ItemRef ItemOID="I_MEDH_MHENDAT" OrderNumber="3" Mandatory="No"/>
  </ItemGroupDef>
  <ItemDef OID="I_AE_AETERM" Name="AETERM" DataType="text" Length="200"><Question><TranslatedText>Event term</TranslatedText></Question></ItemDef>
  <ItemDef OID="I_AE_AESTDAT" Name="AESTDAT" DataType="date"><Question><TranslatedText>Start date</TranslatedText></Question></ItemDef>
  <ItemDef OID="I_AE_AEENDAT" Name="AEENDAT" DataType="date"><Question><TranslatedText>End date</TranslatedText></Question></ItemDef>
  <ItemDef OID="I_AE_AESEV" Name="AESEV" DataType="text"><Question><TranslatedText>Severity</TranslatedText></Question><CodeListRef CodeListOID="CL_1"/></ItemDef>
  <ItemDef OID="I_AE_AEACN" Name="AEACN" DataType="text"><Question><TranslatedText>Action taken</TranslatedText></Question><OpenClinica:MultiSelectListRef MultiSelectListID="MSL_1"/></ItemDef>
  <ItemDef OID="I_AE_AEWT" Name="AEWT" DataType="float" SignificantDigits="1"><Question><TranslatedText>Weight at onset</TranslatedText></Question></ItemDef>
  <ItemDef OID="I_MEDH_MHTERM" Name="MHTERM" DataType="text"><Question><TranslatedText>Condition</TranslatedText></Question></ItemDef>
  <ItemDef OID="I_MEDH_MHSTDAT" Name="MHSTDAT" DataType="date"><Question><TranslatedText>Start</TranslatedText></Question></ItemDef>
  <ItemDef OID="I_MEDH_MHENDAT" Name="MHENDAT" DataType="date"><Question><TranslatedText>End</TranslatedText></Question></ItemDef>
  <CodeList OID="CL_1" Name="AESEV" DataType="text">
   <CodeListItem CodedValue="1"><Decode><TranslatedText>Mild</TranslatedText></Decode></CodeListItem>
   <CodeListItem CodedValue="2"><Decode><TranslatedText>Moderate</TranslatedText></Decode></CodeListItem>
  </CodeList>
  <OpenClinica:MultiSelectList ID="MSL_1" Name="AEACN" DataType="text">
   <OpenClinica:MultiSelectListItem CodedOptionValue="NONE"><Decode><TranslatedText>None</TranslatedText></Decode></OpenClinica:MultiSelectListItem>
   <OpenClinica:MultiSelectListItem CodedOptionValue="DRUG"><Decode><TranslatedText>Drug withdrawn</TranslatedText></Decode></OpenClinica:MultiSelectListItem>
  </OpenClinica:MultiSelectList>
 </MetaDataVersion></Study>
 <Study OID="S_SYNTH_SITE"><MetaDataVersion OID="v1" Name="v1"/></Study>
</ODM>""".encode()


def _f(form_id, title, domain, rows, choices=(), visits=("SE_SCREENING",)):
    return {"form_id": form_id, "form_title": title, "form_category": "CDASH_CLINICAL", "cdash_domain": domain,
            "visits_assigned": list(visits), "has_repeating_group": False, "is_epro": False, "reuse_count": len(visits),
            "library_match": {"status": "CDASH_DEFAULT", "source_type": "standard"},
            "settings": {"form_title": title, "form_id": form_id, "version": "1", "style": "theme-grid"},
            "choices": [dict(zip(("list_name", "label", "name"), c), source="CDASH") for c in choices],
            "survey": [{"type": t, "name": n, "label": l, "bind__oc_itemgroup": form_id, "completion_status": "COMPLETE",
                        "library_source": "CDASH_DEFAULT", "flag_reason": "", **extra} for t, n, l, extra in rows],
            "cross_form_dependencies": []}


PROTOCOL_TEXT = ("8.3 Adverse events. All adverse events will be recorded from consent.\n"
                 "For each adverse event the investigator must record whether the event led to\n"
                 "hospitalisation of the participant, and the action taken with study drug.\n"
                 "9.1 Vital signs are measured at every visit.")


def spec():
    """A protocol-analysed study: AE (matches the AE standard by domain), VS, a custom form, and a form reading AE."""
    return copy.deepcopy({
        "study_meta": {"protocol_number": "SYN-001", "study_title": "Synthetic study"},
        "timepoint_csv": {"rows": [{"event": "SE_SCREENING", "timepoint": "Screening"},
                                   {"event": "SE_COMMON", "timepoint": "Common"}]},
        "schedule_of_events": {"form_placements": [{"target_visit_oid": "SE_COMMON", "form_id": "AE"},
                                                   {"target_visit_oid": "SE_SCREENING", "form_id": "VS"}]},
        "forms": [
            _f("AE", "Adverse Events", "AE", [
                ("text", "AETERM", "Adverse event", {}),
                ("date", "AESTDAT", "Start date", {"constraint": ". <= today()", "constraint_message": "No future dates."}),
                ("date", "AEENDAT", "End date", {}),
                ("select_one YN", "AEHOSP", "Did the event lead to hospitalisation?", {}),
                ("text", "AECOMMENT", "Comment", {}),
            ], choices=[("YN", "Yes", "Y"), ("YN", "No", "N")], visits=("SE_COMMON",)),
            _f("VS", "Vital Signs", "VS", [("date", "VSDAT", "Date", {}), ("decimal", "TEMP", "Temperature", {})]),
            _f("DIARY", "Patient Diary", None, [("text", "DIARYTXT", "Entry", {})]),
        ],
        "review_flags": {},
    })
