# Radar — stock idea tracker

Single-file, offline-capable dashboard for tracking stock ideas from first spark to verdict.
Works in any browser (phone, tablet, PC). Optional GitHub sync keeps all devices on the same data.

## Setup (once)

1. Create a GitHub repo, e.g. `radar`. Put `index.html` in it. Commit.
2. Settings → Pages → Source: *Deploy from a branch*, branch `main`, folder `/ (root)`.
   Your app is now at `https://<username>.github.io/radar/`.
   On the iPhone open it in Safari → Share → "Add to Home Screen": you get the Radar icon and it opens full-screen like an app.
3. Token for sync: GitHub → Settings → Developer settings → Personal access tokens → **Fine-grained tokens**
   → Generate. Repository access: *Only select repositories* → `radar`.
   Permissions → Repository permissions → **Contents: Read and write**. Copy the token.
4. In the app: `⋯` → Owner / repo `<username>/radar`, branch `main`, path `ideas.json`, paste the token → Save.
   Do this once per device. The app creates `ideas.json` in the repo on the first save.

Without a token the app still works, data stays in that browser's localStorage. Export/Import JSON is always available as a fallback.

## Live prices (GitHub Action + Yahoo Finance)

A scheduled GitHub Action (`.github/workflows/prices.yml`) reads `ideas.json`, fetches a quote for every card via
[yfinance](https://github.com/ranaroussi/yfinance) and commits `prices.json`. The app reads that file. Free for public repos, no API key.

Setup:
1. Upload the `.github` folder (with `workflows/prices.yml` and `scripts/fetch_prices.py`) into the repo root.
2. Repo → Settings → Actions → General → *Workflow permissions* → **Read and write permissions** → Save.
3. Actions tab → *prices* → **Run workflow** once to test. `prices.json` appears in the repo.
4. Optional, for the "Refresh prices now" button in `⋯`: edit your token and add **Actions: Read and write**.

Runs every 2 hours (07:17–21:17 UTC, Mon–Fri) and whenever `ideas.json` changes, so a new card gets its price within a minute. Change the `cron` line to taste.

Symbols: the card's **Yahoo symbol** field, or the ticker if empty. US stocks work as-is (`KO`, `PEP`);
everything else needs the exchange suffix: `ENI.MI` (Milan), `SAP.DE` (Xetra), `NESN.SW` (Zurich), `ULVR.L` (London, in GBp), `NOVN.SW`, `ASML.AS`.
Cards without a quote show "⚠ no quote".

Notes: Yahoo data is unofficial and can lag ~15 min or occasionally fail from GitHub's servers — then the last good price is kept and marked with a yellow dot.
GitHub pauses scheduled workflows in repos with no activity for 60 days; one click in the Actions tab re-enables it.

## Workflow

Radar → Researching → Watchlist → Bought → Passed / Sold

- Each card is a *thesis at a point in time*: price and date when logged, thesis, bull, bear, kill criteria, conviction, "buy below", next review date, dated journal notes.
- **Scorecard** shows every idea with logged price vs. current price (live quote, or typed in by hand as fallback), day change and trailing dividend yield. Passed ideas show what you missed, Bought ideas show how it went.
- Cards with an overdue review date are flagged.

## Import from Claude

At the end of a stock discussion, ask Claude for "a Radar card" and paste the JSON into `Import from Claude`. Format (all fields optional except `ticker` or `name`):

```json
{
  "ticker": "PEP",
  "symbol": "PEP",
  "name": "PepsiCo",
  "sector": "Consumer staples",
  "source": "Claude chat 2026-11-05",
  "status": "researching",
  "loggedDate": "2026-11-05",
  "loggedPrice": 142.30,
  "currency": "USD",
  "buyBelow": 135,
  "conviction": 3,
  "thesis": "…",
  "bull": "…",
  "bear": "…",
  "kill": "I'm wrong if …",
  "nextReview": "2027-05-05",
  "notes": "optional first journal entry"
}
```

`status` is one of `radar`, `researching`, `watchlist`, `bought`, `out`. An array of several objects is fine too. Code fences around the JSON are stripped automatically.

## Sync details

- `ideas.json` is read on open and whenever the tab becomes visible again; saved ~1.5 s after each change.
- Merge is per card by `id`; the newer `updatedAt` wins. Deleted cards are kept as tombstones so a deletion on one device isn't undone by another.
- If two devices save at the same moment the app re-pulls, merges and retries once.
