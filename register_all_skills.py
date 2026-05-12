#!/usr/bin/env python3
"""
register_all_skills.py — Register every skill folder in skills/ with the
Anthropic Skills API, replacing any previously registered skill of the same
display_title.

Why this script exists:
  Skills are renamed in this repo (e.g., protocol-to-edc-structure →
  protocol-analysis). Renaming a local folder does NOT update what's
  registered in the API — that requires deleting the old skill and creating
  a new one. This script does that for every skill folder, idempotently.

Usage:
    cd ~/oc-ai-pipeline
    export ANTHROPIC_API_KEY=sk-ant-...
    python register_all_skills.py             # full run
    python register_all_skills.py --dry-run   # show what would happen
    python register_all_skills.py --only edc-builder protocol-analysis

After completion, skills_registry.json is written next to this script with
{ "<folder-name>": "skill_01...", ... } so the pipeline can look up IDs.
"""

import os, sys, json, argparse, zipfile, tempfile, re
from pathlib import Path

try:
    import anthropic
except ImportError:
    sys.exit("Please run: pip install --upgrade anthropic")

REPO_ROOT     = Path(__file__).parent.resolve()
SKILLS_ROOT   = REPO_ROOT / "skills"
REGISTRY_FILE = REPO_ROOT / "skills_registry.json"
BETA_HEADERS  = ["skills-2025-10-02"]


def read_skill_name(skill_md_path: Path) -> str:
    """Extract the 'name:' field from SKILL.md YAML frontmatter."""
    text = skill_md_path.read_text(encoding="utf-8")
    # Frontmatter is the block between the first two '---' lines
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise ValueError(f"No YAML frontmatter found in {skill_md_path}")
    fm = parts[1]
    m = re.search(r"^name:\s*(\S+)", fm, re.MULTILINE)
    if not m:
        raise ValueError(f"No 'name:' field in frontmatter of {skill_md_path}")
    return m.group(1).strip()


def discover_skill_folders(only=None):
    """Find every folder under skills/ that has a SKILL.md at top level."""
    folders = []
    if not SKILLS_ROOT.exists():
        sys.exit(f"Skills directory not found: {SKILLS_ROOT}")
    for d in sorted(SKILLS_ROOT.iterdir()):
        if not d.is_dir():
            continue
        if only and d.name not in only:
            continue
        skill_md = d / "SKILL.md"
        if not skill_md.exists():
            print(f"  SKIP {d.name}/ — no SKILL.md at top level")
            continue
        try:
            name = read_skill_name(skill_md)
        except ValueError as e:
            print(f"  SKIP {d.name}/ — {e}")
            continue
        folders.append((d, name))
    return folders


def make_skill_zip(folder: Path, tmpdir: str) -> Path:
    """
    Build a zip archive of the skill folder. The Skills API expects all files
    rooted under a single top-level directory matching the skill name.
    """
    zip_path = Path(tmpdir) / f"{folder.name}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in folder.rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                arcname = Path(folder.name) / path.relative_to(folder)
                zf.write(path, arcname.as_posix())
    return zip_path


def list_existing_skills(client) -> dict:
    """Return { display_title -> skill_id } for all custom skills in workspace."""
    out = {}
    cursor = None
    while True:
        kwargs = {"betas": BETA_HEADERS, "limit": 50}
        if cursor:
            kwargs["after_id"] = cursor
        page = client.beta.skills.list(**kwargs)
        for sk in page.data:
            # Only consider custom skills (skip pre-built anthropic ones)
            if getattr(sk, "source", None) == "custom" or sk.id.startswith("skill_"):
                out[sk.display_title] = sk.id
        if not getattr(page, "has_more", False):
            break
        cursor = page.last_id
    return out


def delete_skill_with_versions(client, skill_id: str):
    """Delete all versions of a skill, then delete the skill itself."""
    # List versions
    cursor = None
    deleted_versions = 0
    while True:
        kwargs = {"betas": BETA_HEADERS, "limit": 50}
        if cursor:
            kwargs["after_id"] = cursor
        page = client.beta.skills.versions.list(skill_id=skill_id, **kwargs)
        for v in page.data:
            client.beta.skills.versions.delete(
                skill_id=skill_id, version=v.version, betas=BETA_HEADERS,
            )
            deleted_versions += 1
        if not getattr(page, "has_more", False):
            break
        cursor = page.last_id
    # Now delete the skill
    client.beta.skills.delete(skill_id=skill_id, betas=BETA_HEADERS)
    return deleted_versions


def create_skill_from_zip(client, display_title: str, zip_path: Path):
    """Upload a skill zip and return the new skill_id."""
    with open(zip_path, "rb") as f:
        result = client.beta.skills.create(
            display_title=display_title,
            files=[f],
            betas=BETA_HEADERS,
        )
    return result.id


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="Show what would happen without making API calls")
    ap.add_argument("--only", nargs="*",
                    help="Limit to specific folder names")
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY env var not set")

    folders = discover_skill_folders(only=args.only)
    if not folders:
        sys.exit("No skill folders found")

    print(f"Discovered {len(folders)} skill folder(s):")
    for d, name in folders:
        print(f"  {d.name:30s}  (frontmatter name: {name})")
    print()

    if args.dry_run:
        print("DRY RUN — no API calls will be made")
        print("To actually run, omit --dry-run")
        return

    client = anthropic.Anthropic()

    print("Listing currently registered custom skills...")
    existing = list_existing_skills(client)
    print(f"  {len(existing)} skill(s) currently registered:")
    for title, sid in sorted(existing.items()):
        print(f"    {title:30s}  →  {sid}")
    print()

    registry = {}
    with tempfile.TemporaryDirectory() as tmpdir:
        for folder, name in folders:
            display_title = folder.name  # use folder name as display_title
            print(f"── {display_title} " + "─" * (50 - len(display_title)))

            # Step 1: delete prior registration if it exists
            # We match on display_title equal to folder name. If a previous
            # registration used the OLD folder name (e.g. protocol-to-edc-structure),
            # also try to find and delete that.
            old_titles_to_check = [display_title]
            # Map old → new for the renames
            rename_map = {
                "protocol-analysis":  "protocol-to-edc-structure",
                "protocol-summary":   "protocol-to-pricing-summary",
                "pricing-quote":      "pricing-model",
            }
            if display_title in rename_map:
                old_titles_to_check.append(rename_map[display_title])

            for title in old_titles_to_check:
                if title in existing:
                    sid = existing[title]
                    print(f"  Deleting existing skill '{title}' ({sid})...")
                    n = delete_skill_with_versions(client, sid)
                    print(f"    deleted {n} version(s) and the skill")

            # Step 2: build zip
            zip_path = make_skill_zip(folder, tmpdir)
            zip_size_mb = zip_path.stat().st_size / 1024 / 1024
            print(f"  Built {zip_path.name} ({zip_size_mb:.2f} MB)")
            if zip_size_mb > 30:
                print(f"  ⚠ EXCEEDS 30 MB API LIMIT — skipping")
                continue

            # Step 3: upload
            print(f"  Uploading to Skills API...")
            skill_id = create_skill_from_zip(client, display_title, zip_path)
            print(f"  ✓ Registered: {skill_id}")
            registry[display_title] = skill_id

    # Write registry
    REGISTRY_FILE.write_text(json.dumps(registry, indent=2))
    print()
    print(f"Wrote registry → {REGISTRY_FILE}")
    print()
    print("Final mapping:")
    for k, v in registry.items():
        print(f"  {k:30s}  →  {v}")


if __name__ == "__main__":
    main()
