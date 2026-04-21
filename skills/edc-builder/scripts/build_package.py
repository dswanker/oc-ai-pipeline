"""
build_package.py — EDC Build Package Assembler
Assembles all build outputs into a single zip file for download.

Usage:
    from build_package import build_package
    zip_path = build_package(spec_data, build_log, forms_dir, csv_dir,
                             checklist_dir, output_dir)
"""

import os, zipfile, datetime

README_TEMPLATE = """EDC BUILD PACKAGE
=================
Protocol:   {protocol}
Study ID:   {study_id}
Build Date: {date}
Built by:   Claude (edc-builder skill)

CONTENTS
--------
forms/           — One XLSForm .xlsx file per CRF ({n_forms} forms)
csv/             — Supporting CSV files
  {study_id}_tpt.csv     — Study timepoint lookup table
  labranges.csv          — Laboratory reference ranges
checklist/       — Study build checklist
  {protocol}_Build_Checklist.pdf   — Printable sign-off document
  {protocol}_Build_Checklist.xlsx  — Digital QA checklist

UPLOAD INSTRUCTIONS
-------------------
1. Review the Build Checklist PDF and obtain required sign-offs
2. For each form in forms/:
   a. Open the .xlsx file and verify the content
   b. Upload to OpenClinica Study Designer
   c. Publish the form
3. Upload {study_id}_tpt.csv as an external dataset named '{study_id}_tpt'
4. Upload labranges.csv as an external dataset named 'labranges'
5. After study configuration is complete, update all cross-form OID
   placeholders (marked [EVENT_OID] and [FORM_OID]) with actual OIDs
   from the OpenClinica Data Dictionary

ITEMS REQUIRING ATTENTION
-------------------------
{attention_items}

FORMS BUILT
-----------
{forms_list}

FORMS WITH PLACEHOLDER VALUES (require site-specific completion)
----------------------------------------------------------------
{placeholder_forms}
"""


def build_package(spec_data, build_log, forms_dir, csv_dir,
                  checklist_dir, output_dir):
    """
    Assemble all build outputs into a zip file.
    Returns the path to the zip file.
    """
    meta     = spec_data.get('study_meta', {})
    protocol = meta.get('protocol_number', 'STUDY')
    study_id = meta.get('study_id', 'study')
    today    = datetime.date.today().strftime('%Y%m%d')
    today_hr = datetime.date.today().strftime('%d %b %Y')

    zip_name = f"{protocol}_EDC_Build_{today}.zip"
    zip_path = os.path.join(output_dir, zip_name)
    folder   = f"{protocol}_EDC_Build_{today}"

    # Build attention items text
    attention = []
    for ph in build_log.get('placeholder_applied', []):
        attention.append(f"  - {ph.get('form_id','')}: {ph.get('note','')}")
    for oid in build_log.get('oid_placeholders', []):
        attention.append(f"  - {oid.get('form_id','')}.{oid.get('field','')}: OID confirmation needed")
    attention_text = '\n'.join(attention) if attention else "  None — build is complete"

    # Forms list
    forms_list = '\n'.join(f"  - {f}" for f in build_log.get('forms_built', []))

    # Placeholder forms
    ph_forms = '\n'.join(
        f"  - {p.get('form_id','')}: {', '.join(str(f) for f in p.get('fields',[])[:3])}"
        for p in build_log.get('placeholder_applied', [])
        if p.get('form_id') != 'labranges.csv'
    ) or "  None"

    readme = README_TEMPLATE.format(
        protocol=protocol,
        study_id=study_id,
        date=today_hr,
        n_forms=len(build_log.get('forms_built', [])),
        attention_items=attention_text,
        forms_list=forms_list,
        placeholder_forms=ph_forms,
    )

    os.makedirs(output_dir, exist_ok=True)

    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:

        # README
        zf.writestr(f"{folder}/BUILD_README.txt", readme)

        # forms/
        if os.path.isdir(forms_dir):
            for fname in sorted(os.listdir(forms_dir)):
                if fname.endswith('.xlsx'):
                    zf.write(os.path.join(forms_dir, fname),
                             f"{folder}/forms/{fname}")

        # csv/
        if os.path.isdir(csv_dir):
            for fname in sorted(os.listdir(csv_dir)):
                if fname.endswith('.csv'):
                    zf.write(os.path.join(csv_dir, fname),
                             f"{folder}/csv/{fname}")

        # checklist/
        if os.path.isdir(checklist_dir):
            for fname in sorted(os.listdir(checklist_dir)):
                if fname.endswith('.pdf') or fname.endswith('.xlsx'):
                    zf.write(os.path.join(checklist_dir, fname),
                             f"{folder}/checklist/{fname}")

    # Report zip contents
    with zipfile.ZipFile(zip_path, 'r') as zf:
        names = zf.namelist()
    print(f"Package: {zip_path}")
    print(f"  {len(names)} files in zip")
    for n in sorted(names):
        print(f"  {n}")

    return zip_path


if __name__ == "__main__":
    print("build_package.py ready — call build_package() to assemble")
