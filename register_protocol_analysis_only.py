"""One-off: upload a new version of the protocol-analysis skill."""
import anthropic, os, zipfile, io, sys
from pathlib import Path

SKILLS_DIR = Path(__file__).parent / "skills"
FOLDER = "protocol-analysis"
SKILL_ID = "skill_01VoEEkRHuNQKo8V9B4YAbPE"  # existing protocol-analysis skill

def zip_skill_folder(folder_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(folder_path.rglob("*")):
            if file.is_file():
                arcname = file.relative_to(folder_path.parent)
                zf.write(file, arcname)
    return buf.getvalue()

def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    folder_path = SKILLS_DIR / FOLDER

    print(f"Zipping {folder_path}...", end=" ", flush=True)
    zip_bytes = zip_skill_folder(folder_path)
    print(f"{len(zip_bytes) // 1024}KB")

    print("Uploading new version to Skills API...", end=" ", flush=True)
    version = client.beta.skills.versions.create(
        skill_id=SKILL_ID,
        files=[("skill.zip", io.BytesIO(zip_bytes))],
        betas=["skills-2025-10-02"],
    )
    print("done")
    print(f"\n=== NEW VERSION ===")
    print(f"Skill ID:    {SKILL_ID}  (unchanged — no env var update needed)")
    print(f"New version: {getattr(version, 'version', None) or version}\n")

if __name__ == "__main__":
    main()
