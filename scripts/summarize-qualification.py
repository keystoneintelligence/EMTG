"""Summarize disjoint JUnit suites without hiding skips or command failures."""
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

root = Path(sys.argv[1])
summary = {"commands": json.loads((root / "commands.json").read_text(encoding="utf-8-sig")), "suites": {}}
for path in sorted(root.glob("*.xml")):
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    for case in ET.parse(path).iter("testcase"):
        key = "failed" if case.find("failure") is not None else "errors" if case.find("error") is not None else "skipped" if case.find("skipped") is not None else "passed"
        counts[key] += 1
    summary["suites"][path.stem] = counts
(root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
print(json.dumps(summary["suites"], indent=2))
