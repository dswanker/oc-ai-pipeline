"""Derive the compact USDM class schema used by usdm_input.validate from DDF-RA's dataStructure.yml.

Usage: python scripts/build_usdm_class_schema.py <path to DDF-RA/Deliverables/UML/dataStructure.yml> <DDF-RA commit>
Writes cdisc_standards/usdm/usdm_4_0_classes.json: class -> attribute -> [cardinality, [types]].
Needs PyYAML (not a pipeline dependency; run it in any environment that has it).
Content based on DDF-RA (GitHub) used under the CC-BY-4.0 license.
"""
import json, os, sys
import yaml

src, commit = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "")
model = yaml.safe_load(open(src))
classes = {}
for name, body in model.items():
    attrs = {}
    for attr, spec in (body.get("Attributes") or {}).items():
        types = [t.get("$ref", "").replace("#/", "") for t in spec.get("Type") or [] if isinstance(t, dict)]
        attrs[attr] = [str(spec.get("Cardinality") or ""), types]
    classes[name] = attrs
out = {"source": "cdisc-org/DDF-RA Deliverables/UML/dataStructure.yml", "commit": commit, "usdm_version": "4.0",
       "attribution": "Content based on DDF-RA (GitHub) used under the CC-BY-4.0 license.", "classes": classes}
dst = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "cdisc_standards", "usdm", "usdm_4_0_classes.json")
json.dump(out, open(dst, "w"), separators=(",", ":"), sort_keys=True)
print(len(classes), "classes ->", os.path.normpath(dst))
