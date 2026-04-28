"""
Smoke tests — confirm the package imports cleanly and core types
have the shape we expect.

Once individual modules are implemented, add real unit tests next to
each (test_form_parser.py, test_matcher.py, etc.).
"""
from __future__ import annotations

import pytest


def test_imports() -> None:
    """All modules import without error."""
    import app.config  # noqa: F401
    import app.deps  # noqa: F401
    import app.main  # noqa: F401
    import app.routes.health  # noqa: F401
    import app.routes.ingest  # noqa: F401
    import app.routes.jobs  # noqa: F401
    import app.routes.retrieve  # noqa: F401
    import app.routes.webhook  # noqa: F401
    import core.ctgov_client  # noqa: F401
    import core.embed  # noqa: F401
    import core.fingerprint  # noqa: F401
    import core.form_parser  # noqa: F401
    import core.matcher  # noqa: F401
    import core.monday_client  # noqa: F401
    import core.vector_store  # noqa: F401
    import workers.ingest_worker  # noqa: F401
    import workers.queue  # noqa: F401


def test_app_factory() -> None:
    """The FastAPI app builds without raising."""
    from app.main import create_app

    app = create_app()
    assert app.title == "OC4 Study Build Trainer"


def test_health_endpoint() -> None:
    """GET /health returns 200 and the expected body."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    client = TestClient(create_app())
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "oc-study-build-trainer"}


def test_match_decision_thresholds() -> None:
    """
    Sanity-check the matcher's decision logic.

    With no candidates → NO_MATCH.
    """
    from core.fingerprint import StudyFingerprint
    from core.matcher import MatchDecision, score_candidates

    fp = StudyFingerprint(sponsor="Acme", intervention=["DrugX"], indication="Cancer", phase="2")
    result = score_candidates(fp, [])
    assert result.decision == MatchDecision.NO_MATCH


@pytest.mark.parametrize(
    "fp_phase,ct_phase,expected",
    [
        ("2", "Phase 2", True),
        ("Phase 1/2", "1/2", True),
        ("3", "Phase 2", False),
    ],
)
def test_phase_normalization(fp_phase: str, ct_phase: str, expected: bool) -> None:
    """Phase comparison normalizes 'Phase X' notation."""
    from core.matcher import _phase_match

    assert _phase_match(fp_phase, ct_phase) == expected
