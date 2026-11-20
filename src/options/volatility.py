"""Evaluate model-priced European option strategies on historical observations."""
import math
import random
from bisect import bisect_left
from dataclasses import dataclass
from itertools import accumulate
from statistics import NormalDist, StatisticsError, correlation, fmean, median
from market_data import HOUR, YEAR

NORMAL = NormalDist()


def price_delta(spot, strike, sigma, years, side):
    deviation = sigma * math.sqrt(years)
    d1 = math.log(spot / strike) / deviation + deviation / 2
    d2 = d1 - deviation
    if side == "call":
        return spot * NORMAL.cdf(d1) - strike * NORMAL.cdf(d2), NORMAL.cdf(d1)
    return strike * NORMAL.cdf(-d2) - spot * NORMAL.cdf(-d1), -NORMAL.cdf(-d1)


@dataclass(frozen=True)
class Contract:
    strategy: str
    bars: int
    delta: float = .25
    multiplier: float = 1.
    bid: float = .94
    ask: float = 1 / .94
    open_fee: float = .0003
    delivery_fee: float = .00015
    fee_cap: float = .125
    hedge_fee: float = .0005
    hedge_band: float = .05

    def __post_init__(self):
        if self.strategy not in ("covered-call", "cash-secured-put", "hedged-call", "hedged-put", "long-straddle"):
            raise ValueError("unknown option strategy")
        if self.bars < 1 or not 0 < self.delta < 1 or self.multiplier <= 0:
            raise ValueError("invalid tenor, delta or volatility multiplier")
        if not 0 < self.bid <= self.ask:
            raise ValueError("quote ratios must be positive and bid must not exceed ask")
        if not all(math.isfinite(x) and 0 <= x <= 1 for x in
                   (self.hedge_fee, self.open_fee, self.delivery_fee, self.fee_cap, self.hedge_band)):
            raise ValueError("fees and hedge band must be fractions between zero and one")
        if not all(map(math.isfinite, (self.delta, self.multiplier, self.bid, self.ask))):
            raise ValueError("contract assumptions must be finite")


def settlement(bars, i, contract, forecast_strike_iv=None):
    j = i + contract.bars
    start, spot, vol = bars[i]
    end, final, _ = bars[j]
    years = (end - start) / YEAR
    iv = vol * contract.multiplier
    if contract.strategy == "long-straddle":
        premium = entry_fees = delivery_fees = 0.
        for side in ("call", "put"):
            mark, _ = price_delta(spot, spot, iv, years, side)
            cost = mark * contract.ask
            intrinsic = max(0., final - spot if side == "call" else spot - final)
            premium += cost
            entry_fees += min(contract.open_fee * spot, contract.fee_cap * cost)
            if intrinsic:
                delivery_fees += min(contract.delivery_fee * final, contract.fee_cap * intrinsic)
        outlay = premium + entry_fees
        if outlay <= 0:
            raise ValueError("modelled straddle price is below numerical precision")
        pnl = abs(final - spot) - outlay - delivery_fees
        return pnl / spot, pnl / outlay, premium / spot, final / spot - 1
    side = "call" if contract.strategy.endswith("call") else "put"
    sign = 1 if side == "call" else -1
    deviation = (forecast_strike_iv or iv) * math.sqrt(years)
    strike = spot * math.exp(deviation ** 2 / 2 - sign * NORMAL.inv_cdf(contract.delta) * deviation)
    mark, _ = price_delta(spot, strike, iv, years, side)
    premium = mark * contract.bid
    payoff = max(sign * (final - strike), 0.)
    fees = min(contract.open_fee * spot, contract.fee_cap * premium)
    if payoff:
        fees += min(contract.delivery_fee * final, contract.fee_cap * payoff)
    hedge_pnl = 0.
    if contract.strategy.startswith("hedged"):
        position = 0.
        for k in range(i, j):
            time, current, _ = bars[k]
            _, delta = price_delta(current, strike, iv, (end - time) / YEAR, side)
            if k == i or abs(delta - position) > contract.hedge_band:
                hedge_pnl -= contract.hedge_fee * abs(delta - position) * current
                position = delta
            hedge_pnl += position * (bars[k + 1][1] - current)
        hedge_pnl -= contract.hedge_fee * abs(position) * final
    capital = spot if side == "call" else strike
    excess = (premium - fees - payoff + hedge_pnl) / capital
    underlying = final / spot - 1
    total = underlying + excess if contract.strategy == "covered-call" else excess
    return excess, total, premium / capital, underlying


def fit_har(features, targets):
    """Least-squares on an intercept and daily, weekly and monthly realized variance.
    Modified Gram-Schmidt omits dependent features on flat or short training series.
    """
    columns = list(zip(*features, strict=True))
    basis, transforms = [], []
    for index, column in enumerate(columns):
        residual = list(column)
        weights = [float(i == index) for i in range(len(columns))]
        for unit, coefficients in zip(basis, transforms):
            projection = math.sumprod(residual, unit)
            residual = [x - projection * q for x, q in zip(residual, unit)]
            weights = [x - projection * q for x, q in zip(weights, coefficients)]
        norm = math.hypot(*residual)
        if norm > max(1., math.hypot(*column)) * 1e-10:
            basis.append([x / norm for x in residual])
            transforms.append([x / norm for x in weights])
    return [sum(math.sumprod(q, targets) * weights[i] for q, weights in zip(basis, transforms))
            for i in range(len(columns))]


def sample_interval(data, block, seed=7, repeats=600):
    """Moving-block confidence interval for a mean over consecutive decisions."""
    n = len(data)
    if n < 3 * block:
        return None
    sums = list(accumulate(data, initial=0.))
    rng = random.Random(seed)
    means = []
    for _ in range(repeats):
        total = 0.
        for pos in range(0, n, block):
            size = min(block, n - pos)
            k = rng.randrange(n - block + 1)
            total += sums[k + size] - sums[k]
        means.append(total / n)
    means.sort()
    return [means[int(.025 * repeats)], means[int(.975 * repeats)]]


def sample_trades(bars, contract, *, stride, fraction, forecast=False,
                  forecast_strike=False, ratio=None, filters=False, events=()):
    if not 0 < fraction < 1 or stride < 1:
        raise ValueError("invalid sample split or decision stride")
    step = bars[1][0] - bars[0][0]
    n = contract.bars
    cut = int(len(bars) * fraction)
    squares = [math.log(b[1] / a[1]) ** 2 for a, b in zip(bars, bars[1:])]
    sums = list(accumulate(squares, initial=0.))
    annual = YEAR / step
    def rv(start, end):
        return (sums[end] - sums[start]) * annual / (end - start)
    features = {}
    if forecast:
        spans = [round(hours * HOUR / step) for hours in (24, 168, 720)]
        for i in range(max(spans), len(bars) - n, stride):
            features[i] = [1.] + [rv(i - k, i) for k in spans]
        train = [i for i in features if i + n < cut]
        if len(train) < 4:
            raise ValueError("too few completed training decisions for the HAR model")
        weights = fit_har([features[i] for i in train], [rv(i, i + n) for i in train])
    elif forecast_strike or filters or ratio is not None:
        raise ValueError("conditional signals and forecast strikes require a HAR forecast")
    schedule = sorted(events)
    rows = []
    for i in features if forecast else range(0, len(bars) - n, stride):
        if i < cut <= i + n:  # Never train on a settlement inside the holdout.
            continue
        time, spot, vol = bars[i]
        iv = vol * contract.multiplier
        predicted = max(1e-10, math.sumprod(weights, features[i])) if forecast else None
        take = True
        if ratio is not None:
            q = iv * iv / predicted
            take = q < ratio if contract.strategy == "long-straddle" else q > ratio
        if filters:
            event = bisect_left(schedule, time - HOUR)
            near = event < len(schedule) and schedule[event] <= bars[i + n][0] + 2 * HOUR
            momentum = math.log(spot / bars[i - spans[1]][1])
            adverse = ((contract.strategy.endswith("call") and momentum > .06) or
                       (contract.strategy.endswith("put") and momentum < -.06))
            take = take and not (near or adverse or features[i][1] > 2 * features[i][3])
        trade = settlement(bars, i, contract, math.sqrt(predicted) if forecast_strike else None) if take else None
        rows.append((i, i >= cut, iv ** 2, rv(i, i+n), predicted, trade))
    return rows


def summarize(rows, tenor, stride):
    if not rows:
        return {"decisions": 0, "trades": 0}
    trades = [r[5] for r in rows if r[5] is not None]
    implied = fmean(r[2] for r in rows)
    realized = fmean(r[3] for r in rows)
    gaps = [r[2] - r[3] for r in rows]
    block = max(2, math.ceil(tenor / stride) + 1)
    result = dict(decisions=len(rows), trades=len(trades), implied_vol=math.sqrt(implied),
                  realized_vol=math.sqrt(realized), mean_variance_gap=fmean(gaps),
                  variance_gap_ci95=sample_interval(gaps, block))
    if all(r[4] is not None for r in rows) and len(rows) > 1:
        try:
            result["forecast_realized_correlation"] = correlation([r[4] for r in rows], [r[3] for r in rows])
        except StatisticsError:
            result["forecast_realized_correlation"] = None
    if not trades:
        return result
    excess = [t[0] for t in trades]
    # For conditional strategies keep skipped decisions in calendar order.
    payoffs = [r[5][0] if r[5] else 0. for r in rows]
    result.update(mean_option_return=fmean(excess), win_rate=fmean(x > 0 for x in excess),
                  worst_option_return=min(excess), mean_total_return=fmean(t[1] for t in trades),
                  mean_premium_fraction=fmean(t[2] for t in trades),
                  return_per_decision_ci95=sample_interval(payoffs, block))
    rallies = sorted(trades, key=lambda t: t[3])[-max(1, math.ceil(.05 * len(trades))):]
    result["top_rally_mean_total_return"] = fmean(t[1] for t in rallies)
    # Partition regular entry times into nonoverlapping portfolios, including
    # initial capital in the peak so the first loss counts toward drawdown.
    spacing = math.ceil(tenor / stride)
    paths = []
    for offset in range(min(spacing, len(rows))):
        equity = peak = 1.
        drawdown = 0.
        for r in rows[offset::spacing]:
            if r[5]:
                equity *= 1 + r[5][1]
                peak = max(peak, equity)
                drawdown = min(drawdown, equity / peak - 1)
                if equity <= 0: break
        paths.append((equity, drawdown))
    result.update(median_ending_multiple=median(x for x, _ in paths),
                  worst_chain_drawdown=min(x for _, x in paths))
    return result
