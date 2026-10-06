import collections, json, re, openpyxl
D = json.load(open('docs/uat_analysis/participant_data.json'))
def run(label, path):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    rows = list(wb['UAT_Cases'].iter_rows(values_only=True)); h = [str(c or '').strip() for c in rows[2]]
    cases = [dict(zip(h, r)) for r in rows[3:] if r[0]]
    per = {pk: {tuple(r[:4]): r[4] for r in rs} for pk, rs in D[label].items()}
    orig = collections.Counter(str(c['Test Result']) for c in cases)
    new = collections.Counter(); still = []; flipped = collections.Counter(); oidprob = 0
    for c in cases:
        res, act = str(c['Test Result']), str(c['Actual Result'])
        if act.startswith('Import failed'): new['Fail (item-ID mismatch)'] += 1; oidprob += 1; continue
        if res not in ('Pass', 'Fail'): new[res] += 1; continue
        key = (str(c['Study_Event_OID']).upper(), str(c['Form_OID']).upper(), str(c['Item_Group_OID']).upper(), str(c['Item_OID']).upper())
        stored = per.get(str(c['Participant_Key']), {}).get(key)
        lv = str(c['Load_Value'] or '').strip()
        if stored is not None and str(stored).strip() == lv:
            new['Pass'] += 1
            if res == 'Fail': flipped['Fail -> Pass'] += 1
        else:
            new['Fail (real mismatch)'] += 1; still.append((c, stored))
    print(f"\n######## {label}")
    print("  as reported by the loader      :", dict(orig))
    print("  re-scored, participant-aware   :", dict(new), "| flipped from Fail to Pass:", dict(flipped))
    why = collections.Counter()
    for c, st in still:
        lv = str(c['Load_Value']).strip()
        why['stored value absent for that participant' if st is None else ('value was transformed/rounded' if re.sub(r'\D', '', str(st)) == re.sub(r'\D', '', lv) else 'different value stored')] += 1
    print("  remaining real mismatches by kind:", dict(why))
    for c, st in still[:6]:
        print(f"     {str(c['UAT Case ID'])[:8]} {str(c['Form_OID'])[:11]:11s} {str(c['Item_OID'])[-22:]:22s} {str(c['Participant_ID'])[-4:]} load={str(c['Load_Value'])[:22]!r} stored={str(st)[:22]!r} scen={str(c['Scenario'])[:30]!r}")
run('Detroit', 'docs/uat_analysis/detroit_bioivt_uat_results.xlsx'); run('Precision', 'docs/uat_analysis/precision_bioivt_uat_results.xlsx')
