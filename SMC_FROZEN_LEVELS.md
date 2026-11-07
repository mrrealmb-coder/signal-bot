# SMC immutable published levels

New XAUUSD SMC signals persist entry/SL/TP rounded to the actual message precision,
original source/basis and setup metadata under state.smc[key].frozen. Tracking and
/update reuse these levels. Current fetched bars are normalized to the original
basis; changing gold offset no longer silently changes an existing plan's levels.
Filled setups remain tracked if the latest detector switches to a new structure
or no longer returns that setup. A new setup may still replace an UNFILLED pending
limit, with the existing cancellation notice; this preserves prior entry selection.

Missing source metadata or insufficient history raises an error instead of moving
levels. Existing records without frozen metadata retain the legacy path; historical
levels/results are not rewritten or guessed. Deployment check found no active
pending/filled XAUUSD SMC needing migration.

The original M15 OHLC convention (SL first on both-hit bars, partial-candle status,
historical fill time) is preserved. This change is price consistency, not proof of
actual broker fills or a new profitable strategy. Intrabar ambiguity is not solved.

## Stale LIMIT experiment — NOT enabled live

Prespecified research rule: cancel a pending order if a completed M15 candle,
without touching entry, has already moved >=1 initial R favorably away from entry.
Same-candle entry/favorable movement does not get retrospectively cancelled.

Reconstructed six previously sent M15 SMC setup timestamps in raw XAUT coordinates;
original HTF admission is inherited from the sent timestamp, not rerun. Freeze the
setup at its own timestamp and independently observe 12h with six-hour pending
expiry. This is NOT a replay of original replacement order or broker executions.

Without stale cancellation: one potential TP, two SL, three expiries.
With the rule: all six cancel before entry. It avoids the two SLs but also loses
the potential TP. In particular the first setup had been replaced in the live
sequence; its standalone replay TP is NOT a previously delivered live TP result.
Six examples do not establish an improvement. Keep cancellation research-only.

Unit and main-loop integration checks cover changing basis, original feed/ledger
levels, disappeared/new structure, existing pending replacement, source failures,
rounding, terminal stability and stale-rule timing. No real test Telegram messages.
