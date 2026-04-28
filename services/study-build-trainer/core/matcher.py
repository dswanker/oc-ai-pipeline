"""
CT.gov match confidence scoring.

Given a fingerprint and a list of CTGovCandidate results, score each
candidate and decide whether we have an auto-ingest-quality match,
ambiguous candidates that need human review, or no match at all.

## Composite score formula (Phase 1, target ≥0.9 for auto-ingest)

  score = max(0.0, min(1.0,
      0.40 * sponsor_match
    + 0.40 * intervention_match
    + 0.10 * indication_match
    + 0.10 * phase_match
  ))

Subject to the gating rule:

  auto_ingest = (score ≥ 0.9) AND sponsor_match AND intervention_match
              AND (indication_match OR phase_match)

That gating rule is the explicit ask from the design conversation:
"high confidence" requires a sponsor + intervention match plus at
least one of indication / phase.

## Match definitions (each returns 0.0 or 1.0)

  sponsor_match: case-insensitive substring of fingerprint.sponsor in
    candidate.sponsor (or vice versa). Future: fuzzy match for slight
    name variations.

  intervention_match: any fingerprint intervention substring-matches
    any candidate intervention.

  indication_match: fingerprint.indication substring-matches any
    candidate.condition. Synonyms expansion (e.g. "prostate cancer" ↔
    "prostatic neoplasms") deferred to a later phase.

  phase_match: fingerprint.phase exactly equals candidate.phase after
    normalization ("Phase 2" → "2", "Phase 1/Phase 2" → "1/2").

## Returns

  match_decision: AUTO_INGEST | NEEDS_REVIEW | NO_MATCH

  - AUTO_INGEST: top candidate qualifies under the gating rule above
  - NEEDS_REVIEW: at least one candidate scores ≥ 0.5 but no auto-ingest
  - NO_MATCH: no candidates at all, or top score < 0.5
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.config import settings
from core.ctgov_client import CTGovCandidate
from core.fingerprint import StudyFingerprint


class MatchDecision(StrEnum):
    AUTO_INGEST = "auto_ingest"
    NEEDS_REVIEW = "needs_review"
    NO_MATCH = "no_match"


@dataclass
class ScoredCandidate:
    candidate: CTGovCandidate
    score: float  # 0.0–1.0
    sponsor_match: bool
    intervention_match: bool
    indication_match: bool
    phase_match: bool


@dataclass
class MatchResult:
    decision: MatchDecision
    scored: list[ScoredCandidate]  # sorted descending by score
    """Top candidate is at scored[0] when decision != NO_MATCH."""


def score_candidates(
    fingerprint: StudyFingerprint,
    candidates: list[CTGovCandidate],
) -> MatchResult:
    """Score a list of CT.gov candidates against a fingerprint."""
    scored = [_score_one(fingerprint, c) for c in candidates]
    scored.sort(key=lambda s: s.score, reverse=True)

    if not scored:
        return MatchResult(decision=MatchDecision.NO_MATCH, scored=[])

    top = scored[0]
    auto_ingest_ok = (
        top.score >= settings.ctgov_auto_ingest_threshold
        and top.sponsor_match
        and top.intervention_match
        and (top.indication_match or top.phase_match)
    )

    if auto_ingest_ok:
        decision = MatchDecision.AUTO_INGEST
    elif top.score >= 0.5:
        decision = MatchDecision.NEEDS_REVIEW
    else:
        decision = MatchDecision.NO_MATCH

    return MatchResult(decision=decision, scored=scored)


def _score_one(fp: StudyFingerprint, c: CTGovCandidate) -> ScoredCandidate:
    sponsor_m = _sponsor_match(fp.sponsor, c.sponsor)
    intervention_m = _intervention_match(fp.intervention or [], c.interventions)
    indication_m = _indication_match(fp.indication, c.conditions)
    phase_m = _phase_match(fp.phase, c.phase)

    score = (
        0.40 * float(sponsor_m)
        + 0.40 * float(intervention_m)
        + 0.10 * float(indication_m)
        + 0.10 * float(phase_m)
    )

    return ScoredCandidate(
        candidate=c,
        score=score,
        sponsor_match=sponsor_m,
        intervention_match=intervention_m,
        indication_match=indication_m,
        phase_match=phase_m,
    )


def _sponsor_match(fp_sponsor: str | None, ct_sponsor: str | None) -> bool:
    if not fp_sponsor or not ct_sponsor:
        return False
    a, b = fp_sponsor.lower().strip(), ct_sponsor.lower().strip()
    return a in b or b in a


def _intervention_match(fp_intvs: list[str], ct_intvs: list[str]) -> bool:
    if not fp_intvs or not ct_intvs:
        return False
    fp_lower = {x.lower().strip() for x in fp_intvs}
    ct_lower = {x.lower().strip() for x in ct_intvs}
    # any-to-any substring match
    for a in fp_lower:
        for b in ct_lower:
            if a in b or b in a:
                return True
    return False


def _indication_match(fp_ind: str | None, ct_conds: list[str]) -> bool:
    if not fp_ind or not ct_conds:
        return False
    needle = fp_ind.lower().strip()
    return any(needle in c.lower() or c.lower() in needle for c in ct_conds)


def _phase_match(fp_phase: str | None, ct_phase: str | None) -> bool:
    if not fp_phase or not ct_phase:
        return False
    return _normalize_phase(fp_phase) == _normalize_phase(ct_phase)


def _normalize_phase(p: str) -> str:
    """Normalize phase strings: 'Phase 2' → '2', 'Phase 1/Phase 2' → '1/2'."""
    s = p.lower().replace("phase", "").replace(" ", "").strip()
    return s
