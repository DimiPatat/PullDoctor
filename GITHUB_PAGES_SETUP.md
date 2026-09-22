# Hosting your raid reports on GitHub Pages (fully automatic)

This sets up 🩺PullDoctor to, twice a week, automatically:
1. Find your guild's most recently uploaded Warcraft Logs report — **no link needed from you**.
2. Generate the same HTML report as `generate_html_report.py`.
3. Publish it to a public GitHub Pages site.

Runs **Monday and Wednesday at 23:15 Brussels time**, without you touching anything.

---

## Step 1 — Push this project to a GitHub repository

If it isn't already:
```powershell
cd C:\PullDoctor
git init
git add .
git commit -m "Initial commit"
```
Create a new repository on [github.com](https://github.com/new), then:
```powershell
git remote add origin https://github.com/<your-username>/<your-repo>.git
git branch -M main
git push -u origin main
```

## Step 2 — Enable GitHub Pages (Actions-based deployment)

1. On GitHub, go to your repo → **Settings** → **Pages**.
2. Under **"Build and deployment" → Source**, choose **"GitHub Actions"** (not "Deploy from a branch").
3. That's it — nothing to configure yet; the workflow itself will handle deployment the first time it runs.

## Step 3 — Add your Warcraft Logs API credentials as repository secrets

Go to **Settings → Secrets and variables → Actions → New repository secret**, and add two secrets:

| Name | Value |
|---|---|
| `WCL_CLIENT_ID` | Your Warcraft Logs API client ID |
| `WCL_CLIENT_SECRET` | Your Warcraft Logs API client secret |

(Same credentials you already use locally in your `.env` file — see them at [warcraftlogs.com](https://www.warcraftlogs.com) under your API client settings if you need to look them up again.)

## Step 4 — Tell it which guild to watch

Edit **`guild_config.py`** and fill in the three placeholder values. Open your guild's page on Warcraft Logs to find them — the URL looks like:

```
https://www.warcraftlogs.com/guild/eu/silvermoon/My+Raid+Team
                                  ^^  ^^^^^^^^^^  ^^^^^^^^^^^^
                              region  server_slug   guild_name
```

```python
GUILD_NAME = "My Raid Team"
GUILD_SERVER_SLUG = "silvermoon"
GUILD_SERVER_REGION = "eu"
```

Commit and push this change:
```powershell
git add guild_config.py
git commit -m "Configure guild for automated reports"
git push
```

## Step 5 — Test it manually (don't wait for Monday/Wednesday)

Go to your repo's **Actions** tab → **"Scheduled Raid Report"** workflow → **"Run workflow"** button → **Run workflow**.

This triggers an immediate run with `--force`, bypassing the day/time check, so you can confirm everything works right away instead of waiting for the next scheduled evening.

Watch the run's logs — within a minute or two, it will:
- Look up your guild's latest report
- Generate the HTML
- Commit it into `docs/reports/`
- Deploy to Pages

## Step 6 — Find your site's URL

Once the first successful deployment finishes, go back to **Settings → Pages** — GitHub will show your live URL there, typically:
```
https://<your-username>.github.io/<your-repo>/
```
That page lists every report generated so far, newest first, each linking to the full HTML report.

---

## How the schedule actually works (and why it's reliable across DST)

GitHub Actions' cron scheduler only runs in **UTC**, but Brussels shifts between CET (winter) and CEST (summer) — so the workflow uses **two cron entries**, one for each half of the year, switching over by month. That alone would be off by up to an hour for about a week around each DST changeover in late March/October.

To guarantee correctness anyway, `publish_scheduled_report.py` checks the **real** current Brussels time (via Python's `zoneinfo`, which has its own always-correct DST rules) before doing any real work — the cron just needs to be an *approximately* right trigger; the script itself is what guarantees it only ever actually runs on a genuine Monday/Wednesday evening, regardless of the time of year. If the cron fires at the "wrong" moment (e.g. during that DST transition week), it simply exits immediately with no changes, no wasted API calls, and no bad commit.

## Adjusting the schedule or window

- **Different days/time**: edit the two `cron:` lines in `.github/workflows/scheduled_report.yml`, and the `target_hour`/`target_minute`/`scheduled_weekdays` defaults in `schedule_guard.py` to match.
- **Widen the "how recent" search window**: if your guild hasn't raided in a while, pass `--recent-days 30` (edit the workflow's run step) so the guild-report lookup searches further back.
- **Include trash pulls / shorter fights**: add `--include-trash` and/or `--min-duration 0` to the same run step, same as `generate_html_report.py`.

## Troubleshooting

- **"No reports found" in the Action log** → your guild hasn't uploaded anything within the `--recent-days` window (default 10 days). Either wait, or manually re-run with a wider window.
- **"guild_config.py still has its placeholder values"** → you skipped Step 4.
- **Workflow runs but the site 404s** → double-check Step 2 (Pages source must be "GitHub Actions", not "Deploy from a branch"), and that the first run has actually completed successfully at least once.
