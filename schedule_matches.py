#!/usr/bin/env python3
"""schedule_matches.py — inasoma mechi kutoka fawa-live na kuanzisha 'Watch match' kwa mechi zinazokaribia kuanza (au zilizoanza hivi punde).
Haizindui mechi iliyoshaanzishwa (inaangalia runs za masaa 8 yaliyopita). Env: CONTENT_URL, CONTENT_KEY, GITHUB_TOKEN, GITHUB_REPOSITORY,
REF_NAME, LEAD_MIN (default 25), LATE_MIN (default 15), MATCH_TZ (default Africa/Lagos = saa ya chanzo cha data), WATCH_DRY_RUN (default true)."""
import json, os, re, sys, urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

E = os.environ.get
BASE, KEY = E("CONTENT_URL", "").rstrip("/") + "/functions/v1/fawa-live", E("CONTENT_KEY", "")
API, REPO, TOKEN, REF = E("GITHUB_API", "https://api.github.com"), E("GITHUB_REPOSITORY", ""), E("GITHUB_TOKEN", ""), E("REF_NAME", "main")
DAR = ZoneInfo("Africa/Dar_es_Salaam")   # watch_match.py inatarajia KICKOFF kwa saa ya Dar es Salaam
TZ, LEAD, LATE, DRY = ZoneInfo(E("MATCH_TZ", "Africa/Lagos")), int(E("LEAD_MIN", "25")), int(E("LATE_MIN", "100")), E("WATCH_DRY_RUN", "true")


def http(url, method="GET", headers=None, body=None):
    req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(), method=method,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    return json.loads(raw) if raw else {}


def gh(path, method="GET", body=None):
    return http(API + path, method, {"Authorization": "Bearer " + TOKEN, "Accept": "application/vnd.github+json"}, body)


def kickoff(info, now):
    m = re.search(r"(\d{1,2}):(\d{2})\s*$", info or "")
    if not m:
        return None
    base = now.replace(hour=int(m[1]), minute=int(m[2]), second=0, microsecond=0)
    return min((base + timedelta(days=d) for d in (-1, 0, 1)), key=lambda k: abs((k - now).total_seconds()))


def main():
    now = datetime.now(TZ)
    matches = http(BASE + "/matches", headers={"Authorization": "Bearer " + KEY}).get("matches", [])
    runs = gh(f"/repos/{REPO}/actions/workflows/watch-match.yml/runs?per_page=100").get("workflow_runs", [])
    recent = {r.get("display_title", "") for r in runs
              if now - datetime.fromisoformat(r["created_at"].replace("Z", "+00:00")).astimezone(TZ) < timedelta(hours=8)}
    started, no_time, no_slug, outside, dup = 0, 0, 0, 0, 0
    print(f"Saa ya chanzo cha data ({TZ.key}): {now:%H:%M} | saa ya Dar: {now.astimezone(DAR):%H:%M} | dirisha: dakika -{LATE} hadi +{LEAD}")
    for i, m in enumerate(matches):
        k = kickoff(m.get("info"), now)
        if i < 8:   # sampuli ya data ili kuona sehemu zipi zipo
            print(f"  • {m.get('title')!r} info={m.get('info')!r} slug={m.get('slug')!r} -> kickoff={k:%H:%M}" if k else
                  f"  • {m.get('title')!r} info={m.get('info')!r} slug={m.get('slug')!r} -> hakuna saa. Sehemu: {sorted(m)}")
        if not m.get("slug"):
            no_slug += 1; continue
        if not k:
            no_time += 1; continue
        mins = (k - now).total_seconds() / 60
        if not -LATE <= mins <= LEAD:
            outside += 1; continue
        if f"Watch {m['slug']}" in recent:
            dup += 1; continue
        gh(f"/repos/{REPO}/actions/workflows/watch-match.yml/dispatches", "POST",
           {"ref": REF, "inputs": {"slug": m["slug"], "title": m.get("title", ""), "kickoff": k.astimezone(DAR).strftime("%H:%M"), "logo": m.get("logo") or "", "dry_run": DRY}})
        started += 1
        print(f"▶️ {m.get('title')} ({k:%H:%M} {TZ.key}, baada ya dakika {mins:.0f}) -> Watch match imeanzishwa (dry_run={DRY})")
    print(f"Mechi zilizopo: {len(matches)} | zilizoanzishwa sasa: {started} | hazina saa: {no_time} | hazina slug: {no_slug} | nje ya dirisha: {outside} | zimeshaanzishwa: {dup}")


if __name__ == "__main__":
    sys.exit(main())
