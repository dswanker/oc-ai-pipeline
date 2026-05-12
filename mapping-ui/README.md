# OC EDC Mapping Review UI

React web application for reviewing and editing EDC migration field mappings before triggering the OC4 build.

## What it does

- Loads a Study Spec JSON from a Monday.com item (via the `file_mm2gefht` column)
- Displays all form-level and item-level spec fields in an editable table
- DM edits field names, labels, types, constraints, codelists, visit assignments
- Saves the reviewed spec back to Monday.com and optionally triggers the build

## URL parameters

| Param | Required | Description |
|-------|----------|-------------|
| `item` | Yes | Monday.com item ID (e.g. `11985196614`) |
| `board` | No | Monday.com board ID — needed for triggering build |
| `token` | Yes | Monday.com API token |
| `demo` | No | Set to `true` to load sample data without Monday |

**Example:**
```
https://mapping.yourdomain.railway.app/?item=11985196614&board=18409146946&token=eyJ...
```

## Running locally

```bash
npm install
npm start
# Opens http://localhost:3000?demo=true
```

## Deploy to Railway

1. Create a new Railway service in the `oc-ai-pipeline` project
2. Point it at the `mapping-ui/` directory (or a separate repo)
3. Railway auto-detects `railway.toml` and builds with nixpacks
4. Set env var `REACT_APP_MONDAY_TOKEN` if you want a default token

## How Monday.com integration works

### Loading the spec
1. Fetches item column values to find the latest file asset ID in `file_mm2gefht`
2. Gets the download URL via the `assets` API
3. Downloads and parses the JSON

### Saving back
1. POSTs the reviewed JSON to `https://api.monday.com/v2/file` as a file upload
2. Attaches it to the `file_mm2gefht` column
3. If "Save & Build" was clicked, also sets the status column (`color_mm2h9g3m`) to `"Send to AI"` to trigger the pipeline

## Column IDs (Services Study AI Hub board)

| Purpose | Column ID |
|---------|-----------|
| Study Spec JSON | `file_mm2gefht` |
| AI Status trigger | `color_mm2h9g3m` |
| Source EDC System | `dropdown_mm382w7d` |

## Project structure

```
src/
  App.jsx              # Root — URL param loading, state, mutations
  App.css              # Full design system
  api/
    monday.js          # fetchSpec(), saveSpec(), Monday API calls
  components/
    Header.jsx         # Top bar with stats and save buttons
    Sidebar.jsx        # Form list with flagged/placeholder counts
    MappingTable.jsx   # Main field-level table (all 19 columns)
    FormPanel.jsx      # Form-level properties tab
    ChoicesPanel.jsx   # Codelist editor tab
  data/
    demoSpec.js        # Full CV3001 demo spec for ?demo=true
```
