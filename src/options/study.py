#!/usr/bin/env python3
"""Command-line entry point for read-only volatility research."""
import argparse
import csv
import json
import math
import sys
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from statistics import fmean

from market_data import HOUR, calibrate_chain, parse_bars, price_history, public_data
from volatility import Contract, sample_trades, summarize


def instant(text):
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("include a UTC offset in timestamps")
    return int(parsed.timestamp() * 1000)


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    history = subs.add_parser("history", help="write aligned public close and IV bars as CSV")
    history.add_argument("currency", choices=("BTC", "ETH"))
    history.add_argument("start", type=instant)
    history.add_argument("end", type=instant)
    history.add_argument("--hours", type=int, choices=(1, 24), default=24)
    chain = subs.add_parser("calibrate", help="measure IV and bid/ask to mark from a current option quote")
    chain.add_argument("currency", choices=("BTC", "ETH"))
    chain.add_argument("--days", type=float, required=True)
    chain.add_argument("--side", choices=("C", "P"), required=True)
    chain.add_argument("--delta", type=float, required=True)
    for name in ("evaluate", "breakeven"):
        study = subs.add_parser(name)
        study.add_argument("csv", type=Path)
        study.add_argument("--strategy", choices=("covered-call", "cash-secured-put", "hedged-call",
                                                  "hedged-put", "long-straddle"), default="covered-call")
        study.add_argument("--bars", type=int, required=True, help="bars to expiry")
        study.add_argument("--stride", type=int, default=1, help="bars between decisions")
        study.add_argument("--train-fraction", type=float, default=.6)
        study.add_argument("--delta", type=float, default=.25)
        study.add_argument("--multiplier", type=float, default=1.)
        study.add_argument("--bid", type=float, default=.94)
        study.add_argument("--ask", type=float, default=1 / .94)
        study.add_argument("--open-fee", type=float, default=.0003)
        study.add_argument("--delivery-fee", type=float, default=.00015)
        study.add_argument("--fee-cap", type=float, default=.125)
        study.add_argument("--hedge-band", type=float, default=.05)
        study.add_argument("--hedge-fee", type=float, default=.0005)
        if name == "evaluate":
            study.add_argument("--har", action="store_true", help="fit a historical-variance forecast on training bars")
            study.add_argument("--forecast-strike", action="store_true")
            study.add_argument("--iv-variance-ratio", type=float)
            study.add_argument("--filters", action="store_true", help="exclude volatility spikes, adverse momentum and supplied events")
            study.add_argument("--event", action="append", type=instant, default=[])
    args = parser.parse_args(argv)
    if args.command == "history":
        csvout = csv.writer(sys.stdout)
        csvout.writerow(("time_ms", "close", "iv"))
        for row in price_history(args.currency, args.start, min(args.end, int(time.time() * 1000)), args.hours):
            csvout.writerow(row)
        return
    if args.command == "calibrate":
        now = int(time.time() * 1000)
        index = public_data("get_volatility_index_data", currency=args.currency, start_timestamp=now-72*HOUR,
                            end_timestamp=now, resolution="3600")
        closed = [r for r in index["data"] if r[0]+HOUR <= now]
        if not closed:
            raise ValueError("no completed index bar for calibration")
        quotes = public_data("get_book_summary_by_currency", currency=args.currency, kind="option")
        result = calibrate_chain(quotes, max(closed, key=lambda r:r[0])[4]/100,
                                 now, args.days, args.side, args.delta, .04)
    else:
        with args.csv.open(newline="") as stream:
            bars = parse_bars(stream)
        contract = Contract(**{key: getattr(args, key) for key in Contract.__dataclass_fields__})
        sampling = dict(stride=args.stride, fraction=args.train_fraction)
        if args.command == "evaluate":
            if args.iv_variance_ratio is not None and not (math.isfinite(args.iv_variance_ratio) and args.iv_variance_ratio > 0):
                raise ValueError("variance ratio must be finite and positive")
            trades = sample_trades(bars, contract, **sampling, forecast=args.har, forecast_strike=args.forecast_strike,
                                   ratio=args.iv_variance_ratio, filters=args.filters, events=args.event)
            result = {name: summarize([r for r in trades if r[1] == holdout], args.bars, args.stride)
                      for name, holdout in (("train", False), ("holdout", True))}
            result["pricing"] = "modelled on implied volatility"
        else:
            if args.strategy == "long-straddle":
                raise ValueError("breakeven measures the IV multiplier needed for an option sale")
            previous = {}
            result = {"train": None, "holdout": None, "pooled": None}
            for k in range(71):
                multiplier = .4 + .02 * k
                rows = sample_trades(bars, replace(contract, multiplier=multiplier), **sampling)
                for name, label in (("train", False), ("holdout", True), ("pooled", None)):
                    pnl = [r[5][0] for r in rows if label is None or r[1] == label]
                    if not pnl or result[name] is not None:
                        continue
                    value = fmean(pnl)
                    if value == 0:
                        result[name] = multiplier
                    elif name in previous and previous[name] < 0 < value:
                        result[name] = multiplier - .02 * value / (value - previous[name])
                    previous[name] = value
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    try:
        run()
    except (ValueError, OSError, KeyError) as error:
        sys.exit(str(error))
