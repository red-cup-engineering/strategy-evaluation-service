"""Public Deribit market observations. Volatility is expressed as a decimal."""
import csv
import json
import math
from datetime import datetime, timezone
from statistics import NormalDist
from urllib.parse import urlencode
from urllib.request import urlopen

HOUR = 3_600_000
YEAR = 365 * 24 * HOUR
CDF = NormalDist().cdf


def public_data(method, **params):
    url = "https://www.deribit.com/api/v2/public/" + method + "?" + urlencode(params)
    with urlopen(url, timeout=30) as response:
        envelope = json.load(response)
    if "error" in envelope:
        raise ValueError(f"Deribit {method}: {envelope['error']}")
    return envelope["result"]


def price_history(currency, start, end, hours, request=public_data):
    """Yield completed perpetual bars paired with the latest available DVOL close."""
    if hours not in (1, 24) or end <= start:
        raise ValueError("a positive time range and hourly or daily bars are required")
    step = hours * HOUR
    spots, vols = {}, {}
    # Each interval is smaller than the endpoint's per-call row limit. Include
    # one earlier DVOL bar because daily spot and index bars may close hours apart.
    cursor = start - 2 * step
    while cursor < end:
        stop = min(end, cursor + 500 * step)
        query = dict(start_timestamp=cursor, end_timestamp=stop)
        chart = request("get_tradingview_chart_data", instrument_name=currency + "-PERPETUAL",
                        resolution="60" if hours == 1 else "1D", **query)
        for tick, close in zip(chart["ticks"], chart["close"], strict=True):
            spots[tick + step] = close
        index = request("get_volatility_index_data", currency=currency,
                        resolution="3600" if hours == 1 else "1D", **query)
        for tick, _, _, _, close in index["data"]:
            vols[tick + step] = close / 100
        cursor = stop
    ordered = iter(sorted(vols))
    following = next(ordered, None)
    latest = None
    for tick in sorted(spots):
        while following is not None and following <= tick:
            latest, following = following, next(ordered, None)
        if start <= tick <= end and latest is not None and tick - latest < step:
            yield tick, spots[tick], vols[latest]


def calibrate_chain(quotes, volatility, as_of, days, option, delta, tolerance):
    """Choose a two-sided quote near the target expiry and absolute delta."""
    if (not all(map(math.isfinite, (volatility, days, delta, tolerance))) or
            volatility <= 0 or days <= 0 or not 0 < delta < 1 or tolerance < 0):
        raise ValueError("invalid chain calibration inputs")
    candidates = []
    for row in quotes:
        name = row["instrument_name"]
        parts = name.split("-")
        if len(parts) != 4 or parts[3] != option:
            continue
        expiry = datetime.strptime(parts[1], "%d%b%y").replace(hour=8, tzinfo=timezone.utc)
        years = (expiry.timestamp() * 1000 - as_of) / YEAR
        iv = (row.get("mark_iv") or 0) / 100
        spot = row.get("underlying_price") or 0
        strike = float(parts[2])
        bid, ask, mark = (row.get(key) for key in ("bid_price", "ask_price", "mark_price"))
        if years <= 0 or min(spot, strike, iv) <= 0 or not (bid and ask and mark and 0 < bid <= ask and mark > 0):
            continue
        sd = iv * math.sqrt(years)
        call_delta = CDF(math.log(spot / strike) / sd + sd / 2)
        abs_delta = call_delta if option == "C" else 1 - call_delta
        if abs(abs_delta - delta) > tolerance:
            continue
        candidates.append((abs(years * 365 - days), abs(abs_delta - delta), name,
                           dict(instrument=name, days=years * 365, delta=abs_delta,
                                iv_multiplier=iv / volatility, bid_to_mark=bid / mark,
                                ask_to_mark=ask / mark)))
    return min(candidates, key=lambda item: item[:3])[-1] if candidates else None


def parse_bars(stream):
    rows = csv.DictReader(stream)
    result = []
    for row in rows:
        time, close, vol = int(row["time_ms"]), float(row["close"]), float(row["iv"])
        if not (math.isfinite(close) and math.isfinite(vol) and min(close, vol) > 0):
            raise ValueError("close and implied volatility must be finite and positive")
        if result and time <= result[-1][0]:
            raise ValueError("timestamps must increase strictly")
        result.append((time, close, vol))
    if len(result) < 3:
        raise ValueError("at least three observations are required")
    interval = result[1][0] - result[0][0]
    if interval not in (HOUR, 24 * HOUR) or any(b[0] - a[0] != interval for a, b in zip(result, result[1:])):
        raise ValueError("supply contiguous hourly or daily observations; split gaps before evaluation")
    return result
