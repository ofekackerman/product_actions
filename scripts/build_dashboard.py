#!/usr/bin/env python3
"""
Build the FR Priority Dashboard.

1. Pulls all OPEN feature requests from the Jira FR project.
2. Computes KPIs, Top 15, per-customer counts, score distribution, alignment,
   renewal urgency, and (optionally) request-frequency themes from clusters.json.
3. Renders a self-contained interactive HTML file to <out>/index.html
   (plus <out>/data.json).
4. Optionally publishes a native summary page to Confluence that links to the
   interactive version.

Env vars:
  ATLASSIAN_BASE_URL   e.g. https://cloudshare.atlassian.net
  ATLASSIAN_EMAIL      Atlassian account email
  ATLASSIAN_API_TOKEN  Atlassian API token
  HIGH_SCORE           high-score threshold (default 60)
  PAGES_URL            public URL of the interactive dashboard (for the Confluence link)
  CONFLUENCE_PAGE_ID   page to update weekly (only if --publish-confluence)

Usage:
  python scripts/build_dashboard.py --out dist [--publish-confluence]
"""
import os, sys, json, base64, argparse, datetime
import requests

# --- Field map (confirmed IDs) ---
F_SCORE="customfield_12330"; F_ARR="customfield_12331"; F_CUST="customfield_11698"
F_CHURN="customfield_12571"; F_RENEW="customfield_12537"; F_ALIGN="customfield_12367"
F_HEALTH="customfield_12538"; F_EFFORT="customfield_11962"
FIELDS=["key","summary","status","created","duedate",F_SCORE,F_ARR,F_CUST,F_CHURN,F_RENEW,F_ALIGN,F_HEALTH,F_EFFORT]

BASE=os.environ["ATLASSIAN_BASE_URL"].rstrip("/")
AUTH=base64.b64encode(f'{os.environ["ATLASSIAN_EMAIL"]}:{os.environ["ATLASSIAN_API_TOKEN"]}'.encode()).decode()
HDR={"Authorization":f"Basic {AUTH}","Accept":"application/json","Content-Type":"application/json"}
HIGH=int(os.environ.get("HIGH_SCORE","60"))
TODAY=datetime.date.today()

def opt(v):
    if isinstance(v,dict): return v.get("value") or v.get("name")
    return v
def num(x): return x if isinstance(x,(int,float)) else None
def days_to(d):
    if not d: return None
    try: return (datetime.date.fromisoformat(d[:10])-TODAY).days
    except Exception: return None

def fetch_open_frs():
    """Uses the current Jira Cloud search endpoint with token pagination."""
    url=f"{BASE}/rest/api/3/search/jql"
    jql='project = FR AND statusCategory != Done ORDER BY cf[12330] DESC'
    rows=[]; token=None
    while True:
        payload={"jql":jql,"fields":FIELDS,"maxResults":100}
        if token: payload["nextPageToken"]=token
        r=requests.post(url,headers=HDR,data=json.dumps(payload),timeout=60)
        r.raise_for_status()
        data=r.json()
        for it in data.get("issues",[]):
            fl=it["fields"]
            rows.append({
                "key":it["key"],"summary":(fl.get("summary") or "").strip(),
                "score":fl.get(F_SCORE),"arr":fl.get(F_ARR) or 0,
                "customer":fl.get(F_CUST) or "—","churn":opt(fl.get(F_CHURN)),
                "renewal":fl.get(F_RENEW),"align":opt(fl.get(F_ALIGN)) or "—",
                "health":fl.get(F_HEALTH),"effort":opt(fl.get(F_EFFORT)),
            })
        token=data.get("nextPageToken")
        if not token or data.get("isLast",True): break
    return rows

def frobj(r):
    return {"key":r["key"],
            "summary":(r["summary"][:60]+("…" if len(r["summary"])>60 else "")),
            "customer":r["customer"],
            "score":r["score"] if num(r["score"]) is not None else "—",
            "arr":r["arr"]}

def compute(rows):
    from collections import Counter, defaultdict
    arr_total=sum(r["arr"] for r in rows if num(r["arr"]))
    high=sum(1 for r in rows if num(r["score"]) is not None and r["score"]>=HIGH)
    def at_risk(r):
        if r["churn"] in ("Subscription Churn","Downsell"): return True
        d=days_to(r["renewal"]); return d is not None and d<180
    risk=sum(1 for r in rows if at_risk(r))
    scored=[r for r in rows if num(r["score"]) is not None]

    top15=[{**frobj(r),"align":r["align"],"score":r["score"]}
           for r in sorted(scored,key=lambda r:r["score"],reverse=True)[:15]]

    cust=defaultdict(lambda:{"count":0,"arr":0,"frs":[]})
    for r in rows:
        if r["customer"]=="—": continue
        c=cust[r["customer"]]; c["count"]+=1; c["arr"]=max(c["arr"],r["arr"]); c["frs"].append(frobj(r))
    cust_list=sorted([{"customer":k,**v} for k,v in cust.items()],
                     key=lambda x:(x["count"],x["arr"]),reverse=True)
    for c in cust_list:
        c["frs"].sort(key=lambda x:(x["score"] if isinstance(x["score"],(int,float)) else -1),reverse=True)

    bins=[(0,19),(20,29),(30,39),(40,49),(50,59),(60,69),(70,100)]
    labels=["0–19","20–29","30–39","40–49","50–59","60–69","70+"]
    dist_counts=[]; dist_frs=[]
    for lo,hi in bins:
        grp=sorted([r for r in scored if lo<=r["score"]<=hi],key=lambda r:r["score"],reverse=True)
        dist_counts.append(len(grp)); dist_frs.append([frobj(r) for r in grp])

    al=Counter(r["align"] or "Unassigned" for r in rows)
    align=[{"label":k,"count":v} for k,v in al.most_common()]

    def rbucket(r):
        d=days_to(r["renewal"])
        if d is None: return "No date"
        if d<180: return "<180 days"
        if d<365: return "180–364"
        if d<500: return "365–499"
        return "500+"
    order=["<180 days","180–364","365–499","500+","No date"]
    rc=Counter(rbucket(r) for r in rows)
    renew=[{"label":k,"count":rc.get(k,0)} for k in order]

    clusters=load_clusters(rows)

    return {"meta":{"week_of":TODAY.isoformat(),"open":len(rows),"arr_total":arr_total,
                    "high_count":high,"high_threshold":HIGH,"risk_count":risk,
                    "scored":len(scored),"customers":len({r['customer'] for r in rows if r['customer']!='—'})},
            "top15":top15,"customers":cust_list,
            "dist":{"labels":labels,"counts":dist_counts,"frs":dist_frs},
            "align":align,"renew":renew,"clusters":clusters}

def load_clusters(rows):
    """Reads scripts/clusters.json (theme -> list of FR keys) and computes stats.
    Themes change slowly; regenerate this file periodically (see the guide)."""
    path=os.path.join(os.path.dirname(__file__),"clusters.json")
    if not os.path.exists(path): return []
    themes=json.load(open(path)).get("themes",[])
    info={r["key"]:r for r in rows}
    out=[]
    for t in themes:
        keys=[k for k in t["keys"] if k in info]   # only still-open FRs
        if not keys: continue
        custs={}
        for k in keys:
            r=info[k]
            if r["customer"]!="—": custs[r["customer"]]=max(custs.get(r["customer"],0), r["arr"] or 0)
        out.append({"theme":t["theme"],"fr_count":len(keys),"customers":len(custs),
                    "arr":sum(custs.values()),
                    "members":[{"key":k,"summary":info[k]["summary"][:48]} for k in keys]})
    out.sort(key=lambda x:(x["fr_count"],x["customers"]),reverse=True)
    return out

# ------------------------------------------------------------------ HTML
def render_html(data):
    tpl=open(os.path.join(os.path.dirname(__file__),"dashboard_template.html"),encoding="utf-8").read()
    return tpl.replace("__PAYLOAD__", json.dumps(data,ensure_ascii=False))

# ------------------------------------------------------------------ Confluence
def publish_confluence(data):
    page_id=os.environ.get("CONFLUENCE_PAGE_ID")
    if not page_id:
        print("CONFLUENCE_PAGE_ID not set — skipping Confluence publish."); return
    pages_url=os.environ.get("PAGES_URL","")
    m=data["meta"]
    def money(v): return f"${v/1e6:.2f}M" if v>=1e6 else (f"${round(v/1e3)}k" if v>=1e3 else f"${v}")
    rows_top="".join(
        f'<tr><td><a href="{BASE}/browse/{r["key"]}">{r["key"]}</a></td><td>{r["summary"]}</td>'
        f'<td>{r["customer"]}</td><td>{money(r["arr"])}</td><td>{r["score"]}</td></tr>'
        for r in data["top15"])
    rows_theme="".join(
        f'<tr><td>{c["theme"]}</td><td>{c["fr_count"]}</td><td>{c["customers"]}</td><td>{money(c["arr"])}</td></tr>'
        for c in data["clusters"])
    link=(f'<p><strong><a href="{pages_url}">Open the interactive dashboard →</a></strong> '
          f'(charts, drill-downs, full backlog)</p>') if pages_url else ""
    body=f"""
<ac:structured-macro ac:name="info"><ac:rich-text-body>
<p>Weekly snapshot · week of {m['week_of']} · open feature requests only.</p>{link}
</ac:rich-text-body></ac:structured-macro>
<p><strong>Open FRs:</strong> {m['open']} &nbsp;·&nbsp; <strong>ARR represented:</strong> {money(m['arr_total'])}
 &nbsp;·&nbsp; <strong>High score (&ge;{m['high_threshold']}):</strong> {m['high_count']}
 &nbsp;·&nbsp; <strong>Churn / near-renewal:</strong> {m['risk_count']}</p>
<h2>Top 15 by score</h2>
<table><tbody><tr><th>FR</th><th>Request</th><th>Customer</th><th>ARR</th><th>Score</th></tr>{rows_top}</tbody></table>
<h2>Most-requested themes</h2>
<table><tbody><tr><th>Theme</th><th>FRs</th><th>Customers</th><th>Combined ARR</th></tr>{rows_theme}</tbody></table>
<p><em>Generated automatically. Do not edit by hand — changes are overwritten weekly.</em></p>
"""
    # get current version, then PUT with version+1
    g=requests.get(f"{BASE}/wiki/api/v2/pages/{page_id}",headers=HDR,
                   params={"body-format":"storage"},timeout=60); g.raise_for_status()
    cur=g.json(); ver=cur["version"]["number"]
    put={"id":str(page_id),"status":"current","title":cur["title"],
         "body":{"representation":"storage","value":body},
         "version":{"number":ver+1,"message":f"Weekly refresh {m['week_of']}"}}
    p=requests.put(f"{BASE}/wiki/api/v2/pages/{page_id}",headers=HDR,data=json.dumps(put),timeout=60)
    p.raise_for_status()
    print(f"Confluence page {page_id} updated to version {ver+1}.")

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--out",default="dist")
    ap.add_argument("--publish-confluence",action="store_true")
    a=ap.parse_args()
    rows=fetch_open_frs()
    print(f"Fetched {len(rows)} open FRs.")
    data=compute(rows)
    os.makedirs(a.out,exist_ok=True)
    json.dump(data,open(os.path.join(a.out,"data.json"),"w"),ensure_ascii=False,indent=2)
    open(os.path.join(a.out,"index.html"),"w",encoding="utf-8").write(render_html(data))
    print(f"Wrote {a.out}/index.html")
    if a.publish_confluence:
        publish_confluence(data)

if __name__=="__main__":
    main()
