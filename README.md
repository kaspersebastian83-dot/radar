# Radar — stock idea tracker

Single-file, offline-capable dashboard for tracking stock ideas from first spark to verdict.
Works in any browser (phone, tablet, PC). Optional GitHub sync keeps all devices on the same data.

**Version:** shown next to the logo (desktop) and in `⋯` (always). Tap it for the changelog.
If the version you see is older than the newest entry in the `CHANGELOG` list at the top of the script in `index.html`, your browser has an old copy cached: pull to refresh, or close and reopen the home-screen app.
Every change to the app bumps `VERSION` and adds a changelog entry (major: new area, minor: new feature, patch: fix).

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

## Portfolio tab

Separate from the idea tracking: what you actually own, and whether any of it is a good moment to add.

- `portfolio.json` (in the repo, public): one entry per holding with ISIN, Yahoo symbol, type (stock / fund / bond fund), optional `addBelow` and `maxWeight`, optional `idea` (Radar ticker it links to), note. **No share counts, no values.**
- Share counts come from the Trade Republic *Vermögensübersicht* PDF: Portfolio → "Update from Trade Republic PDF". It's parsed in the browser (pdf.js) and kept in that browser's localStorage only. Import it once per device; re-import when you've bought or sold.
- **Add score** = the Buy score with `addBelow` in place of `buyBelow` (60 / 30 / 10; Add zone only at or below the level). Without an own add level, a Radar card on the same symbol lends its buy-below. Without any level a stock tops out at "Getting close", never "Add zone".
- **Max weight** (default 7 %, editable in the tab, overridable per holding): a stock above it is listed as *Full* and not flagged, however cheap. Funds are shown for weight and yield, not scored unless you give them an add level.
- A live quote more than 20 % off the statement price is flagged: usually the wrong Yahoo symbol.
- The badge on the Portfolio tab counts holdings in the add zone.
- Claude can update add levels with an Import from Claude block: `{"holdings":[{"symbol":"ALV.DE","addBelow":420}]}` (matched by id, ISIN or symbol).

## Next € tab (v3.2.0)

"Where does my next €1,000 go?" Every idea and stock holding with a valuation gets an expected return a year over ~5 years: dividend yield × (1 − withholding Germany doesn't credit) + long-run growth (`val.g`, else 5-yr dividend growth capped at 6 %) + annualised catch-up to `val.base`. With a statement on the device, soft penalties apply when the amount would push one sector (Yahoo sector) above 20 % of the depot or one foreign currency above 40 %, and holdings that would go over their max weight are held back. Only stocks at or below their level are recommended; the bond fund's yield is shown as the low-risk line. German tax is the same for all and left out.

## Decision journal (v3.3.0)

Decisions are journal notes with a `kind` (`buy`, `add`, `trim`, `sell`, `pass`, `review`), the price and symbol at the time, and the tapped answers (`why`, `anyway`, `look`, `reason`, `checked`, `changed`, `checks`). Buying (idea → Bought, or "I added" on a holding) runs an 8-point checklist; reviews show "I'm wrong if" and record Still holds / Changed / Broken. A broken thesis (`broken` date on the card or holding) holds the stock back in Next €. Passing stores `passed: {date, price, reason}`. ⋯ → Decision journal lists them all with the price then and now.

## Since v3.4.0

- Optional second level: `buyBelow2` on idea cards, `addBelow2` on holdings ("back up the truck"); shown on the 52-week bar and in rows/cards.
- Conviction on holdings (`conviction` 1–5, else the linked Radar card's stars): max weight = default (3★) ± 1.5 points per star, unless `maxWeight` is set.
- `sw.js`: network-first service worker, so the home-screen app opens offline with the last data.
- A red note on top when `prices.json` hasn't changed for longer than the weekday/weekend schedule allows.

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
  "val": {
    "date": "2026-11-05", "price": 142.30,
    "methods": [{"t": "18× 2027e EPS $8.40", "v": 151}, {"t": "Gordon on FCF/sh $7.20, g 3 %, 8 % hurdle", "v": 148}],
    "base": 150, "discount": 10, "why": "wide moat", "g": 3,
    "bear": 110, "bearWhy": "$7 EPS × 16×",
    "reverse": "today's price implies ~4 %/yr growth"
  },
  "conviction": 3,
  "thesis": "…",
  "bull": "…",
  "bear": "…",
  "kill": "I'm wrong if …",
  "nextReview": "2027-05-05",
  "notes": "optional first journal entry"
}
```

`status` is one of `radar`, `researching`, `watchlist`, `bought`, `out`.

`val` (optional, since v3.1.0) is the valuation behind the level: `base` defaults to the average of the method values, `discount` is the margin of safety in %, `bear` the bear-case value. Without `buyBelow`, the import sets it to base × (1 − discount). The card shows how far today's price is from fair value and from the bear case; the detail sheet shows the whole valuation and marks it stale once results came out after `date` (or after ~6 months). Holdings take the same `val` object in a `{"holdings":[…]}` block; `addBelow` is then derived the same way unless given. An array of several objects is fine too. Code fences around the JSON are stripped automatically.

## Sync details

- `ideas.json` is read on open and whenever the tab becomes visible again; saved ~1.5 s after each change.
- Merge is per card by `id`; the newer `updatedAt` wins. Deleted cards are kept as tombstones so a deletion on one device isn't undone by another.
- If two devices save at the same moment the app re-pulls, merges and retries once.
