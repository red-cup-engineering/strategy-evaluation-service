import csv
import io
import math
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src/options"))
from market_data import HOUR, YEAR, calibrate_chain, parse_bars, price_history
from volatility import Contract, fit_har, price_delta, sample_trades, settlement, summarize


class VolatilityTests(unittest.TestCase):
    def test_pricing_and_capital(self):
        call, call_delta = price_delta(100., 110., .32, .5, "call")
        put, put_delta = price_delta(100., 110., .32, .5, "put")
        self.assertAlmostEqual(call-put, -10.)
        self.assertAlmostEqual(call_delta-put_delta, 1.)
        bars = [(0, 100., .32), (24 * HOUR, 110., .32)]
        excess, total, premium, hold = settlement(bars, 0, Contract("covered-call", 1))
        self.assertAlmostEqual(total, hold+excess)
        self.assertGreater(premium, 0.)
        self.assertGreater(hold, total)
        short = settlement(bars, 0, Contract("hedged-put", 1))[0]
        free = settlement(bars, 0, Contract("hedged-put", 1, hedge_fee=0))[0]
        self.assertLess(short, free)
        straddle = settlement(bars, 0, Contract("long-straddle", 1))
        self.assertAlmostEqual(straddle[0] / straddle[2], straddle[1])

    def test_no_holdout_leakage(self):
        bars = [(i*HOUR, 100*math.exp(.03*math.sin(i/22)+.01*math.cos(i/7)), .31) for i in range(1600)]
        contract = Contract("cash-secured-put", 48)
        def sample(series):
            return sample_trades(series, contract, stride=6, fraction=.6, forecast=True, forecast_strike=True)
        rows = sample(bars)
        modified = sample([(t, p*1.5 if i >= 960 else p, v) for i,(t,p,v) in enumerate(bars)])
        self.assertEqual([r for r in rows if not r[1]], [r for r in modified if not r[1]])
        self.assertTrue(all(r[0] + 48 < 960 or r[0] >= 960 for r in rows))
        self.assertTrue(all(r[4] > 0 for r in rows))
        self.assertLessEqual(summarize(rows, 48, 6)["worst_chain_drawdown"], 0.)
        self.assertAlmostEqual(fit_har([[1.,0.,0.,0.]] * 4, [2.] * 4)[0], 2.)

    def test_alignment_and_gap_handling(self):
        def data(method, **_):
            if method == "get_tradingview_chart_data":
                return dict(ticks=[HOUR//2, 3*HOUR//2], close=[100, 101])
            return dict(data=[[0,0,0,0,30],[HOUR,0,0,0,40],[2*HOUR,0,0,0,50]])
        self.assertEqual(list(price_history("BTC", HOUR, 3*HOUR, 1, data)),
                         [(3*HOUR//2,100,.30), (5*HOUR//2,101,.40)])
        with self.assertRaises(ValueError):
            parse_bars(io.StringIO(f"time_ms,close,iv\n0,100,.4\n{HOUR},110,.4\n{3*HOUR},90,.4\n"))

    def test_calibrate(self):
        now = datetime(2025, 1, 1, 8, tzinfo=timezone.utc).timestamp() * 1000
        quote = dict(instrument_name="BTC-8JAN25-100-C", underlying_price=100., mark_iv=48.,
                     mark_price=.05, bid_price=.04, ask_price=.06)
        result = calibrate_chain([quote], .40, now, 7, "C", .5, .04)
        self.assertEqual(result["days"], 7.)
        self.assertAlmostEqual(result["iv_multiplier"], 1.2)
        self.assertAlmostEqual(result["bid_to_mark"], .8)
        self.assertIsNone(calibrate_chain([quote], .40, now, 7, "P", .25, .04))
