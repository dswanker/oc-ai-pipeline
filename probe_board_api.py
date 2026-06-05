#!/usr/bin/env python3
"""
probe_board_api.py — One-off probe of the OC designer API surface.

Discovers which endpoints are available for reading and clearing board
content. READ-ONLY (only GET requests); safe to run against production.

Run with:
    railway run python probe_board_api.py
"""
from __future__ import annotations

import os
import sys

import httpx


# ── Config ──────────────────────────────────────────────────────────────
AUTH_HOST = "https://cust1.build.openclinica.io"
BASE      = "https://cust1.design.openclinica.io"
BOARD_ID  = "wQyCTnJFKjyGMQ9d9"
# Board targeted by the PATCH probe (set-default-version). Picked
# explicitly by the operator so the read-only GET probe loop above
# can still target the original BOARD_ID for backstory context.
PATCH_BOARD_ID = "DMjtshj8C8sC8yLgc"


def _find_creds() -> tuple[str, str, str]:
    """Pick the first env-var pair that's set; return (user, pw, source)."""
    candidates = [
        ("OC_API_USERNAME",   "OC_API_PASSWORD"),    # production pipeline
        ("OC_SERVICE_EMAIL",  "OC_SERVICE_PASSWORD"),
        ("OC_USERNAME",       "OC_PASSWORD"),
    ]
    for u_var, p_var in candidates:
        u = os.environ.get(u_var, "").strip()
        p = os.environ.get(p_var, "").strip()
        if u and p:
            return u, p, f"{u_var}/{p_var}"
    return "", "", ""


def get_token() -> str:
    """Auth via the same flow pipeline._get_oc_token uses."""
    user, pw, source = _find_creds()
    if not user:
        print("ERROR: No credentials found. Checked OC_API_USERNAME / "
              "OC_SERVICE_EMAIL / OC_USERNAME pairs.", file=sys.stderr)
        print(f"Env vars present matching OC_*: "
              f"{sorted(k for k in os.environ if k.startswith('OC_'))}",
              file=sys.stderr)
        sys.exit(1)

    print(f"[auth] using credentials from {source}")
    url = f"{AUTH_HOST}/user-service/api/oauth/token"
    with httpx.Client(timeout=30) as c:
        r = c.post(url,
                   headers={"Content-Type": "application/json"},
                   json={"username": user, "password": pw})
    if r.status_code != 200:
        print(f"[auth] FAILED {r.status_code}: {r.text[:300]}",
              file=sys.stderr)
        sys.exit(2)
    token = r.text.strip()
    print(f"[auth] OK ({len(token)} chars)")
    return token


def probe(client: httpx.Client, url: str) -> int:
    """Issue one GET, print status + body snippet. Return status code (0 on error)."""
    print(f"\n── GET {url}")
    try:
        r = client.get(url, timeout=10)
    except Exception as e:
        print(f"   EXCEPTION: {type(e).__name__}: {e}")
        return 0
    body = r.text or ""
    print(f"   status: {r.status_code}")
    print(f"   body:   {body[:500]!r}")
    return r.status_code


def _pick_target_card(client: httpx.Client) -> tuple[str, int] | None:
    """GET PATCH_BOARD_ID and return (card_id, version_id) for the
    first non-archived card with at least one version. Returns None if
    the board GET fails or no eligible card is found."""
    url = f"{BASE}/api/boards/{PATCH_BOARD_ID}"
    print(f"\n── lookup target card via GET {url}")
    r = client.get(url, timeout=15)
    print(f"   status: {r.status_code}")
    if r.status_code != 200:
        print(f"   body:   {r.text[:300]!r}")
        return None
    data = r.json()
    cards = data.get("cards") or []
    for c in cards:
        if c.get("archived"):
            continue
        versions = c.get("versions") or []
        if not versions:
            continue
        vid = versions[0].get("id")
        cid = c.get("_id")
        if cid and isinstance(vid, int):
            print(f"   picked card _id={cid} formOcoid={c.get('formOcoid')!r} "
                  f"title={c.get('title')!r}")
            print(f"   picked version id={vid} ocoid={versions[0].get('ocoid')!r}")
            return cid, vid
    print("   no eligible card (non-archived, ≥1 version) found")
    return None


def _request_dump(method: str, url: str, client: httpx.Client,
                  json_body: dict | None = None) -> int:
    """Issue one request, print status + headers + body snippet."""
    print(f"\n── {method} {url}")
    if json_body is not None:
        print(f"   body sent: {json_body}")
    try:
        r = client.request(method, url, json=json_body, timeout=15)
    except Exception as e:
        print(f"   EXCEPTION: {type(e).__name__}: {e}")
        return 0
    print(f"   status: {r.status_code}")
    # Surface Allow / Content-Type — useful for OPTIONS and verb-rejected calls.
    for h in ("allow", "content-type", "x-error-message"):
        if h in r.headers:
            print(f"   header {h}: {r.headers[h]}")
    body = r.text or ""
    print(f"   body:   {body[:800]!r}")
    return r.status_code


def probe_set_default_version(client: httpx.Client) -> None:
    """Probe candidate REST endpoints for setting a card's default version.

    Order tried:
      1. OPTIONS /api/boards/{board_id}/cards/{card_id} — discover verbs
      2. PATCH   /api/boards/{board_id}/cards/{card_id}  body {"_version": <int>}
      3. PUT     /api/boards/{board_id}/cards/{card_id}  body {"_version": <int>}
      4. PATCH   /api/boards/{board_id}  body {"_id": cardId, "_version": <int>}
         (fallback proposed by the operator if /cards/ subresource is 404)
    """
    print("\n══ PATCH probe: set default version ════════════════════════")
    picked = _pick_target_card(client)
    if not picked:
        print("   → cannot proceed without a target card")
        return
    card_id, version_id = picked

    card_url  = f"{BASE}/api/boards/{PATCH_BOARD_ID}/cards/{card_id}"
    board_url = f"{BASE}/api/boards/{PATCH_BOARD_ID}"
    payload_card_only  = {"_version": version_id}
    payload_with_card  = {"_id": card_id, "_version": version_id}

    # 1. OPTIONS — tells us which verbs the server admits.
    _request_dump("OPTIONS", card_url, client)

    # 2. PATCH on /cards/{id} — the proposed primary endpoint.
    patch_status = _request_dump("PATCH", card_url, client,
                                  json_body=payload_card_only)
    if patch_status in (200, 204):
        print("\n   ✓ PATCH /cards/{id} succeeded — this is the endpoint")
        return

    # 3. PUT on /cards/{id} — second-choice verb on the same resource.
    if patch_status in (404, 405):
        put_status = _request_dump("PUT", card_url, client,
                                    json_body=payload_card_only)
        if put_status in (200, 204):
            print("\n   ✓ PUT /cards/{id} succeeded — use PUT instead of PATCH")
            return

    # 4. Fallback: PATCH the board with the card identifier in the body.
    if patch_status == 404:
        print("\n   /cards/{id} returned 404 — trying board-level PATCH")
        _request_dump("PATCH", board_url, client,
                      json_body=payload_with_card)


def main() -> None:
    token = get_token()
    headers = {"Authorization": f"Bearer {token}"}

    urls = [
        f"{BASE}/api/boards/{BOARD_ID}",
        f"{BASE}/api/board/{BOARD_ID}",
        f"{BASE}/1/boards/{BOARD_ID}",
        f"{BASE}/1/boards/{BOARD_ID}/lists",
        f"{BASE}/1/boards/{BOARD_ID}/lists?cards=all",
        f"{BASE}/api/importStudy/{BOARD_ID}",   # GET instead of POST
        f"{BASE}/api/studies/{BOARD_ID}",
    ]

    successes: list[str] = []
    with httpx.Client(headers=headers, follow_redirects=False) as client:
        for url in urls:
            code = probe(client, url)
            if code == 200:
                successes.append(url)

        # PATCH probe is the new addition — reuses the same client/token.
        probe_set_default_version(client)

    print("\n══ SUMMARY ══════════════════════════════════════════════════")
    print(f"endpoints probed:    {len(urls)}")
    print(f"endpoints returning 200: {len(successes)}")
    for u in successes:
        print(f"  ✓ {u}")
    if not successes:
        print("  (none — see per-URL status codes above)")


if __name__ == "__main__":
    main()
