// src/api/odmParser.js
// Parses CDISC ODM XML in the browser into a structured source tree
// Used to populate the left panel of the mapping workbench

/**
 * Parse ODM XML string into a source tree:
 * {
 *   studyName, sourceSystem, odm_version,
 *   forms: [{
 *     oid, name, repeating,
 *     item_groups: [{
 *       oid, name, repeating,
 *       items: [{
 *         oid, name, label, dataType, codeListOid,
 *         length, significantDigits, mandatory
 *       }]
 *     }]
 *   }],
 *   codelists: { [oid]: [{ codedValue, decode }] }
 * }
 */
export function parseODM(xmlString) {
  const parser = new DOMParser();
  const doc = parser.parseFromString(xmlString, "application/xml");

  const parseError = doc.querySelector("parsererror");
  if (parseError) throw new Error("Invalid XML: " + parseError.textContent.slice(0, 200));

  const odm = doc.querySelector("ODM");
  if (!odm) throw new Error("No ODM root element found");

  const study = doc.querySelector("Study");
  const studyName = study?.getAttribute("OID") || "Unknown Study";
  const sourceSystem = odm.getAttribute("Originator") || odm.getAttribute("SourceSystem") || "Unknown";
  const odmVersion = odm.getAttribute("ODMVersion") || "1.3";

  // Build item lookup: OID → item definition
  const itemDefs = {};
  doc.querySelectorAll("ItemDef").forEach(el => {
    const oid = el.getAttribute("OID");
    const question = el.querySelector("Question TranslatedText");
    const codeListRef = el.querySelector("CodeListRef");
    const aliases = [...el.querySelectorAll("Alias")];
    const cdashAlias = aliases.find(a => a.getAttribute("Context") === "CDASH")?.getAttribute("Name") || "";

    itemDefs[oid] = {
      oid,
      name: el.getAttribute("Name") || oid,
      label: question?.textContent?.trim() || el.getAttribute("Name") || oid,
      dataType: el.getAttribute("DataType") || "text",
      length: el.getAttribute("Length") || "",
      significantDigits: el.getAttribute("SignificantDigits") || "",
      codeListOid: codeListRef?.getAttribute("CodeListOID") || "",
      cdashAlias,
      mandatory: false, // filled in from ItemRef
    };
  });

  // Build item group lookup
  const itemGroupDefs = {};
  doc.querySelectorAll("ItemGroupDef").forEach(el => {
    const oid = el.getAttribute("OID");
    const name = el.getAttribute("Name") || oid;
    const repeating = el.getAttribute("Repeating") === "Yes";

    // Vendor extension: mdsol:IsLog
    const isLog = el.getAttributeNS("http://www.mdsol.com/ns/odm/metadata", "IsLog") === "Yes";

    const items = [];
    el.querySelectorAll("ItemRef").forEach(ref => {
      const itemOid = ref.getAttribute("ItemOID");
      const mandatory = ref.getAttribute("Mandatory") === "Yes";
      const orderNumber = parseInt(ref.getAttribute("OrderNumber") || "0", 10);
      if (itemDefs[itemOid]) {
        items.push({
          ...itemDefs[itemOid],
          mandatory,
          orderNumber,
        });
      }
    });

    items.sort((a, b) => a.orderNumber - b.orderNumber);

    itemGroupDefs[oid] = { oid, name, repeating: repeating || isLog, items };
  });

  // Build form list from FormDef
  const forms = [];
  doc.querySelectorAll("FormDef").forEach(el => {
    const oid = el.getAttribute("OID");
    const name = el.getAttribute("Name") || oid;
    const repeating = el.getAttribute("Repeating") === "Yes";

    const item_groups = [];
    el.querySelectorAll("ItemGroupRef").forEach(ref => {
      const igOid = ref.getAttribute("ItemGroupOID");
      if (itemGroupDefs[igOid]) {
        item_groups.push({ ...itemGroupDefs[igOid] });
      }
    });

    forms.push({ oid, name, repeating, item_groups });
  });

  // Build codelist lookup
  const codelists = {};
  doc.querySelectorAll("CodeList").forEach(el => {
    const oid = el.getAttribute("OID");
    const entries = [];
    el.querySelectorAll("CodeListItem, EnumeratedItem").forEach(item => {
      const codedValue = item.getAttribute("CodedValue") || "";
      const decode = item.querySelector("Decode TranslatedText")?.textContent?.trim() || codedValue;
      entries.push({ codedValue, decode });
    });
    codelists[oid] = entries;
  });

  return { studyName, sourceSystem, odmVersion, forms, codelists };
}

/**
 * Flatten source tree to a map of all items by OID for quick lookup.
 * Returns { [oid]: { ...item, formName, groupName } }
 */
export function buildItemIndex(sourceTree) {
  const index = {};
  (sourceTree?.forms || []).forEach(form => {
    (form.item_groups || []).forEach(group => {
      (group.items || []).forEach(item => {
        index[item.oid] = { ...item, formName: form.name, formOid: form.oid, groupName: group.name, groupOid: group.oid };
      });
    });
  });
  return index;
}

/**
 * Fetch the ODM XML from a Monday item's source_edc_export column.
 * Returns the raw XML string.
 */
export async function fetchODMFromMonday(itemId, token) {
  const SOURCE_COL = "file_mm2npqge"; // source_edc_export column

  const q = `query($ids:[ID!]!) {
    items(ids:$ids) {
      column_values(ids:["${SOURCE_COL}"]) {
        id
        ... on FileValue { files { asset_id name } }
      }
    }
  }`;

  const res = await fetch("https://api.monday.com/v2", {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: token },
    body: JSON.stringify({ query: q, variables: { ids: [itemId] } }),
  });
  const data = await res.json();
  if (data.errors) throw new Error(data.errors[0].message);

  const col = data?.data?.items?.[0]?.column_values?.[0];
  const files = col?.files;
  if (!files?.length) return null; // no ODM uploaded — that's OK

  const assetId = files[files.length - 1].asset_id;

  // Get download URL
  const assetRes = await fetch("https://api.monday.com/v2", {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: token },
    body: JSON.stringify({
      query: `query($ids:[ID!]!) { assets(ids:$ids) { public_url } }`,
      variables: { ids: [assetId] },
    }),
  });
  const assetData = await assetRes.json();
  const url = assetData?.data?.assets?.[0]?.public_url;
  if (!url) return null;

  const xmlRes = await fetch(url);
  if (!xmlRes.ok) throw new Error(`Failed to download ODM XML: ${xmlRes.status}`);
  return xmlRes.text();
}
