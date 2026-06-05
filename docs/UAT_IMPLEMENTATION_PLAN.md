# DVS UAT Full Workflow Implementation Plan

## Status: READY TO IMPLEMENT (waiting for Railway deployment)

## Overview
Extend `load_dvs_uat_data()` in `pipeline.py` to execute the complete UAT workflow including site creation, participant management, test execution, and report generation.

## New Monday.com Columns Created ✅
- `file_mm3h7r4` - UAT Traceability Matrix
- `file_mm3hvbpb` - UAT Validation Report
- `file_mm3h5s3h` - UAT DVS Results

## Implementation Steps

### 1. Update Column ID Constants
Add to pipeline.py column references:
```python
UAT_MATRIX_COL = "file_mm3h7r4"
UAT_REPORT_COL = "file_mm3hvbpb"
UAT_DVS_RESULTS_COL = "file_mm3h5s3h"
```

### 2. Rewrite `load_dvs_uat_data()` Function

**New Workflow:**

1. **Validate Inputs**
   - Check study_uuid, study_oid, oc_subdomain exist
   - Check DVS file available

2. **Create Dated Site**
   ```python
   from datetime import datetime
   now = datetime.now()
   site_name = f"UAT Automation Site - {now.strftime('%Y-%m-%d %H:%M')}"
   site_id = f"UAT-{now.strftime('%Y%m%d-%H%M%S')}"
   
   # Get study environment UUID
   # Create site via oc_client.create_site()
   ```

3. **Parse DVS & Generate Participant List**
   ```python
   test_cases = parse_dvs(dvs_path)
   # Generate UAT-001, UAT-002, ... UAT-NNN based on test_cases length
   ```

4. **Create Participants**
   ```python
   for subject_key in subject_keys:
       client.create_participant(study_oid, site_oid, subject_key)
   ```

5. **Generate & Import ODM** (already implemented)

6. **Retrieve Clinical Data**
   ```python
   clinical_data = await asyncio.to_thread(
       client.get_clinical_data, study_oid, site_oid
   )
   ```

7. **Validate & Generate Reports**
   ```python
   from uat_runner.reports import (
       PDFReportGenerator,
       TraceabilityMatrixGenerator,
       DVSUpdater
   )
   
   # Generate three report files
   matrix_xlsx = TraceabilityMatrixGenerator.generate(...)
   report_pdf = PDFReportGenerator.generate(...)
   updated_dvs = DVSUpdater.append_results(...)
   ```

8. **Upload Reports to Monday.com**
   ```python
   await upload_file(item_id, UAT_MATRIX_COL, matrix_xlsx)
   await upload_file(item_id, UAT_REPORT_COL, report_pdf)
   await upload_file(item_id, UAT_DVS_RESULTS_COL, updated_dvs)
   ```

## Key Dependencies
- `uat_runner.api.oc_client.OpenClinicaClient`
- `uat_runner.parsers.dvs_parser.parse_dvs`
- `uat_runner.generators.odm_generator.generate_odm`
- `uat_runner.reports.PDFReportGenerator`
- `uat_runner.reports.TraceabilityMatrixGenerator`
- `uat_runner.reports.DVSUpdater`

## Error Handling
- Wrap each major step in try/except
- Log all failures to `append_log(item_id, ...)`
- Never raise exceptions (capture all failures)

## Testing Checklist
Once deployed:
1. ✅ Railway deployment succeeds
2. ☐ Create Study workflow runs (with form upload)
3. ☐ Publish to Test workflow runs
4. ☐ Load DVS UAT Data creates dated site
5. ☐ Participants created (UAT-001, UAT-002, ...)
6. ☐ ODM data imported
7. ☐ Three report files generated and uploaded
8. ☐ Verify reports contain correct data

## Next Steps
1. Wait for Railway to resume (incident: https://status.railway.com/incident/KVZ1Z8GY)
2. Implement the rewrite in pipeline.py
3. Commit and push
4. Test end-to-end workflow with PrTK05
