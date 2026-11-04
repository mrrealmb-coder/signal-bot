# Breakout Entry / SL / TP v1

Experimental Telegram-only method, 2026-11-04. Standalone movement alerts are
disabled. The existing SMC, VP, AMD and VA80 trading rules are unchanged.

## Chronological replay

2026-09-15 00:00 UTC to 2026-11-04 14:15 UTC. 72,855 continuous one-minute
XAUTUSDT candles; no missing minute in the downloaded window.

| Result | Count |
|---|---:|
| Strong movement events | 148 |
| Filled paper plans | 51 |
| TP | 16 |
| SL | 29 |
| Six-hour / market-close exit | 6 |
| Entry/SL crossed before activation | 42 |
| Cancelled before entry | 23 |
| Rejected SL distance | 27 |
| Blocked by an existing plan | 4 |
| Unfilled expiry | 1 |
| Ambiguous or missing-data outcomes | 0 |

21/51 completed plans were positive after $0.60/oz modeled costs. Net result:
**+4.51R at $0.30/oz; +1.56R at $0.60/oz.** Gross movement summed across completed
plans was +826.3 pips; after the latter modeled cost +520.3 pips. Here 1 pip =
$0.10. This is a small historical edge, not evidence of reliable live returns.
The period was previously inspected; this is not out-of-sample validation.

This week: three paper entries, two TP and one SL, +2.70R after $0.60/oz.

| UB time | Side | Entry | SL | TP | Outcome | Gross pips |
|---|---|---:|---:|---:|---|---:|
| Nov 3 08:15 | BUY STOP | 3942.61 | 3939.60 | 3948.63 | TP | +60.2 |
| Nov 4 08:00 | BUY STOP | 3936.33 | 3929.00 | 3950.99 | SL | -73.3 |
| Nov 4 13:45 | BUY STOP | 3982.52 | 3956.99 | 4033.58 | TP | +510.6 |

These are raw XAUT prices, not OANDA/broker levels. Actual timing, spreads,
slippage and broker sessions can change outcomes. Live messages use a recently
measured spot offset, held fixed for each plan. Lot size floors to 0.01 and
reserves $0.60/oz costs inside the configured $30 modeled risk budget.

## Delivery and lifecycle verification

The production state machine reproduced all **51** filled replay plans with
identical entry, stop, target, fill, exit, outcome and end time. This uses mock
Telegram acknowledgements, not real chat messages or broker fills.

116 conditional setups per chat were simulated. 41 were cancelled when a level
was touched before activation; the other pre-activation rejection was already
crossed at the simulated publication quote, so was never sent. All 116 had an
ending notice. Across three roles: 348 setup acknowledgements, 153 entry-level
notices, 348 ending acknowledgements, zero duplicate acknowledged messages.

Confirmed recipients are not resent; transient failures retry only before the
setup delivery deadline. Telegram has no idempotency key: a timeout after server
acceptance can still cause a duplicate or an unacknowledged delivery. A chat
without a setup acknowledgement is not sent later entry/exit notices.

The method has its own `entry_plans` state/history; it does not write orders,
the broker feed, or the legacy P&L ledger. `/update` shows current levels,
delivery acknowledgement count and stale/error state. No historical setups are
broadcast on first activation. No real Telegram test messages were sent.

## Reproduce

```powershell
py -3 -X utf8 download_entry_data.py
py -3 -X utf8 replay_entry_plans.py --research-dir D:/Projects/signal-bot-audit-20261104
py -3 -X utf8 verify_entry_replay.py --research-dir D:/Projects/signal-bot-audit-20261104
py -3 -X utf8 -m unittest -q test_update.py test_movement_watch.py test_entry_plans.py
```

The research directory is an existing local input dependency, not included in
this deployment. Local raw pages, individual plans, manifest and full report
are under ignored `entry_results/`. The frozen minute-data SHA256 is
`3248bb1bfb3f5242ed8d8cd7d58d9d3afc7545c4fbd1248c380c8a0482d5df4d`.
