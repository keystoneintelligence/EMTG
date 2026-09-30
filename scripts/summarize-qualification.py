"""Summarize JUnit evidence; requested native/package skips are failures."""
import argparse
import json
from pathlib import Path
import xml.etree.ElementTree as ET

SUITES = {"python": ("python", None, False), "fast-ctest": ("fast", None, True),
          "release-ctest": ("release", None, True), "bounded-native": ("bounded-native", 8, True),
          "aeps": ("aeps", 4, True), "package": ("package", 2, True)}


def summarize(root):
    commands = json.loads((root / "commands.json").read_text(encoding="utf-8-sig"))
    if isinstance(commands, dict):
        commands = [commands]
    summary = {"commands": commands, "suites": {}, "failures": []}
    if not commands:
        summary["failures"].append("no commands recorded")
    for command in commands:
        if command["exit_code"]:
            summary["failures"].append(command["name"] + ": nonzero exit")
        if command["name"] not in SUITES:
            continue
        name, expected, required = SUITES[command["name"]]
        try:
            cases = list(ET.parse(root / (name + ".xml")).iter("testcase"))
            counts = {key: 0 for key in ("passed", "failed", "errors", "skipped")}
            for case in cases:
                key = "failed" if case.find("failure") is not None else "errors" if case.find("error") is not None else "skipped" if case.find("skipped") is not None else "passed"
                counts[key] += 1
            summary["suites"][name] = counts
            if (not cases or counts["failed"] or counts["errors"] or required and counts["skipped"]
                    or expected is not None and len(cases) != expected):
                summary["failures"].append(name + ": incomplete or failed qualification")
        except (OSError, ET.ParseError):
            summary["failures"].append(name + ": missing or malformed JUnit")
    summary["passed"] = not summary["failures"]
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    summary = summarize(args.root)
    (args.root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return int(not summary["passed"])


if __name__ == "__main__":
    raise SystemExit(main())
