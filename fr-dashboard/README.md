# FR Priority Dashboard — generator

The weekly routine produces `fr_data.json`, then runs `build_dashboard.py`, which renders
`index.html` (latest) and a dated `YYYY-MM-DD.html` snapshot. GitHub Pages serves these; the
weekly Confluence page iframes the dated file.

## Files
- `build_dashboard.py` — deterministic renderer. Reads `fr_data.json` + `template.html`, writes the HTML. Do not change weekly.
- `template.html` — the HTML/CSS/Chart.js shell with a single placeholder `__PAYLOAD__`.
- `fr_data.json` — the weekly data snapshot. The routine OVERWRITES this each run.
- `index.html`, `YYYY-MM-DD.html` — generated output (committed each week).

## Run
    python build_dashboard.py

## Data contract (fr_data.json)
The routine must produce exactly this shape; the renderer and drill-downs depend on it.

    {
      "meta":   {"week_of","open","arr_total","high_count","high_threshold",
                 "risk_count","scored","customers"},
      "top15":  [{"key","summary","customer","arr","align","score"}],
      "customers":[{"customer","count","arr",
                    "frs":[{"key","summary","customer","score","arr"}]}],   // sorted by count desc
      "dist":   {"labels":[...7 ranges...],
                 "counts":[...7 ints...],
                 "frs":[[...FRs per bin...] x7]},                            // powers the score drill-down
      "align":  [{"label","count"}],
      "renew":  [{"label","count"}],   // buckets: <180 days,180–364,365–499,500+,No date
      "clusters":[{"theme","fr_count","customers","arr","top_score",
                   "members":[{"key","summary"}]}]                           // sorted by fr_count desc
    }

Notes:
- `customers[].frs` and `dist.frs` are the lists shown when a bar is clicked — include them.
- `clusters` is the semantic grouping (same-underlying-request), independent of `align`.
- High-score threshold is 60 (`meta.high_threshold`); change in one place if you retune it.
