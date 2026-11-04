# Movement watch v1

Adds an informational XAUUSD M15 movement notification to approved Day, Swing and Scalp chats. Existing SMC, VP, AMD, VA80 trade rules, risk, ledger and broker feed are unchanged. A movement message has no entry, SL, TP or lot recommendation.

Detection matches the audited strong-watch candidate: closed-candle BOS/CHoCH, three-bar pivot confirmation, body >=0.5 ATR14, directional close in the outer 30%, EMA20 slope over three bars agreeing. Daily SMA200 is displayed as context and does not veto notifications. This is not a validated directional trading edge: the 09/15–11/04 exploratory audit had 148 alerts, 48.6% correct one-hour ending direction, 29 reaching +2 ATR before -1 ATR, 71 reaching the opposite threshold first, and 48 reaching neither.

Operational behavior:

- Enable using `movement_watch: true` on the XAUUSD/15m watch entry in config.json. Set false to stop these notifications without changing other strategies.
- First activation records the latest closed candle and sends no historical alert. Only later fresh candles are eligible. Current incomplete candles are excluded.
- Same direction cooldown is one hour. Opposite-direction observations may arrive sooner.
- Three-minute expiry measured from the source candle close; delayed starts do not backfill. Downtime processes only the latest candle.
- Queue and delivery attempts are saved before network calls. Acknowledgements require Telegram `ok` and `message_id`. Only unacknowledged recipients retry after one minute; disabled/removed chats receive no retries. A slow request cannot cause a stale send to the next recipient.
- Delivery is at-least-once. A timeout after Telegram accepts a message can cause a duplicate retry. Local atomic state replacement protects against partial writes, but an abruptly lost GitHub runner can lose changes not yet committed remotely.
- `/update` includes watch status, last evaluated candle and delivery acknowledgement count. This is an operational state, not a broker position.
- No synthetic messages or activation announcements are broadcast. The first real notification awaits a qualifying new closed candle.

Checks:

```sh
python -m unittest -q test_update.py test_movement_watch.py
python verify_movement_replay.py --research-dir /path/to/signal-bot-audit-20261104
```

The first command is hermetic and also runs in GitHub Actions on Python 3.12. The second uses the frozen research snapshot and mock recipients; it must reproduce all 148 audited strong-watch events with exactly 444 acknowledged mock deliveries and no duplicates under repeated polling. It never reads Telegram secrets or sends real messages.

Post-deploy verification: inspect `state.json` → `movement_watch` → `XAUUSD|15m` for version `movement-watch-v1`, initialized_at, watermark and evaluated_at. A later bot-authored state commit proves the running job loaded and executed this module. Delivery itself is proven only by receipts from a subsequent real qualifying event.
