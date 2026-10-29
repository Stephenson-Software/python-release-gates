"""Markdown for the run summary from result.json: `summarize.py <result.json> <title>`."""

import json
import os
import sys

path, title = sys.argv[1], sys.argv[2]
if not os.path.exists(path):
    print(f"## {title} — no result written (the harness did not run)")
    raise SystemExit
r = json.load(open(path))
if not r.get("complete", True):  # a result.json written before `complete` existed has no key
    verdict = "DID NOT FINISH (cancelled, timed out or killed; the assertions below are partial)"
else:
    verdict = "PASSED" if r.get("passed") else "FAILED"
print(f"## {title} — {verdict}")
print(f"`{r.get('repository')}` @ `{r.get('sha')}`\n")
print("| assertion | result | detail |\n|---|---|---|")
for a in r.get("assertions") or []:
    detail = str(a.get("detail", "")).replace("|", "\\|").replace("\n", "<br>")[:1500]
    print(f"| {a.get('name')} | {'✅' if a.get('passed') else '❌'} | {detail} |")
