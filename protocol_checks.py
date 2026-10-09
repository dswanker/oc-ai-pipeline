"""PROTOCOL_CHECKS_MERGED=1: the three checks that read the protocol after matching are ONE AI call.

Today three calls each send the whole protocol: the protocol-specified fields of matched standard forms
(standards_match), the completeness checklist (protocol_forms) and the protocol basis of every form
(protocol_basis). Merged, the protocol is sent once with the three tasks' instructions (each task's own wording,
unchanged) and inputs, and the model returns one JSON object with one section per task. Each section is then handed,
as that task's answer, to the validator the task has always had.

Order: the call is made after the form conventions, on the forms as they are then. The basis check needs the FINAL
forms, and the completeness check may still add a form. So the basis task also judges the customer standard forms
not used yet (what the completeness check takes first when it adds a form); a form added from elsewhere (CDASHIG,
CRF standards) is judged by a small follow-up basis call for just that form.

A task that does not need to run is left out; with fewer than two tasks there is nothing to merge and the tasks run
as before. If the merged call fails, or a section is missing from the answer, that task runs as its own call.
Default OFF."""
import copy
import json
import os
import re

TASKS = (("protocol_fields", "PROTOCOL-SPECIFIED FIELDS"), ("completeness", "COMPLETENESS CHECKLIST"),
         ("basis", "PROTOCOL BASIS OF EVERY FORM"))
MAX_POSSIBLE_FORMS = 20
MAX_TOKENS = 32000

HEAD = """You carry out separate TASKS on the same clinical trial protocol. Each task has its own INSTRUCTIONS and its own
INPUT below, under its own heading. Do every task exactly as its instructions say and independently of the other
tasks: an instruction, a rule or an input of one task never applies to another.

Where a task's instructions tell you to answer or return ONLY JSON, that JSON is the task's section of one combined
answer. Answer ONLY with one JSON object, no prose, no code fences, with one key per task:
__SHAPE__
"""


def enabled():
    return os.environ.get("PROTOCOL_CHECKS_MERGED", "0") == "1"


def _log(msg):
    print(f"[protocol-checks] {msg}", flush=True)


def build_request(tasks, protocol_text=None):
    """tasks: {task key: (prompt, extra_text)} for the tasks that need to run (two or three of TASKS).
    protocol_text: given once here when the protocol is not sent as a PDF (the tasks' own inputs then carry none).
    Returns (prompt, extra_text)."""
    names = [(k, title) for k, title in TASKS if tasks.get(k)]
    shape = "{" + ", ".join(f'"{k}": <the JSON of TASK {i}>' for i, (k, _t) in enumerate(names, 1)) + "}"
    prompt = HEAD.replace("__SHAPE__", shape)
    extra = []
    for i, (k, title) in enumerate(names, 1):
        prompt += f"\n\n=== TASK {i}: {title}: INSTRUCTIONS ===\n{tasks[k][0].strip()}\n"
        extra.append(f"=== TASK {i}: {title}: INPUT ===\n{tasks[k][1].strip()}")
    text = "\n\n".join(extra)
    if str(protocol_text or "").strip():
        text += "\n\n=== PROTOCOL TEXT (for every task) ===\n" + str(protocol_text)
    return prompt, text


def split_response(text, keys):
    """{task key: that task's answer as JSON text, or None when the section is missing or not an object}."""
    out = {k: None for k in keys}
    try:
        t = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(text or "").strip())
        try:
            data = json.loads(t)
        except Exception:
            m = re.search(r"\{.*\}", t, re.S)
            data = json.loads(m.group(0)) if m else None
        if isinstance(data, dict):
            for k in keys:
                if isinstance(data.get(k), dict):
                    out[k] = json.dumps(data[k], ensure_ascii=False)
    except Exception as e:
        _log(f"merged answer not readable ({type(e).__name__}); every task runs as its own call")
    return out


def with_possible_forms(spec, sources, limit=MAX_POSSIBLE_FORMS):
    """A copy of the spec's form list plus the customer standard forms not used yet, for the basis task: when the
    completeness check adds one of them, its basis has already been judged. The spec itself is not changed."""
    tmp = dict(spec)
    forms = [f for f in spec.get("forms") or []]
    have = {str(f.get("form_id")) for f in forms if isinstance(f, dict)}
    used = {str((f.get("customer_standard") or {}).get("form_oid")) for f in forms if isinstance(f, dict)
            and f.get("customer_standard")}
    added = []
    for s in (sources or {}).get("forms") or []:
        oid = str(s.get("form_oid"))
        if oid in have or oid in used or len(added) >= limit:
            continue
        added.append({"form_id": oid, "form_title": s.get("title") or oid, "survey": copy.deepcopy(s.get("survey") or []),
                      "choices": [], "customer_standard": {"form_oid": oid}})
    tmp["forms"] = forms + added
    return tmp, [f["form_id"] for f in added]


def answered_forms(basis_text):
    """Form ids a basis answer covers."""
    try:
        data = json.loads(basis_text)
        return {str(f.get("form_id")) for f in data.get("forms") or [] if isinstance(f, dict) and f.get("form_id")}
    except Exception:
        return set()


def merge_basis(first, second):
    """Two basis answers (JSON texts) as one: the forms of both, the first answer winning for a form in both."""
    try:
        a = json.loads(first)
        b = json.loads(second)
        have = {str(f.get("form_id")) for f in a.get("forms") or [] if isinstance(f, dict)}
        a["forms"] = list(a.get("forms") or []) + [f for f in b.get("forms") or [] if isinstance(f, dict)
                                                  and str(f.get("form_id")) not in have]
        return json.dumps(a, ensure_ascii=False)
    except Exception:
        return first
