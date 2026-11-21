# Independent Breakout paper test

Started (UTC): 2026-11-07T18:15:15.807000+00:00
Last checked (UTC): 2026-11-21T11:22:49.632000+00:00

Independently detected paper setups: 37; profile eligible: 10; completed resolved pairs: 4; fetch/validation errors: 0.
No Telegram messages, no broker orders, no change to original Entry/SL/TP. Prior UTC-day XAUT candle-volume proxy, not broker footprint. Uses raw XAUT prices, NOT spot-adjusted broker prices. No state.json or Telegram acknowledgement dependency. Signals before this independent test initialization excluded.

|Plan|Signal UTC|Profile gate|2R result|Profile result|Reach pip|
|---|---|---|---|---|---:|
|entry-v1:1794183300000:1|2026-11-09T00:15:00+00:00|no_level|crossed_before_publication|not admitted|0.0 (incomplete)|
|entry-v1:1794223800000:1|2026-11-09T11:30:00+00:00|no_level|tp|not admitted|195.5|
|entry-v1:1794254400000:1|2026-11-09T20:00:00+00:00|no_level|crossed_before_publication|not admitted|0.0 (incomplete)|
|entry-v1:1794270600000:-1|2026-11-10T00:30:00+00:00|eligible|sl|sl|38.1|
|entry-v1:1794278700000:-1|2026-11-10T02:45:00+00:00|eligible|sl|sl|31.6|
|entry-v1:1794328200000:-1|2026-11-10T16:30:00+00:00|no_level|sl|not admitted|73.4|
|entry-v1:1794359700000:-1|2026-11-11T01:15:00+00:00|eligible|crossed_before_publication|crossed_before_publication|0.0 (incomplete)|
|entry-v1:1794361500000:1|2026-11-11T01:45:00+00:00|too_close|expired|not admitted|0.0 (incomplete)|
|entry-v1:1794374100000:1|2026-11-11T05:15:00+00:00|too_close|crossed_before_publication|not admitted|0.0 (incomplete)|
|entry-v1:1794388500000:-1|2026-11-11T09:15:00+00:00|eligible|crossed_before_publication|crossed_before_publication|0.0 (incomplete)|
|entry-v1:1794398400000:-1|2026-11-11T12:00:00+00:00|eligible|tp|tp|482.4|
|entry-v1:1794420000000:-1|2026-11-11T18:00:00+00:00|no_level|crossed_before_publication|not admitted|0.0 (incomplete)|
|entry-v1:1794438900000:-1|2026-11-11T23:15:00+00:00|no_level|crossed_before_publication|not admitted|0.0 (incomplete)|
|entry-v1:1794445200000:1|2026-11-12T01:00:00+00:00|eligible|sl|sl|0.9|
|entry-v1:1794489300000:-1|2026-11-12T13:15:00+00:00|no_level|sl|not admitted|9.7|
|entry-v1:1794569400000:-1|2026-11-13T11:30:00+00:00|too_close|sl|not admitted|6.3|
|entry-v1:1794576600000:-1|2026-11-13T13:30:00+00:00|eligible|crossed_before_publication|crossed_before_publication|0.0 (incomplete)|
|entry-v1:1794825900000:1|2026-11-16T10:45:00+00:00|no_level|sl|not admitted|76.1|
|entry-v1:1794844800000:-1|2026-11-16T16:00:00+00:00|too_close|sl|not admitted|23.7|
|entry-v1:1794852900000:-1|2026-11-16T18:15:00+00:00|too_close|cancelled|not admitted|0.0 (incomplete)|
|entry-v1:1794893400000:1|2026-11-17T05:30:00+00:00|no_level|crossed_before_publication|not admitted|0.0 (incomplete)|
|entry-v1:1794903300000:-1|2026-11-17T08:15:00+00:00|too_close|cancelled|not admitted|0.0 (incomplete)|
|entry-v1:1794910500000:-1|2026-11-17T10:15:00+00:00|too_close|sl|not admitted|38.6|
|entry-v1:1794929400000:1|2026-11-17T15:30:00+00:00|too_close|cancelled|not admitted|0.0 (incomplete)|
|entry-v1:1794960900000:-1|2026-11-18T00:15:00+00:00|no_level|tp|not admitted|589.0|
|entry-v1:1794974400000:-1|2026-11-18T04:00:00+00:00|no_level|crossed_before_publication|not admitted|0.0 (incomplete)|
|entry-v1:1794983400000:-1|2026-11-18T06:30:00+00:00|no_level|cancelled|not admitted|0.0 (incomplete)|
|entry-v1:1795005900000:-1|2026-11-18T12:45:00+00:00|no_level|sl|not admitted|62.8|
|entry-v1:1795009500000:1|2026-11-18T13:45:00+00:00|eligible|crossed_before_publication|crossed_before_publication|0.0 (incomplete)|
|entry-v1:1795056300000:-1|2026-11-19T02:45:00+00:00|eligible|crossed_before_publication|crossed_before_publication|0.0 (incomplete)|
|entry-v1:1795080600000:-1|2026-11-19T09:30:00+00:00|too_close|crossed_before_publication|not admitted|0.0 (incomplete)|
|entry-v1:1795089600000:-1|2026-11-19T12:00:00+00:00|no_level|sl|not admitted|69.3|
|entry-v1:1795118400000:-1|2026-11-19T20:00:00+00:00|no_level|crossed_before_publication|not admitted|0.0 (incomplete)|
|entry-v1:1795138200000:-1|2026-11-20T01:30:00+00:00|no_level|tp|not admitted|554.3|
|entry-v1:1795171500000:1|2026-11-20T10:45:00+00:00|eligible|cancelled|cancelled|0.0 (incomplete)|
|entry-v1:1795181400000:-1|2026-11-20T13:30:00+00:00|no_level|cancelled|not admitted|0.0 (incomplete)|
|entry-v1:1795188600000:-1|2026-11-20T15:30:00+00:00|no_level|cancelled|not admitted|0.0 (incomplete)|

Same completed pairs, $0.60/oz modeled costs:
- baseline: net -1.53R; mean reach error 192.50 pip.
- profile: net +6.48R; mean reach error 215.90 pip.

Ambiguous or data-gap outcomes are retained in breakout_paper_state.json and excluded from resolved-pair totals. Unequal entry availability and no-level/too-close skips are not wins. This is a delayed paper observation of frozen signal-time rules, not proof of live executions.

Modeled activation is signal close +60s; processing is delayed, so this is not a live execution-latency test.
Skipped downtime windows: 1. Detector watermark: 1795259700000.
