import asyncio, collections, sys
from urllib.parse import quote
import httpx, xml.etree.ElementTree as ET
sys.path.insert(0, '/Users/danswanker/oc-ai-pipeline')
import uat_loader as U
SUB, STUDY = 'bioivt', 'S_PRECISIO_1118(TEST)'
NS = '{http://www.cdisc.org/ns/odm/v1.3}'
async def main():
    tok = await U._get_oc_token(SUB, 'dswanker@openclinica.com')
    print("token obtained:", bool(tok))
    base = U._pages_base(SUB)
    for subj in ('*',):
        url = f"{base}/pages/auth/api/clinicaldata/{quote(STUDY, safe='')}/{subj}/*/*?clinicalData=n&includeMetadata=y&includeDN=n&includeAudits=n&showArchived=n"
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.get(url, headers={'Authorization': f'Bearer {tok}', 'Accept': 'application/xml'})
        print("metadata GET:", r.status_code, len(r.text), "bytes")
        if not r.is_success: print(r.text[:300]); return
        open('/Users/danswanker/oc-ai-pipeline/tmp/precision_bioivt_metadata.xml', 'w').write(r.text)
    root = ET.fromstring(r.text)
    items = {i.get('OID'): i.get('Name') for i in root.iter(NS + 'ItemDef')}
    groups = {g.get('OID'): (g.get('Name'), [x.get('ItemOID') for x in g.findall(NS + 'ItemRef')]) for g in root.iter(NS + 'ItemGroupDef')}
    forms = {f.get('OID'): (f.get('Name'), [x.get('ItemGroupOID') for x in f.findall(NS + 'ItemGroupRef')]) for f in root.iter(NS + 'FormDef')}
    print(f"metadata: {len(forms)} forms, {len(groups)} item groups, {len(items)} items")
    print("\nWHAT OPENCLINICA ACTUALLY ASSIGNED (form -> item group -> first items):")
    for f in ('F_DM', 'F_DMONC', 'F_IE8200', 'F_IE1009', 'F_IE6504', 'F_CM'):
        if f not in forms: print(f"  {f}: not in metadata"); continue
        for g in forms[f][1]:
            its = groups.get(g, ('', []))[1]
            print(f"  {f:9s} group {g:18s} {len(its):>2} items: {[ (i, items.get(i)) for i in its[:3] ]}")
    print("\nthe loader/DVS expected, e.g. for F_DMONC: group IG_DEMOG_DMONC, item I_DEMOG_DIN, I_DEMOG_VISIT")
    for want in ('I_DEMOG_DIN', 'I_DEMOG_VISIT', 'IG_DEMOG_DMONC', 'IG_DEMOG_DM'):
        print(f"   {want}: item exists={want in items} group exists={want in groups}", end='')
        owner = [f for f, (_, gs) in forms.items() if any(want == g or want in groups.get(g, ('', []))[1] for g in gs)]
        print(f" | belongs to form(s): {owner}")
    dup = collections.Counter(n for n in items.values() if n in ('DIN', 'VISIT', 'IEINC01'))
    print("\nhow many ItemDefs share the name:", dict(dup))
asyncio.run(main())
