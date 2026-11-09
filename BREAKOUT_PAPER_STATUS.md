# Independent Breakout paper test

Started (UTC): 2026-11-07T18:15:15.807000+00:00
Last checked (UTC): 2026-11-09T18:55:02.018000+00:00

Independently detected paper setups: 2; profile eligible: 0; completed resolved pairs: 0; fetch/validation errors: 0.
No Telegram messages, no broker orders, no change to original Entry/SL/TP. Prior UTC-day XAUT candle-volume proxy, not broker footprint. Uses raw XAUT prices, NOT spot-adjusted broker prices. No state.json or Telegram acknowledgement dependency. Signals before this independent test initialization excluded.

|Plan|Signal UTC|Profile gate|2R result|Profile result|Reach pip|
|---|---|---|---|---|---:|
|entry-v1:1794183300000:1|2026-11-09T00:15:00+00:00|no_level|crossed_before_publication|not admitted|0.0 (incomplete)|
|entry-v1:1794223800000:1|2026-11-09T11:30:00+00:00|no_level|tp|not admitted|195.5|

Ambiguous or data-gap outcomes are retained in breakout_paper_state.json and excluded from resolved-pair totals. Unequal entry availability and no-level/too-close skips are not wins. This is a delayed paper observation of frozen signal-time rules, not proof of live executions.

Modeled activation is signal close +60s; processing is delayed, so this is not a live execution-latency test.
Skipped downtime windows: 0. Detector watermark: 1794249900000.
