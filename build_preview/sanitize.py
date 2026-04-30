"""
Workaround for a known edc-builder bug where some XLSForm `survey` sheets
emit *_PHANTOM end_group rows with no matching begin_group. pyxform rejects
those forms outright. This sanitizer strips those rows so the renderer can
display the form. The proper fix belongs in edc-builder itself.
"""
import openpyxl


def sanitize_xlsform_bytes(src_bytes: bytes) -> bytes:
    """Take XLSForm .xlsx bytes, return cleaned .xlsx bytes."""
    import io
    src = io.BytesIO(src_bytes)
    wb = openpyxl.load_workbook(src)
    ws = wb['survey']
    rows = list(ws.iter_rows(values_only=False))
    if not rows:
        out = io.BytesIO()
        wb.save(out)
        return out.getvalue()

    headers = [c.value for c in rows[0]]
    name_idx = headers.index('name') if 'name' in headers else None
    type_idx = headers.index('type') if 'type' in headers else None
    if name_idx is None or type_idx is None:
        out = io.BytesIO()
        wb.save(out)
        return out.getvalue()

    rows_to_delete = []
    for i, row in enumerate(rows[1:], start=2):
        name = row[name_idx].value or ''
        type_v = (row[type_idx].value or '').strip()
        if 'PHANTOM' in str(name) and type_v in ('end_group', 'end group', 'begin_group', 'begin group'):
            rows_to_delete.append(i)

    for r in reversed(rows_to_delete):
        ws.delete_rows(r, 1)

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def get_form_settings_bytes(xlsform_bytes: bytes) -> dict:
    import io
    wb = openpyxl.load_workbook(io.BytesIO(xlsform_bytes), read_only=True)
    if 'settings' not in wb.sheetnames:
        return {}
    s = wb['settings']
    rows = list(s.iter_rows(values_only=True))
    if not rows or len(rows) < 2:
        return {}
    return dict(zip(rows[0], rows[1]))
