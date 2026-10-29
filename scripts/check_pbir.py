"""Validate every PBIR JSON file against Microsoft's published schemas (fetched once into .cache/schemas).

Also checks that no measure name clashes with a column name (Desktop rejects that; schema checks don't).
"""
import json
import re
import sys
import urllib.request
from pathlib import Path

from jsonschema import Draft7Validator
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".cache" / "schemas"
PREFIX = "https://developer.microsoft.com/json-schemas/fabric/"
RAW = "https://raw.githubusercontent.com/microsoft/json-schemas/main/fabric/"


def fetch(uri):
    rel = uri.removeprefix(PREFIX)
    path = CACHE / rel
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(urllib.request.urlopen(RAW + rel, timeout=60).read())
    return Resource.from_contents(json.loads(path.read_text(encoding="utf-8")))


def main():
    registry = Registry(retrieve=fetch)
    report = next((ROOT / "powerbi").glob("*.Report"))
    bad = 0
    for f in sorted((report / "definition").rglob("*.json")):
        doc = json.loads(f.read_text(encoding="utf-8"))
        schema = fetch(doc["$schema"]).contents
        errors = list(Draft7Validator(schema, registry=registry).iter_errors(doc))
        for e in errors[:3]:
            print(f"{f.relative_to(report)}: {e.json_path}: {e.message[:200]}")
        bad += bool(errors)
    model = next((ROOT / "powerbi").glob("*.SemanticModel"))
    for t in (model / "definition" / "tables").glob("*.tmdl"):
        text = t.read_text(encoding="utf-8")
        cols = {c.strip("'").lower() for c in re.findall(r"^\tcolumn ('[^']+'|\S+)", text, re.M)}
        clash = {m.strip("'") for m in re.findall(r"^\tmeasure ('[^']+'|\S+)", text, re.M)
                 if m.strip("'").lower() in cols}
        if clash:
            print(f"{t.name}: measure/column name clash {sorted(clash)}")
            bad += 1
    print("PBIR check:", "FAILED" if bad else "all files valid")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
