@echo off
set GITHUB_REPO=YOUR-GITHUB-USERNAME/anzca-anki-dashboard
set GITHUB_TOKEN=PASTE-YOUR-TOKEN-HERE
set ANKI_ROOT_DECKS=ANZCA primary;Pharmacology
python sync_anki.py
pause
