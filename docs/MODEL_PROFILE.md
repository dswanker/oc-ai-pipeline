# Model per step (`MODEL_PROFILE`)

Default OFF: with `MODEL_PROFILE` unset (or `default`) every call uses the model it has always used and the request
is byte-identical (`tests/test_cost_switches.py` snapshots, `tests/test_model_profile.py`).

| Setting | Effect |
|---|---|
| `MODEL_PROFILE=default` | Today's models. |
| `MODEL_PROFILE=opus55` | Every call that uses Opus 4.7 today uses `claude-opus-5-5`. |
| `MODEL_PROFILE=mixed55` | `claude-opus-5-5` for the steps that decide forms, visits and fields; `claude-sonnet-5-5` for every other call that uses Opus 4.7 today. |
| `MODEL_STEP_<STEP>=<model id>` | One step's model, whatever the profile (`MODEL_STEP_PRICING_SUMMARY=claude-opus-4-7`). |
| `MODEL_EFFORT=low|medium|high|xhigh|max` | Effort level sent to 5.5 models. Unset: the model's default (Opus 5.5 medium, Sonnet 5.5 high). |
| `MODEL_THINKING_HEADROOM` | Minimum extra `max_tokens` for a 5.5 model (default 16000). |

The step of a call is named where the call is made (`pipeline._ai("<step>", ...)`, `claude_client.STEP`); the model
is chosen in `claude_client.model_for`. A call without a step name counts as one of the other calls.

## Call inventory

Typical tokens are the mean of three full runs of one protocol on today's models (input includes cached tokens).

| Step | Where | Today | Input / output tokens | opus55 | mixed55 |
|---|---|---|---|---|---|
| `quick_analysis` | `trainer_integration.run_protocol_analysis_quick` | Opus 4.7 | 130,046 / 123 | Opus 5.5 | Sonnet 5.5 |
| `main_analysis` | `pipeline.run_pipeline` | Opus 4.7 | 157,179 / 44,928 | Opus 5.5 | Opus 5.5 |
| `standards_close_call` | `pipeline._standards_match_step` | Opus 4.7 | 1,437 / 12 | Opus 5.5 | Opus 5.5 |
| `protocol_fields` | `pipeline._protocol_fields_call` | Opus 4.7 | 75,528 / 2,682 | Opus 5.5 | Opus 5.5 |
| `completeness` | `pipeline._protocol_forms_step` | Opus 4.7 | 133,776 / 7,326 | Opus 5.5 | Opus 5.5 |
| `basis` | `pipeline._protocol_basis_step` | Opus 4.7 | 138,415 / 5,382 | Opus 5.5 | Opus 5.5 |
| `schedule_question` | `pipeline._schedule_from_soa_step` | Opus 4.7 | 7,470 / 71 | Opus 5.5 | Opus 5.5 |
| `merged_checks` (only with `PROTOCOL_CHECKS_MERGED=1`) | `pipeline._merged_protocol_checks` | Opus 4.7 | 174,033 / 15,661 | Opus 5.5 | Opus 5.5 |
| `concept_tagging` | `pipeline._tag_concepts` | Opus 4.7 | 74,090 / 5,608 | Opus 5.5 | Sonnet 5.5 |
| `standard_logic` | `pipeline._propose_standard_logic` | Opus 4.7 | 67,668 / 5,802 | Opus 5.5 | Sonnet 5.5 |
| `sdv_endpoint` | `pipeline._study_config_step` | Opus 4.7 | 166,540 / 648 | Opus 5.5 | Sonnet 5.5 |
| `ai_edit_checks` | `pipeline._propose_ai_edit_checks` | Opus 4.7 | 41,015 / 3,275 | Opus 5.5 | Sonnet 5.5 |
| `pricing_summary` | `pipeline.run_pipeline` | Opus 4.7 | 43,420 / 9,328 | Opus 5.5 | Sonnet 5.5 |
| `dvs_added_checks`, `dvs_build_json`, `dvs_translate` | `pipeline.run_pipeline` (edited-DVS paths) | Opus 4.7 | not part of a fresh run | Opus 5.5 | Sonnet 5.5 |
| migration call | `migration_pipeline` (no step name) | Opus 4.7 | not measured | Opus 5.5 | Sonnet 5.5 |
| `skill_run` | `claude_client.run_skill` | Opus 4.7 | not measured | Opus 5.5 | Sonnet 5.5 |

Already on Sonnet, unchanged in every profile: the scheduling pass (`pipeline._extract_scheduling_block`,
`claude-sonnet-4-6`, called directly, so it is also outside economy mode), the XLSForm self-correction
(`skills/edc-builder/scripts/build_xlsforms.py`, `claude-sonnet-4-20250514`), and the design-change and
email-change intake skills (`claude-sonnet-4-20250514`).

`skill_run`, the edited-DVS calls and the migration call are mapped but were not run on a 5.5 model.

## What changes in a request for a 5.5 model

Checked against the Anthropic documentation on 2026-10-09. Prompt and content blocks are never changed.

| Feature | Opus 4.7 (today) | Opus 5.5 / Sonnet 5.5 | Adaptation |
|---|---|---|---|
| Model id | `claude-opus-4-7` | `claude-opus-5-5`, `claude-sonnet-5-5` | id only |
| PDF document blocks, prompt caching (5-minute and 1-hour), Message Batches | supported | supported; cache minimum 512 tokens; cache read 0.05 x input | none |
| Long output | `output-128k-2025-02-19` beta | 128K is the standard limit; the docs say to remove the header | header dropped, call goes through the normal (non-beta) endpoint |
| Thinking | off unless asked | always on (Opus 5.5), on by default (Sonnet 5.5); `max_tokens` covers thinking plus text; thinking is billed as output | `max_tokens` raised by a third, at least 16,000, capped at 128,000; the reply's text blocks are read by type |
| Effort | n/a | default medium (Opus 5.5), high (Sonnet 5.5) | left at the default unless `MODEL_EFFORT` is set |
| Sampling parameters, prefill, forced tool choice | not used by the pipeline | rejected | none needed |
| Refusal (`stop_reason: refusal`) | n/a | possible | raised as a failed call; the build continues as for any failed AI call |

List prices used ($ per million tokens: input, 5-minute cache write, 1-hour cache write, cache read, output):
Opus 4.7 5 / 6.25 / 10 / 0.50 / 25; Opus 5.5 4 / 5 / 8 / 0.20 / 20; Sonnet 5.5 2 / 2.50 / 4 / 0.10 / 10. Batch is
half of each. `claude_client.call_cost` prices a usage record at its model's prices.

## Test of 2026-10-09 (one protocol, 3 full runs per arm, batch mode, cost switches off)

A = default, M1 = `opus55`, M2 = `mixed55`. Arm A's front half (analysis to schedule step) is the three validated
runs of the previous train, continued through the remaining steps; the code they depend on is unchanged.

| Arm | Normal-price cost per full run | Economy (batch) | Output tokens per run | Pass rule |
|---|---|---|---|---|
| A | $7.36, $7.37, $7.31 (mean $7.35) | $3.67 | about 85,000 | 3 of 3 |
| M1 | $7.09, $7.70, $7.21 (mean $7.34) | $3.67 | 149,000 to 177,000 | 0 of 3 |
| M2 | $6.33, $5.85, $6.10 (mean $6.09) | $3.05 | 158,000 to 176,000 | 0 of 3 |

- **Cost.** Opus 5.5 is 20% cheaper per token, but it always thinks: output roughly doubles in every call (the
  main analysis from 45,000 to 78,000 to 110,000 tokens). `opus55` costs the same as today; `mixed55` is 17% cheaper.
- **Why the 5.5 arms fail.** In 6 of 6 runs the analysis adds a baseline event for the control group that the
  Schedule of Activities has no column for; the schedule step keeps and flags it, so there are 20 events, not 19.
  In 4 of 6 runs two forms without a protocol basis are kept: the 5.5 analysis marks a form without a library
  source with a status that `protocol_basis.source_kind` reads as a customer library form (a substring test), so
  the basis check keeps it as a customer standard form. One run has no vaccination form (the item was mapped to
  the medication form).
- **Other consistent differences.** More forms and more fields on non-standard forms; more protocol-specified
  fields; 4 (M1) and 3 (M2) SDV endpoints linked in every run against 1, 1, 3; AI standard-logic proposals higher
  with Opus 5.5 (44 to 51) and lower with Sonnet 5.5 (20 to 24) than today (31 to 36); SDV Required items and AI
  edit check targets vary less between runs than today. Pricing summary fields, standard-form pairing, the dosing
  pair, Concomitant Procedures and build errors (none) are the same.
- **Winner: A.** Neither profile passes.

## Stage 2 (winner A, truncated runs, with `PROTOCOL_FORMS_KIND_NAMES=1`)

`PROTOCOL_FORMS_KIND_NAMES=1` (default OFF) adds one rule to the completeness instruction: name each assessment by
the kind of data collected, not by the wording of the sentence. The quick and main analysis of each run are the
ones of arm A (unchanged code), so W, T and C of one run number share one analysis.

| Arm | Switches | Cold-cache normal-price cost | Pass rule |
|---|---|---|---|
| W | none | $4.74, $4.84, $4.72 (mean $4.76) | 3 of 3 |
| T | merged checks + trim | $3.86, $4.00, $4.53 (mean $4.13) | 0 of 3 |
| C | `PROTOCOL_DOC_FIRST=checks` + merged checks + trim | $3.92, $4.03, $3.97 (mean $3.97) | 2 of 3 |

- T: Concomitant Procedures is missing in two runs (the merged call does not list it); the third keeps a form the
  basis check removes elsewhere.
- C: with the naming rule the item is named concomitant procedures in two runs (0 of 3 before the rule); in the
  third it is still named after the sentence and a new form is added instead of the customer's standard form.
- The three C runs were started together, but their batches were processed one after another: one wrote the
  cache entry and two read it. The cold-cache cost prices those two reads as the write a single run pays.
- The naming rule helps and is not enough. Merged checks and protocol-first stay off.
