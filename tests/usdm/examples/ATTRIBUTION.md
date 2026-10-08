# USDM example files

Content based on [DDF-RA (GitHub)](https://github.com/cdisc-org/DDF-RA) used under the
[CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/) license.

Unmodified copies from `Documents/Examples/` of cdisc-org/DDF-RA, commit
`aa303cb32f5d3ceecc68a16803e26720d2c1fc26` (2025-06-03, USDM 4.0):

| File | Source path |
|---|---|
| CDISC_Pilot_Study.json | Documents/Examples/CDISC_Pilot/CDISC_Pilot_Study.json |
| EliLilly_NCT03421379_Diabetes.json | Documents/Examples/EliLilly_NCT03421379_Diabetes/EliLilly_NCT03421379_Diabetes.json |
| Alexion_NCT04573309_Wilsons.json | Documents/Examples/Alexion_NCT04573309_Wilsons/Alexion_NCT04573309_Wilsons.json |

Used as a general regression benchmark for `usdm_input.py` (tests/usdm/benchmark.py), not as tuning targets.
The class schema in `cdisc_standards/usdm/usdm_4_0_classes.json` is derived from the same repository
(`Deliverables/UML/dataStructure.yml`, `scripts/build_usdm_class_schema.py`).
