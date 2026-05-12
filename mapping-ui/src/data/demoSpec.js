// src/data/demoSpec.js
// Full demo spec matching real CV3001 Medidata Rave output structure

export const DEMO_SPEC = {
  study_name: "CV3001",
  source_system: "Medidata Rave",
  version: "V0512.0351",
  _item_id: null,
  _item_name: "DEMO — Medidata Test Study Migration",
  forms: [
    {
      form_id: "DM", form_title: "Demographics", form_category: "DEMOGRAPHICS",
      cdash_domain: "DM", visits_assigned: ["SE_SCREEN","SE_BASELINE"],
      has_repeating_group: false, is_epro: false, arm_applicability: "ALL",
      reuse_count: 1, complexity: "simple",
      settings: { form_title: "Demographics", form_id: "F_DM_1", version: "1", style: "theme-grid", cro_accessible: false },
      library_match: { status: "CDASH_MATCH", source_type: "CDASH_STANDARD", fields_from_library: 5, fields_extended: 2 },
      cross_form_dependencies: [],
      choices: [
        { list_name: "CL_SEX",    name: "M", label: "Male",   source: "ODM_CODELIST" },
        { list_name: "CL_SEX",    name: "F", label: "Female", source: "ODM_CODELIST" },
        { list_name: "CL_SEX",    name: "U", label: "Unknown",source: "ODM_CODELIST" },
        { list_name: "CL_RACE",   name: "1", label: "American Indian or Alaska Native", source: "ODM_CODELIST" },
        { list_name: "CL_RACE",   name: "2", label: "Asian",  source: "ODM_CODELIST" },
        { list_name: "CL_RACE",   name: "3", label: "Black or African American", source: "ODM_CODELIST" },
        { list_name: "CL_RACE",   name: "4", label: "Native Hawaiian or Other Pacific Islander", source: "ODM_CODELIST" },
        { list_name: "CL_RACE",   name: "5", label: "White",  source: "ODM_CODELIST" },
        { list_name: "CL_RACE",   name: "6", label: "Multiple", source: "ODM_CODELIST" },
        { list_name: "CL_ETHNIC", name: "HIS", label: "Hispanic or Latino",     source: "ODM_CODELIST" },
        { list_name: "CL_ETHNIC", name: "NOT", label: "Not Hispanic or Latino", source: "ODM_CODELIST" },
        { list_name: "CL_ETHNIC", name: "UNK", label: "Unknown",                source: "ODM_CODELIST" },
      ],
      survey: [
        { name:"SUBJID",  label:"Subject ID",   bind__oc_itemgroup:"DM", type:"calculate", appearance:"w2", required:"",    readonly:"yes", constraint:"", constraint_message:"", relevant:"", calculation:"instance('clinicaldata')/ODM/ClinicalData/SubjectData/@SubjectKey", hint:"", bind__oc_briefdescription:"", bind__oc_description:"", completion_status:"COMPLETE",    library_source:"CDASH_DEFAULT", flag_reason:"", source_field:"SUBJID",  source_group:"IG_DM" },
        { name:"SITEID",  label:"Site ID",      bind__oc_itemgroup:"DM", type:"calculate", appearance:"w2", required:"",    readonly:"yes", constraint:"", constraint_message:"", relevant:"", calculation:"instance('clinicaldata')/ODM/ClinicalData/SubjectData/SiteKey",      hint:"", bind__oc_briefdescription:"", bind__oc_description:"", completion_status:"COMPLETE",    library_source:"CDASH_DEFAULT", flag_reason:"", source_field:"SITEID",  source_group:"IG_DM" },
        { name:"BRTHDAT", label:"Date of Birth",bind__oc_itemgroup:"DM", type:"date",      appearance:"w2", required:"",    readonly:"",   constraint:"", constraint_message:"", relevant:"", calculation:"", hint:"Enter as YYYY-MM-DD", bind__oc_briefdescription:"", bind__oc_description:"", completion_status:"COMPLETE",    library_source:"CDASH_DEFAULT", flag_reason:"", source_field:"BRTHDAT", source_group:"IG_DM" },
        { name:"SEX",     label:"Sex",          bind__oc_itemgroup:"DM", type:"select",    appearance:"minimal", required:"yes", readonly:"", constraint:"", constraint_message:"", relevant:"", calculation:"", hint:"", bind__oc_briefdescription:"Biological sex at birth", bind__oc_description:"", completion_status:"COMPLETE", library_source:"CDASH_DEFAULT", flag_reason:"", source_field:"SEX", source_group:"IG_DM", list_name:"CL_SEX" },
        { name:"RACE",    label:"Race",         bind__oc_itemgroup:"DM", type:"select",    appearance:"minimal", required:"",  readonly:"", constraint:"", constraint_message:"", relevant:"", calculation:"", hint:"", bind__oc_briefdescription:"", bind__oc_description:"", completion_status:"COMPLETE",    library_source:"CDASH_DEFAULT", flag_reason:"", source_field:"RACE",    source_group:"IG_DM", list_name:"CL_RACE" },
        { name:"ETHNIC",  label:"Ethnicity",    bind__oc_itemgroup:"DM", type:"select",    appearance:"minimal", required:"",  readonly:"", constraint:"", constraint_message:"", relevant:"", calculation:"", hint:"", bind__oc_briefdescription:"", bind__oc_description:"", completion_status:"COMPLETE",    library_source:"CDASH_DEFAULT", flag_reason:"", source_field:"ETHNIC",  source_group:"IG_DM", list_name:"CL_ETHNIC" },
        { name:"COUNTRY", label:"Country",      bind__oc_itemgroup:"DM", type:"select",    appearance:"minimal", required:"yes",readonly:"", constraint:"", constraint_message:"", relevant:"", calculation:"", hint:"", bind__oc_briefdescription:"", bind__oc_description:"", completion_status:"COMPLETE",    library_source:"CDASH_DEFAULT", flag_reason:"", source_field:"COUNTRY", source_group:"IG_DM", list_name:"CL_COUNTRY" },
      ]
    },
    {
      form_id: "IE", form_title: "Inclusion/Exclusion Criteria", form_category: "ADMINISTRATIVE",
      cdash_domain: "IE", visits_assigned: ["SE_SCREEN"],
      has_repeating_group: false, is_epro: false, arm_applicability: "ALL",
      reuse_count: 1, complexity: "moderate",
      settings: { form_title: "Inclusion/Exclusion Criteria", form_id: "F_IE_1", version: "1", style: "theme-grid", cro_accessible: false },
      library_match: { status: "CDASH_MATCH", source_type: "CDASH_STANDARD", fields_from_library: 3, fields_extended: 1 },
      cross_form_dependencies: [],
      choices: [
        { list_name: "CL_IEORRES", name: "Y", label: "Yes / Met", source: "ODM_CODELIST" },
        { list_name: "CL_IEORRES", name: "N", label: "No / Not Met", source: "ODM_CODELIST" },
      ],
      survey: [
        { name:"SUBJID",    label:"Subject ID",           bind__oc_itemgroup:"IE", type:"calculate", appearance:"w2",    required:"",    readonly:"yes", constraint:"", constraint_message:"", relevant:"", calculation:"instance('clinicaldata')/ODM/ClinicalData/SubjectData/@SubjectKey", hint:"", bind__oc_briefdescription:"", bind__oc_description:"", completion_status:"COMPLETE", library_source:"CDASH_DEFAULT", flag_reason:"", source_field:"SUBJID",    source_group:"IG_IE" },
        { name:"IETESTCD",  label:"IE Test Code (INC)",   bind__oc_itemgroup:"IE", type:"select",    appearance:"minimal",required:"yes", readonly:"",   constraint:"", constraint_message:"", relevant:"", calculation:"", hint:"", bind__oc_briefdescription:"Inclusion criterion code", bind__oc_description:"", completion_status:"COMPLETE", library_source:"CDASH_DEFAULT", flag_reason:"", source_field:"IETESTCD", source_group:"IG_INC", list_name:"CL_IETESTCD" },
        { name:"IETESTCD_2",label:"IE Test Code (EXC)",   bind__oc_itemgroup:"IE", type:"select",    appearance:"minimal",required:"yes", readonly:"",   constraint:"", constraint_message:"", relevant:"", calculation:"", hint:"", bind__oc_briefdescription:"Exclusion criterion code", bind__oc_description:"", completion_status:"FLAGGED",  library_source:"CDASH_EXTENDED", flag_reason:"Duplicate CDASH alias — verify correct OC4 field name for exclusion criteria group", source_field:"IETESTCD", source_group:"IG_EXC", list_name:"CL_IETESTCD" },
        { name:"IEORRES",   label:"IE Result",            bind__oc_itemgroup:"IE", type:"select",    appearance:"minimal",required:"yes", readonly:"",   constraint:"", constraint_message:"", relevant:"", calculation:"", hint:"", bind__oc_briefdescription:"", bind__oc_description:"", completion_status:"COMPLETE", library_source:"CDASH_DEFAULT", flag_reason:"", source_field:"IEORRES",   source_group:"IG_IE", list_name:"CL_IEORRES" },
      ]
    },
    {
      form_id: "AE", form_title: "Adverse Events", form_category: "SAFETY",
      cdash_domain: "AE", visits_assigned: ["SE_SCREEN","SE_BASELINE","SE_WEEK4","SE_WEEK8","SE_WEEK12","SE_EOT","SE_FOLLOW"],
      has_repeating_group: true, is_epro: false, arm_applicability: "ALL",
      reuse_count: 1, complexity: "moderate",
      settings: { form_title: "Adverse Events", form_id: "F_AE_1", version: "1", style: "theme-grid", cro_accessible: true },
      library_match: { status: "CDASH_MATCH", source_type: "CDASH_STANDARD", fields_from_library: 6, fields_extended: 1 },
      cross_form_dependencies: [
        { source_form: "DM", source_field: "SUBJID", target_field: "SUBJID", xpath_expression: "instance('clinicaldata')/ODM/ClinicalData/SubjectData/@SubjectKey" }
      ],
      choices: [
        { list_name: "CL_AESEV", name: "MILD",     label: "Mild",     source: "ODM_CODELIST" },
        { list_name: "CL_AESEV", name: "MODERATE", label: "Moderate", source: "ODM_CODELIST" },
        { list_name: "CL_AESEV", name: "SEVERE",   label: "Severe",   source: "ODM_CODELIST" },
        { list_name: "CL_YN",    name: "Y",         label: "Yes",      source: "ODM_CODELIST" },
        { list_name: "CL_YN",    name: "N",         label: "No",       source: "ODM_CODELIST" },
        { list_name: "CL_AEOUT", name: "RECOVERED", label: "Recovered/Resolved",          source: "ODM_CODELIST" },
        { list_name: "CL_AEOUT", name: "RECOVERING",label: "Recovering/Resolving",        source: "ODM_CODELIST" },
        { list_name: "CL_AEOUT", name: "NOTRECOV",  label: "Not Recovered/Not Resolved",  source: "ODM_CODELIST" },
        { list_name: "CL_AEOUT", name: "FATAL",     label: "Fatal",                       source: "ODM_CODELIST" },
        { list_name: "CL_AEOUT", name: "UNK",       label: "Unknown",                     source: "ODM_CODELIST" },
      ],
      survey: [
        { name:"SUBJID",  label:"Subject ID",         bind__oc_itemgroup:"AE", type:"calculate", appearance:"w2",    required:"",    readonly:"yes", constraint:"", constraint_message:"", relevant:"", calculation:"instance('clinicaldata')/ODM/ClinicalData/SubjectData/@SubjectKey", hint:"", bind__oc_briefdescription:"", bind__oc_description:"", completion_status:"COMPLETE", library_source:"CDASH_DEFAULT", flag_reason:"", source_field:"SUBJID",  source_group:"IG_AE" },
        { name:"AETERM",  label:"Adverse Event Term",  bind__oc_itemgroup:"AE", type:"text",      appearance:"w4",    required:"yes", readonly:"",   constraint:"", constraint_message:"", relevant:"", calculation:"", hint:"Enter verbatim term as reported", bind__oc_briefdescription:"Verbatim AE term", bind__oc_description:"Enter the adverse event exactly as reported by the subject or observed by the investigator.", completion_status:"COMPLETE", library_source:"CDASH_DEFAULT", flag_reason:"", source_field:"AETERM",  source_group:"IG_AE" },
        { name:"AESTDAT", label:"AE Start Date",       bind__oc_itemgroup:"AE", type:"date",      appearance:"w2",    required:"yes", readonly:"",   constraint:". <= today()", constraint_message:"Start date cannot be in the future", relevant:"", calculation:"", hint:"", bind__oc_briefdescription:"", bind__oc_description:"", completion_status:"COMPLETE", library_source:"CDASH_DEFAULT", flag_reason:"", source_field:"AESTDAT", source_group:"IG_AE" },
        { name:"AEENDAT", label:"AE End Date",         bind__oc_itemgroup:"AE", type:"date",      appearance:"w2",    required:"",    readonly:"",   constraint:". >= ../AESTDAT or . = \"\"", constraint_message:"End date must be on or after start date", relevant:"", calculation:"", hint:"Leave blank if ongoing", bind__oc_briefdescription:"", bind__oc_description:"", completion_status:"COMPLETE", library_source:"CDASH_DEFAULT", flag_reason:"", source_field:"AEENDAT", source_group:"IG_AE" },
        { name:"AESEV",   label:"Severity",            bind__oc_itemgroup:"AE", type:"select",    appearance:"minimal",required:"yes", readonly:"",   constraint:"", constraint_message:"", relevant:"", calculation:"", hint:"", bind__oc_briefdescription:"NCI CTCAE severity grade", bind__oc_description:"", completion_status:"COMPLETE", library_source:"CDASH_DEFAULT", flag_reason:"", source_field:"AESEV",   source_group:"IG_AE", list_name:"CL_AESEV" },
        { name:"AESER",   label:"Serious AE",          bind__oc_itemgroup:"AE", type:"select",    appearance:"minimal",required:"yes", readonly:"",   constraint:"", constraint_message:"", relevant:"", calculation:"", hint:"", bind__oc_briefdescription:"", bind__oc_description:"", completion_status:"COMPLETE", library_source:"CDASH_DEFAULT", flag_reason:"", source_field:"AESER",   source_group:"IG_AE", list_name:"CL_YN" },
        { name:"AEOUT",   label:"Outcome",             bind__oc_itemgroup:"AE", type:"select",    appearance:"minimal",required:"",    readonly:"",   constraint:"", constraint_message:"", relevant:"", calculation:"", hint:"", bind__oc_briefdescription:"", bind__oc_description:"", completion_status:"COMPLETE", library_source:"CDASH_DEFAULT", flag_reason:"", source_field:"AEOUT",   source_group:"IG_AE", list_name:"CL_AEOUT" },
      ]
    },
    {
      form_id: "CM", form_title: "Concomitant Medications", form_category: "SAFETY",
      cdash_domain: "CM", visits_assigned: ["SE_SCREEN","SE_BASELINE","SE_WEEK4","SE_WEEK8","SE_WEEK12","SE_EOT"],
      has_repeating_group: true, is_epro: false, arm_applicability: "ALL",
      reuse_count: 1, complexity: "moderate",
      settings: { form_title: "Concomitant Medications", form_id: "F_CM_1", version: "1", style: "theme-grid", cro_accessible: true },
      library_match: { status: "CDASH_MATCH", source_type: "CDASH_STANDARD", fields_from_library: 5, fields_extended: 2 },
      cross_form_dependencies: [],
      choices: [
        { list_name: "CL_CMDOSU", name: "mg",   label: "mg",      source: "PLACEHOLDER" },
        { list_name: "CL_CMDOSU", name: "mcg",  label: "mcg",     source: "PLACEHOLDER" },
        { list_name: "CL_CMDOSU", name: "g",    label: "g",       source: "PLACEHOLDER" },
        { list_name: "CL_CMDOSU", name: "mL",   label: "mL",      source: "PLACEHOLDER" },
        { list_name: "CL_CMDOSU", name: "IU",   label: "IU",      source: "PLACEHOLDER" },
      ],
      survey: [
        { name:"SUBJID",  label:"Subject ID",     bind__oc_itemgroup:"CM", type:"calculate", appearance:"w2",    required:"",    readonly:"yes", constraint:"", constraint_message:"", relevant:"", calculation:"instance('clinicaldata')/ODM/ClinicalData/SubjectData/@SubjectKey", hint:"", bind__oc_briefdescription:"", bind__oc_description:"", completion_status:"COMPLETE",     library_source:"CDASH_DEFAULT",  flag_reason:"", source_field:"SUBJID",  source_group:"IG_CM" },
        { name:"CMTRT",   label:"Medication Name", bind__oc_itemgroup:"CM", type:"text",      appearance:"w4",    required:"yes", readonly:"",   constraint:"", constraint_message:"", relevant:"", calculation:"", hint:"", bind__oc_briefdescription:"Verbatim medication name", bind__oc_description:"", completion_status:"COMPLETE",     library_source:"CDASH_DEFAULT",  flag_reason:"", source_field:"CMTRT",   source_group:"IG_CM" },
        { name:"CMSTDAT", label:"Start Date",      bind__oc_itemgroup:"CM", type:"date",      appearance:"w2",    required:"",    readonly:"",   constraint:"", constraint_message:"", relevant:"", calculation:"", hint:"", bind__oc_briefdescription:"", bind__oc_description:"", completion_status:"COMPLETE",     library_source:"CDASH_DEFAULT",  flag_reason:"", source_field:"CMSTDAT", source_group:"IG_CM" },
        { name:"CMENDAT", label:"End Date",        bind__oc_itemgroup:"CM", type:"date",      appearance:"w2",    required:"",    readonly:"",   constraint:"", constraint_message:"", relevant:"", calculation:"", hint:"Leave blank if ongoing", bind__oc_briefdescription:"", bind__oc_description:"", completion_status:"COMPLETE",     library_source:"CDASH_DEFAULT",  flag_reason:"", source_field:"CMENDAT", source_group:"IG_CM" },
        { name:"CMINDC",  label:"Indication",      bind__oc_itemgroup:"CM", type:"text",      appearance:"w3",    required:"",    readonly:"",   constraint:"", constraint_message:"", relevant:"", calculation:"", hint:"", bind__oc_briefdescription:"", bind__oc_description:"", completion_status:"FLAGGED",      library_source:"AI_GENERATED",   flag_reason:"Not in source ODM — inferred from protocol. Verify this field is required.", source_field:"CMINDC",  source_group:"" },
        { name:"CMDOSE",  label:"Dose",            bind__oc_itemgroup:"CM", type:"float",     appearance:"w2",    required:"",    readonly:"",   constraint:". > 0", constraint_message:"Dose must be positive", relevant:"", calculation:"", hint:"", bind__oc_briefdescription:"", bind__oc_description:"", completion_status:"COMPLETE",     library_source:"CDASH_DEFAULT",  flag_reason:"", source_field:"CMDOSE",  source_group:"IG_CM" },
        { name:"CMDOSU",  label:"Dose Unit",       bind__oc_itemgroup:"CM", type:"select",    appearance:"minimal",required:"",   readonly:"",   constraint:"", constraint_message:"", relevant:"", calculation:"", hint:"", bind__oc_briefdescription:"", bind__oc_description:"", completion_status:"PLACEHOLDER",  library_source:"AI_GENERATED",   flag_reason:"Codelist missing in source ODM — manual entry required", source_field:"CMDOSU",  source_group:"IG_CM", list_name:"CL_CMDOSU" },
      ]
    },
    {
      form_id: "VS", form_title: "Vital Signs", form_category: "EFFICACY",
      cdash_domain: "VS", visits_assigned: ["SE_SCREEN","SE_BASELINE","SE_WEEK4","SE_WEEK8","SE_WEEK12","SE_EOT"],
      has_repeating_group: false, is_epro: false, arm_applicability: "ALL",
      reuse_count: 1, complexity: "simple",
      settings: { form_title: "Vital Signs", form_id: "F_VS_1", version: "1", style: "theme-grid", cro_accessible: true },
      library_match: { status: "CDASH_MATCH", source_type: "CDASH_STANDARD", fields_from_library: 5, fields_extended: 0 },
      cross_form_dependencies: [],
      choices: [
        { list_name: "CL_VSTESTCD", name: "SYSBP",  label: "Systolic Blood Pressure",  source: "ODM_CODELIST" },
        { list_name: "CL_VSTESTCD", name: "DIABP",  label: "Diastolic Blood Pressure", source: "ODM_CODELIST" },
        { list_name: "CL_VSTESTCD", name: "PULSE",  label: "Pulse Rate",               source: "ODM_CODELIST" },
        { list_name: "CL_VSTESTCD", name: "TEMP",   label: "Temperature",              source: "ODM_CODELIST" },
        { list_name: "CL_VSTESTCD", name: "RESP",   label: "Respiratory Rate",         source: "ODM_CODELIST" },
        { list_name: "CL_VSTESTCD", name: "WEIGHT", label: "Weight",                   source: "ODM_CODELIST" },
        { list_name: "CL_VSTESTCD", name: "HEIGHT", label: "Height",                   source: "ODM_CODELIST" },
      ],
      survey: [
        { name:"SUBJID",   label:"Subject ID",       bind__oc_itemgroup:"VS", type:"calculate", appearance:"w2",    required:"",    readonly:"yes", constraint:"", constraint_message:"", relevant:"", calculation:"instance('clinicaldata')/ODM/ClinicalData/SubjectData/@SubjectKey", hint:"", bind__oc_briefdescription:"", bind__oc_description:"", completion_status:"COMPLETE", library_source:"CDASH_DEFAULT", flag_reason:"", source_field:"SUBJID",   source_group:"IG_VS" },
        { name:"VSTESTCD", label:"VS Test Code",     bind__oc_itemgroup:"VS", type:"select",    appearance:"minimal",required:"yes", readonly:"",   constraint:"", constraint_message:"", relevant:"", calculation:"", hint:"", bind__oc_briefdescription:"", bind__oc_description:"", completion_status:"COMPLETE", library_source:"CDASH_DEFAULT", flag_reason:"", source_field:"VSTESTCD", source_group:"IG_VS", list_name:"CL_VSTESTCD" },
        { name:"VSORRES",  label:"Result (Original)",bind__oc_itemgroup:"VS", type:"text",      appearance:"w2",    required:"yes", readonly:"",   constraint:"", constraint_message:"", relevant:"", calculation:"", hint:"", bind__oc_briefdescription:"", bind__oc_description:"", completion_status:"COMPLETE", library_source:"CDASH_DEFAULT", flag_reason:"", source_field:"VSORRES",  source_group:"IG_VS" },
        { name:"VSORRESU", label:"Result Unit",      bind__oc_itemgroup:"VS", type:"select",    appearance:"minimal",required:"",    readonly:"",   constraint:"", constraint_message:"", relevant:"", calculation:"", hint:"", bind__oc_briefdescription:"", bind__oc_description:"", completion_status:"COMPLETE", library_source:"CDASH_DEFAULT", flag_reason:"", source_field:"VSORRESU", source_group:"IG_VS", list_name:"CL_VSORRESU" },
        { name:"VSDAT",    label:"Date",             bind__oc_itemgroup:"VS", type:"date",      appearance:"w2",    required:"yes", readonly:"",   constraint:"", constraint_message:"", relevant:"", calculation:"", hint:"", bind__oc_briefdescription:"", bind__oc_description:"", completion_status:"COMPLETE", library_source:"CDASH_DEFAULT", flag_reason:"", source_field:"VSDAT",    source_group:"IG_VS" },
      ]
    },
  ]
};
