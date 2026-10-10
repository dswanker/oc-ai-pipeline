# Visit schedule from the protocol's structure, and two tighter protocol checks

Train of 2026-10-09 (second cost train). Three findings of the cost A/B, each fixed behind its own kill switch.

| Change | Module | Kill switch |
|---|---|---|
| The visit schedule follows the protocol's Schedule of Activities and timepoint lists | `protocol_schedule.py` | `SCHEDULE_FROM_SOA=0` (`SCHEDULE_FROM_SOA_AI=0` for the AI question alone) |
| Every data item an entry asks to be recorded is an assessment | `protocol_forms.py` | `PROTOCOL_FORMS_ALL_ITEMS=0` |
| A basis passage must name the form's subject | `protocol_basis.py` | `PROTOCOL_BASIS_SUBJECT=0` |

With a switch at `0` the request and the behaviour are what they were before the train (the request snapshots of
`tests/test_cost_switches.py` prove it for the two instructions).

## 1. Visit schedule (`protocol_schedule.py`)

The protocol analysis named a different set of events on every run of one protocol: timepoints the protocol lists
were left out, folded into another event, or an end-of-study event was added next to the visit the schedule already
labels as the end of study.

### What is read from the protocol (deterministic)

- **Column headers** of every Schedule of Activities table (`protocol_structure` finds the tables), from the PDF's
  layout text. Header lines stacked over one column are joined. A header printed once over several columns (a
  spanning header) is given to each of them: a column whose header starts below a line is offered to the headers of
  that line on either side and takes the one whose text is then centred over its columns. A table continued on the
  next page, or a second grid under the same caption with its own header lines, is read as more columns of the
  same table.
- **Marks** of each row, placed in the nearest column, and the table's numbered notes.
- **Timepoint lists**: a table whose first-column rows are mostly time expressions (a row ending in a colon heads
  the rows under it), and a note that a cell or a row label refers to and that enumerates three or more time
  expressions.
- **Time expressions** are parsed generically: a unit and a number or range (`Week 2-3`, `W 4-5`, `Day -28 to Day
  0`) or a number or range and a unit (`2-4 hours`, `24 hrs`). Nothing is recognised by topic.

### Units

Headers and timepoints that are the same visit form one unit. Each merge is recorded with its reason:

1. the same label, written differently;
2. the same time label on entries that have no visit name of their own (a spanning header, a heading of list rows
   and a relative word such as "post" are not a visit name);
3. a header with nothing marked under it that has the time label of exactly one named visit;
4. no time label on either side and one label contains the other (or is listed under it).

A named visit and a time-only entry with the same time label stay two units when the time-only one has activity
marked (the analysis keeps them apart too).

A unit without a time label that is not a named visit column is put to **one small validated AI question**: does
the protocol state that it takes place at the same visit as another unit? The answer counts only with a verbatim
quote that is found in the protocol and names both units. Without a usable answer nothing is added for that unit;
it is flagged.

### Events against units

An event stands for a unit by its time label and its name (what an event only mentions in brackets counts half; an
event that carries the time labels of several units is scored on its name only). One assignment is chosen for all
events (the same optimal assignment as standards matching).

| Finding | Fix-up |
|---|---|
| A unit has no event | An event is added, labelled as the protocol labels it, placed after the event of the unit before it. Its forms are the forms of the schedule rows marked in that column (through the completeness check's row to form mapping); for an entry of a timepoint list, the forms every other visit of that list has. No other form is placed. |
| An event names several units | It keeps one; the others get their own event (logged as a split). |
| A second event stands for a unit that has one | It is dropped; its forms move to the event that stays. The schedule's own bracket label (for example the abbreviation the protocol defines for the last visit) identifies the visit. |
| An event stands for no unit | Kept and flagged. The unscheduled, common and early-termination events are not checked. |

Safeguards: nothing is changed when fewer than half of the scheduled events match what was read (the reading is
then not trusted), when more than 12 events would be added, on a reused or edited specification (fix-ups are
recorded and flagged, not applied), or for a Word protocol that could not be converted to PDF.

The step runs at the end of the post-matching steps, after the completeness check and the basis check, because the
forms of an added event come from the completeness check's mapping. State: `study_meta.schedule_from_soa` (units,
merges with reasons and quotes, fix-ups, flags). Every fix-up is a review flag and a line of the monday log.

### Limits

- Column positions come from layout text, not from the PDF's ruling lines. An unusual table layout can yield wrong
  headers; the safeguards above then leave the events alone.
- Timepoints within one day become events when the protocol lists them as timepoints. That is what the fix asks
  for; a protocol with a dense sampling table reaches the 12-event limit and is flagged instead.
- The arm of an added event is taken from the events of the same source tables.

## 2. Every recorded data item is an assessment (`protocol_forms.py`)

The completeness instruction asked for the assessments of each checklist entry; the model answered with the
entry's main topic and skipped a second, unrelated data item in the same section. The instruction now asks, for
every entry, for every distinct data item the text instructs to be recorded, each with the sentence that asks for
it. Quote verification is unchanged.

Mapping to forms: for wording the pipeline's rules do not know, the CDASH domain is only the model's guess. Such
an assessment is now covered by a form only when the form names it (at least half of its words, weighted by how
few forms carry each word, in the title, a question or a choice). Otherwise no form collects it and one is added
through the usual chain (customer standard, customer CRF standards, CDASHIG).

`PROTOCOL_FORMS_KIND_NAMES=1` (default OFF) adds one more rule: an assessment is named by the kind of data
collected, the way a case report form for it would be titled, not by the wording of the sentence that asks for it.
Tested in `docs/MODEL_PROFILE.md` (Stage 2).

## 3. A basis passage names the form's subject (`protocol_basis.py`)

A form was sometimes kept on a passage about something else: a sentence the model classified as an instruction to
record, or a schedule row with a generic label. After the AI answer, a passage now supports a form only when it
names what the form is about:

- a word of its title (also its title before matching and the customer standard's title), without generic words: a
  word in more than a quarter of the form titles, as in standards matching;
- a name the protocol declares to be the same thing (the alias table of standards matching), or an abbreviation of
  the title;
- one of its field labels, all its words, of which one occurs in this form's labels only.

The eligibility form keeps its own rule. The instruction of the call says the same, so the model offers passages
that pass.

## Tests

`tests/test_protocol_schedule.py` (synthetic tables: stacked and spanning headers, a second grid, note and table
timepoints, missing events, a folded event, a merge with and without a verified reason, a duplicate end event, a
reused specification, kill switches), `tests/test_protocol_forms.py`, `tests/test_protocol_basis.py`.

## Validation of 2026-10-09

Three fresh runs of one protocol, switches off, offline, through the schedule step (batch mode). The protocol has
two schedule tables (one with a second grid on the next page), 14 numbered notes and two timepoint tables; the
reader finds 29 column headers and 32 listed timepoints, which form 18 visits.

| | Run 1 | Run 2 | Run 3 |
|---|---|---|---|
| Events of the analysis | 16 | 15 | 15 |
| Fix-ups | 3 added | 4 added | 4 added |
| Events after the step | 19 | 19 | 19 |
| Visits of the protocol's schedule with an event or a stated merge | 18 of 18 | 18 of 18 | 18 of 18 |
| Events without a visit | 0 | 0 | 0 |
| Build errors | 0 | 0 | 0 |

- The three merges that are decisions are the same in every run: two headers with nothing marked under them are
  the named visits with the same time label, and the one label without a time of its own is the visit the protocol
  states it shares a day with (AI question, quote verified).
- Replayed offline on the nine saved runs of the first cost test (15 to 21 events, including a folded event, a
  second end-of-study event and two redundant events): 19 events in eight, 20 in one, where the analysis had an
  extra event the schedule does not have (kept and flagged).
- The vaccination data item is listed by the completeness check with its sentence in 3 of 3 runs, and a form
  collects it in 3 of 3.
- The performance-status form is not built in 3 of 3 runs (the analysis created it in 2).
- Three more runs with the cost switches on give the same 19 events (0, 1 and 5 fix-ups, among them a split of a
  folded event).

Two details were changed after these runs and checked by replaying the saved runs offline (same events, same
fix-ups): an added event is placed after the latest event of the visits before it (one run had it one position
early), and an assessment whose domain is the model's guess goes to the form whose title has all its words.

