# FUTURE PROJECT — OC4 EDC UAT Runner (Test Data Execution Service)

**Created:** 24 April 2026
**Status:** PLANNING ONLY — Not started. Awaiting account move + engineering input.
**Intended home:** Anthropic Enterprise account (currently on personal)
**Timeline:** PoC 3-4 weeks, internal tool 6-8 weeks, productized 6-12 months total
**Related projects:** FUTURE-PROJECT-rag-and-trainer.md

---

## 1. Vision

Build a standalone microservice ("OC4 EDC UAT Runner") that executes the UAT
test cases from a DVS (Data Validation Specification) XLSX against a published
OpenClinica study, captures actual results, and updates the DVS with pass/fail
outcomes.

The product is sellable two ways:

1. **Bundled with oc-ai-pipeline** as Chain E — automatically test what we just
   built. Customers using the build pipeline get UAT execution as a value-add.

2. **Standalone product** — any customer with a DVS XLSX formatted to our spec
   can use the UAT Runner against their own OpenClinica instance, regardless of
   how the DVS was created (our pipeline, hand-built, or third-party tool).

The standardized DVS XLSX format becomes the **product interface** that
decouples build from test, opening a market for UAT-execution-only customers.

---

## 2. The 6-step workflow

| # | Step | Documented? | Notes |
|---|---|---|---|
| 1 | Publish study from Designer to Test environment | ⚠️ UNVERIFIED | No public REST endpoint visible. UI says "single click." Need engineering team to confirm or expose endpoint. **Assumption for v1: human publishes manually.** |
| 2 | Create synthetic test site in Test env | ✅ Yes | `POST /api/study-environments/{uuid}/sites` |
| 3 | Create participant per UAT case | ✅ Yes | `POST /pages/auth/api/clinicaldata/studies/{studyOID}/sites/{siteOID}/participants` |
| 4 | Schedule events the participant needs | ✅ Yes | `POST /pages/auth/api/studies/{studyOID}/sites/{siteOID}/events` |
| 5 | Import test data via CDISC ODM XML | ✅ Yes | `POST /pages/auth/api/clinicaldata/import/xml` returns jobUuid; poll `GET /jobs/{jobUuid}/downloadFile` for log |
| 6 | Retrieve clinical data + queries (DNs) | ✅ Yes | `GET /pages/auth/api/clinicaldata/{studyOID}/{participantOID}/{studyEventOID}/{formOID}?clinicalData=y&includeDN=y&includeAudits=y` |

**v1 simplification:** human handles step 1. Runner waits for the study to be in
"published to Test" state (detectable via `published` boolean on
StudyEnvironmentDTO) before executing steps 2-6.

---

## 3. Architecture

### 3.1 Microservice shape

```
oc-uat-runner/                    NEW separate repo
  app/
    main.py                       FastAPI entry point
    routes.py                     /uat/run, /uat/status/{jobId}, /uat/results/{jobId}

  core/
    dvs_parser.py                 Read DVS XLSX → structured UAT cases
    odm_xml_builder.py            Convert UAT case → CDISC ODM XML
    oc_client.py                  OC API client (likely shared with oc-ai-pipeline)
    publish_check.py              Verify study is published-to-Test before starting
    test_executor.py              Run a single UAT case end-to-end
    result_verifier.py            Compare expected vs actual

  worker/
    uat_runner.py                 Background job executor (long-running)
    job_queue.py                  Queue for batching imports
    rate_limiter.py               Respect OC API rate limits

  config/
    customer_profiles/            Per-customer OC URLs, credentials

  outputs/
    dvs_results.py                Update DVS XLSX with pass/fail + actual results
    validation_traceability.py    Generate Validation Traceability Matrix (XLSX)
    validation_summary.py         Generate Validation Summary Report (PDF)
    monday_updater.py             (Optional) Push all 3 output files to Monday

  tests/
    fixtures/                     Sample DVS files, expected outputs
    test_e2e.py
```

### 3.2 Integration with oc-ai-pipeline

```
monday webhook → oc-ai-pipeline → Chains A, B, C, D complete
                                ↓
                    [optional] Chain E: POST oc-uat-runner /uat/run
                                ↓
                    [optional] Chain F: POST oc-trainer /retrieve (RAG)
```

oc-ai-pipeline becomes the **orchestrator**. Each new microservice is:
- Independently deployable (different Railway services or hosts)
- Independently scalable (UAT Runner needs more memory/time than pipeline)
- Independently sellable (customer can buy UAT Runner without build pipeline)
- Failure-isolated (if UAT Runner is down, build pipeline still works)

### 3.3 Three usage modes

**Mode 1 — Internal (your team running it for clients):**
- Customer provides: DVS XLSX + OC instance URL + service account credentials
- Your team kicks off via web UI or API call
- Runner executes, returns updated DVS + summary report

**Mode 2 — Self-serve customer product:**
- Customer logs into a portal
- Uploads their DVS XLSX
- Connects their OC instance via OAuth or stored credentials
- Clicks "Execute UAT"
- Watches real-time progress
- Downloads marked-up DVS when complete

**Mode 3 — Embedded in oc-ai-pipeline:**
- After Chain D completes, optionally trigger Chain E
- Updates Monday item with UAT pass rate
- DVS XLSX gets re-uploaded to Monday with results filled in

All three modes share the same backend. Different front-ends.

---

## 4. Output documents

Each UAT run produces three output files. All are generated from the same
data the runner already collects — no additional API calls required.

---

### 4.1 Updated DVS XLSX

The original DVS XLSX returned with three new columns populated on the
UAT_Cases sheet:

| Column | Content |
|---|---|
| Actual Result | What the system actually returned (error message, inserted value, DN text) |
| Test Result | PASS / FAIL / SKIPPED |
| Execution Date | ISO timestamp of when this case was executed |

SKIPPED cases include a reason (e.g. "cross-form: build ZIP not provided" or
"blocked by failed prerequisite case").

This is the working document the build team uses to triage failures.

---

### 4.2 Validation Traceability Matrix (VTM) — XLSX

A regulatory-grade traceability document mapping every requirement to its
test cases and recorded outcomes. Required by FDA 21 CFR Part 11 and ICH E6
GCP guidelines for validated clinical systems.

**Sheet structure:**

| Column | Description |
|---|---|
| Form | Form short code (e.g. AE, VS, DM) |
| Field OID | Item OID from OC study |
| Field Label | Human-readable field label |
| Validation Rule Type | Constraint / Required / Calculation / Conditional Display / Cross-form |
| Rule Description | The validation rule in plain English |
| DVS Check ID | Check identifier from DVS |
| UAT Case ID | QT-ID from DVS UAT_Cases sheet |
| Test Scenario | Brief description (e.g. "Value above upper range limit") |
| Input Data | Exact data entered for this case |
| Expected Result | What should happen |
| Actual Result | What did happen |
| Test Result | PASS / FAIL / SKIPPED |
| Executed By | Service account or user ID |
| Execution Date | ISO timestamp |
| Environment | OC Test environment URL |
| OC Version | Version of OpenClinica used |

**Key property:** Every validation rule in the study is covered by at least
one row. Regulators can confirm 100% rule coverage at a glance.

**Format:** XLSX. Tabular, sortable, filterable. One row per UAT case.
Multiple rows per field when multiple test scenarios exist for one rule.

---

### 4.3 Validation Summary Report (VSR) — PDF

A narrative executive summary of the validation run. This is the document
a QA manager or Sponsor representative reviews and signs before the study
goes to Production.

**Sections:**

1. **Study Information**
   - Protocol number, study title, OC study UUID
   - Test environment URL and OC version
   - Date of execution, executed by

2. **Scope**
   - Forms included in scope
   - Number of validation rules tested
   - Number of UAT cases executed

3. **Results Summary**
   - Total cases: N
   - Passed: N (N%)
   - Failed: N (N%)
   - Skipped: N (N%) with reason breakdown
   - Pass rate

4. **Failed Cases** (if any)
   - Table of failed cases with Check ID, form, field, expected vs actual
   - Severity classification (Minor / Major / Critical)
   - Disposition (Accepted as-is / Remediation required)

5. **Deviations and Anomalies**
   - Any test environment issues, skipped cases, or unexpected behaviors

6. **Conclusion**
   - "The study build meets validation requirements and is approved for
     release to the Production environment."
   - OR: "N items require remediation before Production release. See
     Section 4 for details."

7. **Signature Block**
   - Executed by: _________________ Date: _______
   - Reviewed by: _________________ Date: _______
   - Approved by: _________________ Date: _______

**Format:** PDF. Professionally formatted, consistent with clinical
regulatory documentation standards. Matches the visual style of other
OpenClinica output documents (quote PDFs, study spec PDFs).

---

### 4.4 Monday integration (optional)

When UAT Runner is triggered from oc-ai-pipeline (Chain E), all three output
files are uploaded to the relevant Monday item columns:
- Updated DVS XLSX → DVS file column
- VTM XLSX → new "UAT Results" column
- VSR PDF → new "Validation Summary" column
- Monday item status updated → "UAT Complete" or "UAT Failed"

---

## 5. Standard customer-provided input package

This is the commercially smart insight: standardize the inputs as the
**interface contract** between build and test phases. External customers
provide one or two standardized files:

**Required:** DVS XLSX (formatted to our spec)
**Optional but strongly recommended:** EDC build ZIP (XLSForm files)

The DVS XLSX defines what to test. The build ZIP defines how the forms
relate to each other (dependency graph for cross-form/cross-visit cases).

**With both files →** runner can execute all UAT case types
**With DVS only →** runner executes single-form cases; cross-form cases
are skipped with a warning report

Required deliverables for this to work as a standalone product:

- **Public format documentation** — or customer onboarding doc — describing
  required sheets, columns, value formats for the DVS XLSX
- **Downloadable DVS template** — blank XLSX customers can fill in
- **Format validator** — runner detects malformed DVS files and returns
  actionable error messages ("Row 47 missing 'Expected Result' value")
- **Versioned schema** — DVS_v1, DVS_v2 etc. so we can evolve the format
  without breaking existing customers
- **Build ZIP parser** — module that extracts dependency info from XLSForm
  files (likely shared with edc-builder skill)

---

## 6. Open questions (need answers before code starts)

### 5.1 Publish step automation
- Is there a programmatic publish-to-Test endpoint? (unverified)
- If not: request engineering build one, or use Playwright browser automation?
- For v1 we ASSUME human publishes manually — this needs revisit later

### 5.2 Customer credential model
Two paths, each with tradeoffs:
- **Bearer token per run:** simplest. Customer pastes a token at run-start.
  No storage, no compliance burden. Token expires, customer re-auths.
- **Stored OAuth refresh tokens:** better UX. Customer connects once. More
  security overhead, audit logging, potential compliance scope (HIPAA, etc.)
- Hybrid: bearer for trial customers, OAuth for paying enterprise

### 5.3 DVS format ownership
- Publish format publicly → broader market for standalone UAT-only customers
- Keep format closed → captive audience but smaller market
- Recommendation TBD — depends on pricing and competitive strategy

### 5.4 Test environment isolation — PID block allocation
**Resolved approach:**
- Each UAT run allocates a contiguous PID block. Run 1 uses UAT-001 to
  UAT-010, Run 2 uses UAT-011 to UAT-020, etc.
- PID prefix: **`UAT-`** (e.g. `UAT-001`, `UAT-002`) so synthetic data is
  unambiguous and never confused with real participants.
- Block size = number of distinct participants needed for that run's UAT
  cases (some cases share a participant for cross-form/cross-visit
  dependencies; runner determines grouping).
- At run start, runner queries OC for highest existing `UAT-NNN` PID and
  allocates next block from there. Idempotent and safe across multiple
  concurrent runs (with row-level locking on the allocation step).
- Dedicated UAT site within Test env, created once and reused across runs.
- Cleanup of synthetic participants is **out of scope** — they remain in
  Test env as audit trail. Can be archived manually if needed.

**Open questions:**
- OC Test environment must support custom PIDs (vs. system-generated). Need
  engineering team to confirm.
- Maximum block size? OC participant cap may apply.

### 5.5 Cross-form constraint sequencing — two-tier dependency resolution
**Resolved approach:** Runner uses one of two paths depending on input source.

**Path A — Internal customers (using oc-ai-pipeline):**
- Runner has access to the Study Specification JSON we generate
- Study Spec encodes form sequencing, visit schedule, and cross-form xpath
  references (per OC-4 rule)
- Runner reads Spec, builds dependency graph, topologically sorts UAT cases
- Imports independent cases first, then dependent cases in correct order

**Path B — External customers (DVS XLSX + EDC build ZIP):**
- Customer provides BOTH DVS XLSX AND the build ZIP (XLSForm files)
- Runner parses XLSForm files to extract dependency info from:
  - `bind::oc:itemgroup` references
  - `calculate` field formulas
  - `constraint` xpath patterns referencing other items
  - `relevant` (skip-logic) expressions
- Builds equivalent dependency graph as Path A
- Build ZIP is **OPTIONAL** for external customers:
  - With ZIP → full cross-form/cross-visit case support
  - Without ZIP → cross-form cases SKIPPED with clear warning
    ("23 cases skipped: cross-form sequencing requires build ZIP")
  - Basic single-form constraint cases still run

**Implementation notes:**
- Need a build-ZIP parser module that extracts dependency info from
  XLSForm files. Could share code with the existing edc-builder skill that
  already understands these patterns.
- Some constraints depend on cross-VISIT data (e.g. "labs at week 4 ≥
  baseline labs"). Runner must schedule multiple events and partially
  populate them before constraint cases can fire. More complex but
  tractable with topological sort.
- Cases with circular dependencies → hard error, log and skip.

### 5.6 Rate limits and performance
- 500+ UAT cases × per-import-job × poll-for-completion = potentially hours
- Mitigations:
  - Batched ODM XML imports (50-100 cases per job)
  - Parallel participant creation
  - Smart retry with exponential backoff on 429s
- Need to measure typical case count and tune accordingly

### 5.7 Result categorization
- Happy path → Status=Inserted, no DN → ✅ PASS
- Sad path → Status=Failed with constraint errorCode → ✅ PASS (error fired correctly)
- Sad path → Inserted with DN → ✅ PASS (constraint generated query)
- Inserted, no error, no DN → ❌ FAIL (constraint didn't fire when it should have)
- Failed with WRONG errorCode → ❌ FAIL (wrong constraint fired)
- Need clear taxonomy in result_verifier.py

---

## 7. Sequencing and milestones

### Phase 0: Validate the unknown (1 week)
- Engineering team confirms: programmatic publish API or browser automation?
- Decision shapes everything downstream
- v1 assumes human publish — Phase 0 is about understanding what v2 requires

### Phase 1: Internal proof of concept (3-4 weeks)
- Build minimal `oc-uat-runner` against ONE protocol (e.g. CRS-138)
- Manually format ~10 UAT cases from CRS-138's DVS
- Verify import → retrieval → comparison roundtrip works
- No customer-facing features, no UI, just CLI
- **Success criterion:** can detect a constraint that DOESN'T fire when it should

### Phase 2: Internal production tool (6-8 weeks)
- Full DVS XLSX parser
- Add batching, retry, rate limit handling
- Integrate with oc-ai-pipeline as optional Chain E
- Use it on every internal build to validate
- **Success criterion:** zero false positives across 5+ real builds

### Phase 3: Customer-facing product (3-6 months)
- Web UI for upload + monitor + download
- DVS format documentation + template
- Per-customer credential management
- Pricing + packaging decisions
- Pilot with 1-2 friendly customers
- **Success criterion:** 1 paying customer using it standalone

**Total realistic timeline: 6-12 months from start to commercial product.**

---

## 8. Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Publish API doesn't exist | Medium | High | v1 uses human publish; Phase 0 investigates |
| Test env participant collisions across runs | Medium | Low | PID block allocation with `UAT-NNN` prefix; each run gets contiguous block |
| Cross-form sequencing too complex | Medium | Medium | Two-tier resolution: Spec-based (internal) or ZIP-parsed (external); skip + warn when no ZIP available |
| Test env pollution causes customer issues | Medium | Medium | Dedicated UAT site; `UAT-` prefixed PIDs make synthetic data unambiguous |
| Rate limits make runs too slow | Low | Medium | Batch imports; measure and tune |
| Customers won't share OC credentials | Low | Low | Bearer-token mode handles this |
| Format changes break existing customers | Medium | Medium | Versioned schema (DVS_v1, v2) |
| OC Test env doesn't allow custom PIDs | Unknown | High | Engineering team to confirm; fallback = system-generated with `UAT_` tag |

---

## 9. Decisions needed before code starts

1. **Engineering team review** of publish-to-Test feasibility
2. **Credential model:** bearer per run vs stored OAuth (or both?)
3. **DVS format:** public vs closed schema
4. **Pricing:** standalone vs bundle-only, subscription vs per-run
5. **Hosting:** cloud-only vs on-prem option
6. **Repo structure:** new repo vs monorepo with oc-ai-pipeline

---

## 10. When to resume

**Trigger conditions:**
- User has moved to Anthropic Enterprise account
- Engineering team has weighed in on publish-to-Test question
- RAG/Trainer Phase 1 is complete or in progress (sequence these separately)

**First action when resuming:**
Open this doc. Confirm assumptions still hold. Schedule engineering review of
Phase 0 questions. Then start Phase 1 PoC against CRS-138.

---

## 11. Notes from the 24-25 April 2026 conversation

**24 April session:**
- User asked theoretically whether oc-ai-pipeline could load DVS test data and
  see results.
- Research confirmed: 5 of 6 steps have documented public APIs. Step 1
  (publish-to-Test) is the unknown.
- User explicitly said: "let's assume that a human publishes to test for now...
  I may come back and tell you to automate this at some point."
- User asked if this should be a skill: NO. Skills don't fit long-running
  stateful orchestration. This is a microservice.
- User asked if oc-ai-pipeline can call this microservice: YES. Standard
  HTTP-API integration pattern, same as how it would call oc-trainer for RAG.
- User confirmed: "I don't want to start building this." Document only.
- User wants both this and the RAG/Trainer to be sellable products that
  customers without our pipeline can also use.

**25 April session — risk refinements:**
- Risk 2 (test env isolation): User specified PID block allocation pattern.
  Each run reserves contiguous block (Run 1 = UAT-001 to UAT-010, Run 2 =
  UAT-011 to UAT-020, etc). Confirmed `UAT-` prefix for synthetic PIDs.
- Risk 3 (cross-form sequencing): User proposed two-tier approach. Internal
  runs use Study Spec dependency graph. External runs use DVS XLSX + EDC
  build ZIP, runner reverse-engineers dependencies from XLSForm files.
  Build ZIP is OPTIONAL — without it, cross-form cases are skipped with
  warning, basic constraint cases still run.
- Risk 4 (test data realism): User acknowledged.
- Risk 5 (credentials): User confirmed credential passing mechanism needed
  but deferred specific model decision (bearer vs OAuth) to productization.

---

## 12. Monday Board Structure — DVS UAT Validation Hub

**Purpose:** Orchestrate UAT test runs, track execution status, store results

**Board Name:** DVS UAT Validation Hub

| Column Name | Type | Purpose |
|------------|------|---------|
| Name | Text | Test run name (e.g., "CRS-136 UAT Run 1") |
| Study Identifier | Text | Protocol number (e.g., "CRS-136") |
| Customer | People | Account owner |
| DVS Input | File | DVS XLSX to execute |
| EDC Build ZIP | File | Optional - XLSForm package for cross-form validation |
| Study Spec JSON | File | Optional - for internal customers with cross-form dependencies |
| Test Environment | Dropdown | Test URL (e.g., "acmebio.build.openclinica.io") |
| Study OID | Text | Auto-populated after publish |
| Site OID | Text | Auto-populated after site creation |
| Participant Count | Number | How many test participants to create (default: # of DVS test cases) |
| UAT Status | Status | Not Started → Publishing → Site Created → Participants Created → Data Imported → Validation Running → Complete → Failed |
| DVS Output | File | Updated DVS with Pass/Fail results |
| Validation Report | File | PDF summary of test outcomes |
| Traceability Matrix | File | DVS checks → XLSForm → Protocol mapping |
| Test Data ODM | File | CDISC ODM XML used for data import |
| Error Log | Long Text | Failure details for debugging |
| Run Duration | Numbers | Time elapsed (minutes) |
| Pass Rate | Numbers | % of DVS checks that passed |

**Relation to AI Hub:**
- Board relation column linking UAT runs to AI Hub items
- Enables: "Show me all UAT runs for this study build"

---

## 13. AI Hub Integration Details

**New AI Hub Column:**
- **Column name:** "DVS for UAT"
- **Type:** File
- **Purpose:** DVS XLSX formatted for UAT Runner input
- **Auto-populated:** After DVS generation complete
- **Workflow:** User downloads DVS + EDC Build ZIP + Study Spec JSON → uploads to UAT Hub board

**Data Flow:**
AI Hub Pipeline
→ Generates: Protocol Specification (JSON/XLSX/PDF)
→ Generates: EDC Build ZIP (XLSForm package)
→ Generates: DVS XLSX (validation spec)
↓
User downloads all three artifacts
↓
User creates DVS UAT run item
→ Uploads DVS XLSX (required)
→ Uploads EDC Build ZIP (optional - enables cross-form validation)
→ Uploads Study Spec JSON (optional - internal customers only)
↓
UAT Runner executes tests
↓
Results posted back to monday.com DVS UAT Hub

---

## 14. Human Interaction Points — Detailed Breakdown

### Required Human Actions

**1. Publish study to Test** (until API exists)
- **When:** Before creating monday.com UAT run item
- **Who:** OpenClinica admin or developer
- **Time:** ~5 minutes
- **Blocker:** No programmatic API (engineering question sent May 18, 2026)
- **Workarounds:** Manual UI publish (v1), browser automation (v2)

**2. Create UAT run item** in monday.com
- **When:** After study is published to Test
- **Who:** Validator / QA team member
- **Time:** ~2 minutes
- **Inputs:** DVS XLSX, Test environment URL, optional EDC Build ZIP, optional Study Spec JSON
- **Trigger:** Sets status to "Ready to Run" → webhook fires

**3. Review results**
- **When:** After automation completes (~5-15 minutes)
- **Who:** Validator / QA team member
- **Time:** ~15-30 minutes
- **Deliverables:** Updated DVS, Validation Report, Traceability Matrix
- **Action:** Investigate failures, iterate on build if needed

### Optional Human Actions

**4. Provide Study Spec JSON** (internal customers only)
- **When:** If cross-form validation tests are needed
- **Who:** AI Hub pipeline user
- **Time:** Already generated by AI Hub (no extra work)
- **Benefit:** Enables testing of cross-form dependencies (e.g., AGE calculated from BRTHDAT)

**5. Retry failed checks** (manual investigation)
- **When:** If automation reports failures
- **Who:** Developer
- **Time:** Variable (depends on failure complexity)
- **Common causes:** Missing XLSForm validation logic, incorrect DVS expectations, data generation issues

---

## 15. Error Handling — Concrete Examples

### Error Log Format (Monday.com Long Text Column)
2026-05-18 14:32:15 - Site creation successful (SS_UATSITE_001)
2026-05-18 14:32:22 - Participant UAT-001 created
2026-05-18 14:32:23 - Participant UAT-002 created
2026-05-18 14:32:24 - Participant UAT-003 created
2026-05-18 14:32:45 - ODM import started
2026-05-18 14:32:47 - ERROR: Participant UAT-003 data rejected - missing required field BRTHDAT
2026-05-18 14:33:10 - Validation comparison complete (45/52 checks passed)
2026-05-18 14:33:12 - DVS Output uploaded to monday.com
2026-05-18 14:33:13 - Status updated: Complete (87% pass rate)

### Failure Scenarios & Recovery

**Scenario 1: Publish not complete**
- **Detection:** Study OID not found in Test environment
- **Action:** Fail fast, update status to "Failed - Study Not Published"
- **User notification:** Error log + email (if configured)
- **Recovery:** User publishes study manually, clicks "Retry" button

**Scenario 2: Site creation fails**
- **Detection:** 400/500 from site creation API
- **Action:** Retry 3x with exponential backoff (5s, 15s, 45s)
- **If still failing:** Log error, fail run
- **Common causes:** Invalid timezone, duplicate uniqueIdentifier

**Scenario 3: Participant creation partial failure**
- **Detection:** Some participants created, others failed
- **Action:** Continue with successful participants, log failures
- **Result:** Partial test coverage (e.g., 18/25 DVS checks tested)
- **Recovery:** User can manually create missing participants, re-run

**Scenario 4: Data import validation errors**
- **Detection:** ODM import returns validation errors
- **Action:** Parse error response, log specific issues per participant
- **Result:** Some test data loaded, some rejected
- **Example:** "UAT-003: BRTHDAT required but not provided"

---

## 16. Success Metrics by Phase

### Phase 1 Success (MVP CLI Tool)
- ✅ End-to-end execution on CRS-138 (one complete study)
- ✅ DVS updated with Pass/Fail/Not Tested results for all checks
- ✅ Validation Report matches manual validation results (spot-checked by QA)
- ✅ Traceability Matrix complete (all DVS checks mapped to XLSForm rows)
- ✅ No false positives (checks marked failed when they should pass)
- ✅ Clear error messages for any failures

### Phase 2 Success (Monday.com Integration)
- ✅ Monday.com webhook triggers work reliably (100% success rate over 20 runs)
- ✅ 5+ studies validated via automation (diverse protocol types)
- ✅ <5% false positive rate (checks incorrectly marked as failed)
- ✅ Internal team adoption: 50% of UAT validation runs use automation
- ✅ Average run time: <15 minutes (setup through results upload)
- ✅ Zero production incidents from automated test data

### Phase 3 Success (Production Product)
- ✅ External customer pilot: 1-2 customers using UAT Runner
- ✅ DVS schema published & documented (public or partner-only)
- ✅ Standalone product offering defined (pricing, packaging, support)
- ✅ 90% customer satisfaction score (survey after 5 runs)
- ✅ Revenue target: $X per customer per month (TBD with finance)
- ✅ Support burden: <2 hours per week per customer

---

## 17. Next Immediate Actions (Week of May 18, 2026)

**✅ COMPLETED:**
1. Engineering questions sent to OpenClinica (May 18, 2026)
2. Design doc updated with implementation details (this section)

**⏳ IN PROGRESS:**
3. Awaiting OpenClinica Engineering response on publish API

**🔜 NEXT:**
4. Create DVS UAT Validation Hub board in monday.com (pending user approval)
5. Define DVS XLSX output format in AI Hub pipeline (coordinate with dvs-specification skill)
6. Set up Railway service skeleton: `oc-uat-runner` (after Phase 0 decisions made)

---

**Last Updated:** May 18, 2026
**Implementation Status:** Phase 0 - Engineering Q&A
**Next Milestone:** Phase 0 complete (after OpenClinica response + internal decisions)
