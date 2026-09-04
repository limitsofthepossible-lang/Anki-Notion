# ANZCA Anki → GitHub Pages → Notion

This dashboard keeps AnkiConnect private on your computer. A local Python script reads read-only statistics from AnkiConnect and uploads only `data/stats.json` to your GitHub repository. GitHub Pages serves the dashboard. Notion embeds the Pages URL.

## Dashboard
- overall cards seen / total / progress / unseen
- reviews today
- reviews in last 7 and 30 days
- recent 14-day activity chart
- every deck and subdeck separately
- per-deck seen/unseen, due cards, 30-day reviews and Again count
- last sync time

## Setup

### 1. Create a GitHub repository
Create a repository named something like `anzca-anki-dashboard`.

For the simplest GitHub Free setup, make it **public**. GitHub Pages is available for public repositories on GitHub Free. A public repository means the statistics file is publicly accessible, so do not put card contents, names, personal notes, or other sensitive information into this repo.

### 2. Upload this folder
Upload all files, preserving:
- `index.html`
- `sync_anki.py`
- `sync_anki.bat`
- `data/`
- `.github/workflows/deploy.yml`

Commit to `main`.

### 3. Enable GitHub Pages
Repository → Settings → Pages → Build and deployment → Source: **GitHub Actions**.

GitHub's current Pages documentation describes this workflow and the required Pages permissions.

### 4. Create a GitHub token
Create a fine-grained Personal Access Token for this repository with permission to write repository contents.

Do NOT put the token in this repository or in any file you upload.

### 5. Set two Windows environment variables
Open PowerShell and run:

$env:GITHUB_TOKEN="YOUR_TOKEN_HERE"
$env:GITHUB_REPO="YOUR_GITHUB_USERNAME/anzca-anki-dashboard"

For a permanent user-level setup, Windows can also set these through System Properties → Environment Variables. Treat the token like a password.

### 6. Test the sync
Make sure Anki is open, then double-click `sync_anki.bat`.

It should say:
`Synced Anki statistics to GitHub.`

Refresh your GitHub repository. `data/stats.json` should have appeared/updated.

### 7. Open the GitHub Pages URL
It will usually be:
https://YOUR_USERNAME.github.io/anzca-anki-dashboard/

GitHub documents that Pages URLs follow this pattern for project sites.

### 8. Embed in Notion
Notion homepage → `/embed` → paste the GitHub Pages URL.

## Keeping it updated
The included script is intentionally manual for this first version. Double-click `sync_anki.bat` after a study session, or run it whenever you want the dashboard updated.

Once the basic version works, Windows Task Scheduler can run it automatically (e.g. every 30–60 minutes while the computer is on). Do not schedule it more frequently than necessary.

## Security
The script only talks to AnkiConnect at `127.0.0.1:8765`. It does not expose AnkiConnect to the internet and does not upload card contents.

The GitHub repository receives only aggregate statistics in `data/stats.json`.

## Important interpretation
"Completed" means the card is no longer new/unseen. It does NOT mean the card is mature or permanently learned. The dashboard therefore also reports due cards and review activity.
