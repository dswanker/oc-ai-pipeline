"""The requests the cost-switch tests compare (tests/test_cost_switches.py). Each case is built by the code under
test from one synthetic study; `python3 tests/standards/cost_cases.py <repo root> <out.json>` writes the cases as
built by the code in <repo root> (used once, on the commit before the switches, to record cost_switch_snapshots.json).
Synthetic data only."""
import asyncio
import contextlib
import copy
import io
import json
import os
import sys

PROTOCOL = ("5.1 Inclusion criteria: participants must be 18 years of age or older.\n"
            "8.3 Adverse events. All adverse events will be recorded in the eCRF from consent.\n"
            "For each adverse event the investigator must record whether the event led to hospitalisation.\n"
            "9.1 Vital signs. Systolic blood pressure will be recorded in the eCRF at every visit.\n"
            "9.2 Widget count. The number of widgets will be recorded in the eCRF at screening.\n")


def _form(fid, title, domain, rows, choices=(), visits=("SE_SCREENING",)):
    return {"form_id": fid, "form_title": title, "form_category": "CDASH_CLINICAL", "cdash_domain": domain,
            "visits_assigned": list(visits), "has_repeating_group": False, "is_epro": False, "reuse_count": 1,
            "library_match": {"status": "CDASH_DEFAULT", "source_type": "standard"},
            "settings": {"form_title": title, "form_id": fid, "version": "1"},
            "choices": [dict(zip(("list_name", "label", "name"), c)) for c in choices],
            "survey": [{"type": t, "name": n, "label": l, "bind__oc_itemgroup": fid, **x} for t, n, l, x in rows],
            "cross_form_dependencies": []}


def study():
    """Two analysed forms and one custom form; labels with display markup, a choice list, an existing check."""
    return {
        "study_meta": {"protocol_number": "SYN-001", "study_title": "Synthetic study", "phase": "2"},
        "timepoint_csv": {"rows": [{"event": "SE_SCREENING", "timepoint": "Screening"}]},
        "schedule_of_events": {"form_placements": []},
        "review_flags": {},
        "forms": [
            _form("AE", "Adverse Events", "AE", [
                ("text", "AETERM", "<span style=\"color:white\"> </span>", {}),
                ("date", "AESTDAT", "Start   date", {"constraint": ". <= today()"}),
                ("date", "AEENDAT", "<b>End date</b>", {}),
                ("select_one NY", "AEHOSP", "Did the event lead to hospitalisation?", {}),
                ("text", "AEOTH", "If other, specify", {})],
                choices=[("NY", "<i>Yes</i>", "Y"), ("NY", "No", "N")], visits=("SE_COMMON",)),
            _form("VS", "Vital Signs", "VS", [
                ("date", "VSDAT", "Date of measurement", {}),
                ("integer", "SYSBP", "Systolic blood pressure (mmHg)", {})]),
            _form("WID", "Widget Count", None, [
                ("integer", "WIDN", "Number of <u>widgets</u>", {}),
                ("text", "WIDCOM", "Comments", {})]),
        ],
    }


def standard_ae():
    import standards_match as sm
    survey = [{"type": "text", "name": "AETERM", "label": "Adverse event term", "bind__oc_itemgroup": "AEGEN"},
              {"type": "date", "name": "AESTDAT", "label": "<span>Start date</span>", "bind__oc_itemgroup": "AEGEN"},
              {"type": "date", "name": "AEENDAT", "label": "End date", "bind__oc_itemgroup": "AEGEN"},
              {"type": "select_one SEV", "name": "AESEV", "label": "Severity", "bind__oc_itemgroup": "AEGEN"}]
    m = sm._model("AEGEN", "AE General", survey, [{"list_name": "SEV", "label": "<b>Mild</b>", "name": "1"},
                                                    {"list_name": "SEV", "label": "Moderate", "name": "2"}],
                  {"form_title": "AE General", "form_id": "AEGEN"}, [], sm.SRC_XLSFORM, "AEGEN.xlsx", "xlsform")
    m["domain"] = "AE"
    return m


def matched():
    import standards_match as sm
    sm._CDASH["v"] = (None, {}, {}, set())
    with contextlib.redirect_stdout(io.StringIO()):
        return sm.apply(study(), {"forms": [standard_ae()], "files": [], "fingerprint": "f1"})


class _Stream:
    def __init__(self, sink, kwargs):
        sink.append(kwargs)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get_final_message(self):
        class _B:
            text = "{}"

        class _M:
            content = [_B()]
            usage = None
        return _M()


class FakeClient:
    """Stands in for anthropic.AsyncAnthropic: records what every request would send."""
    calls = []

    def __init__(self, *a, **k):
        outer = self

        class _Messages:
            def stream(self, **kwargs):
                outer.calls.append(("messages", kwargs))
                return _Stream([], kwargs)

        class _Beta:
            messages = _Messages()

            def __init__(self):
                class _BM:
                    def stream(self, **kwargs):
                        outer.calls.append(("beta", kwargs))
                        return _Stream([], kwargs)
                self.messages = _BM()
        self.messages = _Messages()
        self.beta = _Beta()


def client_requests():
    """What call_claude sends for the shapes of call the pipeline makes."""
    import claude_client as cc
    real = cc.anthropic.AsyncAnthropic
    FakeClient.calls = []
    cc.anthropic.AsyncAnthropic = FakeClient
    os.environ.setdefault("ANTHROPIC_API_KEY", "test")
    pdf = b"%PDF-1.4 synthetic protocol bytes"
    try:
        async def run():
            with contextlib.redirect_stdout(io.StringIO()):
                await cc.call_claude("QUICK PROMPT", pdf_bytes=pdf, cache_prompt=False, max_tokens=2000)
                await cc.call_claude("ANALYSIS PROMPT", pdf_bytes=pdf, extra_text="conventions", max_tokens=96000,
                                     extended_output=True, images=[("image/png", "aW1n")])
                await cc.call_claude("CHECK PROMPT", pdf_bytes=pdf, extra_text="FORMS", max_tokens=16000, cache_prompt=False)
                await cc.call_claude("SPEC PROMPT", extra_text="FIELDS", max_tokens=8000, cache_prompt=False)
                await cc.call_claude("PRICING PROMPT", extra_text="Study Specification JSON:\n{}", max_tokens=64000)
        asyncio.run(run())
    finally:
        cc.anthropic.AsyncAnthropic = real
    return [[kind, kwargs] for kind, kwargs in FakeClient.calls]


def builder_requests():
    """(prompt, extra_text) of every request builder a switch touches, on the synthetic study."""
    import ai_edit_checks, ai_standard_logic, sdv_proposals, standards_match as sm, protocol_forms as pf, protocol_basis as pb
    spec = matched()
    out = {"ai_edit_checks": ai_edit_checks.build_request(copy.deepcopy(spec)),
           "ai_standard_logic": ai_standard_logic.build_request(copy.deepcopy(spec), PROTOCOL),
           "sdv_endpoints_pdf": sdv_proposals.build_request(copy.deepcopy(spec), PROTOCOL, with_text=False),
           "sdv_endpoints_text": sdv_proposals.build_request(copy.deepcopy(spec), PROTOCOL),
           "protocol_fields": sm.build_add_request(copy.deepcopy(spec), PROTOCOL),
           "completeness": pf.build_request(copy.deepcopy(spec), PROTOCOL, with_text=False),
           "basis": pb.build_request(copy.deepcopy(spec), PROTOCOL, with_text=False)}
    try:
        import cdisc_concepts
        with contextlib.redirect_stdout(io.StringIO()):
            out["concept_tagging"] = cdisc_concepts.build_request(copy.deepcopy(spec), None, qrs=False)
    except Exception as e:      # the CDISC layer is not installed here: the case is left out on both sides
        out["concept_tagging"] = f"unavailable: {type(e).__name__}"
    return {k: (list(v) if isinstance(v, tuple) else v) for k, v in out.items()}


def all_cases():
    return {"client": client_requests(), "builders": builder_requests()}


if __name__ == "__main__":
    sys.path.insert(0, sys.argv[1])
    os.chdir(sys.argv[1])
    json.dump(all_cases(), open(sys.argv[2], "w"), indent=1, sort_keys=True)
    print("written", sys.argv[2])
