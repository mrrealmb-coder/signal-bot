// Both trading runtimes use this file. cut is the setup candle's exclusive UTC close.
export const auctionVersion = 'xau-auction-v1'
export const minutes = 6
const minute = 60_000

export function auctionUrl(cut) {
  return 'https://fapi.binance.com/fapi/v1/klines?symbol=XAUUSDT&interval=1m' +
    `&startTime=${cut - minutes * minute}&endTime=${cut - 1}&limit=${minutes}`
}

export function readAuction(rows, cut, direction, method) {
  const missing = { verdict: 'wait', pattern: 'data', cut }
  if (![1, -1].includes(direction) || !Number.isSafeInteger(cut) || cut % minute !== 0 ||
      !Array.isArray(rows) || rows.length !== minutes) return missing
  const bars = []
  for (const [i, row] of rows.entries()) {
    if (!Array.isArray(row) || ![0,1,2,3,4,5,6,8,9].every(k =>
      (typeof row[k] === 'number' || typeof row[k] === 'string' && row[k].trim() !== '') && Number.isFinite(Number(row[k])))) return missing
    const [time, open, high, low, close, volume, end, , trades, bought] = row.map(Number)
    if (time !== cut - (minutes - i) * minute || end !== time + minute - 1 ||
        low <= 0 || low > Math.min(open, close) || high < Math.max(open, close) ||
        volume <= 0 || !Number.isInteger(trades) || trades < 1 || bought < 0 || bought > volume) return missing
    bars.push({ high, low, close, delta: 2 * bought - volume })
  }
  const range = bars.slice(0, -2)
  const low = Math.min(...range.map(bar => bar.low))
  const high = Math.max(...range.map(bar => bar.high))
  const [test, next] = bars.slice(-2)
  let side = 0
  let pattern = 'neutral'
  if (test.low < low && test.delta < 0 && test.close > low && next.low >= test.low && next.close > test.high) {
    side = 1
    pattern = 'reclaim'
  } else if (test.high > high && test.delta > 0 && test.close < high && next.high <= test.high && next.close < test.low) {
    side = -1
    pattern = 'reclaim'
  } else if (test.close > high && next.close > high && test.delta > 0 && next.delta > 0) {
    side = 1
    pattern = 'acceptance'
  } else if (test.close < low && next.close < low && test.delta < 0 && next.delta < 0) {
    side = -1
    pattern = 'acceptance'
  }
  // VP trades an hourly breakout. An opposite one-minute extension alone does not cancel it.
  const veto = side === -direction && (method !== 'vp' || pattern === 'reclaim')
  return { verdict: veto ? 'block' : 'allow', pattern, side, cut, low, high }
}
