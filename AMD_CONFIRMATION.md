# AMD 5m confirmed entry, $6 maximum target distance

Only AMD detector/message/lifecycle changes; existing NY session and daily direction filter remain. SMC, VP, VA80 and Breakout rules/config unchanged.

After the first eligible sweep/reclaim, wait for a closed M5 candle beyond that signal candle's low (SELL) or high (BUY), within three M5 bars. Any original SL/TP touch before confirmation cancels, with SL-first ambiguity handling. If no confirmation, no signal that day. Confirmation close becomes Entry; original SL and six-hour/day deadline remain. Recheck positive risk <=6 original ATR and original target room >0.5R. Keep the original target if nearer, otherwise cap at Entry +/-6 USD/oz. No 15m EMA filter.

New AMD records persist source/basis so subsequent outcome checks normalize current bars to the published price frame. Legacy records without confirmation_version retain old outcome semantics. Existing bot Market-message delivery and OHLC tracking remain; this does not place or verify broker orders. Synthetic backtest delayed fills are not a guarantee of user fills.

Exploratory May-Nov NY result: 18 modeled entries, 13 TP/5 SL, +2.71R at cost0.60, +1.35R at cost1.20, closed-trade DD2.34R. Original target alone with confirmation was -0.48R. Only18 trades, previously inspected PAXG proxy data, no independent holdout. Known Nov9 bad SELL rejected before confirmation; not an independent validation. No profitability guarantee. Signal frequency decreases.

Validation: 77 unittest checks pass. Added closed-bar confirmation, cap, cancellation, BUY symmetry, legacy lifecycle, and basis-change invariance coverage. Two existing no-backfill tests now mark current weekly report as already sent in their fixtures, preventing calendar-Monday report delivery from contaminating assertions; no report production behavior changed.

Rollback: revert the deployment code commit, preserve state/history. New record basis handling should be retained for any still-open new AMD record when rolling back strategy rules.
