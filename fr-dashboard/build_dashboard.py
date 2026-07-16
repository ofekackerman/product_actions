#!/usr/bin/env python3
"""Build the FR Priority Dashboard.

Reads (from this folder):
  fr_data.json   weekly data snapshot (produced by the routine each run)
  template.html  the HTML shell containing the single placeholder __PAYLOAD__
Writes (into this folder):
  index.html        the latest dashboard
  <week_of>.html    a dated snapshot, named from fr_data.json -> meta.week_of

Rendering is deterministic: only fr_data.json changes week to week.
Run:  python build_dashboard.py
"""
import json, os

HERE = os.path.dirname(os.path.abspath(__file__))

def main():
    with open(os.path.join(HERE, "fr_data.json"), encoding="utf-8") as f:
        data = json.load(f)
    with open(os.path.join(HERE, "template.html"), encoding="utf-8") as f:
        template = f.read()

    payload = json.dumps(data, ensure_ascii=False)
    html = template.replace("__PAYLOAD__", payload)

    with open(os.path.join(HERE, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)

    week = (data.get("meta") or {}).get("week_of", "snapshot")
    dated = os.path.join(HERE, f"{week}.html")
    with open(dated, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Wrote index.html and {week}.html ({len(html):,} bytes)")

if __name__ == "__main__":
    main()
