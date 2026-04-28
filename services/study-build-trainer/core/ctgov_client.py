"""
clinicaltrials.gov API client.

CT.gov v2 API base: https://clinicaltrials.gov/api/v2/
Docs: https://clinicaltrials.gov/data-api/api

Two endpoints we care about:

  GET /studies                       — search for studies matching query params
  GET /studies/{nctId}               — fetch full record for a specific study
  GET /studies/{nctId}/document/{docId} — fetch attached protocol document

The protocol PDF is available via the document endpoint ONLY if the
sponsor uploaded one (typically only after results are posted, and
sometimes never). For studies without an attached protocol, we get
back the structured record only.

No auth required for CT.gov. Be polite — set a meaningful User-Agent
(see settings.ctgov_user_agent) and don't hammer the API.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import httpx
import structlog

from app.config import settings
from core.fingerprint import StudyFingerprint

logger = structlog.get_logger(__name__)


@dataclass
class CTGovCandidate:
    """A single CT.gov study record matching a search query."""

    nct_id: str
    title: str = ""
    sponsor: str | None = None
    interventions: list[str] = field(default_factory=list)
    conditions: list[str] = field(default_factory=list)
    phase: str | None = None
    study_type: str | None = None
    has_protocol_document: bool = False
    """True iff CT.gov has a protocol PDF attached to this record."""


class CTGovClient:
    BASE_URL = "https://clinicaltrials.gov/api/v2"

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers={"User-Agent": settings.ctgov_user_agent},
            timeout=30.0,
        )

    async def search(
        self,
        fingerprint: StudyFingerprint,
        *,
        max_results: int = 10,
    ) -> list[CTGovCandidate]:
        """
        Search CT.gov for studies matching a fingerprint.

        Strategy: build a structured query that combines:
          - sponsor (lead organization)
          - intervention names
          - indication / condition

        Phase and therapeutic area are not used as filters here —
        they're used by matcher.py for scoring. Filtering by them in
        the search would be too aggressive (CT.gov phase strings vary).
        """
        # TODO: build query params per CT.gov v2 API spec.
        # Useful filters:
        #   - query.lead   = sponsor
        #   - query.intr   = intervention
        #   - query.cond   = condition / indication
        #   - pageSize     = max_results
        #   - format       = json
        raise NotImplementedError("CTGovClient.search not yet implemented")

    async def fetch_study(self, nct_id: str) -> dict:
        """Fetch a full CT.gov record by NCT ID. Returns raw JSON."""
        # TODO
        raise NotImplementedError

    async def download_protocol_pdf(self, nct_id: str) -> bytes | None:
        """
        Download the attached protocol PDF if one exists.

        Returns None if no protocol document is available — CT.gov
        does NOT guarantee protocol attachments. Sponsors choose
        whether to upload them.
        """
        # TODO: GET /studies/{nctId}/largeDocument or similar
        # CT.gov v2 has a `studyDocuments` array on the record;
        # need to find the doc with type=PROTOCOL and download it.
        raise NotImplementedError

    async def aclose(self) -> None:
        await self._client.aclose()
