# Prospective paired Profile target observation

**Superseded on 2026-11-08:** the hourly workflow now runs `breakout_paper.py`
independently of Telegram delivery. Current results: `BREAKOUT_PAPER_STATUS.md`.
The older journal is retained as historical evidence, not combined with the new
experiment. The rest of this document describes the former sent-signal observer.

Experimental, separate hourly workflow. Original bot source/config and Telegram
entry/SL/TP remain unchanged. No Telegram or broker credentials are supplied.

After the first successful initialization, admit only newly timestamped entry-v1
plans with at least one Telegram acknowledgement in state.json. First-run older
and already-open plans are excluded. Repeated runs deduplicate by plan ID.

Freeze original entry, SL, ATR, risk and spot basis. Previous complete UTC day's
XAUT minute volume is assigned to $1 typical-price bins, POC and contiguous 70%
value-area boundaries. Nearest forward level minus 0.15 ATR is candidate target.
Require target distance minus $0.60 >= initial risk; otherwise record no-level or
too-close abstention. Do not substitute a 2R fallback for excluded targets.

Both plans are paper-replayed from signal close with original +60s activation,
one-hour pending expiry, six-hour holding and market-close rule. Raw candles are
normalized to original fixed basis. Missing source minutes cause visible errors
and retry, not synthetic wins. Both-hit minute outcomes remain ambiguous.

A third observer ignores TP to measure conservative favorable reach before the
original SL or six-hour limit. Entry-minute extrema are excluded; stop-minute
favorable reach uses opening price only. This is NOT an exact reversal label.

Results: PROFILE_SHADOW_STATUS.md and profile_shadow_state.json in main. Resolved
paired totals exclude ambiguous/data-gap outcomes; all cases remain in journal.
This compares targets on actual published baseline entries; it does not publish
additional Profile signals or change baseline admission. GitHub hourly schedules
may be delayed. Signals can be reconstructed later from retained bot history;
the journal records enrollment time separately from signal time. No fixed sample
size automatically authorizes promotion to live.

Stop: disable the 'Profile TP shadow observation' workflow. Its journal is separate
from state.json and the original bot workflow, so stopping it does not stop signals.
