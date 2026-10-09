"""SPEC_INPUT_TRIM=1: the AI calls that send study data (field lists) send the same facts in fewer tokens.

These calls already send compact field lines, not the specification JSON. What is left to trim without taking a
fact away from a step:
  - display markup in question labels (HTML tags, runs of whitespace): a label that is only markup becomes empty;
    the length cut is applied after the clean-up, so a label never carries less text than before;
  - the form heading repeated on every field line of the concept-tagging request: sent once per form;
  - the review flags of the pricing summary request: that step counts the flags per kind and names the most
    important ones, so a kind with many flags is sent as its count and its first FLAGS_KEPT entries.
Nothing a step can use is dropped: every field, type, choice and existing check is still listed.
Default OFF: the requests are byte-identical to before."""
import os
import re

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def enabled():
    return os.environ.get("SPEC_INPUT_TRIM", "0") == "1"


def clean(text):
    return _WS.sub(" ", _TAG.sub(" ", str(text or ""))).strip()


def label(text, limit=None):
    """A label as a request shows it: unchanged (cut to limit) by default; with the switch on, without markup."""
    t = clean(text) if enabled() else str(text or "")
    return t if limit is None else t[:limit]


def text(value):
    """str(value) by default (exactly as the builders wrote it); without markup with the switch on."""
    return clean(value) if enabled() else str(value)


FLAGS_KEPT = 25


def flags(review_flags, keep=FLAGS_KEPT):
    """The review flags as the pricing summary request sends them: unchanged by default; with the switch on a
    list longer than `keep` becomes {"count": n, "first": [...keep entries], "not_listed": n - keep}."""
    if not enabled() or not isinstance(review_flags, dict):
        return review_flags
    out = {}
    for k, v in review_flags.items():
        if isinstance(v, list) and len(v) > keep:
            out[k] = {"count": len(v), "first": v[:keep], "not_listed": len(v) - keep}
        else:
            out[k] = v
    return out


def log_size(step, before, after):
    """Input size of one request before and after the trim (characters; about 4 per token)."""
    if before:
        print(f"[spec-trim] {step}: {before:,} -> {after:,} characters "
              f"({round(100 * (before - after) / before)}% less, about {(before - after) // 4:,} tokens)", flush=True)


def untrimmed(build, *args, **kwargs):
    """The request the builder makes with the switch off (for the size log). None when it fails."""
    old = os.environ.get("SPEC_INPUT_TRIM")
    os.environ["SPEC_INPUT_TRIM"] = "0"
    try:
        return build(*args, **kwargs)
    except Exception:
        return None
    finally:
        if old is None:
            os.environ.pop("SPEC_INPUT_TRIM", None)
        else:
            os.environ["SPEC_INPUT_TRIM"] = old


def compare(step, build, req, *args, **kwargs):
    """Log the size of req against the untrimmed request of the same builder. Never raises."""
    try:
        if not enabled() or req is None:
            return
        base = untrimmed(build, *args, **kwargs)
        if base is not None:
            log_size(step, len(base[0]) + len(base[1]), len(req[0]) + len(req[1]))
    except Exception:
        pass
