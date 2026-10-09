# Cost of a run: three switches and economy runs

All three switches are OFF by default. Off means the requests are byte-identical to what they were before the
switches existed (`tests/test_cost_switches.py` compares them with snapshots recorded from that commit).

## Where the money goes

A run makes 11 AI calls. Six of them send the whole protocol PDF (about 130,000 tokens each): the trainer quick
analysis, the main analysis, the completeness checklist, the protocol basis, the SDV endpoint link, and (as text,
not PDF) the protocol-specified fields call and the standard-form logic call. `call_claude` put the prompt first and
the PDF after it; the prompts differ, so the PDF was never read from the cache.

## PROTOCOL_DOC_FIRST=1

In every call that sends the protocol PDF the content is: the PDF block with a cache marker, then the prompt block
(keeping its own marker), then images and extra text. Only the order of the blocks and the cache markers change.

- The PDF bytes are the same object in every call of a run; the block is identical, so one cache entry serves the
  run. Each call logs the SHA-256 of the PDF it sends.
- The cache marker has a 1-hour TTL. The main analysis streams for about 6 minutes, so the gap between the start of
  that call and the next protocol call is longer than the 5-minute default (measured: 400 seconds and more).
  `PROTOCOL_DOC_CACHE_TTL=5m` switches to the 5-minute entry.
- Price: the first protocol call of a run writes the entry (2 x the input price for those tokens), every later one
  reads it (0.1 x). A second run of the same protocol within the hour reads it from the first call on.
- The calls that send the protocol as text (protocol-specified fields, standard-form logic) are not changed by this
  switch.
- `PROTOCOL_DOC_FIRST=checks`: the same for every protocol call except the main analysis (the extended-output
  call), whose request stays exactly as it is today. Added after the test below showed that the block order changes
  what the main analysis produces. Not run live; its requests are the tested ones (main analysis as with the
  switch off, the other calls as with the switch on).

## PROTOCOL_CHECKS_MERGED=1 (`protocol_checks.py`)

The protocol-specified fields check, the completeness checklist and the protocol basis check become one call.

- The prompt is a short head plus the three tasks' instructions, each unchanged, under its own heading. The input
  has one section per task. The answer is one JSON object with a key per task.
- Each section goes, as that task's answer, to the validator the task has always had
  (`standards_match.apply_additions`, `protocol_forms.validate_response`, `protocol_basis.apply`).
- Order. The call is made after the form conventions, on the forms as they are then. The basis check needs the
  final forms, and the completeness check may still add a form, so one call cannot be exact. What is merged: the
  basis task also judges the customer standard forms not used yet (what the completeness check takes first when it
  adds a form). A form added from elsewhere (CDASHIG, CRF standards) gets a follow-up basis call for just that form.
- A task that does not need to run is left out; with fewer than two tasks nothing is merged. A missing section, or
  a failed merged call, sends that task back to its own call. The completeness follow-up for unanswered checklist
  entries is unchanged.
- The protocol-specified fields task reads the PDF in the merged call; on its own it reads the extracted text.

## SPEC_INPUT_TRIM=1 (`spec_trim.py`)

The calls that send study data already send compact field lines, not the specification JSON. What is trimmed:

- display markup in question labels (HTML tags, runs of whitespace), in every field-list request;
- the form heading repeated on every line of the concept-tagging request: once per form;
- the review flags of the pricing summary request: a kind with more than 25 flags is sent as its count and its first
  25 entries (the step counts flags per kind and names the most important ones).

No field, type, choice or existing check is dropped. The input size before and after is logged per call
(`[spec-trim] ...`).

## Economy runs (Message Batches API)

monday column "Economy Run (Batch, half price)" (`color_mm7z4qsc`): Yes / No / blank. With Yes every AI call of
that run is sent as a batch of one: same model, same parameters, same content, same beta header. The batch is
polled with backoff (5 s growing to 60 s) until it has ended; the result is parsed as always.

- Checked against the Message Batches documentation (2026-10-09): PDF document blocks, images, prompt caching with
  `cache_control` (the 1-hour TTL is recommended there for batches), beta headers and `max_tokens` up to the model
  limit are supported. Not supported: `stream`, `speed`, `max_tokens: 0`; the pipeline sends none of them in a
  request body. A request that carried one would run normally, and that is logged.
- Logged per call: batch id and wait time. monday log: a line at the start ("Economy run: AI calls are batched at
  half price; this run may take longer") and a progress line while waiting, at most every 10 minutes.
- A batch that errors, expires or is cancelled: that call is retried once as a normal call, and that is logged.
  A call that is cancelled or times out cancels its batch.
- The trainer quick analysis has a 60-second limit; in an economy run it waits up to
  `ECONOMY_QUICK_ANALYSIS_WAIT_S` (default 30 minutes), then the run goes on without examples.
- `ECONOMY_RUNS=0` disables economy mode for every run. `ECONOMY_MAX_WAIT_S` caps the wait per call (default 24 h).

## Usage record

`claude_client.USAGE` holds one record per finished call (input, output, cache read, 5-minute and 1-hour cache
writes, seconds, batch id, wait, price). `claude_client.call_cost` prices a record at the Opus 4.7 list prices;
a batched call costs half.

## Test of 2026-10-09 (one protocol, 3 runs per arm, interleaved A B C A B C A B C)

Full pipeline offline through every AI step, no customer convention answers, no trainer examples. A = all switches
off, B = `PROTOCOL_DOC_FIRST=1`, C = B plus `PROTOCOL_CHECKS_MERGED=1` and `SPEC_INPUT_TRIM=1`. Outputs under
`.cache/abtest/` (not in git). Prices: Opus 4.7 list prices.

| Arm | Calls | Billed per run | Cold-cache per run | AI time per run |
|---|---|---|---|---|
| A | 11 | $7.19, $7.45, $7.18 (mean $7.27) | same | 842, 920, 807 s |
| B | 11 | $5.04, $3.74, $4.50 (mean $4.43) | $5.04, $4.97, $5.73 (mean $5.25) | 865, 839, 898 s |
| C | 9, 10, 9 | $3.82, $3.91, $3.61 (mean $3.78) | $5.06, $5.14, $4.84 (mean $5.01) | 871, 893, 764 s |

"Cold-cache" prices a run as a single production run pays: its first protocol call writes the 1-hour entry. In the
test only B1 was cold; the later B and C runs read the entry an earlier run had left, which is also what a re-run of
the same protocol within the hour pays.

- The protocol cache works across all protocol calls, the extended-output call included: 129,794 tokens written once,
  then read by every later protocol call.
- Seconds between the starts of consecutive protocol calls: 4 to 19, then 368 to 478 (the main analysis), then 67 to
  260. The second gap is why the entry needs the 1-hour TTL.
- The merged call returned three usable sections in 3 of 3 runs; one run needed the follow-up basis call for a form
  the completeness check added from CDASHIG. The three checks cost $2.08 to $2.15 in A, $0.88 to $0.98 in B and
  $0.63 to $0.64 in C.
- The trim cut the concept-tagging request by 46%, the pricing review flags by 55 to 67%, the other field-list
  requests by 5 to 18%: about 65,000 tokens ($0.33) per run on this study.
- Time is unchanged by any switch.

**Outputs.** Run-to-run variation is large in every arm (forms at events, fields of non-standard forms,
protocol-specified fields added and AI edit check targets differ between two runs of the same arm about as much as
between arms). One difference is consistent and comes with the protocol-first order:

- In all 6 runs with the protocol first the main analysis created an End of Study event and the three
  post-injection timepoints; in arm A that happened in 0 of 3 and 1 of 3 runs. The treatment-assessment events arm A
  created in 3 of 3 runs appear in 1 of 6. The second dosing form sits on other events as a result.
- The completeness check names its assessments less uniformly with the protocol first (distance between two runs
  0.06 in A, 0.41 in B, 0.43 in C); the forms they map to and the checklist coverage are as stable as in A.

Standard-form pairing, the dosing pair, the added Concomitant Procedures form, build errors (none) and "not
assessed" checklist entries (none) are the same in all nine runs.

**Economy mode, live, once** (quick analysis and main analysis as batches of one, switches off): both answers parsed;
$0.33 and $1.02 against $0.65 and $2.03 at the normal price (50%); the batches ended after 1,312 and 707 seconds
(the normal calls take about 15 and 400 seconds). The extended-output beta header was accepted in the batch.
