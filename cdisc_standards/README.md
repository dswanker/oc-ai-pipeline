# CDISC standards bundle (deterministic, no AI)

Pinned versions live in `manifest.json`. Changing a version is an explicit upgrade; every build
stamps the versions it used on the Study Spec (`study_meta.cdisc_standards`), and reruns of an
existing study keep its stamped CT version.

| Source | Where | Committed? |
|---|---|---|
| Controlled Terminology | NCI EVS dated archive `SDTM Terminology <ct_version>.txt`, downloaded and cached on first use | No (cache) |
| CDASH CRF specializations | CDISC COSMoS (MIT), `cosmos/` | Yes |
| USDM 4.0 class schema | Derived from cdisc-org/DDF-RA `dataStructure.yml` (CC-BY-4.0 content), `usdm/` | Yes |
| CDASHIG / SDTMIG metadata | CDISC member downloads. NEVER commit (this repo is public). Place in `CDISC_STANDARDS_DIR` (Railway: `/data/cdisc_standards`; local: `.cache/cdisc_standards`) | No |

Without the CDASHIG file, binding falls back to the COSMoS codelist, then to general name rules.
