import asyncio, json, sys
sys.path.insert(0, '/app')
import uat_loader as U
SUB = 'bioivt'
STUDIES = {'Detroit': 'S_DETROITD_6724(TEST)', 'Precision': 'S_PRECISIO_1118(TEST)'}
async def main():
    tok = await U._get_oc_token(SUB, 'dswanker@openclinica.com')
    out = {}
    for label, study in STUDIES.items():
        parts = await U._list_participants(SUB, study, tok)
        res = {}
        for p in parts:
            key, oid = p.get('subjectKey'), p.get('subjectOid')
            if not key or not oid: continue
            lk = await U._fetch_clinical_data(SUB, study, oid, tok)
            res[key] = [[*k, v] for k, v in lk.items()]
        out[label] = res
        print(f"{label}: {len(parts)} participants listed; per-participant item counts: {{k[-4:]: len(v) for k, v in res.items()}}".replace('{k[-4:]: len(v) for k, v in res.items()}', str({k[-4:]: len(v) for k, v in res.items()})), file=sys.stderr)
    print("JSON_START" + json.dumps(out) + "JSON_END")
asyncio.run(main())
