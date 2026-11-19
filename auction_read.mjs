import { auctionUrl, readAuction, auctionVersion } from './auctionAdmission.mjs'

const [cut, direction] = process.argv.slice(2, 4).map(Number)
const method = process.argv[4]
const source = 'PAXGUSDT'
let rows
let error
try {
  const response = await fetch(auctionUrl(cut, source), { signal: AbortSignal.timeout(6000) })
  if (response.ok) rows = await response.json()
  else error = `http_${response.status}`
} catch { error = 'unavailable' }
console.log(JSON.stringify({ ...readAuction(rows, cut, direction, method), source, version: auctionVersion, error }))
