"""Pipeline Mode: "Logic + UAT" and "UAT only" on a customer's existing study (pipeline_modes.py). OpenClinica and
monday are mocked: every call the flow makes goes through `deps`. Full build is whatever the pipeline did before:
a blank or missing column never reaches this module's run."""
import asyncio, io, os, sys, types, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
for p in (ROOT, HERE, os.path.join(ROOT, "skills", "logic-coverage", "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)
import openpyxl

import pipeline_modes as pm
from monday_client import COL
from standards import fixtures as fx

EVENTS_XML = ('<ODM xmlns="http://www.cdisc.org/ns/odm/v1.3"><Study OID="S_T"><MetaDataVersion OID="v1">'
              '<StudyEventDef OID="SE_ONE" Name="One" Repeating="No" Type="Scheduled">'
              '<FormRef FormOID="F_AE" Mandatory="No"/><FormRef FormOID="F_AEGEN" Mandatory="No"/></StudyEventDef>'
              '</MetaDataVersion></Study></ODM>')


def cols(mode=None, uuid="u-1", sub="acme", title="Pipeline Mode"):
    c = {COL["oc_subdomain"]: {"text": sub}, COL["study_uuid"]: {"text": uuid}, COL["study_oid"]: {"text": ""},
         COL["oc_email"]: {"text": "dm@example.com"}, COL["protocol_number"]: {"text": "T-1"}}
    if mode is not None:
        c["color_new"] = {"title": title, "text": mode}
    return c


class Deps(types.SimpleNamespace):
    """Records what the flow did; defaults describe a study that is ready for UAT."""

    def __init__(self, **over):
        super().__init__()
        self.col_ids, self.log, self.uploads, self.status, self.texts, self.calls = COL, [], {}, [], {}, []
        self.files = over.pop("files", [])
        self.study_forms = over.pop("study_forms", {"forms": [("AE.xml", fx.ODM)], "log": ["Existing study: 2 form(s) fetched"], "note": ""})
        self.env = over.pop("env", {"uuid": "env-1", "oid": "S_T(TEST)", "status": "AVAILABLE"})
        self.sites = over.pop("sites", [{"oid": "S_SITE(TEST)"}])
        self.valid = over.pop("valid", True)
        self.token_error = over.pop("token_error", None)
        self.uat = over.pop("uat", {"success": True, "errors": [], "participants_created": ["UAT-1"]})
        self.protocol = over.pop("protocol", False)

    async def append_log(self, item, msg): self.log.append(msg)
    async def set_status(self, item, key): self.status.append(key)
    async def set_text(self, item, col, value): self.texts[col] = value
    async def upload_file(self, item, col, name, data): self.uploads[col] = (name, data)
    async def download_files(self, item, col): return self.files
    async def get_token(self, sub, email):
        if self.token_error:
            raise self.token_error
        return "tok"
    async def test_environment(self, sub, uuid, email):
        if isinstance(self.env, Exception):
            raise self.env
        return self.env
    async def list_sites(self, sub, env_uuid, email): return self.sites
    async def session_valid(self, sub, email): return self.valid
    async def pause_for_auth(self, item): self.status.append("paused")
    async def fetch_study_forms(self, sub, uuid, token):
        self.calls.append(("fetch_study_forms", sub, uuid))
        return self.study_forms
    async def fetch_metadata(self, sub, oid, token): return EVENTS_XML
    async def has_protocol(self, item): return self.protocol
    async def run_uat_loader(self, item, titles):
        self.calls.append(("uat", sorted(titles)))
        return self.uat


def go(mode, deps, c=None):
    return asyncio.run(pm.run_mode("1", c or cols(), mode, deps))


def text(deps):
    return "\n".join(deps.log)


# ── the column ────────────────────────────────────────────────────────────────

def test_blank_missing_or_unknown_mode_is_a_full_build():
    assert pm.read_mode(cols()) == pm.FULL                       # the column does not exist
    assert pm.read_mode(cols("")) == pm.FULL and pm.read_mode(cols("Something else")) == pm.FULL
    assert pm.read_mode(cols("Full build")) == pm.FULL
    assert pm.read_mode({}) == pm.FULL and pm.read_mode(None) == pm.FULL


def test_modes_are_read_by_title_until_the_column_id_is_registered_then_by_id():
    assert pm.read_mode(cols("Logic + UAT")) == pm.LOGIC_UAT
    assert pm.read_mode(cols("uat only")) == pm.UAT_ONLY
    assert pm.read_mode(cols("UAT only", title="Another column")) == pm.FULL
    by_id = {"status_x": {"title": "renamed", "text": "UAT only"}}
    assert pm.read_mode(by_id, {"pipeline_mode": "status_x"}) == pm.UAT_ONLY


def test_column_specification():
    spec = pm.PIPELINE_MODE_COLUMN
    assert spec["title"] == "Pipeline Mode" and spec["labels"] == ["Full build", "Logic + UAT", "UAT only"]
    assert COL.get("pipeline_mode") == "color_mm805s38"   # created on monday 2026-10-10


def test_the_pipeline_branches_only_for_a_mode_other_than_full_build():
    src = open(os.path.join(ROOT, "pipeline.py")).read()
    at = src.index("if _pipeline_mode != _pm.FULL:")
    assert src.index("_pipeline_mode = _pm.read_mode(cols, COL)") < at < src.index("if _uat_only_requested(cols):")
    branch = src[at:src.index("_existing_uuid_early = (cols.get(COL[\"study_uuid\"]", at)]
    assert "run_mode(item_id, cols, _pipeline_mode, _pipeline_mode_deps())" in branch and "return" in branch
    for forbidden in ("create_oc_study", "publish_to_test", "call_claude", "_standards_match_step"):
        assert forbidden not in branch
    deps = src[src.index("def _pipeline_mode_deps():"):src.index("async def _logic_coverage_step(")]
    assert ".post(" not in deps and ".put(" not in deps and ".delete(" not in deps      # OpenClinica: reads only


# ── UAT only ──────────────────────────────────────────────────────────────────

def test_uat_only_reads_the_study_tests_it_as_it_is_and_runs_the_loader():
    d = Deps()
    res = go(pm.UAT_ONLY, d)
    assert res["status"] == "uat_done" and res["applied"] == 0 and res["proposed"] > 0
    assert d.calls[0] == ("fetch_study_forms", "acme", "u-1") and d.calls[-1][0] == "uat"
    assert COL["edc_build"] not in d.uploads                                  # no form is rebuilt
    name, data = d.uploads[COL["dvs_output"]]
    wb = openpyxl.load_workbook(io.BytesIO(data))
    assert name == "T-1_DVS.xlsx" and "LOGIC_COVERAGE" in wb.sheetnames
    cases = [r for r in wb["UAT_Cases"].iter_rows(min_row=4, values_only=True) if r and r[0]]
    head = [c.value for c in wb["UAT_Cases"][3]]
    assert cases and all(r[head.index("Study_Event_OID")] == "SE_ONE" for r in cases
                         if r[head.index("Form_OID")] == "F_AE")              # visits from the published study
    assert d.texts[COL["study_oid"]] == "S_T(TEST)"
    assert d.status == ["uat_loading", "all_complete"]
    assert "No protocol analysis, no standards matching, no build and no publish" in text(d)
    assert "Logic coverage (report)" in text(d)


def test_uat_only_prefers_uploaded_forms_over_the_study():
    d = Deps(files=[("AEGEN.xlsx", fx.xlsform_bytes())])
    res = go(pm.UAT_ONLY, d)
    assert res["status"] == "uat_done" and not [c for c in d.calls if c[0] == "fetch_study_forms"]
    assert "1 form(s) from the files uploaded to the item" in text(d)


def test_a_protocol_is_noted_and_not_analysed():
    d = Deps(protocol=True)
    go(pm.UAT_ONLY, d)
    assert "it is not analysed and changes no form" in text(d)


def test_uat_failure_is_reported():
    d = Deps(uat={"success": False, "errors": ["ODM import failed"]})
    assert go(pm.UAT_ONLY, d)["status"] == "uat_failed"
    assert d.status[-1] == "uat_failed" and "ODM import failed" in text(d)


# ── preconditions ─────────────────────────────────────────────────────────────

def _stopped(deps, c=None, mode=pm.UAT_ONLY):
    res = go(mode, deps, c)
    assert not [x for x in deps.calls if x[0] == "uat"]
    return res, text(deps)


def test_nothing_to_work_on():
    res, log = _stopped(Deps(), cols(uuid=""))
    assert res["status"] == "stopped" and "No forms to work on" in log and "Study UUID and OC Subdomain" in log


def test_no_access_to_the_subdomain():
    res, log = _stopped(Deps(token_error=ValueError("no session")))
    assert res["status"] == "stopped" and "cannot reach acme" in log


def test_study_not_published_to_test():
    res, log = _stopped(Deps(env=ValueError("TEST environment not found")))
    assert res["status"] == "stopped" and "not published to Test" in log
    res, log = _stopped(Deps(env={"uuid": "e", "oid": "o", "status": "DESIGN"}))
    assert res["status"] == "stopped" and "is DESIGN, not AVAILABLE" in log


def test_no_test_site_asks_instead_of_creating_one(monkeypatch):
    res, log = _stopped(Deps(sites=[]))
    assert res["status"] == "stopped" and "has no site, and this run may not create one" in log
    monkeypatch.setenv("UAT_ALLOW_SITE_CREATE", "1")
    assert go(pm.UAT_ONLY, Deps(sites=[]))["status"] == "uat_done"


def test_invalid_browser_login_pauses_for_authentication():
    d = Deps(valid=False)
    res, log = _stopped(d)
    assert res["status"] == "paused_for_auth" and d.status == ["paused"]
    assert "saved browser login is not valid" in log
    assert COL["dvs_output"] not in d.uploads                                 # nothing produced before the pause


# ── Logic + UAT ───────────────────────────────────────────────────────────────

def test_logic_and_uat_delivers_updated_forms_and_stops_until_they_are_published():
    d = Deps()                                     # the study's forms carry no logic (an ODM-style source)
    res = go(pm.LOGIC_UAT, d)
    assert res["status"] == "files_ready" and res["applied"] > 0
    assert not [c for c in d.calls if c[0] == "uat"] and d.status == ["build_complete"]
    name, data = d.uploads[COL["edc_build"]]
    members = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert name == "T-1_Updated_Forms.zip" and "T-1_Updated_Forms/forms/AE.xlsx" in members
    form = openpyxl.load_workbook(io.BytesIO(zipfile.ZipFile(io.BytesIO(data)).read("T-1_Updated_Forms/forms/AE.xlsx")))
    head = [c.value for c in form["survey"][1]]
    assert any(r[head.index("constraint")].value for r in form["survey"].iter_rows(min_row=2))
    assert COL["dvs_output"] in d.uploads
    assert "does not carry these forms yet, so UAT was not run" in text(d)
    assert "Upload and publish the forms" in text(d)


def test_logic_and_uat_goes_on_to_uat_when_nothing_is_left_to_add():
    """Forms that carry their own logic: the audit only proposes, the study already has the forms, UAT runs."""
    d = Deps(files=[("AEGEN.xlsx", fx.xlsform_bytes())])
    res = go(pm.LOGIC_UAT, d)
    assert res["applied"] == 0 and res["proposed"] > 0 and res["status"] == "uat_done"
    assert d.calls[-1][0] == "uat" and d.status[-1] == "all_complete"


def test_logic_and_uat_still_delivers_the_files_when_uat_is_not_possible():
    d = Deps(env=ValueError("TEST environment not found"), files=[("standard.xml", fx.ODM)])
    res = go(pm.LOGIC_UAT, d)
    assert res["status"] == "files_ready" and COL["edc_build"] in d.uploads
    assert "not published to Test" in text(d)


def test_logic_and_uat_without_forms_or_study_stops():
    res, log = _stopped(Deps(), cols(uuid=""), mode=pm.LOGIC_UAT)
    assert res["status"] == "stopped" and "No forms to work on" in log


# ── reading an existing study ─────────────────────────────────────────────────

def test_existing_study_forms_are_fetched_read_only_by_uuid():
    import reference_studies as rs

    class Client:
        def __init__(self): self.calls = []
        async def get(self, url, headers=None, timeout=None):
            self.calls.append(url)
            if "/study-service/api/studies" in url:
                body = [{"uuid": "U-1", "uniqueIdentifier": "STUDY1", "name": "Study 1", "currentBoardUrl": "/b/B1/s"},
                        {"uuid": "U-2", "uniqueIdentifier": "OTHER", "name": "Other", "currentBoardUrl": "/b/B2/o"}]
                return types.SimpleNamespace(status_code=200, json=lambda: body, headers={}, content=b"", raise_for_status=lambda: None)
            if "/api/boards/B1" in url:
                body = {"cards": [{"formOcoid": "F_AE", "title": "AE", "selected_form_version_ocoid": "v1", "versions": [
                    {"ocoid": "v1", "uploadedFileLinks": ["AE.xlsx"], "previewURL": "x/encrypted-versions/KEY/artifacts/a"}]}]}
                return types.SimpleNamespace(status_code=200, json=lambda: body, headers={}, content=b"", raise_for_status=lambda: None)
            if "/form-service/api/encrypted-versions/KEY/artifacts/AE.xlsx" in url:
                return types.SimpleNamespace(status_code=200, json=lambda: {}, headers={}, content=b"XLSX", raise_for_status=lambda: None)
            return types.SimpleNamespace(status_code=200, json=lambda: [], headers={}, content=b"", raise_for_status=lambda: None)
        async def post(self, *a, **k): raise AssertionError("no write and no login may be made")

    client = Client()
    got = asyncio.run(rs.fetch_study_forms("acme", "u-1", "tok", client=client))
    assert got["forms"] == [("AE.xlsx", b"XLSX")] and got["study"]["identifier"] == "STUDY1"
    assert not any("B2" in u for u in client.calls)
    assert all(line.startswith("Existing study:") for line in got["log"])
    missing = asyncio.run(rs.fetch_study_forms("acme", "nope", "tok", client=Client()))
    assert missing["forms"] == [] and "not found" in missing["note"]
    assert asyncio.run(rs.fetch_study_forms("acme", "u-1", "", client=Client()))["forms"] == []
