// src/data/demoODM.js
// Synthetic Medidata Rave ODM XML export for the CV3001 demo
// Mirrors the structure of the demo spec with realistic source field names
// Includes a many-to-one scenario: AESTDAT_YR + AESTDAT_MON + AESTDAT_DAY → AESTDAT

export const DEMO_ODM_XML = `<?xml version="1.0" encoding="UTF-8"?>
<ODM xmlns="http://www.cdisc.org/ns/odm/v1.3"
     xmlns:mdsol="http://www.mdsol.com/ns/odm/metadata"
     ODMVersion="1.3"
     FileType="Snapshot"
     FileOID="CV3001_EXPORT"
     CreationDateTime="2026-05-12T00:00:00"
     Originator="Medidata Rave 5.6.3"
     SourceSystem="Medidata Rave"
     SourceSystemVersion="5.6.3">

  <Study OID="CV3001">
    <GlobalVariables>
      <StudyName>CV3001 Phase III</StudyName>
      <StudyDescription>Cardiovascular Outcomes Study</StudyDescription>
      <ProtocolName>CV3001</ProtocolName>
    </GlobalVariables>

    <MetaDataVersion OID="MDV_1" Name="CV3001 v1.0"
      mdsol:PrimaryFormOID="F_DM"
      mdsol:DefaultMatrixOID="DEFAULT">

      <!-- ═══════════════════════════════════════════════════
           CODELISTS
      ═══════════════════════════════════════════════════ -->
      <CodeList OID="CL_SEX" Name="Sex" DataType="text">
        <CodeListItem CodedValue="M"><Decode><TranslatedText>Male</TranslatedText></Decode></CodeListItem>
        <CodeListItem CodedValue="F"><Decode><TranslatedText>Female</TranslatedText></Decode></CodeListItem>
        <CodeListItem CodedValue="U"><Decode><TranslatedText>Unknown</TranslatedText></Decode></CodeListItem>
      </CodeList>

      <CodeList OID="CL_RACE" Name="Race" DataType="text">
        <CodeListItem CodedValue="1"><Decode><TranslatedText>American Indian or Alaska Native</TranslatedText></Decode></CodeListItem>
        <CodeListItem CodedValue="2"><Decode><TranslatedText>Asian</TranslatedText></Decode></CodeListItem>
        <CodeListItem CodedValue="3"><Decode><TranslatedText>Black or African American</TranslatedText></Decode></CodeListItem>
        <CodeListItem CodedValue="4"><Decode><TranslatedText>Native Hawaiian or Other Pacific Islander</TranslatedText></Decode></CodeListItem>
        <CodeListItem CodedValue="5"><Decode><TranslatedText>White</TranslatedText></Decode></CodeListItem>
        <CodeListItem CodedValue="6"><Decode><TranslatedText>Multiple</TranslatedText></Decode></CodeListItem>
      </CodeList>

      <CodeList OID="CL_ETHNIC" Name="Ethnicity" DataType="text">
        <CodeListItem CodedValue="HIS"><Decode><TranslatedText>Hispanic or Latino</TranslatedText></Decode></CodeListItem>
        <CodeListItem CodedValue="NOT"><Decode><TranslatedText>Not Hispanic or Latino</TranslatedText></Decode></CodeListItem>
        <CodeListItem CodedValue="UNK"><Decode><TranslatedText>Unknown</TranslatedText></Decode></CodeListItem>
      </CodeList>

      <CodeList OID="CL_AESEV" Name="AE Severity" DataType="text">
        <CodeListItem CodedValue="MILD"><Decode><TranslatedText>Mild</TranslatedText></Decode></CodeListItem>
        <CodeListItem CodedValue="MODERATE"><Decode><TranslatedText>Moderate</TranslatedText></Decode></CodeListItem>
        <CodeListItem CodedValue="SEVERE"><Decode><TranslatedText>Severe</TranslatedText></Decode></CodeListItem>
      </CodeList>

      <CodeList OID="CL_YN" Name="Yes/No" DataType="text">
        <CodeListItem CodedValue="Y"><Decode><TranslatedText>Yes</TranslatedText></Decode></CodeListItem>
        <CodeListItem CodedValue="N"><Decode><TranslatedText>No</TranslatedText></Decode></CodeListItem>
      </CodeList>

      <CodeList OID="CL_AEOUT" Name="AE Outcome" DataType="text">
        <CodeListItem CodedValue="RECOVERED"><Decode><TranslatedText>Recovered/Resolved</TranslatedText></Decode></CodeListItem>
        <CodeListItem CodedValue="RECOVERING"><Decode><TranslatedText>Recovering/Resolving</TranslatedText></Decode></CodeListItem>
        <CodeListItem CodedValue="NOTRECOV"><Decode><TranslatedText>Not Recovered/Not Resolved</TranslatedText></Decode></CodeListItem>
        <CodeListItem CodedValue="FATAL"><Decode><TranslatedText>Fatal</TranslatedText></Decode></CodeListItem>
        <CodeListItem CodedValue="UNK"><Decode><TranslatedText>Unknown</TranslatedText></Decode></CodeListItem>
      </CodeList>

      <CodeList OID="CL_VSTEST" Name="Vital Sign Test Code" DataType="text">
        <CodeListItem CodedValue="SYSBP"><Decode><TranslatedText>Systolic Blood Pressure</TranslatedText></Decode></CodeListItem>
        <CodeListItem CodedValue="DIABP"><Decode><TranslatedText>Diastolic Blood Pressure</TranslatedText></Decode></CodeListItem>
        <CodeListItem CodedValue="PULSE"><Decode><TranslatedText>Pulse Rate</TranslatedText></Decode></CodeListItem>
        <CodeListItem CodedValue="TEMP"><Decode><TranslatedText>Temperature</TranslatedText></Decode></CodeListItem>
        <CodeListItem CodedValue="RESP"><Decode><TranslatedText>Respiratory Rate</TranslatedText></Decode></CodeListItem>
        <CodeListItem CodedValue="WEIGHT"><Decode><TranslatedText>Weight</TranslatedText></Decode></CodeListItem>
        <CodeListItem CodedValue="HEIGHT"><Decode><TranslatedText>Height</TranslatedText></Decode></CodeListItem>
      </CodeList>

      <!-- ═══════════════════════════════════════════════════
           ITEM DEFINITIONS
      ═══════════════════════════════════════════════════ -->

      <!-- DM Items -->
      <ItemDef OID="DM.SUBJID"  Name="SUBJID"  DataType="text" Length="20">
        <Question><TranslatedText>Subject ID</TranslatedText></Question>
        <Alias Context="CDASH" Name="SUBJID"/>
      </ItemDef>
      <ItemDef OID="DM.SITEID"  Name="SITEID"  DataType="text" Length="10">
        <Question><TranslatedText>Site ID</TranslatedText></Question>
        <Alias Context="CDASH" Name="SITEID"/>
      </ItemDef>
      <ItemDef OID="DM.BRTHDAT" Name="BRTHDAT" DataType="date">
        <Question><TranslatedText>Date of Birth</TranslatedText></Question>
        <Alias Context="CDASH" Name="BRTHDAT"/>
      </ItemDef>
      <ItemDef OID="DM.SEX"     Name="SEX"     DataType="text">
        <Question><TranslatedText>Sex</TranslatedText></Question>
        <CodeListRef CodeListOID="CL_SEX"/>
        <Alias Context="CDASH" Name="SEX"/>
      </ItemDef>
      <ItemDef OID="DM.RACE"    Name="RACE"    DataType="text">
        <Question><TranslatedText>Race</TranslatedText></Question>
        <CodeListRef CodeListOID="CL_RACE"/>
        <Alias Context="CDASH" Name="RACE"/>
      </ItemDef>
      <ItemDef OID="DM.ETHNIC"  Name="ETHNIC"  DataType="text">
        <Question><TranslatedText>Ethnicity</TranslatedText></Question>
        <CodeListRef CodeListOID="CL_ETHNIC"/>
        <Alias Context="CDASH" Name="ETHNIC"/>
      </ItemDef>
      <ItemDef OID="DM.COUNTRY" Name="COUNTRY" DataType="text" Length="3">
        <Question><TranslatedText>Country</TranslatedText></Question>
        <Alias Context="CDASH" Name="COUNTRY"/>
      </ItemDef>

      <!-- AE Items — NOTE: dates stored as three separate integer fields (many-to-one scenario) -->
      <ItemDef OID="AE.SUBJID"      Name="SUBJID"      DataType="text" Length="20">
        <Question><TranslatedText>Subject ID</TranslatedText></Question>
      </ItemDef>
      <ItemDef OID="AE.AETERM"      Name="AETERM"      DataType="text" Length="200">
        <Question><TranslatedText>Adverse Event Term (verbatim)</TranslatedText></Question>
        <Alias Context="CDASH" Name="AETERM"/>
      </ItemDef>
      <!-- Source stores start date as 3 separate integer fields — many-to-one to AESTDAT -->
      <ItemDef OID="AE.AESTDAT_YR"  Name="AESTDAT_YR"  DataType="integer" Length="4">
        <Question><TranslatedText>AE Start Year (YYYY)</TranslatedText></Question>
      </ItemDef>
      <ItemDef OID="AE.AESTDAT_MON" Name="AESTDAT_MON" DataType="integer" Length="2">
        <Question><TranslatedText>AE Start Month (MM)</TranslatedText></Question>
      </ItemDef>
      <ItemDef OID="AE.AESTDAT_DAY" Name="AESTDAT_DAY" DataType="integer" Length="2">
        <Question><TranslatedText>AE Start Day (DD)</TranslatedText></Question>
      </ItemDef>
      <!-- End date also split — many-to-one to AEENDAT -->
      <ItemDef OID="AE.AEENDAT_YR"  Name="AEENDAT_YR"  DataType="integer" Length="4">
        <Question><TranslatedText>AE End Year (YYYY)</TranslatedText></Question>
      </ItemDef>
      <ItemDef OID="AE.AEENDAT_MON" Name="AEENDAT_MON" DataType="integer" Length="2">
        <Question><TranslatedText>AE End Month (MM)</TranslatedText></Question>
      </ItemDef>
      <ItemDef OID="AE.AEENDAT_DAY" Name="AEENDAT_DAY" DataType="integer" Length="2">
        <Question><TranslatedText>AE End Day (DD)</TranslatedText></Question>
      </ItemDef>
      <ItemDef OID="AE.AESEV"  Name="AESEV"  DataType="text">
        <Question><TranslatedText>AE Severity Grade</TranslatedText></Question>
        <CodeListRef CodeListOID="CL_AESEV"/>
        <Alias Context="CDASH" Name="AESEV"/>
      </ItemDef>
      <ItemDef OID="AE.AESER"  Name="AESER"  DataType="text">
        <Question><TranslatedText>Serious Adverse Event?</TranslatedText></Question>
        <CodeListRef CodeListOID="CL_YN"/>
        <Alias Context="CDASH" Name="AESER"/>
      </ItemDef>
      <ItemDef OID="AE.AEOUT"  Name="AEOUT"  DataType="text">
        <Question><TranslatedText>AE Outcome</TranslatedText></Question>
        <CodeListRef CodeListOID="CL_AEOUT"/>
        <Alias Context="CDASH" Name="AEOUT"/>
      </ItemDef>

      <!-- CM Items -->
      <ItemDef OID="CM.SUBJID"  Name="SUBJID"  DataType="text" Length="20"><Question><TranslatedText>Subject ID</TranslatedText></Question></ItemDef>
      <ItemDef OID="CM.CMTRT"   Name="CMTRT"   DataType="text" Length="200"><Question><TranslatedText>Medication Name (verbatim)</TranslatedText></Question><Alias Context="CDASH" Name="CMTRT"/></ItemDef>
      <ItemDef OID="CM.CMSTDAT" Name="CMSTDAT" DataType="date"><Question><TranslatedText>Medication Start Date</TranslatedText></Question><Alias Context="CDASH" Name="CMSTDAT"/></ItemDef>
      <ItemDef OID="CM.CMENDAT" Name="CMENDAT" DataType="date"><Question><TranslatedText>Medication End Date</TranslatedText></Question><Alias Context="CDASH" Name="CMENDAT"/></ItemDef>
      <ItemDef OID="CM.CMDOSE"  Name="CMDOSE"  DataType="float" Length="10" SignificantDigits="2"><Question><TranslatedText>Dose</TranslatedText></Question><Alias Context="CDASH" Name="CMDOSE"/></ItemDef>
      <ItemDef OID="CM.CMDOSU"  Name="CMDOSU"  DataType="text"><Question><TranslatedText>Dose Unit</TranslatedText></Question><Alias Context="CDASH" Name="CMDOSU"/></ItemDef>

      <!-- VS Items -->
      <ItemDef OID="VS.SUBJID"   Name="SUBJID"   DataType="text" Length="20"><Question><TranslatedText>Subject ID</TranslatedText></Question></ItemDef>
      <ItemDef OID="VS.VSTESTCD" Name="VSTESTCD" DataType="text"><Question><TranslatedText>Vital Sign Test Code</TranslatedText></Question><CodeListRef CodeListOID="CL_VSTEST"/><Alias Context="CDASH" Name="VSTESTCD"/></ItemDef>
      <ItemDef OID="VS.VSORRES"  Name="VSORRES"  DataType="text" Length="20"><Question><TranslatedText>Result (original units)</TranslatedText></Question><Alias Context="CDASH" Name="VSORRES"/></ItemDef>
      <ItemDef OID="VS.VSORRESU" Name="VSORRESU" DataType="text"><Question><TranslatedText>Result Units</TranslatedText></Question><Alias Context="CDASH" Name="VSORRESU"/></ItemDef>
      <ItemDef OID="VS.VSDAT"    Name="VSDAT"    DataType="date"><Question><TranslatedText>Vital Signs Date</TranslatedText></Question><Alias Context="CDASH" Name="VSDAT"/></ItemDef>

      <!-- ═══════════════════════════════════════════════════
           ITEM GROUP DEFINITIONS
      ═══════════════════════════════════════════════════ -->
      <ItemGroupDef OID="IG_DM" Name="DM" Repeating="No">
        <ItemRef ItemOID="DM.SUBJID"  Mandatory="Yes" OrderNumber="1"/>
        <ItemRef ItemOID="DM.SITEID"  Mandatory="Yes" OrderNumber="2"/>
        <ItemRef ItemOID="DM.BRTHDAT" Mandatory="No"  OrderNumber="3"/>
        <ItemRef ItemOID="DM.SEX"     Mandatory="Yes" OrderNumber="4"/>
        <ItemRef ItemOID="DM.RACE"    Mandatory="No"  OrderNumber="5"/>
        <ItemRef ItemOID="DM.ETHNIC"  Mandatory="No"  OrderNumber="6"/>
        <ItemRef ItemOID="DM.COUNTRY" Mandatory="Yes" OrderNumber="7"/>
      </ItemGroupDef>

      <ItemGroupDef OID="IG_AE" Name="AE" Repeating="Yes" mdsol:IsLog="Yes" mdsol:LogDirection="Vertical">
        <ItemRef ItemOID="AE.SUBJID"      Mandatory="Yes" OrderNumber="1"/>
        <ItemRef ItemOID="AE.AETERM"      Mandatory="Yes" OrderNumber="2"/>
        <ItemRef ItemOID="AE.AESTDAT_YR"  Mandatory="Yes" OrderNumber="3"/>
        <ItemRef ItemOID="AE.AESTDAT_MON" Mandatory="Yes" OrderNumber="4"/>
        <ItemRef ItemOID="AE.AESTDAT_DAY" Mandatory="Yes" OrderNumber="5"/>
        <ItemRef ItemOID="AE.AEENDAT_YR"  Mandatory="No"  OrderNumber="6"/>
        <ItemRef ItemOID="AE.AEENDAT_MON" Mandatory="No"  OrderNumber="7"/>
        <ItemRef ItemOID="AE.AEENDAT_DAY" Mandatory="No"  OrderNumber="8"/>
        <ItemRef ItemOID="AE.AESEV"       Mandatory="Yes" OrderNumber="9"/>
        <ItemRef ItemOID="AE.AESER"       Mandatory="Yes" OrderNumber="10"/>
        <ItemRef ItemOID="AE.AEOUT"       Mandatory="No"  OrderNumber="11"/>
      </ItemGroupDef>

      <ItemGroupDef OID="IG_CM" Name="CM" Repeating="Yes" mdsol:IsLog="Yes">
        <ItemRef ItemOID="CM.SUBJID"  Mandatory="Yes" OrderNumber="1"/>
        <ItemRef ItemOID="CM.CMTRT"   Mandatory="Yes" OrderNumber="2"/>
        <ItemRef ItemOID="CM.CMSTDAT" Mandatory="No"  OrderNumber="3"/>
        <ItemRef ItemOID="CM.CMENDAT" Mandatory="No"  OrderNumber="4"/>
        <ItemRef ItemOID="CM.CMDOSE"  Mandatory="No"  OrderNumber="5"/>
        <ItemRef ItemOID="CM.CMDOSU"  Mandatory="No"  OrderNumber="6"/>
      </ItemGroupDef>

      <ItemGroupDef OID="IG_VS" Name="VS" Repeating="No">
        <ItemRef ItemOID="VS.SUBJID"   Mandatory="Yes" OrderNumber="1"/>
        <ItemRef ItemOID="VS.VSTESTCD" Mandatory="Yes" OrderNumber="2"/>
        <ItemRef ItemOID="VS.VSORRES"  Mandatory="Yes" OrderNumber="3"/>
        <ItemRef ItemOID="VS.VSORRESU" Mandatory="No"  OrderNumber="4"/>
        <ItemRef ItemOID="VS.VSDAT"    Mandatory="Yes" OrderNumber="5"/>
      </ItemGroupDef>

      <!-- ═══════════════════════════════════════════════════
           FORM DEFINITIONS
      ═══════════════════════════════════════════════════ -->
      <FormDef OID="F_DM" Name="DM" Repeating="No">
        <ItemGroupRef ItemGroupOID="IG_DM" Mandatory="Yes"/>
      </FormDef>
      <FormDef OID="F_AE" Name="AE" Repeating="Yes">
        <ItemGroupRef ItemGroupOID="IG_AE" Mandatory="Yes"/>
      </FormDef>
      <FormDef OID="F_CM" Name="CM" Repeating="Yes">
        <ItemGroupRef ItemGroupOID="IG_CM" Mandatory="Yes"/>
      </FormDef>
      <FormDef OID="F_VS" Name="VS" Repeating="No">
        <ItemGroupRef ItemGroupOID="IG_VS" Mandatory="Yes"/>
      </FormDef>

    </MetaDataVersion>
  </Study>
</ODM>`;
