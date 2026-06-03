import requests
import json
import os
import re
from datetime import datetime, timezone
from collections import defaultdict

JIRA_BASE    = "https://cloudshare.atlassian.net"
CONF_BASE    = "https://cloudshare.atlassian.net"
JIRA_EMAIL   = os.environ["JIRA_EMAIL"]
JIRA_TOKEN   = os.environ["JIRA_TOKEN"]
CONF_EMAIL   = os.environ["CONFLUENCE_EMAIL"]
CONF_TOKEN   = os.environ["CONFLUENCE_TOKEN"]
PARENT_ID    = "3533963267"
JIRA_PROJECT = "FR"

jira_auth = (JIRA_EMAIL, JIRA_TOKEN)
conf_auth = (CONF_EMAIL, CONF_TOKEN)
TODAY     = datetime.now(timezone.utc).strftime("%Y-%m-%d")
RUN_URL   = f"https://github.com/{os.environ.get('GITHUB_REPOSITORY','ofekackerman/product_actions')}/actions/runs/{os.environ.get('GITHUB_RUN_ID','manual')}"

def jira_get(path, params=None):
    r = requests.get(f"{JIRA_BASE}{path}", auth=jira_auth, params=params)
    r.raise_for_status()
    return r.json()

def extract_arr(desc):
    m = re.search(r'Account ARR[:\s]+([0-9,]+)', desc or "", re.IGNORECASE)
    return int(m.group(1).replace(',', '')) if m else 0

def extract_customer(desc):
    m = re.search(r'Created By[:\s]+(.+)', desc or "", re.IGNORECASE)
    return m.group(1).strip().split('\n')[0] if m else "—"

def extract_arr_status(desc):
    m = re.search(r'Account Status[:\s]+(\w+)', desc or "", re.IGNORECASE)
    return m.group(1).strip() if m else "—"

def priority_badge(priority):
    colors = {"Urgent": ("#ff1744","#fff"), "High": ("#ff6d00","#fff"), "Normal": ("#1976d2","#fff"), "Low": ("#388e3c","#fff")}
    bg, fg = colors.get(priority, ("#757575","#fff"))
    return f'<span style="background:{bg};color:{fg};padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600;">{priority}</span>'

# ── Fetch all issues ──────────────────────────────────────────────────────────
print("Fetching FR issues from Jira...")
all_issues = []
next_page_token = None

while True:
    params = {
        "jql": f"project = {JIRA_PROJECT} ORDER BY cf[12330] DESC, cf[10300] ASC",
        "maxResults": 100,
        "fields": "summary,description,status,priority,customfield_12330,customfield_10300,labels,issuetype,created,updated"
    }
    if next_page_token:
        params["nextPageToken"] = next_page_token

    data = jira_get("/rest/api/3/search/jql", params=params)
    issues = data.get("issues", [])
    all_issues.extend(issues)
    print(f"  Fetched {len(all_issues)} so far...")

    next_page_token = data.get("nextPageToken")
    if not next_page_token or data.get("isLast", False) or len(issues) < 100:
        break

print(f"Total: {len(all_issues)}")

# ── Enrich ────────────────────────────────────────────────────────────────────
enriched = []
for issue in all_issues:
    f = issue["fields"]
    raw = f.get("description")
    desc = ""
    if isinstance(raw, dict):
        parts = []
        def walk(node):
            if isinstance(node, dict):
                if node.get("type") == "text":
                    parts.append(node.get("text", ""))
                for v in node.values():
                    if isinstance(v, (dict, list)):
                        walk(v)
            elif isinstance(node, list):
                for item in node:
                    walk(item)
        walk(raw)
        desc = " ".join(parts)
    elif raw:
        desc = str(raw)

    enriched.append({
        "key":      issue["key"],
        "summary":  f.get("summary", "—"),
        "score":    f.get("customfield_12330") or 0,
        "status":   f.get("status", {}).get("name", "—"),
        "priority": f.get("priority", {}).get("name", "—"),
        "arr":      extract_arr(desc),
        "customer": extract_customer(desc),
        "arr_status": extract_arr_status(desc),
        "updated":  (f.get("updated") or "")[:10],
        "url":      f"{JIRA_BASE}/browse/{issue['key']}",
    })

enriched.sort(key=lambda x: x["score"], reverse=True)
top30 = enriched[:30]
max_score = top30[0]["score"] if top30 else 1

# ── Stats ─────────────────────────────────────────────────────────────────────
total_arr  = sum(i["arr"] for i in enriched)
active_arr = sum(i["arr"] for i in enriched if i["arr_status"] == "Active")
with_score = sum(1 for i in enriched if i["score"] > 0)

stats_html = f"""
<table style="border:none;width:100%;margin-bottom:24px;"><tr>
  <td style="border:none;text-align:center;padding:12px 16px;background:#f3f0ff;border-radius:8px;">
    <div style="font-size:28px;font-weight:800;color:#5c6bc0;">{len(enriched)}</div>
    <div style="font-size:12px;color:#555;">Total Feature Requests</div></td>
  <td style="border:none;width:12px;"></td>
  <td style="border:none;text-align:center;padding:12px 16px;background:#e8f5e9;border-radius:8px;">
    <div style="font-size:28px;font-weight:800;color:#2e7d32;">{with_score}</div>
    <div style="font-size:12px;color:#555;">With FR Score</div></td>
  <td style="border:none;width:12px;"></td>
  <td style="border:none;text-align:center;padding:12px 16px;background:#fff3e0;border-radius:8px;">
    <div style="font-size:28px;font-weight:800;color:#e65100;">${total_arr:,}</div>
    <div style="font-size:12px;color:#555;">Total ARR Represented</div></td>
  <td style="border:none;width:12px;"></td>
  <td style="border:none;text-align:center;padding:12px 16px;background:#e3f2fd;border-radius:8px;">
    <div style="font-size:28px;font-weight:800;color:#1565c0;">${active_arr:,}</div>
    <div style="font-size:12px;color:#555;">Active Account ARR</div></td>
  <td style="border:none;width:12px;"></td>
  <td style="border:none;text-align:center;padding:12px 16px;background:#fce4ec;border-radius:8px;">
    <div style="font-size:28px;font-weight:800;color:#c62828;">{top30[0]["score"] if top30 else 0}</div>
    <div style="font-size:12px;color:#555;">Highest FR Score</div></td>
</tr></table>"""

# ── Top 30 table ──────────────────────────────────────────────────────────────
rows = ""
for rank, i in enumerate(top30, 1):
    bw = round((i["score"] / max_score) * 100) if max_score else 0
    bar = f'<div style="background:#e8eaf6;border-radius:3px;width:80px;display:inline-block;height:8px;vertical-align:middle;margin-right:6px;"><div style="background:#5c6bc0;width:{bw}%;height:8px;border-radius:3px;"></div></div>'
    rows += f"""<tr style="{'background:#f8f8ff;' if rank%2==0 else ''}">
      <td style="text-align:center;font-weight:700;color:#9e9e9e;padding:8px 6px;">{rank}</td>
      <td style="padding:8px 6px;"><a href="{i['url']}" style="font-weight:600;color:#3f51b5;">{i['key']}</a></td>
      <td style="padding:8px 6px;max-width:320px;">{i['summary']}</td>
      <td style="padding:8px 6px;white-space:nowrap;">{bar}<strong style="color:#3f51b5;">{i['score']}</strong></td>
      <td style="padding:8px 6px;">{priority_badge(i['priority'])}</td>
      <td style="padding:8px 6px;font-family:monospace;font-size:12px;">{'${:,}'.format(i['arr']) if i['arr'] else '—'}</td>
      <td style="padding:8px 6px;font-size:12px;color:#666;">{i['customer']}</td>
      <td style="padding:8px 6px;font-size:12px;color:#888;">{i['updated']}</td>
    </tr>"""

top30_html = f"""
<h2 style="color:#3f51b5;border-bottom:2px solid #e8eaf6;padding-bottom:8px;">🏆 Top 30 Feature Requests by FR Score</h2>
<table style="width:100%;border-collapse:collapse;font-size:13px;">
  <thead><tr style="background:#3f51b5;color:#fff;">
    <th style="padding:10px 6px;">#</th><th style="padding:10px 6px;">Key</th>
    <th style="padding:10px 6px;">Summary</th><th style="padding:10px 6px;">FR Score</th>
    <th style="padding:10px 6px;">Priority</th><th style="padding:10px 6px;">ARR</th>
    <th style="padding:10px 6px;">Submitted By</th><th style="padding:10px 6px;">Last Updated</th>
  </tr></thead>
  <tbody>{rows}</tbody>
</table>
<p style="font-size:12px;color:#999;">Showing top 30 of {len(enriched)} total · sorted by FR Score</p>"""

# ── Theme grouping ────────────────────────────────────────────────────────────
groups = defaultdict(list)
THEMES = {
    "Authentication & Security": ["auth","2fa","sso","security","login","password","mfa","saml","ransomware","disable","user access"],
    "Environment Management":    ["environment","vm","blueprint","snapshot","clone","template","provision","machine"],
    "Training & Re-enrollment":  ["training","enroll","re-enroll","student","course","experience","class","learner"],
    "API & Integrations":        ["api","webhook","integration","terraform","sdk","automation","salesforce"],
    "Reporting & Analytics":     ["report","analytic","metric","dashboard","export","csv","insight","usage"],
    "Collaboration & Sharing":   ["share","collaborat","team","invite","participant","guest"],
    "Network & Connectivity":    ["network","vpn","connect","bandwidth","firewall","port","rdp","ssh"],
    "UI & User Experience":      ["ui","ux","interface","design","notification","alert","email","mobile"],
    "Billing & Subscriptions":   ["billing","subscription","credit","payment","cost","quota","limit","plan"],
}
assigned = set()
for theme, keywords in THEMES.items():
    for issue in enriched:
        if issue["key"] in assigned:
            continue
        text = (issue["summary"]).lower()
        if any(kw in text for kw in keywords):
            groups[theme].append(issue)
            assigned.add(issue["key"])
for issue in enriched:
    if issue["key"] not in assigned:
        groups["Other / Unique Requests"].append(issue)
groups = dict(sorted(groups.items(), key=lambda kv: sum(i["score"] for i in kv[1]), reverse=True))

group_sections = ""
for theme, issues in groups.items():
    if not issues:
        continue
    g_score = sum(i["score"] for i in issues)
    g_arr   = sum(i["arr"] for i in issues)
    g_rows  = ""
    for i in sorted(issues, key=lambda x: x["score"], reverse=True):
        g_rows += f"""<tr>
          <td style="padding:6px 8px;"><a href="{i['url']}" style="color:#3f51b5;font-weight:600;">{i['key']}</a></td>
          <td style="padding:6px 8px;">{i['summary']}</td>
          <td style="padding:6px 8px;text-align:center;font-weight:700;color:#3f51b5;">{i['score']}</td>
          <td style="padding:6px 8px;">{priority_badge(i['priority'])}</td>
          <td style="padding:6px 8px;font-size:12px;font-family:monospace;">{'${:,}'.format(i['arr']) if i['arr'] else '—'}</td>
        </tr>"""
    group_sections += f"""
<ac:structured-macro ac:name="expand" ac:schema-version="1">
  <ac:parameter ac:name="title">📂 {theme} · {len(issues)} requests · Score: {g_score} · ARR: ${g_arr:,}</ac:parameter>
  <ac:rich-text-body>
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
      <thead><tr style="background:#eceff1;">
        <th style="padding:8px;">Key</th><th style="padding:8px;">Summary</th>
        <th style="padding:8px;">FR Score</th><th style="padding:8px;">Priority</th><th style="padding:8px;">ARR</th>
      </tr></thead>
      <tbody>{g_rows}</tbody>
    </table>
  </ac:rich-text-body>
</ac:structured-macro>"""

groups_html = f"""
<h2 style="color:#3f51b5;border-bottom:2px solid #e8eaf6;padding-bottom:8px;">🗂️ Feature Requests Grouped by Theme</h2>
<p style="color:#666;font-size:13px;">Click a group to expand. Ordered by total FR Score.</p>
{group_sections}"""

# ── Score distribution ────────────────────────────────────────────────────────
buckets = {"0":0,"1-10":0,"11-20":0,"21-30":0,"31-50":0,"51+":0}
for i in enriched:
    s = i["score"]
    if s==0: buckets["0"]+=1
    elif s<=10: buckets["1-10"]+=1
    elif s<=20: buckets["11-20"]+=1
    elif s<=30: buckets["21-30"]+=1
    elif s<=50: buckets["31-50"]+=1
    else: buckets["51+"]+=1

bmax = max(buckets.values()) or 1
dist_rows = ""
for bucket, count in buckets.items():
    bw = round((count/bmax)*200)
    dist_rows += f"""<tr>
      <td style="padding:6px 12px;font-weight:600;">Score {bucket}</td>
      <td style="padding:6px 12px;"><div style="background:#e8eaf6;border-radius:4px;width:200px;height:14px;display:inline-block;vertical-align:middle;"><div style="background:#5c6bc0;width:{bw}px;height:14px;border-radius:4px;"></div></div></td>
      <td style="padding:6px 12px;font-weight:700;color:#3f51b5;">{count}</td>
      <td style="padding:6px 12px;color:#888;font-size:12px;">{round(count/len(enriched)*100) if enriched else 0}%</td>
    </tr>"""

dist_html = f"""<h2 style="color:#3f51b5;border-bottom:2px solid #e8eaf6;padding-bottom:8px;">📊 Score Distribution</h2>
<table style="border-collapse:collapse;font-size:13px;"><tbody>{dist_rows}</tbody></table>"""

# ── Priority breakdown ────────────────────────────────────────────────────────
p_count = defaultdict(int)
p_arr   = defaultdict(int)
for i in enriched:
    p_count[i["priority"]] += 1
    p_arr[i["priority"]]   += i["arr"]

prio_rows = ""
for p, cnt in sorted(p_count.items(), key=lambda x: -x[1]):
    prio_rows += f"""<tr>
      <td style="padding:6px 12px;">{priority_badge(p)}</td>
      <td style="padding:6px 12px;font-weight:700;">{cnt}</td>
      <td style="padding:6px 12px;font-family:monospace;font-size:12px;">${p_arr[p]:,}</td>
    </tr>"""

prio_html = f"""<h2 style="color:#3f51b5;border-bottom:2px solid #e8eaf6;padding-bottom:8px;">🎯 Breakdown by Priority</h2>
<table style="border-collapse:collapse;font-size:13px;">
  <thead><tr style="background:#eceff1;">
    <th style="padding:8px 12px;">Priority</th><th style="padding:8px 12px;"># Requests</th><th style="padding:8px 12px;">Total ARR</th>
  </tr></thead>
  <tbody>{prio_rows}</tbody>
</table>"""

# ── Assemble & publish ────────────────────────────────────────────────────────
page_body = f"""
<p style="color:#888;font-size:12px;">Generated by <a href="{RUN_URL}">GitHub Actions</a> · {TODAY}</p>
<hr/>
{stats_html}
{top30_html}
<hr/>
{groups_html}
<hr/>
<table style="width:100%;"><tr>
  <td style="vertical-align:top;padding-right:24px;width:50%;">{dist_html}</td>
  <td style="vertical-align:top;width:50%;">{prio_html}</td>
</tr></table>
<hr/>
<h2 style="color:#3f51b5;">📝 Notes &amp; Sprint Selection</h2>
<p><em>Editable section — add ticket keys to pull into next sprint.</em></p>
<ul><li>Add tickets here (e.g. FR-1, FR-57)</li></ul>"""

title = f"FR Dashboard · {TODAY}"
print(f"Creating Confluence page: {title}")

resp = requests.post(
    f"{CONF_BASE}/wiki/rest/api/content",
    auth=conf_auth,
    json={
        "type": "page",
        "title": title,
        "space": {"key": "Product"},
        "space": {"key": "Product"},
        "ancestors": [{"id": PARENT_ID}],
        "body": {"storage": {"value": page_body, "representation": "storage"}}
    },
    headers={"Content-Type": "application/json", "X-Atlassian-Token": "no-check"}
)

if resp.status_code in (200, 201):
    url = f"{CONF_BASE}/wiki{resp.json()['_links']['webui']}"
    print(f"✅ Page created: {url}")
else:
    print(f"❌ Failed: {resp.status_code}")
    print(resp.text)
    exit(1)
