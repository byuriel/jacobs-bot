# Audition Radar

Polls audition listing sources, filters to **70 miles of Fontana, CA** *plus* anything
that accepts video submission, de-duplicates against a local database, and pushes new
calls to a Discord channel. Every listing fires exactly once, ever.

---

## The one design decision worth arguing about

A strict 70-mile geofence would have filtered out the single best-paying opportunity
on his map. Norwegian pays vocalists $1,300/week and casts by emailed reel — no
geography at all. Royal Caribbean takes online profile submissions from anywhere.
A radius filter alone throws those in the trash.

So a listing fires if **any** of these is true:

| Verdict | Meaning | Color in Discord |
|---|---|---|
| `IN RADIUS` | Names a city within 70 mi of Fontana | Green |
| `REMOTE / VIDEO` | Self-tape, reel submission, online profile — location irrelevant | Blue |
| `LOCATION UNKNOWN` / `UNMAPPED` | Couldn't place it. Fires anyway. | Grey |
| `PRIORITY EMPLOYER` | Royal Caribbean, NCLH, Disney, Universal, etc. | **Gold** |

That last grey case is deliberate. A false positive costs three seconds of glancing
at a phone. A false negative costs a booking. The filter fails open.

Anything that names a real city outside 70 miles **and** requires in-person attendance
gets dropped silently.

---

## Setup — runs on GitHub Actions, no VPS needed

This repo is **public**, so the webhook URL is never stored in `config.yaml` — it's
read from the `DISCORD_WEBHOOK_URL` environment variable, which the workflow supplies
from a GitHub Actions secret. `config.yaml`'s `discord_webhook` field is left blank
in git on purpose.

### 1. Get a Discord webhook

In Discord: **Server Settings → Integrations → Webhooks → New Webhook.**
Point it at a channel used for nothing else — `#auditions` — so notifications don't
get buried under conversation. Copy the URL.

On his phone, long-press that channel → **Notification Settings → All Messages**.
A radar he doesn't get pinged by is a bookmark folder with extra steps.

### 2. Add the webhook as a repo secret

GitHub → repo **Settings → Secrets and variables → Actions → New repository secret**.
Name: `DISCORD_WEBHOOK_URL`. Value: the webhook URL from step 1.

### 3. Allow the workflow to push

GitHub → repo **Settings → Actions → General → Workflow permissions** → select
**"Read and write permissions."** The workflow commits the updated `seen.sqlite3`
back to the repo after every run (that's how dedup state survives between runs on a
disposable Actions runner) — it needs push access to do that.

### 4. Merge this branch to `main`

Scheduled GitHub Actions workflows only fire off the **default branch**. Until this
branch is merged, the cron trigger is inert — you can still run it manually in the
meantime (next step).

### 5. Run it by hand from the Actions tab, in order

Repo → **Actions → Audition Radar → Run workflow**, picking the `mode` input each
time:

1. `mode: test-discord` — confirms a green "Audition Radar is live" card lands in
   the channel. If it doesn't, nothing else matters — fix this first.
2. `mode: dry-run` — prints hits to the workflow log, posts nothing, records nothing.
   Check the per-source item counts; the Playbill/BroadwayWorld CSS selectors were
   never validated against live markup, so a source returning 0 items where it
   shouldn't means the selector in `config.yaml` needs a fix.
3. `mode: backfill` — **run exactly once.** Marks everything currently live as
   already-seen and commits `seen.sqlite3` back to the repo, so the first scheduled
   run doesn't dump 40-60 stale listings into the channel and get the whole thing
   muted on day one. Never re-run this — it would swallow real new listings that
   posted between setup and now.

### 6. Let the schedule take over

`.github/workflows/audition-radar.yml` runs `mode: run` every 30 minutes
automatically once merged to `main` — nothing further to do. Confirm it's actually
firing under the repo's **Actions** tab.

Thirty minutes is the right cadence. Appointment-only calls fill in days, not minutes,
and hammering these sites gets the runner's IP blocked — which is a self-inflicted
outage.

### Local dev / testing

```bash
pip3 install requests beautifulsoup4 feedparser pyyaml
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
python3 audition_radar.py --dry-run     # prints hits, posts nothing, records nothing
python3 test_offline.py                 # validates filters, geo, dedup with fixtures
```

Run `test_offline.py` any time you edit the filters in `config.yaml`. It catches
the case where a tweak accidentally starts dropping real calls. Never put the real
webhook in `config.yaml` or commit it — only ever pass it as `DISCORD_WEBHOOK_URL`.

---

## Failure modes it handles

**One dead source doesn't kill the run.** Each source is wrapped independently; the
others still report.

**A totally dead radar announces itself.** If *every* source fails, it posts a red
alert to Discord. This matters more than it sounds: a broken scraper and a quiet
casting week produce identical silence, and silence gets interpreted as "nothing is
happening" for weeks.

**RSS feeds fail loudly.** `feedparser` returns an empty result on a dead feed rather
than raising, which would look like "no auditions." That's converted into an error.

**URL tracking params don't create duplicates.** Dedup keys on host + path only.

---

## Tuning

Everything is in `config.yaml`, no Python required.

- **Too much noise** → add terms to `hard_exclude`, or trim `include`.
- **Missed a call you know about** → check `radar.log` for its title, then find which
  filter dropped it. Usually the fix is a word in `include`.
- **A city keeps showing as UNMAPPED** → add it to the `CITIES` dict in the .py file
  with its coordinates. One line.
- **New source** → add an entry under `sources`. `type: rss` needs only a URL.
  `type: html` needs CSS selectors, which you get from Chrome DevTools → right-click
  the listing → Inspect → Copy selector.

---

## What this does NOT cover, honestly

**Four sources are enabled; three are disabled and marked in the config.** Royal
Caribbean's own audition page, Disney Auditions, and Actors' Equity Casting Call all
render their listings with JavaScript, so a plain HTTP GET returns an empty shell.
Two fixes, in order of effort:

1. Open the page in Chrome DevTools → **Network → XHR**, find the JSON endpoint the
   page actually calls, and point a source at that. Usually 20 minutes per site and
   far more reliable than scraping HTML.
2. Add a Playwright-based fetcher (`pip install playwright && playwright install
   chromium`) and register a `"browser"` entry in the `FETCHERS` dict.

This is less costly than it sounds: **the August 18 Royal Caribbean Los Angeles call
surfaced on Playbill**, which is enabled. Aggregators carry most of what the primary
sites post. But they lag by a day or two, and for appointment-only calls that lag is
real. Worth fixing option 1 when there's an afternoon.

**Actors Access and Backstage require a paid login** and are not scraped. Actors
Access in particular is where Royal Caribbean routes invited appointments — he should
have a profile there regardless of this tool, and check it himself.

**Sources were not live-tested from here.** Neither the Claude session that wrote
this nor the one that wired up GitHub Actions had outbound access to those domains,
so the geo math, filtering, dedup, and Discord payload construction are all verified
against fixtures, but the HTML selectors for Playbill and BroadwayWorld are
best-guess and may need adjusting on first real run. Run `mode: dry-run` from the
Actions tab first and check that the item counts are non-zero.

**Be polite.** `politeness_seconds` is set to 2. Don't lower it. Getting the runner's
IP banned from Playbill costs more than it saves.
