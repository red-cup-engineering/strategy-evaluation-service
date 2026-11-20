# strategy-evaluation-service

<!-- 561-group-standards:badges:start -->
[![Red Cup Engineering](https://img.shields.io/badge/561%20Group-Red%20Cup%20Engineering-c0392b)](https://github.com/red-cup-engineering)
[![License: MIT](https://img.shields.io/badge/license-MIT-c0392b)](LICENSE)
[![Indigenous data: CARE](https://img.shields.io/badge/Indigenous%20data-CARE-4b6f44)](https://github.com/red-cup-engineering/.github/blob/main/GOVERNANCE.md)
[![Tips welcome](https://img.shields.io/badge/tips-welcome-d4a017)](https://github.com/561-group/.github/blob/main/TIPS.md)
<!-- 561-group-standards:badges:end -->

## Option volatility studies

The package includes `option-volatility-study` (Python 3.12 or newer, standard library).
It compares implied and realized variance and evaluates European option payoffs at
modelled prices. Supply contiguous hourly or daily observations as CSV with
`time_ms,close,iv` columns. Timestamps are completed bars in UTC milliseconds,
close is the underlying price, and IV is annualized volatility as a fraction.

```sh
python3 src/options/study.py history BTC 2025-01-01T00:00:00Z 2026-01-01T00:00:00Z > bars.csv
python3 src/options/study.py calibrate BTC --days 7 --side C --delta .10
python3 src/options/study.py evaluate bars.csv --bars 7 --strategy covered-call --delta .10 --multiplier .97
python3 src/options/study.py breakeven bars.csv --bars 7 --delta .10
```

`calibrate` reports the actual tenor, absolute delta, IV multiplier, and bid/ask
ratios of the nearest eligible two-sided quote. Measure those inputs for each
asset and scenario. A current quote supplies an assumption for a historical
study. It does not establish historical execution prices.

`evaluate` reports separate training and holdout results, including option P&L,
the strategy return (including the underlying for a covered call, or measured
on premium for a long straddle), drawdown across nonoverlapping portfolios,
rally outcomes, variance gaps, and moving-block intervals. Fees, hedge bands,
quote ratios and entry cadence are configurable. `--har` forecasts variance
from daily, weekly and monthly trailing realized variance. Training excludes
settlements crossing the split. Combine it with `--iv-variance-ratio` to select
expensive sales or cheap straddles, `--forecast-strike` to set short-option
strikes from forecast IV, and `--filters` with repeated UTC `--event` values to
exclude event windows, volatility spikes and adverse momentum. Delta hedges use
each observed bar and charge turnover costs. Funding and interest are assumed
zero. `breakeven` reports the first upward crossing on the 0.4–1.8 IV-multiplier
grid and returns null when it cannot bracket one.
