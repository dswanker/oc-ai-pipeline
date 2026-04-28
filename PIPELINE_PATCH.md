# Pipeline patch — wire trainer retrieval into EDC structure step

This document describes the small patch to add to `~/oc-ai-pipeline/pipeline.py`
that calls the trainer's `/retrieve` endpoint and injects the formatted
examples block into the existing `extra_text` parameter of the EDC
structure `call_claude` invocation.

## Where the patch goes

In `pipeline.py`, find this block (currently around lines 991–1002):

```python
extra_parts = []
if crf_pdf:
    extra_parts.append("Customer CRF Library (PDF) attached — use as Priority 1.")
if oc_zip:
    extra_parts.append("Customer OC4 XLSForm Standards (ZIP) attached — use as Priority 2.")

print("Step 1: Claude extracting Study Spec JSON...", flush=True)
struct_text = await call_claude(
    EDC_STRUCTURE_PROMPT,
    pdf_bytes  = protocol_pdf or None,
    extra_text = "\n".join(extra_parts) if extra_parts else None,
)
```

Insert the new code AFTER the two existing `if crf_pdf:` / `if oc_zip:`
lines, BEFORE the `print("Step 1: ...")` line. After the patch the
section looks like:

```python
extra_parts = []
if crf_pdf:
    extra_parts.append("Customer CRF Library (PDF) attached — use as Priority 1.")
if oc_zip:
    extra_parts.append("Customer OC4 XLSForm Standards (ZIP) attached — use as Priority 2.")

# ─── Trainer retrieval (B): fetch similar past pairs as few-shot examples ──
# Best-effort — any failure leaves extra_parts unchanged and the
# pipeline continues without examples.
try:
    print("Step 0: Trainer retrieval — quick protocol analysis...", flush=True)
    quick_analysis = await run_protocol_analysis_quick(protocol_pdf or b"")
    if quick_analysis:
        print(f"Step 0: Trainer retrieval — fetching examples (k={TRAINER_K})...",
              flush=True)
        matches = await retrieve_examples(
            quick_analysis, k=TRAINER_K, reserve_same_sponsor=True,
        )
        if matches:
            block = format_examples_block(
                matches,
                sponsor_hint=quick_analysis.get("sponsor"),
                reserve_same_sponsor=True,
            )
            if block:
                extra_parts.append(block)
                await append_log(item_id,
                    f"Trainer retrieval: {len(matches)} similar past "
                    f"build(s) injected as examples.")
except Exception as _trainer_exc:  # noqa: BLE001
    print(f"Trainer retrieval failed: {_trainer_exc} — continuing without examples",
          flush=True)

print("Step 1: Claude extracting Study Spec JSON...", flush=True)
struct_text = await call_claude(
    EDC_STRUCTURE_PROMPT,
    pdf_bytes  = protocol_pdf or None,
    extra_text = "\n".join(extra_parts) if extra_parts else None,
)
```

## Imports to add

At the top of `pipeline.py`, add this import alongside the other
`from ... import ...` lines (any reasonable position works; near the
existing `from claude_client import ...` is natural):

```python
from trainer_integration import (
    run_protocol_analysis_quick,
    retrieve_examples,
    format_examples_block,
)
```

## Module-level constant

Near the top of the module, alongside `STATUS = {...}` and `SKILLS_DIR`,
add:

```python
# Trainer retrieval — number of similar past pairs to request.
# Phase 1 starts at 3; raise after observing prompt length & quality.
TRAINER_K = 3
```

## Env var setup

The trainer URL is read from the `TRAINER_URL` env var by
`trainer_integration.py`. Default is `http://localhost:8001` for local
development. Add to your `.env` (and to Railway's env var settings
when you deploy):

```
TRAINER_URL=http://localhost:8001
```

For Railway production, you'll set this to the trainer service's
internal hostname instead (e.g. `http://trainer.railway.internal:8001`).

## What the patch does

1. **Step 0 — Quick analysis.** Calls `call_claude` with a small
   prompt to extract sponsor / indication / phase / TA from the
   protocol PDF. Takes ~5–15 seconds, costs ~$0.05. The output is
   the canonical query payload for the trainer.

2. **Step 0 — Retrieve.** POSTs the analysis to
   `{TRAINER_URL}/retrieve` with `k=3`. Gets back up to 3 similar
   past pairs from the corpus.

3. **Step 0 — Format and inject.** Formats the matches as a
   prose block with same-sponsor reservation (if any match's sponsor
   matches the current study's sponsor, that match goes to slot 1).
   Appends the block to `extra_parts` so it joins the existing CRF
   library / OC4 standards extras.

4. **Step 1 — As before.** The EDC structure `call_claude` runs
   exactly as it did before, except `extra_text` now includes the
   examples block at the end.

## Failure mode

The whole try/except block is best-effort. If the trainer is down,
times out, or returns garbage, the pipeline logs the error and
continues without examples. Step 1 then runs exactly as it did before
this patch was applied — same prompt, same inputs minus the examples
block. The pipeline's behaviour is unchanged when the trainer isn't
reachable.

## Where to test it

The integration is exercisable end-to-end as soon as:

  1. The trainer service is running on `localhost:8001` (or wherever
     `TRAINER_URL` points).
  2. The trainer's vector store has at least one indexed pair.
  3. A pipeline run is triggered from monday.

You can verify the integration is working by looking at the pipeline
logs — `Step 0: Trainer retrieval — fetching examples...` and the
`Trainer retrieval: N similar past build(s) injected as examples.`
log line confirm the path was taken. Absence of those lines (combined
with `Trainer retrieval failed: ...` or `Trainer retrieval: 0`)
indicates the trainer was unreachable or returned no matches.
