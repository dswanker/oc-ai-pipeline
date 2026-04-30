# Build Preview integration — apply guide (v2)

**Read me first.** The previous delivery I produced contained replacement
copies of `pipeline.py`, `monday_client.py`, `claude_client.py`, and `prompts.py`
that were based on much older versions of those files. **Do not use that
delivery.** This delivery is correct: it contains only NEW files plus
unified-diff patches against your real current files (1505-line pipeline.py
and 286-line monday_client.py).

## What's in this drop

| Path                                | Status   |  Notes                                                  |
|-------------------------------------|----------|---------------------------------------------------------|
| `build_preview/`                    | NEW      | Renderer module + vendored static assets               |
| `scripts/create_build_preview_column.py` | NEW | Already-run setup script (no-op if rerun)              |
| `Dockerfile`                        | NEW      | Microsoft Playwright base image                        |
| `requirements.txt.add`              | ADDS-TO  | Lines to merge into your existing requirements.txt     |
| `railway.toml.replace`              | REPLACES | Switches builder from nixpacks to dockerfile           |
| `monday_client.py.patch`            | PATCH    | +2 lines, 0 deletions                                  |
| `pipeline.py.patch`                 | PATCH    | +55 lines, 0 deletions                                 |
| `build_preview_integration.patch`   | COMBINED | Both above patches in one file                         |
| `INTEGRATION_PATCHES.md`            | DOCS     | Exact diffs explained, in case you'd rather edit by hand|

## Step-by-step apply

These commands assume you've already unzipped the delivery into `~/Downloads/`
and your repo is at `~/oc-ai-pipeline/`.

### 1. Verify your repo is at a clean checkpoint

You don't want to mix this with other in-progress work.

```bash
cd ~/oc-ai-pipeline
git status   # should be clean OR have only the small Step-1-from-last-time staged changes
```

If you still have the bad delivery's staged changes lingering:

```bash
git restore --staged .
git checkout -- pipeline.py monday_client.py claude_client.py prompts.py
```

After that, only files you genuinely want should appear in `git status`. The
two untracked files from before (`pipeline.py.bak2`, `register_all_skills.py`)
are fine to leave alone.

### 2. Drop in the new directories and Dockerfile

```bash
cp -R ~/Downloads/oc-ai-pipeline-build-preview/build_preview ~/oc-ai-pipeline/
cp -R ~/Downloads/oc-ai-pipeline-build-preview/scripts ~/oc-ai-pipeline/
cp ~/Downloads/oc-ai-pipeline-build-preview/Dockerfile ~/oc-ai-pipeline/
cp ~/Downloads/oc-ai-pipeline-build-preview/railway.toml.replace ~/oc-ai-pipeline/railway.toml
```

### 3. Merge requirements.txt

The new dependencies are: `playwright==1.49.0`, `pyxform>=2.0`, `openpyxl>=3.1`,
`pypdf>=4.0`. Open `~/oc-ai-pipeline/requirements.txt` and add any of these
lines that aren't already in it (`openpyxl` likely already is — your
pipeline.py imports it directly).

```bash
cat ~/Downloads/oc-ai-pipeline-build-preview/requirements.txt.add
# Open requirements.txt, append any missing lines, save
```

### 4. Apply the patches

```bash
cd ~/oc-ai-pipeline
patch -p0 < ~/Downloads/oc-ai-pipeline-build-preview/build_preview_integration.patch
```

The `patch` command should print "patching file pipeline.py" and "patching file
monday_client.py" with no rejection messages. If it complains about hunks not
applying, your `pipeline.py` has changed since I read it — let me know and we'll
regenerate.

### 5. Verify

```bash
cd ~/oc-ai-pipeline
# Check the new column ID is in place (already-set by the script you ran earlier)
grep build_preview monday_client.py
# Should show: "build_preview":     "file_mm2x1ey6",

# Check chain_e exists
grep "chain_e" pipeline.py | head -3
# Should show 3 lines: the function def, the launch in tasks list, and the task name

# Check that nothing in your existing code got removed
wc -l pipeline.py monday_client.py
# Expect:  1559 pipeline.py  /  288 monday_client.py
```

### 6. Commit and push

```bash
git status   # should show: modified pipeline.py, modified monday_client.py,
             #              modified railway.toml, modified requirements.txt,
             #              new files in build_preview/ and scripts/, new Dockerfile
git add build_preview scripts Dockerfile railway.toml requirements.txt \
        pipeline.py monday_client.py
git commit -m "Add Build Preview output (local renderer, no Claude API)"
git push
```

Railway will detect the new Dockerfile, build the image (~3-5 min first time),
and redeploy.

## How it integrates

The new `chain_e()` runs in parallel with your existing chains A-D. It's gated
by `_want("build preview")` — same dropdown the rest of your pipeline uses.

- If user selects **Build Preview** alongside other outputs (e.g.
  "Study Build Zip, Build Preview"): chain_c builds the zip, chain_e renders
  the preview from chain_c's output. They share `build_zip_holder[0]` so no
  re-downloading.
- If user selects **only Build Preview**: chain_e detects there's no build zip,
  triggers `_run_edc_and_dvs()` inline (the same function chain_c calls), then
  renders.
- The renderer reads the in-memory `struct_json` directly — no PDF parsing,
  no Monday round-trips for inputs.

Total cost per run: ~10s wall clock, **0 Claude tokens**.

## Testing

In Monday, on board 18409146946:

1. On an item with a protocol PDF attached (or with an existing struct_json
   from a previous run that produced Study Spec + EDC Build outputs):
2. In the `dropdown_mm2nc7d4` ("output_requested") column, select
   **Build Preview**
3. Set the AI trigger to **Send to AI**
4. Watch the AI Run Log. Expected sequence:
   - "Build Preview started."
   - (if needed) EDC build progress messages
   - "Build Preview complete — N bytes uploaded."
5. The new **Build Preview** column will contain
   `<protocol>_Build_Preview_<version>.pdf`
