# tcg-drop-bot

Pokémon + One Piece release calendar for AU retail. Scores every release for **flip** and **hold**, syncs it to Google Calendar (+ an `.ics` feed), and sends Telegram alerts. Runs daily on GitHub Actions, same pattern as card-bot.

## What it does each run (07:47 Sydney)
1. **Discovers** new sets (pokemontcg.io, official One Piece EN product page) and merges them with `data/releases.yaml` (manual entries always win).
2. **Prices** each release on eBay AU (Browse API, trimmed median of sealed listings — asking price, not sold).
3. **Gives a flip verdict.** The flip score (0–100) is built from:
   - eBay premium over AU RRP: 35%
   - JP box premium: 25%
   - chase cards: 20%
   - set type: 15%
   - reprint risk: 5%

   Verdicts:
   - 🟢 **BUY** (flip ≥70): needs real price data and ≥20% profit after eBay fees + postage, buying at RRP.
   - 🟡 **WATCH** (45–69): also the default when there isn't enough data yet. Unknown ≠ pass.
   - ⛔ **PASS**: weak flip signals, or any hard rule fires, whatever the score:
     - no profit after fees
     - margin under 20%
     - JP version already at/below RRP
     - high reprint risk with no strong premium

   A hold score is still calculated and flagged as a "hold candidate" when a set isn't a flip but should hold.
4. **Calendar**: preorder-open, EN release and JP release (leading indicator) events. Colour-coded green/yellow/grey by verdict, with 7-day + 1-day reminders on BUYs.
5. **Telegram**: each alert shows the verdict, estimated profit per unit, the reasons, and "Where" links to Big W, Kmart, Target, EB Games, JB Hi-Fi and your LGS (add LGS URLs in `config.yaml`). It sends:
   - new set found
   - verdict change
   - date moved
   - reminders 14/7/1 days before release/preorders
   - overseas offer clearing your margin
   - Monday digest
6. **Outcomes**: logs the eBay price 14/90/365 days after release into `data/outcomes.json` — the data to retune weights later.

## Setup (~20 min)

1. **Repo**: push this folder to a new GitHub repo (e.g. `miguel190M/tcg-drop-bot`).
2. **Secrets**: add under Settings → Secrets and variables → Actions:

| Secret | Where from |
|---|---|
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Reuse your existing bot or make a new one with @BotFather |
| `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET` | Same keys as card-bot |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | See step 3 — paste the whole JSON key |
| `GOOGLE_CALENDAR_ID` | See step 3 |
| `POKEMONTCG_API_KEY` | Optional, free at dev.pokemontcg.io (higher rate limit) |

3. **Google Calendar**:
   1. In console.cloud.google.com: create a project → enable **Google Calendar API** → IAM → Service Accounts → create one → Keys → Add key → JSON.
   2. In Google Calendar: create a new calendar, e.g. "TCG Drops". Open its Settings → *Share with specific people* → add the service account's email with **Make changes to events**.
   3. Under *Integrate calendar*, copy the **Calendar ID** into `GOOGLE_CALENDAR_ID`.
4. **First run**: Actions → tcg-drop-bot → Run workflow (tick "digest").

**ICS fallback**: with a public repo, any calendar app can subscribe to `https://raw.githubusercontent.com/<you>/tcg-drop-bot/main/data/releases.ics`. Google refreshes subscribed feeds slowly, which is why the API is the primary route.

## Day-to-day
The bot gets better as you feed `data/releases.yaml`. When a 🆕 "needs review" alert lands, add:
- `chase:` the headline Pokémon/characters on the special illustration rares / alt arts. This is the biggest score driver.
- `au_rrp:` e.g. `{etb: 89.95, booster_box: 249}`. This enables the preorder-premium signal.
- `ebay_query:` a tight search string for the sealed product.
- `jp_box_rrp_jpy` / `jp_box_market_jpy`: the JP box price versus RRP. This is the best early signal, and it's available months before the EN release.
- `preorder_open:` the date your game store opens preorders.
- `offers:` overseas listings to check landed cost, e.g.
  ```yaml
  offers:
    - {store: "Example JP shop", country: JP, price: 5400, currency: JPY, qty: 1, shipping: 1500, url: "https://..."}
  ```
  Landed cost = goods + shipping + forwarder + 10% GST (+ import fee over A$1,000). It is compared with the AU eBay price minus eBay fees (13.4%) and postage. You get an alert at ≥25% margin. Tune all of this in `config.yaml`.

## Local
```
pip install -r requirements.txt
python -m unittest discover -s tests
python -m bot.main --offline --digest    # dry run, nothing sent or saved
```

## Known limits (v1)
- eBay prices are **active asking prices**. Sold comps need eBay's restricted Marketplace Insights API.
- The One Piece scraper is pattern-based on the official page's text. If Bandai changes their layout it fails soft (logged, run continues).
- Chase lists and JP prices are manual for now. v2 candidates:
  - auto-pull JP box prices
  - AU retailer stock/preorder monitors for BUYs
  - auto-tuning weights from `outcomes.json`
