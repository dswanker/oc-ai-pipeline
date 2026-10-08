"""core_validation.py: run CDISC CORE conformance rules (cdisc-rules-engine, MIT) on pipeline inputs/outputs.

The engine runs as an external command, fully offline from its shipped cache (resources/cache: rules.pkl,
usdm-4-0-schema.pkl, CT packages). No CDISC Library API key is used. It is not a pipeline dependency: it needs its
own Python (3.12 per its packaging) and is used only when configured:

  CORE_ENGINE_CMD   command that runs the engine CLI, e.g. "/opt/core/venv/bin/python /opt/core/engine/core.py"
  CORE_ENGINE_DIR   working directory for the command (default: the folder of the last path in CORE_ENGINE_CMD)
  CORE_CACHE_DIR    cache path relative to CORE_ENGINE_DIR, or absolute (default: resources/cache)
  CORE_VALIDATE=0   kill switch

Findings are reported (monday log); they never block a build. Any failure returns None.
"""
import json, os, shlex, subprocess, tempfile

TIMEOUT = 300
TOP = 6


def configured():
    return os.environ.get("CORE_VALIDATE", "1") != "0" and bool(os.environ.get("CORE_ENGINE_CMD", "").strip())


def _engine():
    cmd = shlex.split(os.environ["CORE_ENGINE_CMD"])
    cwd = os.environ.get("CORE_ENGINE_DIR") or next(
        (os.path.dirname(os.path.abspath(p)) for p in reversed(cmd) if os.path.isfile(p)), os.getcwd())
    return cmd, cwd, os.environ.get("CORE_CACHE_DIR") or "resources/cache"


def summarize(report):
    """Counts and the most frequent findings of a CORE JSON report."""
    rules = report.get("Rules_Report") or []
    status = {}
    for r in rules:
        s = str(r.get("status") or "").upper()
        status[s] = status.get(s, 0) + 1
    by_rule = {}
    for i in report.get("Issue_Summary") or []:
        k = (i.get("core_id"), i.get("cdisc_rule_id"))
        cur = by_rule.setdefault(k, {"core_id": i.get("core_id"), "cdisc_rule_id": i.get("cdisc_rule_id"), "issues": 0,
                                     "entities": [], "message": str(i.get("message") or "")})
        cur["issues"] += int(i.get("issues") or 0)
        if i.get("entity") and i["entity"] not in cur["entities"]:
            cur["entities"].append(i["entity"])
    top = sorted(by_rule.values(), key=lambda x: (-x["issues"], str(x["core_id"])))
    cd = report.get("Conformance_Details") or {}
    return {"engine_version": cd.get("CORE_Engine_Version"), "standard": f"{cd.get('Standard')} {cd.get('Version')}".strip(),
            "rules_run": len(rules), "rules_passed": status.get("SUCCESS", 0),
            "rules_with_issues": status.get("ISSUE REPORTED", 0), "rules_skipped": status.get("SKIPPED", 0),
            "issues": sum(x["issues"] for x in top), "top": top[:TOP]}


def log_line(summary, what="USDM JSON (input)"):
    line = (f"CORE validation of the {what} ({summary['standard']}, engine {summary['engine_version']}, offline): "
            f"{summary['rules_run']} rules run, {summary['rules_with_issues']} with findings "
            f"({summary['issues']} findings), {summary['rules_skipped']} skipped. Findings do not block the build.")
    if summary["top"]:
        line += " Most frequent: " + "; ".join(
            f"{t['core_id']}/{t['cdisc_rule_id']} x{t['issues']} ({', '.join(t['entities'][:2])}): {t['message'][:90]}"
            for t in summary["top"])
    return line


def run_usdm(usdm_bytes, version="4-0", timeout=TIMEOUT):
    """Run the USDM CORE rules on a USDM JSON. Returns the summary, or None when the engine is not configured or
    the run fails for any reason."""
    if not configured():
        return None
    try:
        cmd, cwd, cache = _engine()
        with tempfile.TemporaryDirectory() as tmp:
            src, out = os.path.join(tmp, "usdm.json"), os.path.join(tmp, "core_report")
            with open(src, "wb") as fh:
                fh.write(usdm_bytes if isinstance(usdm_bytes, (bytes, bytearray)) else json.dumps(usdm_bytes).encode())
            env = {k: v for k, v in os.environ.items() if k != "CDISC_LIBRARY_API_KEY"}  # offline: cache only
            proc = subprocess.run(cmd + ["validate", "-s", "usdm", "-v", version, "-dp", src, "-ca", cache,
                                         "-o", out, "-of", "json"],
                                  cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
            path = out + ".json"
            if proc.returncode != 0 or not os.path.exists(path):
                print(f"[core] engine run failed (exit {proc.returncode}): {(proc.stderr or proc.stdout)[-300:]}", flush=True)
                return None
            with open(path) as fh:
                return summarize(json.load(fh))
    except Exception as e:
        print(f"[core] validation skipped: {e}", flush=True)
        return None
