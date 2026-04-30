"""
build_preview/interactive.py — Phase 1 Interactive Form Simulator

Converts the per-form Enketo HTML produced by enketo-transformer into
standalone interactive HTML files.  Packages them with an index page
into a ZIP that can be opened in any browser — no server required.

Interactivity level (Phase 1):
  ✓ Show/hide fields based on relevance conditions (data-relevant)
  ✓ Constraint error messages on blur/change (data-constraint)
  ✓ Required field highlighting (data-required)
  ✓ Real Enketo grid styling (grid.css inlined)
  ✓ Navigate between forms via index page
  ✗ Cross-form XPath (instance('clinicaldata') → shows blank — Phase 2)
  ✗ Complex XPath functions (date arithmetic, etc. → Phase 2)

Phase 2 will add a mock data store and cross-form reference resolution.
"""
import io
import re
import zipfile


# ── Interactive CSS ────────────────────────────────────────────────────────────
# Different from ANNOT_CSS — branches are JS-controlled (hidden by default),
# constraint messages are hidden until triggered, calculations are invisible.

INTERACTIVE_CSS = """
/* ── OC Simulator chrome ──────────────────────────────────────────────── */
body {
  font-family: "Helvetica Neue", Arial, sans-serif;
  background: #eef2f7;
  margin: 0; padding: 0;
}
.oc-sim-header {
  background: linear-gradient(180deg, #005C87 0%, #004a6e 100%);
  color: white; padding: 12px 20px;
  font-family: "Helvetica Neue", Arial, sans-serif;
  display: flex; align-items: center; justify-content: space-between;
}
.oc-sim-header h2 { margin: 0; font-size: 17px; font-weight: 600; }
.oc-sim-header .meta {
  font-size: 11px; opacity: 0.85; margin-top: 3px;
  font-family: ui-monospace, Menlo, monospace;
}
.oc-sim-header .back-link {
  color: rgba(255,255,255,0.85); font-size: 12px;
  text-decoration: none; white-space: nowrap;
}
.oc-sim-header .back-link:hover { color: white; text-decoration: underline; }
.sim-badge {
  background: #ff9800; color: white; font-size: 10px; font-weight: 700;
  padding: 2px 8px; border-radius: 10px; margin-left: 10px;
  letter-spacing: 0.5px; vertical-align: middle;
}
.form-wrapper {
  max-width: 900px; margin: 0 auto; padding: 16px 20px 40px;
}
.or {
  background: white; border-radius: 0 0 4px 4px;
  box-shadow: 0 1px 4px rgba(0,0,0,0.12);
}

/* ── Relevance chips ──────────────────────────────────────────────────── */
.or-branch[data-relevant]:not([data-relevant=""])::before {
  content: "Show if: " attr(data-relevant);
  display: block; font-size: 9px; color: #5c4a00;
  background: #fef9e7; border-left: 2px solid #f0c040;
  padding: 2px 8px; border-radius: 0 3px 3px 0;
  margin: 0 0 4px 0;
  font-family: ui-monospace, Menlo, monospace;
}

/* ── Constraint / required messages (JS-controlled) ──────────────────── */
.or-constraint-msg, .or-required-msg {
  display: none;
  font-size: 11px; font-weight: 500;
  padding: 3px 10px 3px 8px;
  margin-top: 4px; border-radius: 0 3px 3px 0;
}
.or-constraint-msg {
  color: #b54708; background: #fff7ed;
  border-left: 3px solid #fb923c;
}
.or-constraint-msg::before { content: "⚠ "; }
.or-required-msg {
  color: #991b1b; background: #fef2f2;
  border-left: 3px solid #f87171;
}
.or-required-msg::before { content: "✱ Required"; }

/* ── Calculations / preloads (auto-filled, hide the panel) ───────────── */
#or-calculated-items, #or-preload-items { display: none !important; }

/* ── Invalid field highlight ─────────────────────────────────────────── */
.field-invalid > input, .field-invalid > select, .field-invalid > textarea {
  border-color: #f87171 !important;
  box-shadow: 0 0 0 2px rgba(248,113,113,0.2) !important;
}

/* ── Phase 2 placeholder for cross-form refs ─────────────────────────── */
.xfref-placeholder {
  display: inline-block; font-size: 10px; color: #6366f1;
  background: #eef2ff; border: 1px dashed #a5b4fc;
  padding: 1px 6px; border-radius: 3px;
  font-family: ui-monospace, Menlo, monospace;
}
"""


# ── Interactive JavaScript shim ────────────────────────────────────────────────
# Evaluates the subset of XPath commonly produced by pyxform:
#   ${field} = 'val'   ${field} != ''   selected(${field}, 'val')
#   not(...)  ... and ...  ... or ...  . >= N  string-length(${f}) > 0

INTERACTIVE_JS = r"""
(function () {
  'use strict';

  // ── Value accessors ──────────────────────────────────────────────────
  function getVal(name) {
    // Enketo names forms /data/fieldname — search by name suffix
    var sel = '[name$="/' + name + '"]';
    var els = document.querySelectorAll(sel);
    if (!els.length) {
      // Fallback: data-name attribute
      els = document.querySelectorAll('[data-name$="/' + name + '"]');
    }
    if (!els.length) return '';
    if (els[0].type === 'radio' || els[0].type === 'checkbox') {
      return Array.from(els)
        .filter(function (e) { return e.checked; })
        .map(function (e) { return e.value; })
        .join(' ');
    }
    return els[0].value || '';
  }

  function getCurVal(el) {
    var q = el.closest('.question, .or-group, .or-repeat');
    if (!q) return el.value || '';
    var inputs = q.querySelectorAll('input, select, textarea');
    if (!inputs.length) return '';
    var first = inputs[0];
    if (first.type === 'radio' || first.type === 'checkbox') {
      return Array.from(inputs)
        .filter(function (i) { return i.checked; })
        .map(function (i) { return i.value; })
        .join(' ');
    }
    return first.value || '';
  }

  // ── XPath-subset evaluator ────────────────────────────────────────────
  function evalXPath(expr, curEl) {
    if (!expr) return true;
    expr = expr.trim();

    // Inline string literals replacement: ${name} → js string
    var resolved = expr.replace(/\$\{([^}]+)\}/g, function (_, n) {
      return JSON.stringify(getVal(n.trim()));
    });

    // Current-node dot
    if (curEl) {
      var cv = JSON.stringify(getCurVal(curEl));
      // Replace standalone . not preceded/followed by alphanum
      resolved = resolved.replace(/(^|[^a-zA-Z0-9_])\.((?=[^a-zA-Z0-9_])|$)/g,
        function (m, pre, post) { return pre + cv + post; });
    }

    // XPath → JS translations
    resolved = resolved
      .replace(/\band\b/g, '&&')
      .replace(/\bor\b/g,  '||')
      .replace(/\bnot\s*\(/g, '!(')
      .replace(/\btrue\s*\(\s*\)/g,  'true')
      .replace(/\bfalse\s*\(\s*\)/g, 'false')
      // selected(expr, 'val') → expr.split(' ').indexOf('val') !== -1
      .replace(/selected\s*\(\s*([^,]+),\s*'([^']+)'\s*\)/g, function (_, fExpr, val) {
        return '(' + fExpr.trim() + ').split(" ").indexOf("' + val + '") !== -1';
      })
      // string-length(expr) → (expr).length
      .replace(/string-length\s*\(\s*([^)]+)\s*\)/g, function (_, fExpr) {
        return '(' + fExpr.trim() + ').length';
      })
      // number(expr) → parseFloat(expr)||0
      .replace(/\bnumber\s*\(\s*([^)]+)\s*\)/g, function (_, fExpr) {
        return '(parseFloat(' + fExpr.trim() + ')||0)';
      });

    try {
      /* jshint evil: true */
      return !!Function('"use strict"; return (' + resolved + ')')(); // eslint-disable-line
    } catch (e) {
      // Unknown expression — default to visible (safe)
      return true;
    }
  }

  // ── Visibility update ─────────────────────────────────────────────────
  function updateVisibility() {
    document.querySelectorAll('.or-branch').forEach(function (branch) {
      var expr = branch.getAttribute('data-relevant');
      if (!expr) return;
      var show = evalXPath(expr, null);
      branch.style.display = show ? '' : 'none';
    });
  }

  // ── Constraint check ──────────────────────────────────────────────────
  function checkConstraint(inputEl) {
    var q = inputEl.closest('.question, .or-group');
    if (!q) return;
    var cAttr = inputEl.getAttribute('data-constraint') ||
                q.getAttribute('data-constraint');
    var msgEl = q.querySelector('.or-constraint-msg');
    if (!cAttr || !msgEl) return;

    if (!inputEl.value && inputEl.type !== 'checkbox' && inputEl.type !== 'radio') {
      msgEl.style.display = 'none';
      return;
    }
    var passes = evalXPath(cAttr, inputEl);
    msgEl.style.display = passes ? 'none' : 'inline-block';
    q.classList.toggle('field-invalid', !passes);
  }

  // ── Required check ────────────────────────────────────────────────────
  function checkRequired(inputEl) {
    var q = inputEl.closest('.question, .or-group');
    if (!q) return;
    var reqAttr = inputEl.getAttribute('data-required') ||
                  q.getAttribute('data-required');
    var msgEl = q.querySelector('.or-required-msg');
    if (!reqAttr || !msgEl) return;

    if (reqAttr === 'true()' || reqAttr === '1') {
      var empty = !inputEl.value ||
        (inputEl.type === 'checkbox' && !inputEl.checked);
      msgEl.style.display = empty ? 'inline-block' : 'none';
    }
  }

  // ── Wire events ───────────────────────────────────────────────────────
  document.addEventListener('change', function (e) {
    var t = e.target;
    if (!t || !['INPUT','SELECT','TEXTAREA'].includes(t.tagName)) return;
    updateVisibility();
    checkConstraint(t);
    checkRequired(t);
  });

  document.addEventListener('blur', function (e) {
    var t = e.target;
    if (!t || !['INPUT','SELECT','TEXTAREA'].includes(t.tagName)) return;
    checkConstraint(t);
    checkRequired(t);
  }, true);

  // ── Initial state ─────────────────────────────────────────────────────
  // Hide all validation messages at load time
  document.querySelectorAll('.or-constraint-msg, .or-required-msg')
    .forEach(function (el) { el.style.display = 'none'; });

  // Evaluate initial visibility
  updateVisibility();

  // Debug handle
  window.__sim = { updateVisibility: updateVisibility, evalXPath: evalXPath };

})();
"""


# ── Per-form HTML builder ──────────────────────────────────────────────────────

def build_form_html(fm: dict, raw_form_html: str, grid_css: str,
                    index_href: str = 'index.html') -> str:
    """
    Build a fully self-contained interactive HTML page for one form.

    Args:
        fm: form metadata dict from render.py (fid, title, version, settings)
        raw_form_html: the Enketo HTML from enketo-transformer (NOT annotated)
        grid_css: content of grid.css from the vendor dir
        index_href: relative href back to the index page

    Returns:
        Complete HTML string ready to write to a .html file.
    """
    title   = fm.get('title',   fm.get('fid', ''))
    form_id = fm.get('fid',     '')
    version = fm.get('version', '1')
    settings = fm.get('settings', {})
    form_oid = settings.get('form_id', form_id)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} — OC Form Simulator</title>
  <style>
{grid_css}

{INTERACTIVE_CSS}
  </style>
</head>
<body>

<div class="oc-sim-header">
  <div>
    <h2>{title} <span class="sim-badge">SIMULATOR</span></h2>
    <div class="meta">Form OID: {form_oid} &nbsp;·&nbsp; Version: {version}
      &nbsp;·&nbsp; Interactive preview — constraints and relevance are live</div>
  </div>
  <a class="back-link" href="{index_href}">← All Forms</a>
</div>

<div class="form-wrapper">
  {raw_form_html}
</div>

<script>
{INTERACTIVE_JS}
</script>

</body>
</html>
"""


# ── Index page builder ─────────────────────────────────────────────────────────

def build_index_html(forms_meta: list, study_spec: dict,
                     protocol: str) -> str:
    """
    Build an index.html listing all forms with their visit assignments.
    Includes the SoE matrix as a static HTML table with links.
    """
    events       = study_spec.get('events', [])
    form_to_evts = study_spec.get('form_to_events', {})
    form_inv     = study_spec.get('form_inventory', {})
    metadata     = study_spec.get('metadata', {})
    study_title  = metadata.get('Protocol Title', '')

    # SoE matrix rows
    matrix_head = '<th>Form</th>' + ''.join(
        f'<th><div class="oid">{e["event"]}</div>'
        f'<div class="tp">{e["timepoint"]}</div></th>'
        for e in events
    )
    matrix_rows = []
    for fm in forms_meta:
        fid   = fm['fid']
        title = form_inv.get(fid, {}).get('title', '') or fm.get('title', fid)
        assigned = set(form_to_evts.get(fid, []))
        cells = [f'<td class="form-cell"><a href="{fid}.html">'
                 f'<b>{fid}</b><br><span class="sub">{title}</span></a></td>']
        for e in events:
            if e['event'] in assigned:
                cells.append('<td class="x active">✓</td>')
            else:
                cells.append('<td class="x"></td>')
        matrix_rows.append('<tr>' + ''.join(cells) + '</tr>')

    # Form cards
    cards = []
    for fm in forms_meta:
        fid   = fm['fid']
        title = form_inv.get(fid, {}).get('title', '') or fm.get('title', fid)
        evts  = form_to_evts.get(fid, [])
        evt_str = ', '.join(evts) if evts else '—'
        cards.append(f"""
        <a class="form-card" href="{fid}.html">
          <div class="card-id">{fid}</div>
          <div class="card-title">{title}</div>
          <div class="card-meta">{len(evts)} visit(s)</div>
        </a>""")

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{protocol} — OC Form Simulator</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: "Helvetica Neue", Arial, sans-serif;
           background: #eef2f7; color: #1e293b; }}

    .header {{
      background: linear-gradient(180deg, #005C87 0%, #004a6e 100%);
      color: white; padding: 18px 28px;
    }}
    .header h1 {{ font-size: 20px; font-weight: 700; }}
    .header .sub {{ font-size: 12px; opacity: 0.85; margin-top: 4px; }}
    .sim-badge {{
      background: #ff9800; color: white; font-size: 10px; font-weight: 700;
      padding: 2px 8px; border-radius: 10px; margin-left: 10px;
      letter-spacing: 0.5px; vertical-align: middle;
    }}

    .content {{ max-width: 1100px; margin: 0 auto; padding: 24px 20px 48px; }}

    h2 {{ font-size: 14px; font-weight: 700; color: #475569;
          text-transform: uppercase; letter-spacing: 0.5px;
          margin: 28px 0 12px; }}

    /* Form cards */
    .card-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
      gap: 12px; margin-bottom: 32px;
    }}
    .form-card {{
      background: white; border-radius: 6px; padding: 14px;
      text-decoration: none; color: inherit;
      border: 1px solid #e2e8f0;
      box-shadow: 0 1px 3px rgba(0,0,0,0.06);
      transition: box-shadow 0.15s, border-color 0.15s;
      display: block;
    }}
    .form-card:hover {{
      box-shadow: 0 4px 12px rgba(0,92,135,0.15);
      border-color: #005C87;
    }}
    .card-id {{ font-family: ui-monospace, Menlo, monospace;
                font-size: 13px; font-weight: 700; color: #005C87; }}
    .card-title {{ font-size: 12px; color: #334155; margin-top: 4px;
                   line-height: 1.35; }}
    .card-meta {{ font-size: 10px; color: #94a3b8; margin-top: 6px; }}

    /* SoE matrix */
    .matrix-wrap {{ overflow-x: auto; }}
    table.soe {{
      border-collapse: collapse; font-size: 11px;
      width: 100%; white-space: nowrap;
    }}
    table.soe th, table.soe td {{
      border: 1px solid #e2e8f0; padding: 5px 8px; vertical-align: middle;
    }}
    table.soe thead th {{
      background: #005C87; color: white; font-weight: 600;
      text-align: center;
    }}
    table.soe .form-cell {{
      text-align: left; background: #f8fafc;
      white-space: normal; min-width: 140px;
    }}
    table.soe .form-cell a {{ text-decoration: none; color: #005C87; }}
    table.soe .form-cell a:hover {{ text-decoration: underline; }}
    table.soe .sub {{ font-size: 10px; color: #64748b; }}
    table.soe .x {{ text-align: center; color: #cbd5e1; font-size: 12px; }}
    table.soe .x.active {{ color: #005C87; background: #eef7ff;
                           font-weight: 700; }}
    table.soe .oid {{ font-family: ui-monospace, Menlo, monospace;
                     font-size: 9px; opacity: 0.8; }}
    table.soe .tp {{ font-size: 10px; }}

    .note {{ font-size: 11px; color: #94a3b8; margin-top: 10px; }}
  </style>
</head>
<body>

<div class="header">
  <h1>{protocol} <span class="sim-badge">SIMULATOR</span></h1>
  <div class="sub">{study_title} &nbsp;·&nbsp;
    {len(forms_meta)} forms &nbsp;·&nbsp; {len(events)} events &nbsp;·&nbsp;
    Interactive preview — open any form to test constraints and relevance logic
  </div>
</div>

<div class="content">

  <h2>Forms ({len(forms_meta)})</h2>
  <div class="card-grid">
    {''.join(cards)}
  </div>

  <h2>Schedule of Events</h2>
  <div class="matrix-wrap">
    <table class="soe">
      <thead><tr>{matrix_head}</tr></thead>
      <tbody>{''.join(matrix_rows)}</tbody>
    </table>
  </div>
  <p class="note">
    ✓ = form is assigned to this event &nbsp;·&nbsp;
    Click a form name to open the interactive simulator &nbsp;·&nbsp;
    Cross-form references (instance('clinicaldata')) show blank — Phase 2
  </p>

</div>
</body>
</html>
"""


# ── ZIP packager ────────────────────────────────────────────────────────────────

def build_interactive_zip(forms_with_html: list, study_spec: dict,
                          protocol: str, grid_css: str) -> bytes:
    """
    Package all interactive form HTML files + index into a ZIP.

    Args:
        forms_with_html: list of dicts, each with:
            {'fm': <form meta>, 'raw_html': <enketo HTML string>}
        study_spec: the parsed study spec dict (events, form_to_events, etc.)
        protocol: protocol number string (used in filenames/titles)
        grid_css: content of grid.css

    Returns:
        ZIP bytes ready to upload to monday.com.
    """
    buf = io.BytesIO()
    folder = f'{protocol}_Form_Simulator'

    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:

        # Per-form HTML files
        for entry in forms_with_html:
            fm       = entry['fm']
            raw_html = entry['raw_html']
            fid      = fm['fid']
            html     = build_form_html(fm, raw_html, grid_css,
                                       index_href='index.html')
            zf.writestr(f'{folder}/{fid}.html',
                        html.encode('utf-8', errors='replace'))

        # Index page
        forms_meta = [e['fm'] for e in forms_with_html]
        index_html = build_index_html(forms_meta, study_spec, protocol)
        zf.writestr(f'{folder}/index.html',
                    index_html.encode('utf-8', errors='replace'))

        # README
        readme = (
            f"OC Form Simulator — {protocol}\n"
            f"{'=' * 50}\n\n"
            f"Open index.html in any modern browser to begin.\n\n"
            f"WHAT WORKS (Phase 1)\n"
            f"  - Field show/hide based on relevance conditions\n"
            f"  - Constraint error messages on field blur\n"
            f"  - Required field indicators\n"
            f"  - Real OpenClinica Enketo grid styling\n\n"
            f"WHAT DOESN'T YET (Phase 2)\n"
            f"  - Cross-form references (instance('clinicaldata')) "
            f"→ show blank\n"
            f"  - Complex XPath functions (date arithmetic, etc.)\n\n"
            f"Forms: {len(forms_meta)}\n"
        )
        zf.writestr(f'{folder}/README.txt', readme)

    buf.seek(0)
    return buf.getvalue()
