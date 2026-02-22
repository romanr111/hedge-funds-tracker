from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta

from tracker.application.ports.historical_price_gateway import HistoricalPriceGateway
from tracker.config import PipelineConfig
from tracker.domain.portfolio import PipelineKPI, RiskFilteredSignal, TargetPosition
from tracker.domain.quarters import parse_report_quarter


@dataclass(frozen=True)
class WalkForwardResult:
    status: str
    return_rows: list[dict[str, object]]
    overall_kpis: list[PipelineKPI]
    quarter_kpis: list[PipelineKPI]



def _quarter_end_day(value: str) -> date:
    parsed = parse_report_quarter(value)
    if parsed is None:
        raise ValueError(f"Invalid report quarter: {value}")
    year, quarter = parsed
    if quarter == 1:
        return date(year, 3, 31)
    if quarter == 2:
        return date(year, 6, 30)
    if quarter == 3:
        return date(year, 9, 30)
    return date(year, 12, 31)



def _spearman(values_x: list[float], values_y: list[float]) -> float | None:
    if len(values_x) != len(values_y) or len(values_x) < 2:
        return None
    rank_x = _ranks(values_x)
    rank_y = _ranks(values_y)
    return _pearson(rank_x, rank_y)



def _ranks(values: list[float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    idx = 0
    while idx < len(indexed):
        j = idx
        while j + 1 < len(indexed) and indexed[j + 1][1] == indexed[idx][1]:
            j += 1
        rank = (idx + j + 2) / 2.0
        for k in range(idx, j + 1):
            ranks[indexed[k][0]] = rank
        idx = j + 1
    return ranks



def _pearson(values_x: list[float], values_y: list[float]) -> float | None:
    if len(values_x) != len(values_y) or not values_x:
        return None
    mean_x = sum(values_x) / float(len(values_x))
    mean_y = sum(values_y) / float(len(values_y))
    num = 0.0
    den_x = 0.0
    den_y = 0.0
    for x, y in zip(values_x, values_y):
        dx = x - mean_x
        dy = y - mean_y
        num += dx * dy
        den_x += dx * dx
        den_y += dy * dy
    if den_x <= 0 or den_y <= 0:
        return None
    return num / math.sqrt(den_x * den_y)



def _next_trading_day(days: list[date], target: date) -> date | None:
    for day in days:
        if day >= target:
            return day
    return None



def _prev_trading_day(days: list[date], target: date) -> date | None:
    eligible = [day for day in days if day <= target]
    if not eligible:
        return None
    return eligible[-1]



def _price_on_or_before(series: dict[date, float], day: date) -> float | None:
    eligible = [item for item in series if item <= day]
    if not eligible:
        return None
    target = max(eligible)
    value = series.get(target)
    if value is None or value <= 0:
        return None
    return value



def _return_between(series: dict[date, float], start_day: date, end_day: date) -> float | None:
    start_price = _price_on_or_before(series, start_day)
    end_price = _price_on_or_before(series, end_day)
    if start_price is None or end_price is None or start_price <= 0:
        return None
    return (end_price / start_price) - 1.0



def _max_drawdown(returns: list[float]) -> float:
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for item in returns:
        equity *= 1.0 + item
        if equity > peak:
            peak = equity
        if peak > 0:
            drawdown = (peak - equity) / peak
            if drawdown > max_dd:
                max_dd = drawdown
    return max_dd



def _cagr(returns: list[float]) -> float:
    if not returns:
        return 0.0
    equity = 1.0
    for item in returns:
        equity *= 1.0 + item
    years = len(returns) / 252.0
    if years <= 0 or equity <= 0:
        return 0.0
    return pow(equity, 1.0 / years) - 1.0



def _portfolio_weights_for_date(
    *,
    day: date,
    entries: list[tuple[str, date]],
    exit_by_quarter: dict[str, date],
    positions_by_quarter: dict[str, list[TargetPosition]],
    hold_quarters: int,
) -> dict[str, float]:
    active = [quarter for quarter, entry_day in entries if entry_day <= day <= exit_by_quarter[quarter]]
    if not active:
        return {}
    active = active[-hold_quarters:]
    vintage_weight = 1.0 / float(len(active))
    composed: dict[str, float] = {}
    for quarter in active:
        for position in positions_by_quarter.get(quarter, []):
            composed[position.ticker] = composed.get(position.ticker, 0.0) + (position.weight_capped * vintage_weight)
    return composed



def _turnover(prev_weights: dict[str, float], next_weights: dict[str, float]) -> float:
    tickers = set(prev_weights) | set(next_weights)
    total_abs = sum(abs(next_weights.get(ticker, 0.0) - prev_weights.get(ticker, 0.0)) for ticker in tickers)
    return 0.5 * total_abs



def run_walk_forward_backtest(
    *,
    quarters: list[str],
    positions_by_quarter: dict[str, list[TargetPosition]],
    risk_signals_by_quarter: dict[str, list[RiskFilteredSignal]],
    price_gateway: HistoricalPriceGateway,
    pipeline: PipelineConfig,
    benchmark_ticker: str = "SPY",
) -> WalkForwardResult:
    if not quarters:
        return WalkForwardResult(status="no_quarters", return_rows=[], overall_kpis=[], quarter_kpis=[])

    benchmark_start = _quarter_end_day(quarters[0]) + timedelta(days=45)
    benchmark_end = _quarter_end_day(quarters[-1]) + timedelta(days=365)
    benchmark_prices = price_gateway.get_benchmark_series(benchmark_ticker, benchmark_start, benchmark_end)
    benchmark_days = sorted(benchmark_prices.keys())
    if len(benchmark_days) < 3:
        return WalkForwardResult(status="partial_data", return_rows=[], overall_kpis=[], quarter_kpis=[])

    entries: list[tuple[str, date]] = []
    for quarter in quarters:
        signal_available_day = _quarter_end_day(quarter) + timedelta(days=45)
        entry_day = _next_trading_day(benchmark_days, signal_available_day)
        if entry_day is None:
            continue
        entries.append((quarter, entry_day))

    if not entries:
        return WalkForwardResult(status="partial_data", return_rows=[], overall_kpis=[], quarter_kpis=[])

    exit_by_quarter: dict[str, date] = {}
    for idx, (quarter, _) in enumerate(entries):
        exit_idx = idx + pipeline.hold_quarters
        if exit_idx < len(entries):
            exit_day = entries[exit_idx][1]
        else:
            exit_day = benchmark_days[-1]
        prev_day = _prev_trading_day(benchmark_days, exit_day)
        exit_by_quarter[quarter] = prev_day or exit_day

    all_tickers = sorted(
        {
            position.ticker
            for quarter in quarters
            for position in positions_by_quarter.get(quarter, [])
            if position.ticker and position.ticker != "UNKNOWN"
        }
    )
    start_day = min(day for _, day in entries) - timedelta(days=5)
    end_day = benchmark_days[-1]
    ticker_prices = price_gateway.get_eod_prices(all_tickers, start_day, end_day)

    rebalances = {entry_day for _, entry_day in entries}
    prev_weights: dict[str, float] = {}
    return_rows: list[dict[str, object]] = []
    strategy_gross_returns: list[float] = []
    strategy_net_returns: list[float] = []
    benchmark_returns: list[float] = []

    for idx in range(1, len(benchmark_days)):
        day = benchmark_days[idx]
        prev_day = benchmark_days[idx - 1]

        current_weights = _portfolio_weights_for_date(
            day=prev_day,
            entries=entries,
            exit_by_quarter=exit_by_quarter,
            positions_by_quarter=positions_by_quarter,
            hold_quarters=pipeline.hold_quarters,
        )

        gross_return = 0.0
        for ticker, weight in current_weights.items():
            series = ticker_prices.get(ticker, {})
            prev_price = series.get(prev_day)
            curr_price = series.get(day)
            if prev_price is None or curr_price is None or prev_price <= 0:
                continue
            gross_return += weight * ((curr_price / prev_price) - 1.0)

        benchmark_prev = benchmark_prices.get(prev_day)
        benchmark_curr = benchmark_prices.get(day)
        if benchmark_prev is None or benchmark_curr is None or benchmark_prev <= 0:
            continue
        benchmark_return = (benchmark_curr / benchmark_prev) - 1.0

        turnover = 0.0
        if day in rebalances:
            next_weights = _portfolio_weights_for_date(
                day=day,
                entries=entries,
                exit_by_quarter=exit_by_quarter,
                positions_by_quarter=positions_by_quarter,
                hold_quarters=pipeline.hold_quarters,
            )
            turnover = _turnover(prev_weights, next_weights)
            prev_weights = next_weights

        transaction_cost = turnover * ((pipeline.cost_bps_per_side / 10_000.0) * 2.0)
        net_return = gross_return - transaction_cost

        return_rows.append(
            {
                "date": day.isoformat(),
                "strategy_gross_return": gross_return,
                "strategy_net_return": net_return,
                "benchmark_return": benchmark_return,
                "turnover": turnover,
            }
        )
        strategy_gross_returns.append(gross_return)
        strategy_net_returns.append(net_return)
        benchmark_returns.append(benchmark_return)

    if not return_rows:
        return WalkForwardResult(status="partial_data", return_rows=[], overall_kpis=[], quarter_kpis=[])

    mean_return = sum(strategy_net_returns) / float(len(strategy_net_returns))
    var = sum((item - mean_return) ** 2 for item in strategy_net_returns) / float(len(strategy_net_returns))
    std = math.sqrt(var)
    sharpe = (math.sqrt(252.0) * (mean_return / std)) if std > 0 else 0.0

    cumulative_strategy = 1.0
    cumulative_benchmark = 1.0
    for strategy_return, benchmark_return in zip(strategy_net_returns, benchmark_returns):
        cumulative_strategy *= 1.0 + strategy_return
        cumulative_benchmark *= 1.0 + benchmark_return

    overall_kpis = [
        PipelineKPI(metric="cumulative_return", scope="overall", scope_key=None, value=cumulative_strategy - 1.0),
        PipelineKPI(
            metric="excess_return_vs_spy",
            scope="overall",
            scope_key=None,
            value=(cumulative_strategy - cumulative_benchmark),
        ),
        PipelineKPI(metric="cagr", scope="overall", scope_key=None, value=_cagr(strategy_net_returns)),
        PipelineKPI(metric="sharpe", scope="overall", scope_key=None, value=sharpe),
        PipelineKPI(metric="max_drawdown", scope="overall", scope_key=None, value=_max_drawdown(strategy_net_returns)),
        PipelineKPI(metric="turnover", scope="overall", scope_key=None, value=sum(item["turnover"] for item in return_rows)),
    ]

    quarter_kpis: list[PipelineKPI] = []
    precision_values: list[float] = []
    hit_values: list[float] = []
    ic_values: list[float] = []

    for quarter, entry_day in entries:
        exit_day = exit_by_quarter.get(quarter)
        if exit_day is None or exit_day <= entry_day:
            continue
        benchmark_period_return = _return_between(benchmark_prices, entry_day, exit_day)
        if benchmark_period_return is None:
            continue

        selected = positions_by_quarter.get(quarter, [])
        hits = 0
        total = 0
        for position in selected:
            stock_return = _return_between(ticker_prices.get(position.ticker, {}), entry_day, exit_day)
            if stock_return is None:
                continue
            total += 1
            if stock_return > benchmark_period_return:
                hits += 1
        if total > 0:
            precision = hits / float(total)
            precision_values.append(precision)
            hit_values.append(precision)
            quarter_kpis.append(PipelineKPI(metric="precision_at_k", scope="quarter", scope_key=quarter, value=precision))
            quarter_kpis.append(PipelineKPI(metric="hit_rate_vs_spy", scope="quarter", scope_key=quarter, value=precision))

        scores: list[float] = []
        forward_returns: list[float] = []
        for signal in risk_signals_by_quarter.get(quarter, []):
            if not signal.passed_filters:
                continue
            stock_return = _return_between(ticker_prices.get(signal.ticker, {}), entry_day, exit_day)
            if stock_return is None:
                continue
            scores.append(signal.score_risk)
            forward_returns.append(stock_return)
        ic_value = _spearman(scores, forward_returns)
        if ic_value is not None:
            ic_values.append(ic_value)
            quarter_kpis.append(PipelineKPI(metric="ic", scope="quarter", scope_key=quarter, value=ic_value))

    if precision_values:
        overall_kpis.append(
            PipelineKPI(
                metric="precision_at_k",
                scope="overall",
                scope_key=None,
                value=sum(precision_values) / float(len(precision_values)),
            )
        )
    if hit_values:
        overall_kpis.append(
            PipelineKPI(
                metric="hit_rate_vs_spy",
                scope="overall",
                scope_key=None,
                value=sum(hit_values) / float(len(hit_values)),
            )
        )
    if ic_values:
        overall_kpis.append(
            PipelineKPI(metric="mean_ic", scope="overall", scope_key=None, value=sum(ic_values) / float(len(ic_values)))
        )

    return WalkForwardResult(status="ok", return_rows=return_rows, overall_kpis=overall_kpis, quarter_kpis=quarter_kpis)
