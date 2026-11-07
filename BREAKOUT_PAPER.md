# Independent Breakout paper experiment

The hourly profile-shadow.yml workflow now runs breakout_paper.py. It reads only
public Binance XAUTUSDT minute data and its separate breakout_paper_state.json.
It does not read state.json, Telegram acknowledgements, credentials or config.json.
No messages or broker orders. Original signal methods are not invoked.

Use existing strong M15 BOS/CHoCH detector (rolling 1000 closed bars), original
entry/SL sizing, same-direction 1h cooldown and baseline one-active-plan admission.
For each newly timestamped paper setup, compare 2R against prior UTC-day Profile
target using the frozen Profile rules. No suitable target => documented abstention.
Source coordinates are RAW XAUT, not the live bot's spot-adjusted prices.

Only post-initialization closed candles are processed. Hourly batch catch-up makes
each decision with candles closed at that time; outcomes advance only up to each
decision before admitting the next setup. Modeled activation remains close+60s;
preactivation crosses cancel. This is prospective data observed with delayed
historical processing, NOT validation of live delivery/latency/fills.

Catch-up is capped at 48 hours; larger downtime is explicitly logged and old
unresolved observations are marked data_gap. Missing minutes raise an error and
are retried. No silent wins/backfill from before independent initialization.

Outputs: BREAKOUT_PAPER_STATUS.md and breakout_paper_state.json. Old
profile_shadow_state.json is retained separately. GitHub schedules can be delayed.
The workflow can run while Telegram Breakout entry_plan is false.

Validation: causal and no-Telegram tests; actual Nov6 setup reproduced from raw
data; seven hourly batches exactly match one combined batch for plan outcomes.
Original trade-entry methods VP/AMD/SMC/VA80 are unaffected by this module.
