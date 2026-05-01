"""
build_preview/interactive.py — Phase 1 + 2 Interactive Form Simulator

Phase 1: show/hide, constraints, required — live in every form
Phase 2: cross-form references, pulldata, today(), floor(), once(), concat(),
         if(), div, substr, mock data store persisted in localStorage so values
         entered in one form appear in calculations on other forms.
"""
import io
import re
import zipfile


# ── Interactive CSS ────────────────────────────────────────────────────────────

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

/* ── Constraint / required messages ──────────────────────────────────── */
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

/* ── Calculations / preloads (auto-filled) ────────────────────────────── */
#or-calculated-items, #or-preload-items { display: none !important; }

/* ── Auto-filled field indicator ─────────────────────────────────────── */
input.sim-autofilled, select.sim-autofilled {
  background: #f0f9ff !important;
  border-color: #38bdf8 !important;
  font-style: italic;
  color: #0369a1;
}

/* ── Invalid field highlight ─────────────────────────────────────────── */
.field-invalid > input, .field-invalid > select, .field-invalid > textarea {
  border-color: #f87171 !important;
  box-shadow: 0 0 0 2px rgba(248,113,113,0.2) !important;
}
"""


# ── Phase 2 JavaScript ────────────────────────────────────────────────────────
# Full XPath-subset evaluator + mock data store + cross-form resolver

INTERACTIVE_JS = r"""
/* OC Form Simulator — Phase 2
   XPath evaluator, mock data store, cross-form reference resolution */
(function () {
'use strict';

// ═══════════════════════════════════════════════════════════════════════════
// 1.  MOCK DATA STORE  (localStorage-backed, shared across all form pages)
// ═══════════════════════════════════════════════════════════════════════════

var STORE_KEY = 'oc_sim_v2';

var Store = {
  _cache: null,

  _load: function () {
    if (this._cache) return this._cache;
    try {
      var raw = localStorage.getItem(STORE_KEY);
      this._cache = raw ? JSON.parse(raw) : {};
    } catch (e) { this._cache = {}; }
    return this._cache;
  },

  _save: function () {
    try { localStorage.setItem(STORE_KEY, JSON.stringify(this._cache)); }
    catch (e) {}
  },

  get: function (key) { return this._load()[key]; },

  set: function (key, val) {
    this._load()[key] = val;
    this._save();
  },

  // Store a field value entered by the user: key = "FORM_OID.FIELD_NAME"
  setField: function (formOid, fieldName, val) {
    var d = this._load();
    if (!d.userValues) d.userValues = {};
    d.userValues[formOid + '.' + fieldName] = val;
    // Also store just by fieldName for cross-form lookup convenience
    d.userValues[fieldName] = val;
    this._save();
  },

  getField: function (key) {
    var d = this._load();
    var uv = d.userValues || {};
    if (uv[key] !== undefined) return uv[key];
    // Fall back to mock defaults
    var db = window.__mockDB || {};
    var fd = db.formDefaults || {};
    // Try all forms
    for (var fid in fd) {
      if (fd[fid][key] !== undefined) return fd[fid][key];
    }
    return '';
  },

  // Current event context (set by navigator or URL param)
  currentEvent: function () {
    return this.get('currentEvent') ||
      (window.__mockDB && window.__mockDB.currentEvent) || '';
  },

  setCurrentEvent: function (oid) { this.set('currentEvent', oid); },
};

// ═══════════════════════════════════════════════════════════════════════════
// 2.  CROSS-FORM / EXTERNAL REFERENCE RESOLVER
// ═══════════════════════════════════════════════════════════════════════════

var DB = window.__mockDB || {};

function resolveInstance(expr) {
  // instance('clinicaldata')/ODM/ClinicalData/@StudyOID
  if (/ClinicalData\/@StudyOID/.test(expr)) return DB.studyId || '';

  // instance('clinicaldata')/ODM/ClinicalData/SubjectData/@OpenClinica:StudySubjectID
  if (/SubjectData\/@OpenClinica:StudySubjectID/.test(expr))
    return Store.getField('SUBJID') || DB.subjectId || 'SUBJ-001';

  // instance('clinicaldata')/ODM/ClinicalData/UserInfo/@OpenClinica:UserRole
  if (/UserInfo\/@OpenClinica:UserRole/.test(expr)) return 'Data Entry Person';

  // @StudyEventOID when [@OpenClinica:Current='Yes']
  if (/OpenClinica:Current='Yes'\]\/@StudyEventOID/.test(expr))
    return Store.currentEvent();

  // @OpenClinica:StartDate when current event
  if (/OpenClinica:Current='Yes'\].*@OpenClinica:StartDate/.test(expr))
    return DB.startDate || DB.today || '';

  // ItemData[@ItemOID='FORM.FIELD']/@Value — specific event
  var specificM = expr.match(
    /StudyEventData\[@StudyEventOID='([^']+)'\].*?ItemData\[@ItemOID='([^']+)'\]\/@Value/
  );
  if (specificM) return Store.getField(specificM[2]);

  // ItemData[@ItemOID='FORM.FIELD']/@Value — current event
  var currentM = expr.match(
    /OpenClinica:Current='Yes'\].*?ItemData\[@ItemOID='([^']+)'\]\/@Value/
  );
  if (currentM) return Store.getField(currentM[1]);

  // @ItemGroupRepeatKey (for once() repeat counter)
  if (/@ItemGroupRepeatKey/.test(expr)) return '1';

  return '';
}

function resolvePulldata(csvName, returnCol, matchCol, matchVal) {
  // pulldata('{study_id}_tpt', 'timepoint', 'event', currentEvent)
  if (returnCol === 'timepoint' && matchCol === 'event') {
    var tpts = DB.timepoints || {};
    return tpts[matchVal] || matchVal || '';
  }
  // pulldata('labranges', 'lower'/'upper'/'unit', 'test_code', 'WBC')
  if (csvName === 'labranges' || csvName.indexOf('labrange') === 0) {
    var lr = (DB.labranges || {})[matchVal] || {};
    return lr[returnCol] || '';
  }
  return '';
}

// ═══════════════════════════════════════════════════════════════════════════
// 3.  XPATH-SUBSET EVALUATOR
// ═══════════════════════════════════════════════════════════════════════════

// Cache for once() results: expression → value
var _onceCache = {};

function getVal(name) {
  // First check DOM (user may have typed into this session)
  var sel = '[name$="/' + name + '"]';
  var els = document.querySelectorAll(sel);
  if (!els.length) els = document.querySelectorAll('[data-name$="/' + name + '"]');
  if (els.length) {
    if (els[0].type === 'radio' || els[0].type === 'checkbox') {
      return Array.from(els).filter(function (e) { return e.checked; })
                            .map(function (e) { return e.value; }).join(' ');
    }
    if (els[0].value) return els[0].value;
  }
  // Fall back to store
  return Store.getField(name);
}

function getCurVal(el) {
  var q = el && el.closest('.question, .or-group, .or-repeat');
  if (!q) return el ? (el.value || '') : '';
  var first = q.querySelector('input, select, textarea');
  if (!first) return '';
  if (first.type === 'radio' || first.type === 'checkbox') {
    return Array.from(q.querySelectorAll('input:checked'))
                .map(function (i) { return i.value; }).join(' ');
  }
  return first.value || '';
}

function evalXPath(expr, curEl) {
  if (!expr) return true;
  expr = expr.trim();

  // ── once(inner) — evaluate inner once and cache ──────────────────────
  var onceM = expr.match(/^once\((.+)\)$/s);
  if (onceM) {
    var inner = onceM[1].trim();
    if (_onceCache[inner] !== undefined) return _onceCache[inner];
    var v = evalXPath(inner, curEl);
    _onceCache[inner] = v;
    return v;
  }

  // ── instance('clinicaldata') — delegate to resolver ──────────────────
  if (expr.indexOf("instance('clinicaldata')") === 0 ||
      expr.indexOf('instance("clinicaldata")') === 0) {
    return resolveInstance(expr);
  }

  // ── substr(expr, start, length) ───────────────────────────────────────
  expr = expr.replace(
    /substr\s*\(\s*(instance\([^)]+\)[^,]+),\s*(\d+),\s*(\d+)\s*\)/g,
    function (_, inner, start, len) {
      var s = resolveInstance(inner.trim());
      // XPath substr is 1-indexed
      return JSON.stringify(s.substr(parseInt(start, 10) - 1, parseInt(len, 10)));
    }
  );

  // ── pulldata('csv', returnCol, matchCol, matchExpr) ───────────────────
  expr = expr.replace(
    /pulldata\s*\(\s*'([^']+)'\s*,\s*'([^']+)'\s*,\s*'([^']+)'\s*,\s*([^)]+)\)/g,
    function (_, csv, retCol, matchCol, matchExprRaw) {
      var matchVal = evalXPath(matchExprRaw.trim(), curEl);
      return JSON.stringify(resolvePulldata(csv, retCol, matchCol, String(matchVal)));
    }
  );

  // ── Replace ${field} with value ───────────────────────────────────────
  expr = expr.replace(/\$\{([^}]+)\}/g, function (_, name) {
    return JSON.stringify(getVal(name.trim()));
  });

  // ── Current-node dot ─────────────────────────────────────────────────
  if (curEl) {
    var cv = JSON.stringify(getCurVal(curEl));
    expr = expr.replace(/(^|[^a-zA-Z0-9_'".])\.((?=[^a-zA-Z0-9_])|$)/g,
      function (m, pre, post) { return pre + cv + post; });
  }

  // ── today() ──────────────────────────────────────────────────────────
  expr = expr.replace(/\btoday\s*\(\s*\)/g,
    JSON.stringify(DB.today || new Date().toISOString().slice(0, 10)));

  // ── XPath → JS operator translations ─────────────────────────────────
  expr = expr
    .replace(/\band\b/g, '&&')
    .replace(/\bor\b/g,  '||')
    .replace(/\bnot\s*\(/g, '!(')
    .replace(/\btrue\s*\(\s*\)/g,  'true')
    .replace(/\bfalse\s*\(\s*\)/g, 'false')
    .replace(/\bdiv\b/g, '/')   // XPath div → JS /
    .replace(/\bmod\b/g, '%');  // XPath mod → JS %

  // ── XPath functions → JS ─────────────────────────────────────────────
  // selected(expr, 'val')
  expr = expr.replace(
    /selected\s*\(\s*([^,]+),\s*'([^']+)'\s*\)/g,
    function (_, fExpr, val) {
      return '(' + fExpr.trim() + ').split(" ").indexOf("' + val + '") !== -1';
    }
  );
  // string-length(expr)
  expr = expr.replace(/string-length\s*\(\s*([^)]+)\s*\)/g,
    function (_, f) { return '(' + f.trim() + ').length'; });
  // number(expr)
  expr = expr.replace(/\bnumber\s*\(\s*([^)]+)\s*\)/g,
    function (_, f) { return '(parseFloat(' + f.trim() + ')||0)'; });
  // floor(expr)
  expr = expr.replace(/\bfloor\s*\(/g, 'Math.floor(');
  // ceiling(expr)
  expr = expr.replace(/\bceiling\s*\(/g, 'Math.ceil(');
  // round(expr)
  expr = expr.replace(/\bround\s*\(/g, 'Math.round(');
  // concat(a, b, ...)
  expr = expr.replace(/\bconcat\s*\(/g, '[').replace(/\)(\s*(&&|\|\||==|!=|$))/, '].join("")$1');
  // if(cond, then, else) — XPath if() → JS ternary
  expr = expr.replace(/\bif\s*\(\s*([^,]+),\s*([^,]+),\s*([^)]+)\)/g,
    function (_, cond, then, els) {
      return '((' + cond + ') ? ' + then + ' : ' + els + ')';
    }
  );
  // string(expr)
  expr = expr.replace(/\bstring\s*\(\s*([^)]+)\s*\)/g,
    function (_, f) { return 'String(' + f.trim() + ')'; });
  // abs(expr)
  expr = expr.replace(/\babs\s*\(\s*([^)]+)\s*\)/g,
    function (_, f) { return 'Math.abs(' + f.trim() + ')'; });

  try {
    /* jshint evil: true */
    return Function('"use strict"; return (' + expr + ')')(); // eslint-disable-line
  } catch (e) {
    return null;  // null = failed to evaluate; callers handle this explicitly
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// 4.  VISIBILITY, CONSTRAINTS, CALCULATIONS
// ═══════════════════════════════════════════════════════════════════════════

function updateVisibility() {
  document.querySelectorAll('.or-branch').forEach(function (branch) {
    var expr = branch.getAttribute('data-relevant');
    if (!expr) return;
    var _vis = evalXPath(expr, null);
    if (_vis === null) return;
    branch.style.display = _vis ? '' : 'none';
  });
}

function runCalculations() {
  // Find calculate rows (hidden inputs with data-calculate)
  document.querySelectorAll('[data-calculate]').forEach(function (el) {
    var expr = el.getAttribute('data-calculate');
    if (!expr) return;
    try {
      var val = evalXPath(expr, el);
      if (val === null || val === undefined) return;
      var target = el.querySelector('input, select, textarea') || el;
      if (target.type === 'radio' || target.type === 'checkbox') return;
      var strVal = (val === true) ? 'true' : (val === false) ? 'false'
                 : (val != null ? String(val) : '');
      if (target.value !== strVal) {
        target.value = strVal;
        target.classList.add('sim-autofilled');
      }
    } catch (e) {}
  });
}

function checkConstraint(inputEl) {
  var q = inputEl.closest('.question, .or-group');
  if (!q) return;
  var cAttr = inputEl.getAttribute('data-constraint') ||
              q.getAttribute('data-constraint');
  var msgEl = q.querySelector('.or-constraint-msg');
  if (!cAttr || !msgEl) return;
  if (!inputEl.value && inputEl.type !== 'checkbox' && inputEl.type !== 'radio') {
    msgEl.style.display = 'none'; return;
  }
  var passes = evalXPath(cAttr, inputEl);
  if (passes === null) return;  // can't evaluate — don't flag
  msgEl.style.display = passes ? 'none' : 'inline-block';
  q.classList.toggle('field-invalid', !passes);
}

function checkRequired(inputEl) {
  var q = inputEl.closest('.question, .or-group');
  if (!q) return;
  var reqAttr = inputEl.getAttribute('data-required') ||
                q.getAttribute('data-required');
  var msgEl = q.querySelector('.or-required-msg');
  if (!reqAttr || !msgEl) return;
  if (reqAttr === 'true()' || reqAttr === '1' || reqAttr === 'true') {
    var empty = !inputEl.value ||
      ((inputEl.type === 'checkbox' || inputEl.type === 'radio') && !inputEl.checked);
    msgEl.style.display = empty ? 'inline-block' : 'none';
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// 5.  FIELD VALUE PERSISTENCE
// ═══════════════════════════════════════════════════════════════════════════

var FORM_OID = window.__formOid || '';

function saveField(inputEl) {
  var nameAttr = inputEl.getAttribute('name') || '';
  // Extract field name from /data/FIELDNAME path
  var m = nameAttr.match(/\/([^/]+)$/);
  var fieldName = m ? m[1] : nameAttr;
  if (!fieldName) return;

  var val;
  if (inputEl.type === 'radio' || inputEl.type === 'checkbox') {
    if (!inputEl.checked) return;
    val = inputEl.value;
  } else {
    val = inputEl.value;
  }
  Store.setField(FORM_OID, fieldName, val);
}

function prePopulateFromStore() {
  // Pre-populate fields that have stored/mock values
  document.querySelectorAll('input, select, textarea').forEach(function (el) {
    if (el.type === 'radio' || el.type === 'checkbox') return; // skip
    var nameAttr = el.getAttribute('name') || '';
    var m = nameAttr.match(/\/([^/]+)$/);
    var fieldName = m ? m[1] : nameAttr;
    if (!fieldName || el.value) return;
    var stored = Store.getField(fieldName);
    if (stored) {
      el.value = stored;
      el.classList.add('sim-autofilled');
    }
  });
}

// ═══════════════════════════════════════════════════════════════════════════
// 6.  EVENT CONTEXT FROM URL
// ═══════════════════════════════════════════════════════════════════════════

function initEventContext() {
  var params = new URLSearchParams(window.location.search);
  var ev = params.get('event');
  if (ev) Store.setCurrentEvent(ev);
}

// ═══════════════════════════════════════════════════════════════════════════
// 7.  WIRE-UP
// ═══════════════════════════════════════════════════════════════════════════

document.addEventListener('change', function (e) {
  var t = e.target;
  if (!t || !['INPUT','SELECT','TEXTAREA'].includes(t.tagName)) return;
  saveField(t);
  updateVisibility();
  runCalculations();
  checkConstraint(t);
  checkRequired(t);
});

document.addEventListener('blur', function (e) {
  var t = e.target;
  if (!t || !['INPUT','SELECT','TEXTAREA'].includes(t.tagName)) return;
  checkConstraint(t);
  checkRequired(t);
}, true);

// Hide all validation messages at load
document.querySelectorAll('.or-constraint-msg, .or-required-msg')
  .forEach(function (el) { el.style.display = 'none'; });

// Initialise
initEventContext();
prePopulateFromStore();
runCalculations();
updateVisibility();

// Debug handle
window.__sim = {
  evalXPath:        evalXPath,
  resolveInstance:  resolveInstance,
  resolvePulldata:  resolvePulldata,
  updateVisibility: updateVisibility,
  runCalculations:  runCalculations,
  Store:            Store,
};

})();
"""


# ── Per-form HTML builder ──────────────────────────────────────────────────────

def build_form_html(fm: dict, raw_form_html: str, grid_css: str,
                    mock_db_js: str, index_href: str = 'index.html') -> str:
    """
    Build a fully self-contained interactive HTML page for one form.

    Phase 2 additions vs Phase 1:
      - mock_db_js embedded so the form knows all defaults/timepoints/labranges
      - window.__formOid set so field saves are scoped to the right form
      - ?event= URL param read at load to set current event context
    """
    title    = fm.get('title',   fm.get('fid', ''))
    form_id  = fm.get('fid',     '')
    version  = fm.get('version', '1')
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
      &nbsp;·&nbsp; Constraints, relevance and cross-form references live</div>
  </div>
  <a class="back-link" href="{index_href}">← All Forms</a>
</div>

<div class="form-wrapper">
  {raw_form_html}
</div>

<script>
/* ── Mock data (generated by pipeline from struct_json) ── */
{mock_db_js}
window.__formOid = {repr(form_oid)};
</script>
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
    Index page with clickable SoE matrix.
    Each cell links to FormOID.html?event=SE_OID so the form picks up
    the correct event context on load.
    """
    events       = study_spec.get('events', [])
    form_to_evts = study_spec.get('form_to_events', {})
    form_inv     = study_spec.get('form_inventory', {})
    metadata     = study_spec.get('metadata', {})
    study_title  = metadata.get('Protocol Title', '')

    # Build set of forms that exist as HTML files
    available = {fm['fid'] for fm in forms_meta}

    matrix_head = '<th class="form-col">Form</th>' + ''.join(
        f'<th><div class="oid">{e["event"]}</div>'
        f'<div class="tp">{e["timepoint"]}</div></th>'
        for e in events
    )

    matrix_rows = []
    for fm in forms_meta:
        fid   = fm['fid']
        title = form_inv.get(fid, {}).get('title', '') or fm.get('title', fid)
        assigned = set(form_to_evts.get(fid, []))
        cells = [f'<td class="form-col"><a href="{fid}.html">'
                 f'<b>{fid}</b><br><span class="sub">{title}</span></a></td>']
        for e in events:
            if e['event'] in assigned:
                href = f'{fid}.html?event={e["event"]}'
                cells.append(f'<td class="x active"><a href="{href}">✓</a></td>')
            else:
                cells.append('<td class="x"></td>')
        matrix_rows.append('<tr>' + ''.join(cells) + '</tr>')

    cards = []
    for fm in forms_meta:
        fid   = fm['fid']
        title = form_inv.get(fid, {}).get('title', '') or fm.get('title', fid)
        evts  = form_to_evts.get(fid, [])
        cards.append(f"""
        <a class="form-card" href="{fid}.html">
          <div class="card-id">{fid}</div>
          <div class="card-title">{title}</div>
          <div class="card-meta">{len(evts)} event(s)</div>
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
    .content {{ max-width: 1200px; margin: 0 auto; padding: 24px 20px 48px; }}
    h2 {{ font-size: 13px; font-weight: 700; color: #475569;
          text-transform: uppercase; letter-spacing: 0.5px;
          margin: 28px 0 12px; }}
    .card-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(175px, 1fr));
      gap: 10px; margin-bottom: 32px;
    }}
    .form-card {{
      background: white; border-radius: 6px; padding: 14px;
      text-decoration: none; color: inherit;
      border: 1px solid #e2e8f0;
      box-shadow: 0 1px 3px rgba(0,0,0,0.06);
      transition: box-shadow 0.15s, border-color 0.15s; display: block;
    }}
    .form-card:hover {{
      box-shadow: 0 4px 12px rgba(0,92,135,0.15); border-color: #005C87;
    }}
    .card-id {{ font-family: ui-monospace, Menlo, monospace;
                font-size: 13px; font-weight: 700; color: #005C87; }}
    .card-title {{ font-size: 12px; color: #334155; margin-top: 4px;
                   line-height: 1.35; }}
    .card-meta {{ font-size: 10px; color: #94a3b8; margin-top: 6px; }}
    .matrix-wrap {{ overflow-x: auto; }}
    table.soe {{
      border-collapse: collapse; font-size: 11px; width: 100%;
    }}
    table.soe th, table.soe td {{
      border: 1px solid #e2e8f0; padding: 5px 8px; vertical-align: middle;
    }}
    table.soe thead th {{
      background: #005C87; color: white; font-weight: 600; text-align: center;
      white-space: nowrap;
    }}
    table.soe .form-col {{
      text-align: left; background: #f8fafc;
      white-space: normal; min-width: 130px;
    }}
    table.soe .form-col a {{ text-decoration: none; color: #005C87; }}
    table.soe .form-col a:hover {{ text-decoration: underline; }}
    table.soe .sub {{ font-size: 10px; color: #64748b; }}
    table.soe .x {{ text-align: center; color: #cbd5e1; font-size: 11px; }}
    table.soe .x.active {{ color: #005C87; background: #eef7ff;
                           font-weight: 700; }}
    table.soe .x.active a {{ color: #005C87; text-decoration: none; }}
    table.soe .x.active a:hover {{ text-decoration: underline; }}
    .oid {{ font-family: ui-monospace, Menlo, monospace;
            font-size: 9px; opacity: 0.8; display: block; }}
    .tp  {{ font-size: 10px; display: block; }}
    .note {{ font-size: 11px; color: #94a3b8; margin-top: 10px; }}
    .tip {{
      background: #f0f9ff; border: 1px solid #bae6fd;
      border-radius: 6px; padding: 12px 16px; margin-bottom: 24px;
      font-size: 12px; color: #0369a1;
    }}
    .tip b {{ color: #005C87; }}
    .reset-btn {{
      float: right; background: #fee2e2; color: #991b1b;
      border: 1px solid #fecaca; border-radius: 4px;
      padding: 4px 12px; font-size: 11px; cursor: pointer;
    }}
    .reset-btn:hover {{ background: #fecaca; }}
  </style>
</head>
<body>

<div class="header">
  <h1>{protocol} <span class="sim-badge">SIMULATOR</span></h1>
  <div class="sub">{study_title} &nbsp;·&nbsp;
    {len(forms_meta)} forms &nbsp;·&nbsp; {len(events)} events
  </div>
</div>

<div class="content">

  <div class="tip">
    <button class="reset-btn" onclick="localStorage.clear();location.reload()">
      Reset session
    </button>
    <b>How to use:</b> Open any form and fill in values — they persist across
    forms in this browser session so cross-form references (SUBJID, AGE, etc.)
    auto-populate. &nbsp;·&nbsp; Click any ✓ in the matrix to open a form
    pre-loaded with that visit's event context. &nbsp;·&nbsp; Use "Reset session"
    to clear all entered values and start fresh.
  </div>

  <h2>Forms ({len(forms_meta)})</h2>
  <div class="card-grid">{''.join(cards)}</div>

  <h2>Schedule of Events — click ✓ to open form in visit context</h2>
  <div class="matrix-wrap">
    <table class="soe">
      <thead><tr>{matrix_head}</tr></thead>
      <tbody>{''.join(matrix_rows)}</tbody>
    </table>
  </div>
  <p class="note">
    ✓ = form assigned to this event &nbsp;·&nbsp;
    Click ✓ to open with the correct event pre-loaded &nbsp;·&nbsp;
    Light blue fields = auto-populated from mock data or previous forms
  </p>

</div>
</body>
</html>
"""


# ── ZIP packager ────────────────────────────────────────────────────────────────

def build_interactive_zip(forms_with_html: list, study_spec: dict,
                          protocol: str, grid_css: str,
                          mock_db_js: str = '') -> bytes:
    """
    Package all interactive form HTML files + index into a ZIP.

    Args:
        forms_with_html: list of {'fm': meta, 'raw_html': enketo_html_str}
        study_spec: parsed study spec dict
        protocol: protocol number string
        grid_css: content of grid.css
        mock_db_js: the window.__mockDB = {...}; JS string (Phase 2)

    Returns: ZIP bytes
    """
    buf    = io.BytesIO()
    folder = f'{protocol}_Form_Simulator'

    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:

        for entry in forms_with_html:
            fm       = entry['fm']
            raw_html = entry['raw_html']
            fid      = fm['fid']
            html     = build_form_html(fm, raw_html, grid_css,
                                       mock_db_js, index_href='index.html')
            zf.writestr(f'{folder}/{fid}.html',
                        html.encode('utf-8', errors='replace'))

        forms_meta = [e['fm'] for e in forms_with_html]
        index_html = build_index_html(forms_meta, study_spec, protocol)
        zf.writestr(f'{folder}/index.html',
                    index_html.encode('utf-8', errors='replace'))

        readme = (
            f"OC Form Simulator — {protocol}\n"
            f"{'=' * 50}\n\n"
            f"Open index.html in Chrome or Firefox to begin.\n\n"
            f"WHAT WORKS\n"
            f"  - Field show/hide based on relevance (data-relevant XPath)\n"
            f"  - Constraint error messages on blur/change\n"
            f"  - Required field indicators\n"
            f"  - Cross-form references: SUBJID, AGE, WEIGHT etc. auto-fill\n"
            f"    across forms once entered (localStorage-backed)\n"
            f"  - pulldata() timepoint and lab range lookups\n"
            f"  - today(), floor(), concat(), if(), once(), div, substr\n"
            f"  - Event context: click a visit in the matrix to open a form\n"
            f"    pre-loaded with that event's context\n"
            f"  - Light blue fields = auto-populated from mock defaults\n\n"
            f"LIMITATIONS\n"
            f"  - Complex date arithmetic may not fully evaluate\n"
            f"  - Repeating groups use a fixed mock repeat key of 1\n\n"
            f"RESET\n"
            f"  Click 'Reset session' on the index page to clear all\n"
            f"  entered values and start fresh.\n\n"
            f"Forms included: {len(forms_meta)}\n"
        )
        zf.writestr(f'{folder}/README.txt', readme)

    buf.seek(0)
    return buf.getvalue()
