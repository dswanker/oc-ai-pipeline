"""
register_skills.py — One-time skill registration script.

Run this locally (not on Railway) to upload your custom skills to the
Anthropic Skills API and get back their skill IDs.

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    cd ~/oc-ai-pipeline
    python register_skills.py

Copy the printed skill IDs into Railway environment variables:
    SKILL_ID_PROTOCOL_ANALYSIS
    SKILL_ID_PRICING_QUOTE
    SKILL_ID_EDC_BUILDER
    SKILL_ID_DVS_SPECIFICATION
"""

import anthropic, os, zipfile, io, sys
from pathlib import Path

SKILLS_DIR = Path(__file__).parent / "skills"

SKILLS = [
    # (folder_name, display_title, env_var_name)
    ("protocol-analysis",  "Protocol Analysis",  "SKILL_ID_PROTOCOL_ANALYSIS"),
    ("pricing-quote",      "Pricing Quote",       "SKILL_ID_PRICING_QUOTE"),
    ("edc-builder",        "EDC Builder",         "SKILL_ID_EDC_BUILDER"),
    ("dvs-specification",  "DVS Specification",   "SKILL_ID_DVS_SPECIFICATION"),
]


def zip_skill_folder(folder_path: Path) -> bytes:
    """Zip a skill folder in memory and return the bytes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(folder_path.rglob("*")):
            if file.is_file():
                arcname = file.relative_to(folder_path.parent)
                zf.write(file, arcname)
    return buf.getvalue()


def register_skill(client, folder_name, display_title):
    folder_path = SKILLS_DIR / folder_name
    if not folder_path.exists():
        print(f"  ✗ Folder not found: {folder_path}")
        return None

    print(f"  Zipping {folder_path} ...", end=" ", flush=True)
    zip_bytes = zip_skill_folder(folder_path)
    print(f"{len(zip_bytes) // 1024}KB")

    print(f"  Uploading to Skills API ...", end=" ", flush=True)
    skill = client.beta.skills.create(
        display_title=display_title,
        files=[("skill.zip", io.BytesIO(zip_bytes))],
        betas=["skills-2025-10-02"],
    )
    print(f"done")
    return skill


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    print("\n=== OpenClinica Skill Registration ===\n")

    results = {}
    for folder_name, display_title, env_var in SKILLS:
        print(f"Registering: {display_title}")
        skill = register_skill(client, folder_name, display_title)
        if skill:
            results[env_var] = skill.id
            print(f"  ✓ ID: {skill.id}  version: {skill.latest_version}\n")
        else:
            print(f"  ✗ Skipped\n")

    print("\n=== Add these to Railway environment variables ===\n")
    for env_var, skill_id in results.items():
        print(f"{env_var}={skill_id}")

    print("\n=== Done ===\n")


if __name__ == "__main__":
    main()
