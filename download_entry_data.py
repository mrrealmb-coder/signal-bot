"""Read-only public market-data download, bounded concurrency and cached pages."""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import time
import urllib.request

START = 1789430400000
END = 1793801700000
ROOT = Path(__file__).parent / 'entry_results'


def page(start):
    end = min(start + 1000 * 60000, END)
    path = ROOT / 'pages' / f'{start}.json'
    if path.exists():
        return json.loads(path.read_text())
    url = ('https://data-api.binance.vision/api/v3/klines?symbol=XAUTUSDT'
           f'&interval=1m&startTime={start}&endTime={end-1}&limit=1000')
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                raw = json.load(response)
            rows = [dict(o=float(r[1]), h=float(r[2]), l=float(r[3]), c=float(r[4]),
                         v=float(r[5]), ct=int(r[6])) for r in raw]
            expected = (end-start)//60000
            assert len(rows) == expected, (start, len(rows), expected)
            assert [r['ct'] for r in rows] == list(range(start+59999, end, 60000))
            path.write_text(json.dumps(rows))
            return rows
        except Exception:
            if attempt == 3:
                raise
            time.sleep(1 + attempt)


def main():
    (ROOT / 'pages').mkdir(parents=True, exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        parts = list(pool.map(page, range(START, END, 1000 * 60000)))
    rows = [r for part in parts for r in part]
    assert [r['ct'] for r in rows] == list(range(START+59999, END, 60000))
    path = ROOT / 'XAUTUSDT_1m.json'
    path.write_text(json.dumps(rows))
    manifest = dict(start=START, end=END, count=len(rows),
                    sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    source='https://data-api.binance.vision/api/v3/klines', symbol='XAUTUSDT', interval='1m')
    (ROOT / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest))


if __name__ == '__main__':
    main()
