# Entry plan experiment v1

Frozen before running the new entry/exit replay on 2026-11-04. This is exploratory
research on an already inspected period, not an independent validation set.

Universe: XAUTUSDT proxy for gold; closed 15-minute strong BOS/CHoCH events from
the existing detector (body >= 0.5 ATR, directional close >= 70%, EMA20 slope
aligned). Existing one-hour same-direction cooldown applies. Daily direction
is context only. No changes to the four existing trading strategies.

- BUY STOP = trigger high + 0.05 ATR, rounded up to cents; SELL STOP = trigger
  low - 0.05 ATR, rounded down. Do not enter at the already completed candle close.
- SL = broken structure level -/+ 0.20 ATR, rounded away from entry.
- Reject risk distance outside [0.5, 3] ATR. TP = 2 times initial risk.
- Publish at signal close + 60 seconds in replay. Reject if entry or SL was
  touched in the first minute, or the publication quote is beyond either level.
- Pending entry expires 60 minutes after signal close. Touching SL while still
  pending cancels the plan. Hold a filled plan at most six hours after fill.
- One pending/filled plan at a time for this method; never overwrite it.
- Replay one-minute OHLC in chronological order. No entry/SL/TP before publication.
  Entry and SL in one minute with unknown ordering is AMBIGUOUS, not a win.
  Filled positions touching SL and TP in one minute are AMBIGUOUS. Report both
  lower/upper bounds; don't include ambiguous rows in the resolved win rate.
- Stop entries gapping through entry fill at the worse opening price; gaps
  through SL exit at the worse open. Targets fill at the target, no positive
  gap improvement assumed. Prices beyond both levels at publication are rejected.
- Close before the inherited Friday 21:00 UTC market shutdown; flag missing
  one-minute data instead of bridging gaps or treating them as no movement.
- $30 risk sizing floors to 0.01 lot, contract 100 oz/lot, reserving $0.60/oz
  for modeled round-trip costs; reject if even 0.01 lot exceeds the budget.
  Slippage can still exceed modeled risk. Test costs $0.30 and $0.60/oz.
- Report all rejected, blocked, cancelled, expired, filled, ambiguous and open
  plans; net R, favorable/adverse excursion in pips (0.1 = 1 pip), realized pips,
  and this week's individual Entry/SL/TP. No broker execution or real test messages.

Deployment gate: unit tests are necessary but do not establish a useful strategy.
Do not enable the candidate if the chronological replay has negative aggregate
net R under both cost assumptions. Even a passing result is experimental and
requires forward observation; historical results do not establish live returns.
