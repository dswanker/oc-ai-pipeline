// src/App.jsx
//
// Syndeo top-level. The app was previously a spec-editing UI driven
// by Claude-generated mock fixtures; that flow has been retired in
// favor of a focused gap-analysis review surface. MappingWorkbench
// is self-loading (reads ?item_id= from the URL and fetches from
// /api/gap-report/{item_id}) so this file is intentionally minimal —
// it exists to give CRA an entry point and render the workbench.
//
// The other components (FormPanel, ChoicesPanel, MappingTable,
// ExpressionEditor, TransformPanel, Sidebar, Header, LoadScreen,
// Toast) still live under src/components/ but are unreferenced from
// here. They're kept on disk so anything we want to recover from the
// fixture UI is one git revert away; a follow-up cleanup commit can
// delete them when the gap-report flow has settled.

import MappingWorkbench from "./components/MappingWorkbench";
import "./App.css";

export default function App() {
  return <MappingWorkbench />;
}
