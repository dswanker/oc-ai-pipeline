// src/App.jsx
import { useState, useEffect } from "react";
import { fetchSpec, saveSpec }         from "./api/monday";
import { fetchODMFromMonday, parseODM } from "./api/odmParser";
import { buildInitialMappings, mergeIntoSpec } from "./api/mappingEngine";
import { DEMO_SPEC }    from "./data/demoSpec";
import { DEMO_ODM_XML } from "./data/demoODM";
import LoadScreen       from "./components/LoadScreen";
import Header           from "./components/Header";
import Sidebar          from "./components/Sidebar";
import MappingTable     from "./components/MappingTable";
import FormPanel        from "./components/FormPanel";
import ChoicesPanel     from "./components/ChoicesPanel";
import MappingWorkbench from "./components/MappingWorkbench";
import Toast            from "./components/Toast";
import "./App.css";

function getParams() {
  const p = new URLSearchParams(window.location.search);
  return {
    itemId:  p.get("item")  || p.get("itemId")  || "",
    boardId: p.get("board") || p.get("boardId") || "",
    token:   p.get("token") || process.env.REACT_APP_MONDAY_TOKEN || "",
    demo:    p.get("demo") === "true" || !p.get("item"),
  };
}

export default function App() {
  const params = getParams();
  const [spec,       setSpec]       = useState(null);
  const [sourceTree, setSourceTree] = useState(null);
  const [mappings,   setMappings]   = useState({});
  const [loading,    setLoading]    = useState(true);
  const [loadError,  setLoadError]  = useState("");
  const [saving,     setSaving]     = useState(false);
  const [toast,      setToast]      = useState(null);
  const [activeForm, setActiveForm] = useState(0);
  const [activeTab,  setActiveTab]  = useState("mapping");
  const [dirty,      setDirty]      = useState(false);

  useEffect(() => {
    async function load() {
      if (params.demo) {
        const s = JSON.parse(JSON.stringify(DEMO_SPEC));
        let tree = null;
        try { tree = parseODM(DEMO_ODM_XML); } catch(e) { console.warn(e.message); }
        setSpec(s);
        setSourceTree(tree);
        setMappings(buildInitialMappings(tree, s));
        setLoading(false);
        return;
      }
      if (!params.itemId || !params.token) {
        setLoadError("Missing ?item= and ?token= URL parameters.");
        setLoading(false);
        return;
      }
      try {
        const [s, odmXml] = await Promise.all([
          fetchSpec(params.itemId, params.token),
          fetchODMFromMonday(params.itemId, params.token).catch(() => null),
        ]);
        let tree = null;
        if (odmXml) { try { tree = parseODM(odmXml); } catch(e) { console.warn(e); } }
        const existing = extractMappingsFromSpec(s);
        setSpec(s);
        setSourceTree(tree);
        setMappings(Object.keys(existing).length ? existing : buildInitialMappings(tree, s));
      } catch(e) { setLoadError(e.message); }
      setLoading(false);
    }
    load();
  }, []);

  function extractMappingsFromSpec(s) {
    const m = {};
    (s?.forms||[]).forEach(f => (f.survey||[]).forEach(r => { if (r.mapping) m[`${f.form_id}::${r.name}`] = r.mapping; }));
    return m;
  }

  function updateSpec(fn) { setSpec(prev => { const n=JSON.parse(JSON.stringify(prev)); fn(n); return n; }); setDirty(true); }
  function updateRow(fi,ri,field,val) { updateSpec(s => { s.forms[fi].survey[ri][field]=val; if(field==="name"&&s.forms[fi].survey[ri].completion_status==="PLACEHOLDER"){s.forms[fi].survey[ri].completion_status="COMPLETE";s.forms[fi].survey[ri].flag_reason="";} }); }
  function updateForm(fi,field,val)   { updateSpec(s => { s.forms[fi][field]=val; }); }
  function updateFormSettings(fi,field,val) { updateSpec(s => { if(!s.forms[fi].settings)s.forms[fi].settings={}; s.forms[fi].settings[field]=val; }); }
  function updateChoices(fi,c) { updateSpec(s => { s.forms[fi].choices=c; }); }
  function deleteRow(fi,ri)    { updateSpec(s => { s.forms[fi].survey.splice(ri,1); }); showToast("Field removed"); }
  function addRow(fi) { updateSpec(s => { const f=s.forms[fi]; f.survey.push({name:"NEW_FIELD",label:"New Field",bind__oc_itemgroup:f.form_id,type:"text",appearance:"",required:"",readonly:"",constraint:"",constraint_message:"",relevant:"",calculation:"",hint:"",bind__oc_briefdescription:"",bind__oc_description:"",completion_status:"PLACEHOLDER",library_source:"MANUAL",flag_reason:"Added manually",source_field:"",source_group:""}); }); }
  function addVisit(fi,v)    { updateSpec(s => { if(!s.forms[fi].visits_assigned)s.forms[fi].visits_assigned=[]; if(!s.forms[fi].visits_assigned.includes(v))s.forms[fi].visits_assigned.push(v); }); }
  function removeVisit(fi,vi){ updateSpec(s => { s.forms[fi].visits_assigned.splice(vi,1); }); }
  function updateMapping(key,m) { setMappings(prev=>({...prev,[key]:m})); setDirty(true); }

  async function handleSave(triggerBuild) {
    if (!spec) return;
    setSaving(true);
    try {
      const specWithMappings = mergeIntoSpec(spec, mappings);
      if (params.demo) {
        await new Promise(r=>setTimeout(r,600));
        showToast("Demo mode — would save to Monday.com");
        setDirty(false);
      } else {
        await saveSpec(specWithMappings, params.itemId, params.boardId, params.token, triggerBuild);
        showToast(triggerBuild ? "✓ Saved — build triggered" : "✓ Saved to Monday.com");
        setDirty(false);
      }
    } catch(e) { showToast(e.message||"Save failed","error"); }
    setSaving(false);
  }

  function showToast(msg, type="success") { setToast({msg,type}); setTimeout(()=>setToast(null),4000); }

  if (loading)   return <LoadScreen />;
  if (loadError) return <LoadScreen error={loadError} />;
  if (!spec)     return null;

  const form = spec.forms[activeForm];

  return (
    <div className="app">
      <Header spec={spec} mappings={mappings} dirty={dirty} saving={saving} isDemo={params.demo} onSave={handleSave} />
      <div className="app-body">
        <Sidebar forms={spec.forms} activeForm={activeForm} onSelect={i=>{setActiveForm(i);setActiveTab("mapping");}} />
        <div className="main-area">
          <div className="sub-tabs">
            <button className={`sub-tab ${activeTab==="mapping"?"on":""}`} onClick={()=>setActiveTab("mapping")}>↔ Mapping</button>
            <button className={`sub-tab ${activeTab==="fields"?"on":""}`}  onClick={()=>setActiveTab("fields")}>Field Detail <span className="sub-tab-count">{form.survey.length}</span></button>
            <button className={`sub-tab ${activeTab==="form"?"on":""}`}    onClick={()=>setActiveTab("form")}>Form Properties</button>
            <button className={`sub-tab ${activeTab==="choices"?"on":""}`} onClick={()=>setActiveTab("choices")}>Codelists <span className="sub-tab-count">{(form.choices||[]).length}</span></button>
          </div>
          {activeTab==="mapping"  && <MappingWorkbench spec={spec} formIdx={activeForm} sourceTree={sourceTree} mappings={mappings} onUpdateMapping={updateMapping} onUpdateRow={updateRow} showToast={showToast} />}
          {activeTab==="fields"   && <MappingTable     spec={spec} formIdx={activeForm} onUpdateRow={updateRow} onDeleteRow={deleteRow} onAddRow={addRow} onOpenChoices={()=>setActiveTab("choices")} showToast={showToast} />}
          {activeTab==="form"     && <FormPanel        spec={spec} formIdx={activeForm} onUpdateForm={updateForm} onUpdateSettings={updateFormSettings} onAddVisit={addVisit} onRemoveVisit={removeVisit} />}
          {activeTab==="choices"  && <ChoicesPanel     spec={spec} formIdx={activeForm} focusField={null} onUpdateChoices={updateChoices} />}
        </div>
      </div>
      {toast && <Toast msg={toast.msg} type={toast.type} />}
    </div>
  );
}
