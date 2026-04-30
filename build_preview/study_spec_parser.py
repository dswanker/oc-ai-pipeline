"""
Parse a protocol-analysis Study Specification PDF and extract:
  - Study metadata (Protocol Number, Study ID, Protocol Title)
  - All events (Section 2 timepoint CSV)
  - Form↔event assignments (Section 1 + cross-checked against Section 4)
  - Form metadata (Section 3 Form Inventory)

Used by the Build Preview renderer. Pure-Python; no external services.
"""
import re
import pypdf

# Section 4 field labels — used to bound the "Visits" value
FORM_FIELD_BOUNDS = ['Form OID', 'Form Title', 'Version', 'Style',
                     'Category', 'Visits', 'Repeating', 'Library Match',
                     'Data Items', 'Choice Lists', 'Dependencies']


def extract_text_from_bytes(pdf_bytes: bytes) -> str:
    import io
    r = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    return '\n'.join((p.extract_text() or '') for p in r.pages)


def slice_section(text: str, start_label: str, end_label: str = None) -> str:
    s = text.find(start_label)
    if s < 0:
        return ''
    if end_label:
        e = text.find(end_label, s + len(start_label))
        return text[s:e] if e > 0 else text[s:]
    return text[s:]


def parse_metadata(text: str) -> dict:
    out = {}
    for label in ('Protocol Number', 'Study ID', 'Protocol Title'):
        m = re.search(rf'{re.escape(label)}\s*\n([^\n]+)', text)
        if m:
            out[label] = m.group(1).strip()
    return out


def parse_events_from_section2(text: str) -> list:
    """Section 2 has rows of: SE_OID\\nTimepoint label\\n. Return ordered list."""
    sec2 = slice_section(text, 'SECTION 2', 'SECTION 3')
    lines = [l.strip() for l in sec2.splitlines() if l.strip()]
    events = []
    i = 0
    while i < len(lines):
        if re.fullmatch(r'SE_[A-Z_]+', lines[i]):
            oid = lines[i]
            tp_lines = []
            j = i + 1
            while j < len(lines) and not re.fullmatch(r'SE_[A-Z_]+', lines[j]):
                tp_lines.append(lines[j])
                j += 1
            events.append({'event': oid, 'timepoint': ' '.join(tp_lines)})
            i = j
        else:
            i += 1
    return events


def parse_event_to_forms_from_section1(text: str) -> dict:
    """Section 1 rows: SE_OID, timepoint (multi-line), Arm, Forms Assigned."""
    sec1 = slice_section(text, 'SECTION 1', 'SECTION 2')
    lines = [l.strip() for l in sec1.splitlines() if l.strip()]
    out = {}
    i = 0
    while i < len(lines):
        if re.fullmatch(r'SE_[A-Z_]+', lines[i]):
            oid = lines[i]
            j = i + 1
            # Walk to the arm line (e.g. TREATMENT, CONTROL — assumed all-caps)
            while j < len(lines) and not (lines[j] and re.fullmatch(r'[A-Z_]{4,}', lines[j].split()[0])):
                j += 1
            forms_lines = []
            k = j + 1
            while k < len(lines) and not re.fullmatch(r'SE_[A-Z_]+', lines[k]):
                forms_lines.append(lines[k])
                k += 1
            forms_blob = ' '.join(forms_lines)
            forms = [f.strip() for f in forms_blob.split(',') if f.strip()]
            forms = [f for f in forms if re.fullmatch(r'[A-Z][A-Z0-9_]*', f)]
            out[oid] = forms
            i = k
        else:
            i += 1
    return out


def parse_form_visits_from_section4(text: str) -> dict:
    """Section 4 has per-form blocks containing a 'Visits' field with comma-separated event OIDs."""
    sec4 = slice_section(text, 'SECTION 4', 'SECTION 5')
    out = {}
    for m in re.finditer(r'Form OID\s*\n([A-Z][A-Z0-9_]*)\s*\n', sec4):
        oid = m.group(1)
        rest = sec4[m.end():]
        next_form = re.search(r'Form OID\s*\n[A-Z][A-Z0-9_]*\s*\n', rest)
        chunk = rest[:next_form.start()] if next_form else rest
        v = re.search(r'\nVisits\s*\n', chunk)
        if not v:
            continue
        vstart = v.end()
        end_idx = None
        for fld in FORM_FIELD_BOUNDS:
            if fld == 'Visits':
                continue
            mm = re.search(rf'\n{re.escape(fld)}\s*\n', chunk[vstart:])
            if mm and (end_idx is None or mm.start() < end_idx):
                end_idx = mm.start()
        visits_blob = chunk[vstart: vstart + (end_idx or 200)]
        events = [e.strip() for e in visits_blob.replace('\n', ' ').split(',')]
        events = [e for e in events if re.fullmatch(r'SE_[A-Z_]+', e)]
        out[oid] = events
    return out


def parse_form_inventory_from_section3(text: str) -> dict:
    """Section 3 form inventory — extract title and category for each form OID."""
    sec3 = slice_section(text, 'SECTION 3', 'SECTION 4')
    out = {}
    lines = [l.strip() for l in sec3.splitlines()]
    CAT_TOKENS = {'ADMINISTRATIVE', 'CDASH', 'CDASH_SAFETY', 'CUSTOM',
                  'INFRA', 'INFRASTRUCTURE'}
    i = 0
    while i < len(lines):
        if (lines[i].isdigit() and i + 1 < len(lines)
                and re.fullmatch(r'[A-Z][A-Z0-9_]*', lines[i + 1])):
            oid = lines[i + 1]
            title_lines = []
            j = i + 2
            while j < len(lines) and lines[j] not in CAT_TOKENS:
                title_lines.append(lines[j])
                j += 1
            title = ' '.join(title_lines).strip()
            category = lines[j] if j < len(lines) else ''
            out[oid] = {'title': title, 'category': category}
            i = j + 1
        else:
            i += 1
    return out


def parse_study_spec_bytes(pdf_bytes: bytes) -> dict:
    """Top-level entry. Returns a dict with metadata, events, and form_to_events mapping."""
    text = extract_text_from_bytes(pdf_bytes)
    events = parse_events_from_section2(text)
    sec1_map = parse_event_to_forms_from_section1(text)
    sec4_map = parse_form_visits_from_section4(text)
    inventory = parse_form_inventory_from_section3(text)

    # Reconcile: merge both sources into one canonical mapping.
    form_to_events = {oid: set(evs) for oid, evs in sec4_map.items()}
    for ev_oid, forms in sec1_map.items():
        for f in forms:
            form_to_events.setdefault(f, set()).add(ev_oid)

    return {
        'metadata': parse_metadata(text),
        'events': events,
        'form_inventory': inventory,
        'form_to_events': {k: sorted(v) for k, v in form_to_events.items()},
    }
