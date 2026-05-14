# DSL Operators Reference

Canonical reference for `applies_when` (filter) and `effect` (action)
DSL used in convention records. For convention-record structure see
`convention.schema.json`. For worked examples see populated
conventions under `../global/` once Phase B is complete.

---

## `applies_when` — filter expressions

The `applies_when` block decides whether a convention applies to a
given entity at build time. The entity is one of: study, form, field,
event, choice (matching the convention's `target` field).

### Path expressions

The left side of every condition is a path into the entity context.

| Path prefix | Available when target is | Resolves to |
|---|---|---|
| `study.<field>` | any | Top-level spec field |
| `form.<field>` | form, field | Current form's field |
| `field.<field>` | field | Current field's field |
| `event.<field>` | event | Current visit's field |
| `field.form.<field>` | field | Parent form of the current field |

Array indexing and length:

| Path syntax | Meaning |
|---|---|
| `form.survey[*].name` | Every `name` value across the `survey` array |
| `form.survey.length` | Number of items in `survey` |
| `form.cross_form_dependencies[*].xpath_expression` | Nested traversal |

### Comparison operators

Each condition takes the form `"path": <comparator>`. A bare value is
shorthand for `equals`.

| Operator | Example | Meaning |
|---|---|---|
| (bare value) | `"form.form_id": "DM"` | equals |
| `equals` | `"form.form_id": { "equals": "DM" }` | explicit equals |
| `not_equals` | `"form.form_id": { "not_equals": "DM" }` | not equal |
| `in` | `"form.form_id": { "in": ["AE","CM"] }` | value in list |
| `not_in` | `"form.form_id": { "not_in": ["ICF"] }` | value not in list |
| `matches` | `"field.name": { "matches": "^[A-Z]+DAT$" }` | regex match |
| `gt` / `gte` / `lt` / `lte` | `"form.survey.length": { "gt": 200 }` | numerical |
| `non_empty` | `"field.choice_list": { "non_empty": true }` | path resolves to non-empty |
| `empty` | `"field.notes": { "empty": true }` | path resolves to empty/null |
| `present` | `"field.cross_form_dependencies": { "present": true }` | path exists at all |

### Logical operators

Combine conditions:

| Operator | Meaning |
|---|---|
| `all_of: [<condition>, ...]` | AND of conditions |
| `any_of: [<condition>, ...]` | OR of conditions |
| `none_of: [<condition>, ...]` | NOR of conditions |

Multiple top-level keys in `applies_when` form an implicit `all_of`.

### Soft markers

```json
"applies_when": {
  "form.form_id": "VS",
  "soft": "field captures a vital sign measurement result"
}
```

The string after `"soft":` is Claude-judgment criteria. The engine
ignores it for filtering; it's appended to Claude's prompt as
guidance. Presence of any `soft:` marker in `applies_when` or
`effect` makes the convention `kind: hybrid`.

---

## `effect` — action directives

The `effect` block declares what happens when `applies_when` matches.
Multiple effects can stack; they execute in source order.

### `set`

Overwrite the path unconditionally.

```json
"effect": { "set": { "form.visits_assigned": ["SE_COMMON"] } }
```

### `ensure`

Set the path only if currently empty/null. Non-destructive.

```json
"effect": { "ensure": { "form.has_repeating_group": false } }
```

### `require`

Assertion. If the path resolves to empty at build time, raise a flag
in `review_flags`.

```json
"effect": { "require": "field.cross_form_dependencies[*].xpath_expression" }
```

### `flag`

Add an entry to a `review_flags` category. Used when a convention
detects something humans should look at but doesn't auto-fix.

```json
"effect": {
  "flag": {
    "category": "review_flags.constraint_review",
    "message": "Form ${form.form_id} has ${form.survey.length} items; consider splitting"
  }
}
```

Template variables use `${path}` syntax and resolve against the
current entity context.

### `append_to`

Add a value to a list at the path. Idempotent — does not add if value
already present.

```json
"effect": { "append_to": { "form.visits_assigned": "SE_UNSCHEDULED" } }
```

### `remove_from`

Remove a value from a list at the path. No-op if not present.

```json
"effect": { "remove_from": { "form.visits_assigned": "SE_COMMON" } }
```

### Soft effect

```json
"effect": {
  "soft": "use CDASH naming convention (e.g., VSORRES, VSORRESU)"
}
```

Human-language directive for Claude. The engine takes no action; the
string is appended to Claude's prompt under the active conventions
section.

### Multiple effects on one rule

Effects stack. Execution is in source order.

```json
"effect": {
  "set": { "form.visits_assigned": ["SE_COMMON"] },
  "flag": {
    "category": "review_flags.constraint_review",
    "message": "Form ${form.form_id} pinned to SE_COMMON by global rule"
  }
}
```

---

## Conflict detection

Two conventions at the same scope conflict in two cases:

### Natural-key conflict

Same scope + same `natural_key` = considered to address the same
topic. Promotion of a new convention with a matching natural_key
requires human resolution (replace / supersede / refine).

### Semantic conflict (structured only)

For two structured conventions A and B at the same scope:

- Compute the intersection of their `applies_when` constraint sets.
- If intersection is non-empty AND their `effect` blocks are not
  equivalent → semantic conflict.

Intersection logic is implemented in the engine for the operators
defined above. For hybrid and advisory conventions, semantic conflict
detection is skipped (their behavior is too unstructured to compare
mechanically). Natural-key check still applies.
