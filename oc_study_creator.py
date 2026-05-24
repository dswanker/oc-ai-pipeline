"""
oc_study_creator.py — Thin shims for main.py's checkbox webhook handlers.

main.py imports two names from here:
    - publish_to_test_with_wait(item_id) — fires on the Publish to Test
      checkbox; orchestrates the publish-to-Test REST flow.
    - create_oc_study_with_forms(item_id) — legacy entry point; the
      real flow now runs inside pipeline.run_pipeline.

The heavy lifting (OC token fetch, study-environments lookup, version
publish) lives in pipeline.publish_to_test. This module exists only so
main.py's `from oc_study_creator import ...` line resolves and the
webhooks don't crash with ImportError.
"""
from __future__ import annotations

import traceback

from monday_client import COL, append_log, get_item, set_status


async def publish_to_test_with_wait(item_id: str) -> None:
    """Run the publish-to-Test flow with pipeline_status updates.

    pipeline.publish_to_test() is non-raising — it catches all errors
    and reports outcome via COL["published_status"] = "Published" /
    "Failed". We mirror that outcome into COL["pipeline_status"] so the
    main status column reflects the real state.
    """
    item_id = str(item_id)
    print(f"[oc_study_creator] publish_to_test_with_wait starting "
          f"for {item_id}", flush=True)

    try:
        await set_status(item_id, COL["pipeline_status"],
                         "Publishing to Test")

        # Lazy import — pipeline.py is heavy and we don't want to load
        # it just because main.py imported this module at startup.
        from pipeline import publish_to_test
        await publish_to_test(item_id)

        # publish_to_test wrote its outcome to COL["published_status"];
        # re-read to mirror it into pipeline_status.
        item = await get_item(item_id)
        cols = {cv["id"]: cv for cv in (item.get("column_values") or [])}
        published = (cols.get(COL["published_status"], {}).get("text")
                     or "").strip()

        if published == "Published":
            await set_status(item_id, COL["pipeline_status"],
                             "Published to Test")
            print(f"[oc_study_creator] publish_to_test_with_wait "
                  f"complete for {item_id} "
                  f"— pipeline_status='Published to Test'",
                  flush=True)
        else:
            await set_status(item_id, COL["pipeline_status"], "Failed")
            print(f"[oc_study_creator] publish_to_test_with_wait: "
                  f"published_status={published!r} "
                  f"— pipeline_status='Failed'", flush=True)
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        print(f"[oc_study_creator] publish_to_test_with_wait CRASHED "
              f"for {item_id}: {err}", flush=True)
        print(traceback.format_exc(), flush=True)
        try:
            await set_status(item_id, COL["pipeline_status"], "Failed")
            await append_log(item_id,
                             f"Publish to Test wrapper FAILED: {err}")
        except Exception as inner:
            print(f"[oc_study_creator] status-update fallback also "
                  f"failed: {inner}", flush=True)


async def create_oc_study_with_forms(item_id: str) -> None:
    """Legacy entry point — the real flow runs inside pipeline.run_pipeline.

    Kept as a no-op shim so main.py's webhook import resolves. If the
    Create OC Study checkbox is wired to this, point the user at the
    Send-to-AI trigger instead.
    """
    item_id = str(item_id)
    print(f"[oc_study_creator] create_oc_study_with_forms called for "
          f"{item_id} — this workflow is handled by the main pipeline. "
          f"Trigger 'Send to AI' instead.", flush=True)
