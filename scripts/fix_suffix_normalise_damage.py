#!/usr/bin/env python3
"""
fix_suffix_normalise_damage.py — one-off corrective script for the
BioIVT Precision (PRECISIO_NEW) board.

Root cause (confirmed via git history):
  - June 1 2026, commit ad41770 ("FIX: propagate uploaded form OID+version
    to all sibling cards") correctly solved "No form version defined for
    Form X" by repointing every sibling card to the OID+version the
    pipeline actually just uploaded (the "Layer 1" logic in the
    batch/set-default phase).
  - June 5 2026, commit 9c494a1 added a "suffix-normalise" step earlier in
    the same upload loop that unconditionally strips a form's numeric
    suffix and repoints the card to the bare name, assuming the bare name
    is always a real, already-registered identity. For this board, it
    is not: the bare names are permanently claimed by old "DO NOT USE"
    studies for this same customer (form-service OIDs are scoped
    per-customer, not per-study, and there is no delete/archive API to
    free them). So suffix-normalise silently disconnects the card from
    the ONLY real, versioned form record it has, every single run.

This script performs the same repoint Layer 1 would have, using the
REAL suffixed OID + version ocoid captured from tonight's own upload
logs, with an actual callback (unlike the original suffix-normalise
call, which passes {} instead of a callback and so can never detect
failure).

Usage:
    python scripts/fix_suffix_normalise_damage.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from test_publisher import _find_session  # noqa: E402

BOARD_URL = "https://bioivt.design.openclinica.io/b/v8ezXJGSxfiX3ABjq/precisio_new"

# card_id -> (correct real formOcoid, correct real version ocoid)
# Taken directly from tonight's "sibling-map: X -> oid=... ver=..." log
# lines and the /cards/update card_id values from the original (correct)
# getForm-time card update earlier tonight.
CORRECTIONS = {
    "TX5eqSGtnEpujARv2": ("F_IE8200_8031", "F_IE8200_8031_2026091318"),
    "BqZPRZpBHHHp3pJta": ("F_QSCOG_7447",  "F_QSCOG_7447_2026091318"),
    "5r9XvDC8XTKA98Djc": ("F_ATT_3709",    "F_ATT_3709_2026091318"),
    "oCSgSHocpnsSHsfXF": ("F_IE8009_7796", "F_IE8009_7796_2026091318"),
    "MMSqbEQ2tD3FLkmaa": ("F_IE4800_1079", "F_IE4800_1079_2026091318"),
    "xBJGHpZt524X3xSPZ": ("F_QSALS_1731",  "F_QSALS_1731_2026091318"),
    "32GNkENyC8WMyJNZc": ("F_GEN_8282",    "F_GEN_8282_2026091318"),
    "Z8GgiuoS5GXz2gYge": ("F_IE1009_7030", "F_IE1009_7030_2026091318"),
    "WYj8eELyfPNKwvQNk": ("F_QSPARK_9409", "F_QSPARK_9409_2026091318"),
    "xQrGua8eR2MuzpsB7": ("F_IE5400_6268", "F_IE5400_6268_2026091318"),
    "2iZ5KvRrdXM5P7vJ7": ("F_QSMS_9643",   "F_QSMS_9643_2026091318"),
    "zqHtr5reQ6LvwG2c4": ("F_IE8008_4105", "F_IE8008_4105_2026091318"),
    "2ZfWCq4PBLN54MoWB": ("F_IE8011_853",  "F_IE8011_853_2026091318"),
    "xR34vzGsxwoZE24Jt": ("F_DMONC_6256",  "F_DMONC_6256_2026091318"),
    "aPmZyRrAyNQAk9ebx": ("F_PCONC_4207",  "F_PCONC_4207_2026091318"),
    "epomeigF4B3K5eemG": ("F_IE6504_357",  "F_IE6504_357_2026091318"),
    "4biRFLR2x9GZEMWGf": ("F_BIOMK_3949",  "F_BIOMK_3949_2026091318"),
    "wjet5wiLhgEfxN3A8": ("F_IE8010_3684", "F_IE8010_3684_2026091318"),
}


async def main() -> None:
    session_path = _find_session()
    if not session_path:
        print("❌ No session file found.", file=sys.stderr)
        sys.exit(2)
    print(f"Using session: {session_path}")

    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            ctx = await browser.new_context(storage_state=session_path)
            page = await ctx.new_page()
            print(f"Navigating to board: {BOARD_URL}")
            await page.goto(BOARD_URL, wait_until="networkidle", timeout=60000)
            try:
                await page.wait_for_function(
                    "() => typeof Meteor !== 'undefined' "
                    "&& Meteor.status().connected",
                    timeout=20000)
                print("✅ Meteor client connected")
            except Exception:
                print("⚠️  Meteor client not confirmed connected — "
                      "attempting anyway")

            args = [[cid, oid, ver] for cid, (oid, ver) in CORRECTIONS.items()]
            results = await page.evaluate(
                """(cards) => Promise.all(cards.map(([cardId, oid, ver]) =>
                    new Promise((resolve) => {
                        Meteor.call(
                            '/cards/update',
                            {_id: cardId},
                            {$set: {
                                formOcoid: oid,
                                selected_form_version_ocoid: ver,
                                dateLastActivity: {$date: Date.now()}
                            }},
                            {},
                            (err) => resolve({
                                cardId: cardId, oid: oid,
                                ok: !err,
                                err: err ? String(err) : null
                            })
                        );
                    })
                ))""",
                args,
            )

            ok_count = 0
            for r in results:
                status = "✅" if r.get("ok") else "❌"
                print(f"{status} {r['cardId']} -> {r['oid']}"
                      + ("" if r.get("ok") else f"  ERROR: {r.get('err')}"))
                if r.get("ok"):
                    ok_count += 1

            print(f"\n{ok_count}/{len(CORRECTIONS)} cards corrected successfully.")
            if ok_count < len(CORRECTIONS):
                print("Some corrections failed — do NOT retry publish until "
                      "all 18 succeed.", file=sys.stderr)
                sys.exit(1)
        finally:
            await browser.close()

    print("\n✅ All 18 cards corrected. Try Publish again now.")


if __name__ == "__main__":
    asyncio.run(main())
