"""Referenced OC studies as customer standard sources (reference_studies.py). No network: httpx.MockTransport."""
import asyncio, contextlib, io, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import httpx
import pytest

import monday_client
import reference_studies as rs
import standards_match as sm
from standards import fixtures as fx

KEY_A, KEY_B = "SECRETKEYAAAA1111", "SECRETKEYBBBB2222"
STUDIES = [
    {"uuid": "u1", "uniqueIdentifier": "ALPHA-01", "name": "Alpha study one", "currentBoardUrl": "/b/BOARDalpha01/alpha-study-one"},
    {"uuid": "u2", "uniqueIdentifier": "ALPHA-01X", "name": "Alpha extension", "currentBoardUrl": "/b/BOARDalpha01x/x"},
    {"uuid": "u3", "uniqueIdentifier": "BETA-7", "name": "Beta programme", "oid": "S_BETA7", "currentBoardUrl": "/b/BOARDbeta7/beta"},
    {"uuid": "u4", "uniqueIdentifier": "GAMMA", "name": "No board yet", "currentBoardUrl": None},
]


def _preview(key, file):
    return f"https://t.build.openclinica.io/form-service/api/encrypted-versions/{key}/artifacts/{file}?x=1"


BOARD = {"lists": [{"_id": "l1"}], "cards": [
    # the same form on two events: one card has no versions, the other has the selected one
    {"formOcoid": "AEGEN", "title": "AE General", "archived": False, "versions": []},
    {"formOcoid": "AEGEN", "title": "AE General", "archived": False, "selected_form_version_ocoid": "AEGEN_2", "versions": [
        {"id": 1, "ocoid": "AEGEN_1", "archived": False, "uploadedFileLinks": ["OLD.xlsx"], "previewURL": _preview("OLDKEY", "OLD.xlsx")},
        {"id": 2, "ocoid": "AEGEN_2", "archived": False, "uploadedFileLinks": ["AEGEN.xlsx"], "previewURL": _preview(KEY_A, "AEGEN.xlsx")}]},
    # nothing selected: the latest non-archived version
    {"formOcoid": "VS", "title": "Vital Signs", "archived": False, "selected_form_version_ocoid": "VS_9", "versions": [
        {"id": 5, "ocoid": "VS_1", "archived": False, "uploadedFileLinks": ["VS.xlsx"], "previewURL": _preview(KEY_B, "VS.xlsx")},
        {"id": 7, "ocoid": "VS_3", "archived": True, "uploadedFileLinks": ["VS3.xlsx"], "previewURL": _preview("ARCH", "VS3.xlsx")},
        {"id": 3, "ocoid": "VS_0", "archived": False, "uploadedFileLinks": ["VS0.xlsx"], "previewURL": _preview("OLDER", "VS0.xlsx")}]},
    {"formOcoid": "NOFILE", "title": "Never uploaded", "archived": False, "versions": []},
    {"formOcoid": "GONE", "title": "Archived form", "archived": True, "selected_form_version_ocoid": "G_1", "versions": [
        {"id": 1, "ocoid": "G_1", "archived": False, "uploadedFileLinks": ["G.xlsx"], "previewURL": _preview("GKEY", "G.xlsx")}]},
]}


class Server:
    def __init__(self, fail=(), studies=STUDIES, total=None, flaky=0):
        self.calls, self.fail, self.studies, self.total, self.flaky = [], set(fail), studies, total, flaky

    def __call__(self, request):
        url = str(request.url)
        self.calls.append((request.method, url))
        assert request.method == "GET" or url.endswith("/user-service/api/oauth/token"), "read-only apart from login"
        if url.endswith("/oauth/token"):
            return httpx.Response(401) if "token" in self.fail else httpx.Response(200, text=" TOKEN123 \n")
        assert request.headers["Authorization"] == "Bearer TOKEN123"
        if "/study-service/api/studies" in url:
            page = int(request.url.params["page"])
            size = int(request.url.params["size"])
            return httpx.Response(200, json=self.studies[page * size:(page + 1) * size],
                                  headers={"X-Total-Count": str(self.total or len(self.studies))})
        if "/api/boards/" in url:
            assert request.url.host == "t.design.openclinica.io"
            if "board" in self.fail:
                return httpx.Response(500)
            return httpx.Response(200, json=BOARD)
        if "/encrypted-versions/" in url:
            if self.flaky:
                self.flaky -= 1
                return httpx.Response(503)
            if KEY_B in url and "vs" in self.fail:
                return httpx.Response(404)
            data = fx.xlsform_bytes() if KEY_A in url else fx.xlsform_bytes(fx.VS_SURVEY, [], fx.VS_SETTINGS)
            return httpx.Response(200, content=data)
        return httpx.Response(404)


def _fetch(text, server=None, sub="t"):
    server = server or Server()

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as c:
            return await rs.fetch(sub, text, username="svc", password="pw", client=c)
    return asyncio.run(go()), server


def test_column_is_registered():
    assert monday_client.COL["reference_studies"] == "text_mm7yy8kw"


def test_parse_up_to_five_and_log_the_rest():
    refs, log = rs.parse_references("A, B ,C;D\nE, F, G, A")
    assert refs == ["A", "B", "C", "D", "E"] and "only the first 5" in log[0] and "F, G" in log[0]
    assert rs.parse_references("") == ([], []) and rs.parse_references(None) == ([], [])


def test_resolution_exact_contains_ambiguous_not_found():
    r = lambda x: rs.resolve_study(x, STUDIES)
    assert r("alpha-01")[0]["uuid"] == "u1"                  # exact beats the contains-match on ALPHA-01X
    assert r("Beta programme")[0]["uuid"] == "u3"            # by name
    assert r("s_beta7")[0]["uuid"] == "u3"                   # by OID when the list carries one
    assert r("extension")[0]["uuid"] == "u2"                 # unique contains-match
    assert r("alpha")[0] is None and "ambiguous" in r("alpha")[1]
    assert r("zzz") == (None, "not found") and r("  ")[0] is None


def test_board_id_is_the_segment_after_b_not_the_slug():
    assert rs.board_id({"currentBoardUrl": "/b/wQyCTnJFKjyGMQ9d9/some-slug"}) == "wQyCTnJFKjyGMQ9d9"
    assert rs.board_id({"currentBoardUrl": "https://x.design.openclinica.io/b/AbC123/s"}) == "AbC123"
    assert rs.board_id({"currentBoardUrl": None}) is None and rs.board_id({}) is None


def test_board_parsing_and_version_selection():
    forms, missing = rs.board_forms(BOARD)
    by = {f["form"]: f for f in forms}
    assert set(by) == {"AEGEN", "VS"} and missing == ["NOFILE"]         # archived card ignored
    assert (by["AEGEN"]["file"], by["AEGEN"]["key"], by["AEGEN"]["version"]) == ("AEGEN.xlsx", KEY_A, "AEGEN_2")
    assert (by["VS"]["file"], by["VS"]["key"], by["VS"]["version"]) == ("VS.xlsx", KEY_B, "VS_1")  # latest non-archived
    assert rs.select_version({"versions": []}) is None
    assert rs.select_version({"versions": [{"id": 1, "archived": True}]}) is None


def test_fetch_feeds_standards_matching_as_a_referenced_study():
    res, server = _fetch("ALPHA-01")
    (ref,) = res["referenced"]
    assert ref["label"] == "referenced study ALPHA-01" and sorted(n for n, _ in ref["forms"]) == ["AEGEN.xlsx", "VS.xlsx"]
    assert "no uploaded form: NOFILE" in ref["note"]
    assert res["timing"]["studies_listed"] == 4 and "referenced study ALPHA-01" in res["timing"]
    assert all(m == "GET" or u.endswith("/oauth/token") for m, u in server.calls)
    assert sum("/api/boards/BOARDalpha01" in u for _m, u in server.calls) == 1
    with contextlib.redirect_stdout(io.StringIO()):
        src = sm.load_sources([("standard.xml", fx.ODM)], res["referenced"])
        out = sm.apply(fx.spec(), src)
    got = {m["protocol_form"]: m for m in sm.state(out)["matched"]}
    assert got["AE"]["source"] == "referenced study ALPHA-01" and got["AE"]["standard_form"] == "AEGEN"   # beats the ODM
    assert got["VS"]["source"] == "referenced study ALPHA-01"
    form = next(f for f in out["forms"] if f["form_id"] == "AEGEN")
    assert form["library_match"]["source"] == "referenced study ALPHA-01"
    assert [sm.core_row(r) for r in form["survey"]] == sm.parse_xlsform(fx.xlsform_bytes(), "AEGEN.xlsx")["survey"]
    # an uploaded XLSForm still has priority over the referenced study
    with contextlib.redirect_stdout(io.StringIO()):
        up = sm.load_sources([("AESTD.xlsx", fx.xlsform_bytes(settings={**fx.AE_SETTINGS, "form_id": "AESTD"}))], res["referenced"])
        out2 = sm.apply(fx.spec(), up)
    assert {m["protocol_form"]: m["source"] for m in sm.state(out2)["matched"]}["AE"] == "uploaded XLSForm"


def test_artifact_key_token_and_urls_are_never_logged(capsys):
    res, _ = _fetch("ALPHA-01, zzz, alpha", Server(fail={"vs"}))
    text = "\n".join(res["log"]) + repr(res["timing"]) + repr([(r["label"], r["note"]) for r in res["referenced"]])
    out = capsys.readouterr()
    for secret in (KEY_A, KEY_B, "TOKEN123", "encrypted-versions", "https://", "pw"):
        assert secret not in text and secret not in out.out and secret not in out.err
    assert "could not be fetched: VS" in text and "'zzz' not found on t" in text and "'alpha' ambiguous" in text


def test_failures_never_raise_and_skip_only_what_failed():
    res, _ = _fetch("ALPHA-01", Server(fail={"token"}))
    assert res["referenced"] == [] and "could not be read" in res["log"][0]
    res, _ = _fetch("ALPHA-01, BETA-7", Server(fail={"board"}))
    assert [len(r["forms"]) for r in res["referenced"]] == [0, 0] and all("design board" in r["note"] for r in res["referenced"])
    res, _ = _fetch("GAMMA")
    assert res["referenced"][0]["forms"] == [] and "no design board" in res["log"][0]
    res, _ = _fetch("ALPHA-01", Server(flaky=1))                         # one retry per request
    assert len(res["referenced"][0]["forms"]) == 2
    res, _ = _fetch("ALPHA-01", sub="bad host/../x")
    assert res["referenced"] == [] and "subdomain" in res["log"][0]
    res, server = _fetch("")
    assert res == {"referenced": [], "log": [], "timing": {}} and server.calls == []
    with contextlib.redirect_stdout(io.StringIO()):
        src = sm.load_sources([], _fetch("ALPHA-01", Server(fail={"board"}))[0]["referenced"])
    assert src["forms"] == [] and src["files"][0]["usable"] is False and "NOT usable" in sm.files_log(src)[0]


def test_same_study_twice_more_than_five_and_paging():
    res, server = _fetch("ALPHA-01, Alpha study one, BETA-7, a, b, c, d")
    # first five used, in listed order; the second entry names the first study again; "a" is ambiguous, "b" resolves
    labels = [r["label"] for r in res["referenced"]]
    assert labels[:2] == ["referenced study ALPHA-01", "referenced study BETA-7"]
    assert not any("'c'" in l or "'d'" in l for l in res["log"][1:])           # beyond the first five: never looked up
    assert any("only the first 5" in l for l in res["log"]) and any("same study" in l for l in res["log"])
    many = [{"uuid": f"m{i}", "uniqueIdentifier": f"M-{i:04d}", "name": f"m {i}", "currentBoardUrl": None} for i in range(1500)]
    res, server = _fetch("M-1499", Server(studies=many))
    assert res["timing"]["studies_listed"] == 1500
    assert sum("/study-service/api/studies" in u for _m, u in server.calls) == 2


def test_kill_switch(monkeypatch):
    monkeypatch.setenv("REFERENCE_STUDIES", "0")
    res, server = _fetch("ALPHA-01")
    assert res["referenced"] == [] and server.calls == []


def _fetch_own(text, own_ids, server=None):
    server = server or Server()

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as c:
            return await rs.fetch("t", text, username="svc", password="pw", client=c, own_ids=own_ids)
    return asyncio.run(go()), server


def test_a_study_can_never_reference_itself():
    alpha = next(s for s in STUDIES if s.get("uniqueIdentifier") == "ALPHA-01")
    # typed as the item's own identifier (any case / dashes / spaces): skipped before any lookup
    res, server = _fetch_own("alpha01", ["", "ALPHA-01", ""])
    assert res["referenced"] == [] and server.calls == []
    assert any("can never reference itself" in line for line in res["log"])
    # typed differently but resolves to the item's own study (matched by its UUID): skipped after lookup
    res, _ = _fetch_own("ALPHA-01", [alpha["uuid"], "", ""])
    assert res["referenced"] == [] and any("resolves to this item's own study" in line for line in res["log"])
    # the item's study OID identifies it too
    res, _ = _fetch_own("ALPHA-01", ["", "", "S_ALPHA01(TEST)"])
    assert res["referenced"] == []
    # another study is still fetched when the own study is also listed
    res, _ = _fetch_own("ALPHA-01, BETA-7", ["", "ALPHA-01", ""])
    assert [r["label"] for r in res["referenced"]] and all("ALPHA" not in r["label"] for r in res["referenced"])


def test_is_own_study_rules():
    assert rs.is_own_study("PrTK05", None, ["", "PRTK05", ""])
    assert rs.is_own_study("prtk-05", None, ["", "PrTK05", ""])
    assert rs.is_own_study("X", {"uuid": "u-1", "uniqueIdentifier": "PrTK05"}, ["u-1", "", ""])
    assert not rs.is_own_study("CRS135", {"uuid": "u-2", "uniqueIdentifier": "CRS135"}, ["u-1", "PrTK05", "S_PRTK05(TEST)"])
    assert not rs.is_own_study("PrTK05", None, ["", "", ""])
