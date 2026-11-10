# Independent Breakout paper test

Started (UTC): 2026-11-07T18:15:15.807000+00:00
Last checked (UTC): 2026-11-10T17:56:49.078000+00:00

Independently detected paper setups: 6; profile eligible: 2; completed resolved pairs: 2; fetch/validation errors: 0.
No Telegram messages, no broker orders, no change to original Entry/SL/TP. Prior UTC-day XAUT candle-volume proxy, not broker footprint. Uses raw XAUT prices, NOT spot-adjusted broker prices. No state.json or Telegram acknowledgement dependency. Signals before this independent test initialization excluded.

|Plan|Signal UTC|Profile gate|2R result|Profile result|Reach pip|
|---|---|---|---|---|---:|
|entry-v1:1794183300000:1|2026-11-09T00:15:00+00:00|no_level|crossed_before_publication|not admitted|0.0 (incomplete)|
|entry-v1:1794223800000:1|2026-11-09T11:30:00+00:00|no_level|tp|not admitted|195.5|
|entry-v1:1794254400000:1|2026-11-09T20:00:00+00:00|no_level|crossed_before_publication|not admitted|0.0 (incomplete)|
|entry-v1:1794270600000:-1|2026-11-10T00:30:00+00:00|eligible|sl|sl|38.1|
|entry-v1:1794278700000:-1|2026-11-10T02:45:00+00:00|eligible|sl|sl|31.6|
|entry-v1:1794328200000:-1|2026-11-10T16:30:00+00:00|no_level|filled|not admitted|73.4 (incomplete)|

Same completed pairs, $0.60/oz modeled costs:
- baseline: net -2.14R; mean reach error 135.15 pip.
- profile: net -2.14R; mean reach error 142.50 pip.

Ambiguous or data-gap outcomes are retained in breakout_paper_state.json and excluded from resolved-pair totals. Unequal entry availability and no-level/too-close skips are not wins. This is a delayed paper observation of frozen signal-time rules, not proof of live executions.

Modeled activation is signal close +60s; processing is delayed, so this is not a live execution-latency test.
Skipped downtime windows: 0. Detector watermark: 1794332700000.
