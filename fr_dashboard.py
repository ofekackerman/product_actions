#!/usr/bin/env python3
"""
FR Dashboard Generator
Fetches top 30 FR tickets by FR Score from Jira,
groups similar issues using Claude AI,
and publishes a Confluence dashboard page.
"""

import os
import json
import re
import requests
from datetime import datetime, timezone
from collections import defaultdict

# ─── Config from env vars ───────────────────────────────────────────────────
JIRA_EMAIL        = os.environ["JIRA_EMAIL"]
JIRA_TOKEN        = os.environ["JIRA_TOKEN"]
CONFLUENCE_TOKEN  = os.environ["CONFLUENCE_TOKEN"]   # same token works
OPENAI_KEY        = os.environ["OPENAI_API_KEY"]
CONFLUENCE_HOST   = os.environ.get("CONFLUENCE_HOST", "cloudshare.atlassian.net")
CONFLUENCE_SPACE  = os.environ.get("CONFLUENCE_SPACE", "Product")
CONFLUENCE_PARENT = os.environ.get("CONFLUENCE_PARENT_ID", "3533963267")
JIRA_HOST         = os.environ.get("JIRA_HOST", "cloudshare.atlassian.net")
JIRA_PROJECT      = os.environ.get("JIRA_PROJECT", "FR")
FR_SCORE_FIELD    = "customfield_12330"   # confirmed field for FR Score

# ─── 1. Fetch top 30 FR tickets ─────────────────────────────────────────────
def fetch_fr_tickets(limit=30):
    # Jira Cloud now requires the /search/jql endpoint with cursor-based pagination
    url = f"https://{JIRA_HOST}/rest/api/3/search/jql"
    headers = {"Accept": "application/json"}
    auth = (JIRA_EMAIL, JIRA_TOKEN)

    all_issues = []
    next_page_token = None

    while len(all_issues) < limit:
        payload = {
            "jql": f'project = {JIRA_PROJECT} ORDER BY cf[12330] DESC',
            "maxResults": min(50, limit - len(all_issues)),
            "fields": [
                "summary", "description", "status", "priority",
                "created", FR_SCORE_FIELD, "issuetype",
            ]
        }
        if next_page_token:
            payload["nextPageToken"] = next_page_token

        resp = requests.post(url, headers=headers, auth=auth, json=payload)
        resp.raise_for_status()
        data = resp.json()
        issues = data.get("issues", [])
        if not issues:
            break
        all_issues.extend(issues)
        next_page_token = data.get("nextPageToken")
        if not next_page_token:
            break

    return all_issues[:limit]


# ─── 2. Parse ARR / account info from description ────────────────────────────
def parse_description_meta(description_text: str) -> dict:
    """Extract structured metadata lines from FR description text."""
    meta = {
        "account_arr": None,
        "account_status": None,
        "created_by": None,
        "fr_created_date": None,
        "salesforce_url": None,
    }
    if not description_text:
        return meta

    patterns = {
        "account_arr":    r"Account ARR:\s*([\d,]+)",
        "account_status": r"Account Status:\s*(\w+)",
        "created_by":     r"Created By:\s*(.+?)(?:\n|$)",
        "fr_created_date":r"FR Created Date:\s*([\d\-]+)",
        "salesforce_url": r"(https://cloudshare\.lightning\.force\.com/lightning/r/FR_ticket__c/[^\s\)]+)",
    }
    for key, pattern in patterns.items():
        m = re.search(pattern, description_text, re.IGNORECASE)
        if m:
            meta[key] = m.group(1).strip()

    return meta


def extract_plain_text(description) -> str:
    """Flatten Atlassian Document Format or plain string to plain text."""
    if description is None:
        return ""
    if isinstance(description, str):
        return description
    # ADF format
    texts = []
    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "text":
                texts.append(node.get("text", ""))
            for child in node.get("content", []):
                walk(child)
        elif isinstance(node, list):
            for item in node:
                walk(item)
    walk(description)
    return " ".join(texts)


# ─── 3. Group tickets using OpenAI ───────────────────────────────────────────
def group_tickets_with_ai(tickets: list) -> list:
    """
    Send ticket list to OpenAI and ask it to group similar ones.
    Returns a list of groups: [{theme, tickets: [key, ...], summary}]
    """
    ticket_lines = []
    for t in tickets:
        key     = t["key"]
        title   = t["fields"]["summary"]
        score   = t["fields"].get(FR_SCORE_FIELD, 0) or 0
        desc    = extract_plain_text(t["fields"].get("description"))[:300]
        ticket_lines.append(f"- {key} (score={score}): {title} | {desc}")

    prompt = f"""You are a product manager assistant. Below is a list of {len(tickets)} Feature Request tickets (key, FR score, title, excerpt). 

Group tickets that describe the same or closely related customer need into named themes. A theme must have at least 1 ticket. Tickets that are truly unique stay as a single-ticket group.

For each group output:
- theme: short name (3-6 words)
- summary: 1-sentence description of what customers are asking for
- tickets: array of Jira ticket keys in this group

Return ONLY valid JSON, no markdown, no explanation. Format:
[
  {{"theme": "...", "summary": "...", "tickets": ["FR-1", "FR-2"]}},
  ...
]

Tickets:
{chr(10).join(ticket_lines)}
"""

    response = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENAI_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": "gpt-4o",
            "max_tokens": 2000,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=60,
    )
    response.raise_for_status()
    raw = response.json()["choices"][0]["message"]["content"].strip()

    # Strip possible ```json fences
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    groups = json.loads(raw)
    return groups


# ─── 4. Build Confluence page content (HTML) ─────────────────────────────────
def build_confluence_html(tickets: list, groups: list, run_url: str) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Build a lookup: key → ticket
    ticket_map = {t["key"]: t for t in tickets}

    # ── Summary stats ──
    total_tickets   = len(tickets)
    total_groups    = len(groups)
    top_score       = max((t["fields"].get(FR_SCORE_FIELD) or 0) for t in tickets) if tickets else 0
    arr_values      = []
    for t in tickets:
        desc  = extract_plain_text(t["fields"].get("description"))
        meta  = parse_description_meta(desc)
        if meta["account_arr"]:
            try:
                arr_values.append(int(meta["account_arr"].replace(",", "")))
            except ValueError:
                pass
    total_arr = sum(arr_values)

    html_parts = []

    # Header info
    html_parts.append(f"""
<p>Generated by <a href="{run_url}">GitHub Actions</a> · {today}</p>
<hr/>

<h2>📊 Summary</h2>
<table data-layout="default">
  <tbody>
    <tr>
      <th><strong>Metric</strong></th>
      <th><strong>Value</strong></th>
    </tr>
    <tr><td>Top 30 FRs analysed</td><td>{total_tickets}</td></tr>
    <tr><td>Unique theme groups</td><td>{total_groups}</td></tr>
    <tr><td>Highest FR Score</td><td>{int(top_score)}</td></tr>
    <tr><td>Combined ARR (where available)</td><td>${total_arr:,.0f}</td></tr>
    <tr><td>Report date</td><td>{today}</td></tr>
  </tbody>
</table>
<hr/>

<h2>🗂️ Grouped Feature Requests (by Theme)</h2>
<p><em>Tickets within each group describe the same or closely related customer need, identified automatically by AI. Sorted by combined group FR Score (highest first).</em></p>
""")

    # Sort groups by sum of scores (highest first)
    def group_score(group):
        keys = group.get("tickets", [])
        return sum((ticket_map.get(k, {}).get("fields", {}).get(FR_SCORE_FIELD) or 0) for k in keys)

    sorted_groups = sorted(groups, key=group_score, reverse=True)

    for idx, group in enumerate(sorted_groups, 1):
        theme   = group.get("theme", "Ungrouped")
        summary = group.get("summary", "")
        keys    = group.get("tickets", [])

        # Compute group-level aggregate
        group_tickets     = [ticket_map[k] for k in keys if k in ticket_map]
        group_total_score = sum((t["fields"].get(FR_SCORE_FIELD) or 0) for t in group_tickets)
        ticket_count      = len(group_tickets)

        # ARR for this group
        group_arr = 0
        for t in group_tickets:
            desc = extract_plain_text(t["fields"].get("description"))
            meta = parse_description_meta(desc)
            if meta["account_arr"]:
                try:
                    group_arr += int(meta["account_arr"].replace(",", ""))
                except ValueError:
                    pass

        html_parts.append(f"""
<h3>{idx}. {theme}</h3>
<p>{summary}</p>
<table data-layout="default">
  <tbody>
    <tr>
      <th>Ticket</th>
      <th>Title</th>
      <th>FR Score</th>
      <th>Account ARR</th>
      <th>Account Status</th>
      <th>Created By</th>
      <th>FR Date</th>
      <th>Priority</th>
      <th>Salesforce</th>
    </tr>
""")

        for t in sorted(group_tickets, key=lambda x: (x["fields"].get(FR_SCORE_FIELD) or 0), reverse=True):
            key      = t["key"]
            title    = t["fields"]["summary"]
            score    = int(t["fields"].get(FR_SCORE_FIELD) or 0)
            priority = t["fields"].get("priority", {}).get("name", "-") if t["fields"].get("priority") else "-"
            jira_url = f"https://{JIRA_HOST}/browse/{key}"
            desc     = extract_plain_text(t["fields"].get("description"))
            meta     = parse_description_meta(desc)
            arr_str  = f"${int(meta['account_arr'].replace(',','')):,}" if meta["account_arr"] else "-"
            status_str   = meta["account_status"] or "-"
            created_by   = meta["created_by"] or "-"
            fr_date      = meta["fr_created_date"] or t["fields"].get("created", "")[:10]
            sf_link      = f'<a href="{meta["salesforce_url"]}">SF</a>' if meta["salesforce_url"] else "-"

            html_parts.append(f"""
    <tr>
      <td><a href="{jira_url}">{key}</a></td>
      <td>{title}</td>
      <td><strong>{score}</strong></td>
      <td>{arr_str}</td>
      <td>{status_str}</td>
      <td>{created_by}</td>
      <td>{fr_date}</td>
      <td>{priority}</td>
      <td>{sf_link}</td>
    </tr>""")

        html_parts.append(f"""
  </tbody>
</table>
<p>
  <strong>Group total FR Score:</strong> {int(group_total_score)} &nbsp;|&nbsp;
  <strong>Tickets:</strong> {ticket_count} &nbsp;|&nbsp;
  <strong>Combined ARR:</strong> ${group_arr:,.0f}
</p>
<br/>
""")

    # Full ranked list at the bottom
    html_parts.append("""
<hr/>
<h2>📋 Full Top-30 Ranked List</h2>
<p>All 30 tickets ranked purely by FR Score, for reference.</p>
<table data-layout="default">
  <tbody>
    <tr>
      <th>Rank</th>
      <th>Ticket</th>
      <th>Title</th>
      <th>FR Score</th>
      <th>Account ARR</th>
      <th>Account Status</th>
      <th>Created By</th>
      <th>FR Date</th>
      <th>Priority</th>
    </tr>
""")
    for rank, t in enumerate(tickets, 1):
        key      = t["key"]
        title    = t["fields"]["summary"]
        score    = int(t["fields"].get(FR_SCORE_FIELD) or 0)
        priority = t["fields"].get("priority", {}).get("name", "-") if t["fields"].get("priority") else "-"
        jira_url = f"https://{JIRA_HOST}/browse/{key}"
        desc     = extract_plain_text(t["fields"].get("description"))
        meta     = parse_description_meta(desc)
        arr_str  = f"${int(meta['account_arr'].replace(',','')):,}" if meta["account_arr"] else "-"
        html_parts.append(f"""
    <tr>
      <td>{rank}</td>
      <td><a href="{jira_url}">{key}</a></td>
      <td>{title}</td>
      <td><strong>{score}</strong></td>
      <td>{arr_str}</td>
      <td>{meta["account_status"] or "-"}</td>
      <td>{meta["created_by"] or "-"}</td>
      <td>{meta["fr_created_date"] or t["fields"].get("created", "")[:10]}</td>
      <td>{priority}</td>
    </tr>""")

    html_parts.append("""
  </tbody>
</table>
<hr/>

<h2>✅ Sprint Selection</h2>
<p>Use this section to track which tickets from the report above should be prioritised for development. Add keys below and check them off as decisions are made.</p>
<ul>
  <li>Add ticket keys here (e.g. FR-57, FR-85)</li>
</ul>

<h2>📝 Notes</h2>
<p><em>This section is editable. Anyone with access can add notes, decisions, or context.</em></p>
""")

    return "".join(html_parts)


# ─── 5. Publish to Confluence ─────────────────────────────────────────────────
def publish_to_confluence(title: str, html_body: str) -> str:
    """Create (or update if title already exists) a Confluence page."""
    base = f"https://{CONFLUENCE_HOST}/wiki"
    auth = (JIRA_EMAIL, CONFLUENCE_TOKEN)
    headers = {"Accept": "application/json", "Content-Type": "application/json"}

    # Check if page with same title already exists under parent
    search_url = f"{base}/rest/api/content"
    search_params = {
        "title": title,
        "spaceKey": CONFLUENCE_SPACE,
        "type": "page",
        "expand": "version",
    }
    search_resp = requests.get(search_url, auth=auth, headers=headers, params=search_params)
    search_resp.raise_for_status()
    results = search_resp.json().get("results", [])

    payload = {
        "type": "page",
        "title": title,
        "ancestors": [{"id": CONFLUENCE_PARENT}],
        "space": {"key": CONFLUENCE_SPACE},
        "body": {
            "storage": {
                "value": html_body,
                "representation": "storage",
            }
        },
    }

    if results:
        # Update existing page
        page_id = results[0]["id"]
        version = results[0]["version"]["number"] + 1
        payload["version"] = {"number": version}
        update_url = f"{base}/rest/api/content/{page_id}"
        resp = requests.put(update_url, auth=auth, headers=headers, json=payload)
        resp.raise_for_status()
        page_id = resp.json()["id"]
        print(f"✅ Updated Confluence page ID {page_id}: {title}")
    else:
        # Create new page
        resp = requests.post(search_url, auth=auth, headers=headers, json=payload)
        resp.raise_for_status()
        page_id = resp.json()["id"]
        print(f"✅ Created Confluence page ID {page_id}: {title}")

    return f"https://{CONFLUENCE_HOST}/wiki/spaces/{CONFLUENCE_SPACE}/pages/{page_id}"


# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    run_url = os.environ.get("GITHUB_RUN_URL", "https://github.com")
    today   = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    title   = f"FR Score Dashboard · {today}"

    print("📥 Fetching top 30 FR tickets from Jira …")
    tickets = fetch_fr_tickets(limit=30)
    print(f"   Got {len(tickets)} tickets")

    print("🤖 Grouping tickets with Claude AI …")
    groups = group_tickets_with_ai(tickets)
    print(f"   Identified {len(groups)} theme groups")

    print("🔨 Building Confluence page …")
    html_body = build_confluence_html(tickets, groups, run_url)

    print("🚀 Publishing to Confluence …")
    page_url = publish_to_confluence(title, html_body)
    print(f"   Published → {page_url}")

    # Write URL to GitHub output if available
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as f:
            f.write(f"page_url={page_url}\n")


if __name__ == "__main__":
    main()
