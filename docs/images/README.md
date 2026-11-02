# Screenshots

All images are taken from **demo data**, not a real account: a fake dashboard server (`frontend/e2e/lib/fake-dash.mjs`) feeds the built UI (`frontend/dist`) with the e2e fixtures plus neutral demo numbers (`frontend/e2e/lib/demo.mjs`: a 1000 USDT paper account, invented trades and decisions). No platform, no `state/`, no keys are touched. Never add a screenshot of a real account. The dashboard UI is in Russian, 1440x900 px, every screen in two themes: `<name>-dark.png` (default in the READMEs) and `<name>-light.png`.

| file | page | what it shows |
|---|---|---|
| `overview-*.png` | `/` Обзор | money strip (under risk, room to the day limit, day result, capital), open positions with stop and take, cumulative PnL, what the brain is doing |
| `trades-*.png` | `/trading/trades` Торговля -> Сделки | cumulative PnL, history cards, PnL per trade, exit reasons, error tags |
| `plans-*.png` | `/trading/plans` Торговля -> Планы | plans: waiting for entry, far, in trade, closed; distance to the trigger |
| `day-*.png` | `/trading/day` Торговля -> День | the trading day: start steps, day plan, carried positions, previous days |
| `experiments-*.png` | `/trading/experiments` Торговля -> Эксперименты | hypotheses and experiments: n, mean R, win rate, "too little data" flags |
| `brain-decisions-*.png` | `/brain/decisions` Мозг -> Решения | decision feed of the brain roles and the executor, rejects with reasons |
| `settings-*.png` | `/system/settings` Система -> Настройки | which model serves which role (with fallbacks), connected providers and exchange |

## Regenerate (after a UI change)

Needs Node 22.6+, Microsoft Edge (or another Chromium via `E2E_EDGE_PATH`) and a built `frontend/dist`:

```
npm --prefix frontend/e2e ci          # once: playwright-core and ws
npm --prefix frontend run build       # if the UI changed
npm --prefix frontend run screenshots # all screens, both themes -> docs/images/
npm --prefix frontend run screenshots -- trades   # only names containing "trades"
```

The theme is switched the way the app does it (`localStorage["tb.theme"]`, applied by `theme-init.js` as `data-theme`), not by emulating `prefers-color-scheme`. The script fails if the page logs a console error or the theme did not apply.

Finally: look at every PNG (no home-directory paths, no e-mail, no real amounts, no keys, no "нет связи" banners), keep each file under about 400 KB, then `./tb audit-public`.
