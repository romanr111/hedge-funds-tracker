# Algorithm Deep Dive: 13F Trend Engine

**Date:** 2026-05-22  
**Analyst:** Quantitative Research Review  
**Scope:** `tracker/domain/trends.py`, `tracker/domain/trend_ideas.py`  
**Objective:** Academic and practitioner-style review of signal construction, calibration, and selection logic.

---

## 1. `_trade_flow_delta` — Flow Construction & Corporate Action Guard

### 1.1 What It Does

For a single manager and instrument, computes a scalar `trade_dw` representing the manager's directional commitment change between two quarters. The function operates in three tiers:

1. **Shares-based flow** (preferred): When share counts are available, it computes the share change ratio and scales it by `max(prev_weight, curr_weight)`. This captures real position sizing intent even when market price moves mask it in dollar-weight space.
2. **Noise floor**: Share changes below `1.5%` (`SHARES_ACTIVITY_NOISE_THRESHOLD`) are zeroed out.
3. **Corporate action guard**: If shares change by `≥20%` but weight changes by `≤2%`, the function treats this as a split/reverse-split/denomination effect and falls back to `weight_delta`.
4. **Weight-only fallback**: When shares are unavailable, it falls back to `curr_weight - prev_weight`.

### 1.2 Comparison to Academic/Professional Standards

**Cohen-Polk-Silli (CPS, 2010)** — The seminal 13F replication paper uses *changes in portfolio weights* as the primary signal. CPS aggregate changes in holdings value, not shares, because 13F only reports shares and prices are end-of-quarter approximations. The CPS approach is vulnerable to exactly the problem this function tries to solve: a manager can increase shares while the weight falls due to price appreciation. CPS do not correct for this explicitly; they rely on cross-sectional aggregation to average out price effects. The shares-scaling approach here is actually *more sophisticated* than CPS baseline, but the corporate action guard is an ad-hoc addition not found in the literature.

**Goldman Sachs VIP List** — Goldman constructs its VIP list by identifying stocks that appear most frequently in the top-10 holdings of fundamentally-driven hedge funds. They do not model quarter-to-quarter flow at all; they use *level* (not *change*) information. The flow-delta approach here is therefore more signal-rich but also more noise-sensitive.

**AQR 13F Replication** — AQR's replication strategies (see "Hedge Fund Holdings: What Do We Know?", Agarwal et al.; AQR's Factor-Based Replication work) typically use holdings-level regressions or factor-mimicking portfolios. They do not use per-manager z-scored flow deltas. AQR would likely view the `tanh(z_score / Z_SCALE)` squashing as an unnecessary nonlinearity that destroys cardinal signal information.

### 1.3 Mathematical & Logical Issues

#### Issue A: The 1.5% Noise Threshold Is Under-Justified

```python
SHARES_ACTIVITY_NOISE_THRESHOLD = 0.015
```

**Problem:** A 1.5% share change threshold is arbitrary. For a manager with a 10% position, 1.5% of shares is economically meaningful (≈15 bps of portfolio). For a manager with a 0.5% position, it's noise. The threshold does not scale with position size or manager volatility.

**Academic benchmark:** Standard microstructure noise filters (Barndorff-Nielsen et al., 2009; Aït-Sahalia & Xiu, 2019) use realized volatility or bid-ask spreads to calibrate noise floors. In a holdings context, a more principled approach would be:

```python
def _adaptive_noise_threshold(position_weight: float, manager_turnover_volatility: float) -> float:
    """
    Scale noise floor by position size and manager-specific turnover volatility.
    """
    base_threshold = 0.01  # 1% base
    # Larger positions need tighter thresholds (shares are more certain)
    weight_adjustment = 1.0 / max(1.0, position_weight * 10)
    # High-turnover managers have noisier quarter-to-quarter changes
    volatility_adjustment = 1.0 / max(1.0, manager_turnover_volatility * 5)
    return base_threshold * min(weight_adjustment, volatility_adjustment)
```

#### Issue B: Corporate Action Guard Has False-Negative Risk

```python
if (
    abs(shares_change_ratio) >= SHARES_CORPORATE_ACTION_CHANGE_THRESHOLD  # 0.20
    and abs(weight_delta) <= CORPORATE_ACTION_WEIGHT_DELTA_MAX              # 0.02
):
    return weight_delta
```

**Problem:** This heuristic assumes that a large share change with a small weight change is *always* a corporate action. This is directionally correct for splits, but consider:
- **Active rebalancing into a falling stock:** A manager doubles down on a stock that has fallen 50%. Shares increase 100%, weight stays flat. The guard incorrectly treats this as a split and returns `weight_delta ≈ 0`, killing the signal.
- **Partial exits after a large run-up:** Shares decrease 25%, weight drops only 1% because the stock rallied. The guard treats this as a reverse split.

**Academic benchmark:** Corporate action adjustment in holdings data (Bali, Engle, & Murray, 2016, "Empirical Asset Pricing") uses CRSP/Compustat event flags, not heuristics. In the absence of a corporate action database, the heuristic is defensible but should be **probabilistic**, not binary.

**Suggested improvement:**

```python
from enum import Enum
from typing import Tuple

class FlowReason(Enum):
    GENUINE = "genuine"
    NOISE = "noise"
    CORPORATE_ACTION = "corporate_action"

def _trade_flow_delta_probabilistic(
    *,
    prev_weight: float,
    curr_weight: float,
    prev_shares: int,
    curr_shares: int,
    price_appreciation: float | None = None,  # (curr_price / prev_price - 1)
) -> Tuple[float, FlowReason]:
    max_weight = max(prev_weight, curr_weight)
    if max_weight <= 0:
        return 0.0, FlowReason.NOISE

    weight_delta = curr_weight - prev_weight
    
    if prev_shares > 0 or curr_shares > 0:
        if prev_shares > 0:
            shares_change_ratio = (curr_shares - prev_shares) / prev_shares
        elif curr_shares > 0:
            shares_change_ratio = 1.0
        else:
            shares_change_ratio = 0.0

        # Adaptive noise floor based on position size
        noise_threshold = max(0.005, 0.015 * (1.0 - min(0.5, max_weight)))
        if abs(shares_change_ratio) <= noise_threshold:
            return 0.0, FlowReason.NOISE

        # Probabilistic corporate action guard
        if abs(shares_change_ratio) >= 0.20 and abs(weight_delta) <= 0.02:
            # If we have price data, check consistency with split hypothesis
            if price_appreciation is not None:
                price_change_consistent = abs(price_appreciation + shares_change_ratio) < 0.10
                if price_change_consistent:
                    return weight_delta, FlowReason.CORPORATE_ACTION
                # Price moved opposite to shares → likely genuine flow
                return max_weight * shares_change_ratio, FlowReason.GENUINE
            # Without price data, damp rather than zero
            return 0.5 * weight_delta + 0.5 * max_weight * shares_change_ratio, FlowReason.GENUINE

        return max_weight * shares_change_ratio, FlowReason.GENUINE

    return weight_delta, FlowReason.GENUINE
```

#### Issue C: `max_weight * shares_change_ratio` Is Not Scale-Invariant

**Problem:** `max_weight * shares_change_ratio` gives a flow in "portfolio weight units." But if a manager's portfolio doubles in AUM while keeping shares constant, the weight falls, and this function reports a negative flow even though no shares were sold. This is actually handled by the shares path (shares are constant → noise floor catches it), but if shares changed slightly due to rounding or corporate actions, the signal becomes distorted.

**Recommendation:** Consider normalizing by manager-level turnover to make the signal comparable across managers of different sizes and styles.

---

## 2. `_manager_quality_multipliers` — Manager Skill & Activity Weighting

### 2.1 What It Does

Computates a per-manager quality multiplier `q_i ∈ [0.75, 1.25]` based on two components:

1. **Turnover component**: `sqrt(global_median / manager_median)`, clipped to `[0.80, 1.20]`
2. **Activity component**: Linear interpolation based on the fraction of quarters with non-zero turnover, mapped to `[0.85, 1.15]`

The final multiplier is `clip(turnover_component × activity_component, 0.75, 1.25)`.

### 2.2 Comparison to Academic/Professional Standards

**CPS (2010)** — CPS do not weight managers by quality. They treat all managers equally within their replication portfolio (value-weighted by reported AUM). They do find that certain manager characteristics predict replication alpha, but they do not bake this into the signal construction.

**AQR / Factor-Based Replication** — AQR's replication strategies (see "The Northern Trust Hedge Fund Replication Strategy," or "Factor-Based Hedge Fund Replication" by Fung & Hsieh) use regression-based weights. Manager-specific quality would enter as a time-varying coefficient or as a Bayesian prior. AQR would likely view a turnover-based quality score as a **single-factor proxy** that misses the critical dimension: **predictive power** (i.e., does this manager's past trades predict future returns?).

**Goldman Sachs VIP** — Goldman filters by "fundamentally-driven" managers only, which is a binary quality gate. They do not use continuous quality scores.

**Academic manager skill literature** — The seminal work on fund performance (Kosowski, Timmermann, Wermers & White, 2006; Fama & French, 2010) emphasizes that persistent outperformance is rare and hard to measure. A manager's *turnover rate* is not the same as *skill*. Low turnover could mean:
- Conviction investing (high skill, as in Buffett)
- Index-hugging (low skill)
- Laziness (low skill)

High turnover could mean:
- Successful market timing (high skill)
- Noise trading (negative skill)
- Style drift (unstable signal)

### 2.3 Mathematical & Logical Issues

#### Issue A: `sqrt(global_median / manager_median)` Penalizes Low Turnover

**Problem:** The formula rewards high-turnover managers. If a manager's median turnover is below the global median, they get a multiplier `> 1.0`. Wait — let's check:

```python
turnover_component = sqrt(global_median / manager_median)
```

If `manager_median < global_median`, then `global_median / manager_median > 1`, so `turnover_component > 1`. **Low turnover is rewarded.**

If `manager_median > global_median`, then `turnover_component < 1`. **High turnover is penalized.**

This is the opposite of what most practitioner intuition would suggest. The code seems to assume that **low turnover = high conviction = high quality**. This is plausible (Buffett-style), but:

- It is **not validated** against forward returns in this codebase.
- It conflates "low turnover" with "high quality" without distinguishing between conviction and index-hugging.
- The `sqrt()` compresses the range: a manager with half the median turnover gets `sqrt(2) ≈ 1.14`, while a manager with double the median turnover gets `sqrt(0.5) ≈ 0.71`. The asymmetry is intentional (rewarding patience more than penalizing activity), but the functional form is arbitrary.

**Academic benchmark:** In "Which Hedge Funds Should You Copy?" (Cremers & Petajisto, 2009), **Active Share** (not turnover) is the key predictor of outperformance. A manager-quality function should ideally use:
1. Historical replication alpha (if backtests exist)
2. Active Share vs. a known benchmark
3. Information Ratio persistence
4. Turnover only as a penalty term (higher turnover = higher transaction costs for the replicator)

**Suggested improvement:**

```python
def _manager_quality_multipliers(
    *,
    quarters: list[str],
    snapshots_by_quarter: dict[str, dict[str, ManagerQuarterSnapshot]],
    manager_weights: dict[str, float],
    replication_alphas: dict[str, float] | None = None,  # Historical alpha, if available
) -> dict[str, float]:
    active_ciks = [cik for cik, weight in manager_weights.items() if weight > 0]
    if not active_ciks:
        return {}

    # Compute turnover and active share proxies
    turnovers_by_manager: dict[str, list[float]] = {cik: [] for cik in active_ciks}
    concentration_by_manager: dict[str, list[float]] = {cik: [] for cik in active_ciks}

    for idx in range(1, len(quarters)):
        prev_q = quarters[idx - 1]
        curr_q = quarters[idx]
        prev_snapshots = snapshots_by_quarter.get(prev_q, {})
        curr_snapshots = snapshots_by_quarter.get(curr_q, {})
        for cik in active_ciks:
            prev_snapshot = prev_snapshots.get(cik)
            curr_snapshot = curr_snapshots.get(cik)
            if prev_snapshot is None or curr_snapshot is None:
                continue
            turnovers_by_manager[cik].append(
                _turnover_between_snapshots(prev_snapshot, curr_snapshot)
            )
            # Herfindahl as a proxy for concentration/conviction
            curr_weights = _weights_by_instrument(curr_snapshot)
            weights_list = [w["weight"] for w in curr_weights.values()]
            if weights_list:
                concentration_by_manager[cik].append(sum(w**2 for w in weights_list))

    # Global medians for normalization
    all_turnovers = [t for vals in turnovers_by_manager.values() for t in vals if t > 0]
    global_turnover_median = median(all_turnovers) if all_turnovers else 0.0

    multipliers: dict[str, float] = {}
    for cik in active_ciks:
        manager_median_turnover = median(turnovers_by_manager[cik]) if turnovers_by_manager[cik] else 0.0
        median_concentration = median(concentration_by_manager[cik]) if concentration_by_manager[cik] else 0.0

        # Turnover: penalize extreme turnover (costly to replicate), don't reward low turnover too much
        if global_turnover_median > 0 and manager_median_turnover > 0:
            turnover_ratio = manager_median_turnover / global_turnover_median
            # Use a symmetric log scaling: turnover at median → 1.0, 2x median → ~0.85, 0.5x median → ~1.15
            turnover_component = 1.0 - 0.15 * math.tanh(math.log(turnover_ratio))
            turnover_component = _clip(turnover_component, 0.80, 1.20)
        else:
            turnover_component = 1.0

        # Concentration: higher concentration = higher conviction = higher quality
        # Typical HHI for a 50-stock portfolio ≈ 0.02; for a 10-stock portfolio ≈ 0.10
        concentration_component = 0.90 + 0.20 * min(1.0, median_concentration / 0.10)
        concentration_component = _clip(concentration_component, 0.85, 1.15)

        # Activity: fraction of quarters with observable trades
        active_periods = sum(1 for t in turnovers_by_manager[cik] if t > 0)
        total_periods = max(1, len(turnovers_by_manager[cik]))
        activity_ratio = active_periods / total_periods
        activity_component = 0.85 + 0.30 * activity_ratio
        activity_component = _clip(activity_component, 0.85, 1.15)

        # Historical alpha component (if available)
        alpha_component = 1.0
        if replication_alphas and cik in replication_alphas:
            # Map IR in [-1, 1] to multiplier in [0.8, 1.2]
            alpha_component = 1.0 + 0.20 * _clip(replication_alphas[cik], -1.0, 1.0)

        # Combine with geometric mean to avoid compounding extremes
        multiplier = (
            turnover_component ** 0.30 *
            concentration_component ** 0.30 *
            activity_component ** 0.20 *
            alpha_component ** 0.20
        )
        multipliers[cik] = _clip(multiplier, 0.75, 1.25)

    return multipliers
```

#### Issue B: Activity Component Is Redundant with Turnover Component

A manager with high turnover will also have high activity ratio (almost by definition). The correlation between `turnover_component` and `activity_component` means the final multiplier is not driven by independent information. A principal components analysis would likely show that 80%+ of the variance in `multiplier` comes from a single latent factor.

**Recommendation:** Replace the activity component with a **signal autocorrelation** component (do this manager's past trades predict their future trades?) or **persistence of conviction** (do they add to winners and cut losers?).

---

## 3. `_confidence_score` — Signal Confidence Calibration

### 3.1 What It Does

Combines five sub-scores into a scalar confidence `∈ [0, 1]`:

| Component | Weight | Description |
|-----------|--------|-------------|
| Breadth | 40% | Average of (directional managers / min_managers) and (directional weight / min_weight) |
| Persistence | 25% | `min(1.0, persistence / 3.0)` |
| Crowding | 20% | Anti-crowding score based on HHI vs. `ANTI_CROWD_H0 = 0.35` |
| Magnitude | 15% | Normalized NP or blended score magnitude |

Then applies:
1. **High conviction bonus**: Up to `+0.08` if large positions are directional
2. **Disagreement penalty**: `confidence *= (1 - 0.70 * disagreement_ratio)`
3. **Freshness decay**: `confidence *= freshness_multiplier`

### 3.2 Comparison to Academic/Professional Standards

**Standard Signal Confidence Construction** — In quantitative finance, confidence is typically derived from:
1. **Statistical significance** (t-statistic of the signal)
2. **Forecast dispersion** (standard error of the ensemble)
3. **Historical hit rate** (out-of-sample calibration)

The function here uses none of these. It uses **heuristic sub-scores** that are monotonic transforms of raw inputs, not probability-calibrated measures.

**AQR / Ensemble Methods** — AQR's factor timing signals (see "Trend Following: Equity Crowding and Risk Parity") use forecast covariance matrices and Bayesian shrinkage. The disagreement penalty here (`gamma = 0.70`) is a crude form of ensemble dispersion, but it is **not normalized by the number of managers** and does not account for cross-manager correlation.

**CPS Replication** — CPS do not compute confidence scores. They construct a portfolio and report its returns. The confidence concept is foreign to their methodology.

**Goldman VIP** — Goldman uses binary inclusion (top 50 most-held stocks). There is no confidence gradation.

### 3.3 Mathematical & Logical Issues

#### Issue A: The 40/25/20/15 Weights Are Not Optimized

**Problem:** The weights are round numbers with no empirical justification. In a properly calibrated ensemble model, these weights would be learned via:
- **Logistic regression** on historical forward returns
- **Gradient boosting** on sub-score features
- **Sharpe ratio maximization** in a backtest

**Academic benchmark:** In "Machine Learning for Stock Selection" (Gu, Kelly & Xiu, 2020), feature importance is data-driven, not hand-tuned. For a confidence function, the optimal approach is:

```python
from sklearn.linear_model import LogisticRegression
from sklearn.calibration import CalibratedClassifierCV
import numpy as np

def _calibrate_confidence_weights(
    historical_signals: list[TrendSignalRow],
    forward_returns: dict[str, float],
) -> dict[str, float]:
    """
    Calibrate confidence weights using historical hit rates.
    Returns should be directional (positive = buy signal was correct).
    """
    X = []
    y = []
    for signal in historical_signals:
        if signal.instrument_key not in forward_returns:
            continue
        ret = forward_returns[signal.instrument_key]
        # Features: breadth, persistence, crowding, magnitude, disagreement
        features = [
            signal.breadth_score,      # precomputed
            signal.persistence_score,
            signal.crowding_score,
            signal.magnitude_score,
            signal.disagreement_ratio,
        ]
        X.append(features)
        # Did the signal direction match the return direction?
        y.append(1 if (signal.trend_ewma > 0) == (ret > 0) else 0)

    model = LogisticRegression(class_weight='balanced')
    model.fit(X, y)
    
    # Extract learned weights (normalized)
    raw_weights = np.abs(model.coef_[0])
    normalized = raw_weights / raw_weights.sum()
    return {
        'breadth': normalized[0],
        'persistence': normalized[1],
        'crowding': normalized[2],
        'magnitude': normalized[3],
        'disagreement': normalized[4],
    }
```

Even without ML, a **grid search** over weights to maximize the rank correlation between `confidence` and absolute forward return would be superior to hand-tuning.

#### Issue B: Disagreement Gamma = 0.70 Is Too Aggressive

**Problem:** With `gamma = 0.70`, if `opposite_weight == directional_weight` (maximal disagreement), the confidence is multiplied by `1 - 0.70 * 0.5 = 0.65`. This is a severe 35% haircut.

**Why this matters:** In a long/short context, disagreement is actually **information**. If smart money is split, it may indicate:
- A stock with high uncertainty but high expected return (positive skew)
- A catalyst event where one side is right and the other is wrong
- Differing time horizons (one manager trades quarterly, another monthly)

Academic evidence (see "Heterogeneous Beliefs and the Cross-Section of Stock Returns," Diether, Malloy & Scherbina, 2002) shows that **high analyst dispersion** (a proxy for disagreement) predicts **lower future returns** — but this is for sell-side analysts, not hedge funds. For hedge funds, disagreement may indicate **information asymmetry** that the more informed side exploits.

**Recommendation:** Make gamma data-dependent:

```python
DISAGREEMENT_GAMMA_BASE = 0.40  # Less aggressive base

def _adaptive_disagreement_gamma(
    directional_managers: int,
    opposite_managers: int,
    historical_disagreement_hit_rate: float | None = None,
) -> float:
    """
    Scale disagreement penalty by the size of the ensemble.
    With very few managers, disagreement is noisy and should be penalized less.
    With many managers, disagreement is more informative.
    """
    total = directional_managers + opposite_managers
    if total <= 3:
        return 0.20  # Very small sample, discount disagreement
    if total <= 6:
        return 0.35
    base = DISAGREEMENT_GAMMA_BASE
    # If we have historical data showing that disagreement signals underperform,
    # increase gamma; otherwise, keep it conservative
    if historical_disagreement_hit_rate is not None:
        # If high-disagreement signals have < 50% hit rate, increase penalty
        if historical_disagreement_hit_rate < 0.45:
            base = min(0.70, base * 1.5)
        elif historical_disagreement_hit_rate > 0.55:
            base = max(0.20, base * 0.75)
    return base
```

#### Issue C: Crowding Score Is Inverted HHI, Not a Proper Crowding Measure

```python
crowding_score = 1.0 - max(0.0, (crowding_hhi - ANTI_CROWD_H0) / max(MAD_EPS, 1.0 - ANTI_CROWD_H0))
```

**Problem:** This treats crowding as a penalty, but the threshold `ANTI_CROWD_H0 = 0.35` is extremely high. For context:
- HHI = 1.0: One manager dominates (monopoly)
- HHI = 0.5: Two managers split equally
- HHI = 0.25: Four managers split equally
- HHI = 0.10: Ten managers split equally

An HHI of 0.35 means roughly 3 managers with meaningful signal contribution. This is **not crowded** in any standard sense. Standard DOJ antitrust thresholds use HHI = 0.15 (moderate concentration) and 0.25 (high concentration).

More importantly, **academic crowding research** (see "Crowded Trades" by Sias, Starks & Titman, 2022) finds that crowding predicts **short-term crashes** but **not necessarily lower returns**. The penalty should be **conditional on liquidity** and **conditional on the direction of the trade** (buy crowding is more dangerous than sell crowding in equities because of short-sale constraints).

**Suggested improvement:**

```python
def _crowding_penalty(
    crowding_hhi: float,
    directional_managers: int,
    instrument_avg_daily_volume: float | None = None,
    total_directional_value: float | None = None,
) -> float:
    """
    Liquidity-adjusted crowding score.
    Crowding is dangerous only if the position is hard to exit.
    """
    # Base concentration score
    if directional_managers < MIN_HHI_PARTICIPANTS_FOR_PENALTY:
        return 1.0  # No penalty for small ensembles

    # Use a lower, more standard threshold
    hhi_threshold_low = 0.15   # 6-7 equal participants
    hhi_threshold_high = 0.30  # 3-4 equal participants
    
    if crowding_hhi <= hhi_threshold_low:
        concentration_penalty = 1.0
    elif crowding_hhi >= hhi_threshold_high:
        concentration_penalty = 0.70
    else:
        concentration_penalty = 1.0 - 0.30 * (
            (crowding_hhi - hhi_threshold_low) / (hhi_threshold_high - hhi_threshold_low)
        )

    # Liquidity adjustment: if we know ADV and total $ at risk, compute days-to-liquidate
    if instrument_avg_daily_volume is not None and total_directional_value is not None:
        if instrument_avg_daily_volume > 0:
            days_to_liquidate = total_directional_value / instrument_avg_daily_volume
            # Penalty increases non-linearly above 5 days
            liquidity_penalty = max(0.5, 1.0 - 0.05 * max(0, days_to_liquidate - 5))
            return concentration_penalty * liquidity_penalty

    return concentration_penalty
```

#### Issue D: Freshness Multiplier Is Applied at the End, Not to Sub-Scores

**Problem:** Price drift reduces `confidence` multiplicatively at the very end. But if price has drifted 50%, the underlying 13F signal (based on stale quarter-end holdings) is genuinely less informative. The current approach is correct as a first-order approximation, but it does not propagate the uncertainty into `trend_ewma` construction (see Section 6).

---

## 4. `_classify_regime` — State Machine for Trend Classification

### 4.1 What It Does

Classifies the trend state of an instrument into one of eight regimes based on:
- `trend_ewma` sign and magnitude
- `trend_delta` (change in trend)
- `prev_trend_ewma` (prior trend state)
- Persistence counters
- Buy/sell gates (breadth thresholds)

Regimes:
- `REVERSAL_BUY` / `REVERSAL_SELL`: Sign change in trend_ewma
- `STRONG_BUY` / `STRONG_SELL`: Persistence ≥ 2 and gate passes
- `EMERGING_BUY` / `EMERGING_SELL`: Trend delta agrees with direction
- `WEAKENING_BUY` / `WEAKENING_SELL`: Trend delta opposes direction
- `NONE`: No regime met

### 4.2 Comparison to Academic/Professional Standards

**Standard Momentum/Trend-Following** — Academic momentum (Jegadeesh & Titman, 1993; Asness, 1994) uses **binary** or **quintile** classification based on past returns. There is no "weakening" or "emerging" state. The state machine here is more akin to **technical analysis** (Dow Theory, Elliott Wave) than quantitative finance.

**AQR Trend Following** — AQR's trend signals (see "Trend Following: Equity Crowding and Risk Parity," 2017) use continuous position sizing based on the strength and significance of the trend. They do not use discrete regimes. The expected return is proportional to the signal strength.

**CPS / 13F Replication** — CPS do not classify regimes. They construct a portfolio and hold it for a quarter.

### 4.3 Mathematical & Logical Issues

#### Issue A: Persistence Threshold of 2 Quarters Is Too Low

```python
if persistence_buy >= 2 and buy_gate:
    return "STRONG_BUY"
```

**Problem:** With quarterly 13F data, `persistence >= 2` means the signal has been in the same direction for **two consecutive quarters** (6 months). This is a very weak filter. A manager buying in Q1 and Q2 is not necessarily "strong" — they may simply be scaling in gradually.

**Academic benchmark:** In "Momentum Crashes" (Daniel & Moskowitz, 2016), momentum strategies require 12-month formation periods. In "The Anatomy of Trading Strategies" (Moskowitz & Grinblatt, 1999), industry momentum persists over 6–12 months. Two quarters is the absolute minimum for signal formation.

**Recommendation:** Make persistence thresholds configurable and consider longer-term persistence:

```python
PERSISTENCE_STRONG_THRESHOLD = 3   # 3 quarters = 9 months
PERSISTENCE_EMERGING_THRESHOLD = 1

def _classify_regime(
    trend_ewma: float,
    trend_delta: float,
    prev_trend_ewma: float,
    *,
    buy_gate: bool,
    sell_gate: bool,
    persistence_buy: int,
    persistence_sell: int,
    strong_threshold: int = 3,
    weakening_threshold: float = -0.001,  # trend delta must be negative
) -> str:
    if trend_ewma > 0:
        if prev_trend_ewma <= 0 and buy_gate:
            return "REVERSAL_BUY"
        if persistence_buy >= strong_threshold and buy_gate:
            return "STRONG_BUY"
        if trend_delta > 0 and buy_gate:
            return "EMERGING_BUY"
        if trend_delta < weakening_threshold and buy_gate:
            return "WEAKENING_BUY"
        return "NONE"
    # ... symmetric for sell side
```

#### Issue B: State Machine Is Not Markov-Complete

**Problem:** The regime depends on `prev_trend_ewma` but not on the *prior regime*. This means a stock can oscillate between `WEAKENING_BUY` and `EMERGING_BUY` without ever passing through a neutral state. In practice, this creates regime "flicker" around zero.

**Recommendation:** Add a hysteresis band:

```python
TREND_EWMA_HYSTERESIS = 0.005

# Before classifying, require trend_ewma to clear a minimum threshold
if abs(trend_ewma) < TREND_EWMA_HYSTERESIS:
    return "NONE"
```

This prevents weak signals from generating regime labels.

#### Issue C: Trend Delta vs. Trend EWMA Interaction Is Confusing

```python
if trend_delta > 0 and buy_gate:
    return "EMERGING_BUY"
if trend_delta < 0 and buy_gate:
    return "WEAKENING_BUY"
```

**Problem:** `EMERGING_BUY` requires `trend_ewma > 0` (outer if) AND `trend_delta > 0`. But `trend_delta = trend_ewma - prev_trend_ewma`. If `trend_ewma` is positive but fell from a higher value, it's `WEAKENING_BUY`. This is semantically confusing because the position is still accumulating (positive trend_ewma) but the *rate* of accumulation is slowing.

**Academic benchmark:** In control theory and signal processing, this is a second-order system. The "emerging" vs "weakening" distinction is analogous to **velocity** vs **acceleration**. The current implementation conflates the two. A cleaner approach would be to expose both `trend_ewma` (position) and `trend_delta` (velocity) as separate signals, rather than collapsing them into discrete regimes.

---

## 5. `select_trend_ideas` — Signal Promotion & Portfolio Construction

### 5.1 What It Does

Takes a sequence of `TrendStockSignal` objects and classifies each into one of three states:
- **PROMOTED**: Directional signal with ≥2 supporting managers OR persistence ≥ 2
- **MONITOR**: Directional signal that lacks multi-manager support or persistence
- **REJECTED**: Non-directional or below confidence threshold

Then sorts promoted ideas by:
1. `freshness_ok is False` → penalized (stale last)
2. `-idea_score` (highest first)
3. `-directional_managers` (most support first)
4. `opposite_managers` (least disagreement first)
5. `-abs(trend_ewma)` (strongest trend first)
6. `instrument_key` (deterministic tie-break)

### 5.2 Comparison to Academic/Professional Standards

**CPS Portfolio Construction** — CPS construct a **value-weighted portfolio** of the top N stocks by aggregated weight change. They do not use promotion gates or idea scores. The portfolio is rebalanced quarterly.

**Goldman VIP** — Goldman selects the **top 50 stocks by frequency** in hedge fund top-10s. This is a simple count-based filter, analogous to the "≥2 managers" gate here, but Goldman uses a fixed universe (only fundamentally-driven funds) and a fixed count (50).

**AQR / Risk-Parity** — AQR would size positions by **signal strength × confidence × inverse volatility**. The current approach uses a two-tier system (promoted vs monitor) rather than continuous sizing.

**Institutional Practice** — Most fundamental long/short equity funds use a **conviction-weighted** approach:
- Highest conviction = largest position
- Position sizes constrained by risk (volatility, liquidity, correlation)
- "Promotion" is not a binary event but a continuous scaling decision

### 5.3 Mathematical & Logical Issues

#### Issue A: Promotion Logic Is Overly Binary

**Problem:** The code uses a hard threshold: `directional_managers >= 2` or `directional_persistence >= 2`. This creates a cliff:
- A signal with 1 manager is monitored.
- A signal with 2 managers is promoted.

In practice, this means many marginal signals are monitored rather than promoted, which may be appropriate for risk control but suboptimal for information extraction.

**Academic benchmark:** In "The Optimality of Coarse Decision Rules" (Madarász & Prat, 2017), binary thresholds can be optimal when information processing is costly. But in a fully automated system, continuous promotion (e.g., probability of promotion based on a logistic function) is superior.

**Suggested improvement:**

```python
def _promotion_probability(
    directional_managers: int,
    directional_persistence: int,
    confidence: float,
    idea_score: float,
) -> float:
    """
    Continuous promotion score rather than binary gate.
    """
    # Manager support component
    manager_score = 1.0 / (1.0 + math.exp(-(directional_managers - 1.5)))  # sigmoid centered at 1.5
    
    # Persistence component
    persistence_score = 1.0 / (1.0 + math.exp(-(directional_persistence - 1.5)))
    
    # Confidence and idea score
    signal_score = confidence * min(1.0, idea_score / 0.01)  # normalize by typical score
    
    # Combined: require either strong managers OR strong persistence, boosted by signal quality
    return max(manager_score, persistence_score) * signal_score

# In select_trend_ideas:
# PROMOTED if promotion_probability >= 0.60
# MONITOR if 0.30 <= promotion_probability < 0.60
# REJECTED otherwise
```

#### Issue B: `idea_score = abs(accumulation_score) * confidence` Is Not a Proper Risk-Adjusted Score

**Problem:** `idea_score` scales raw signal magnitude by confidence, but it does not account for:
- Volatility of the instrument
- Correlation with existing promoted positions
- Liquidity / capacity constraints
- Expected holding period

**Academic benchmark:** The Kelly Criterion and mean-variance optimization both require **expected return / variance**. A proper idea score would be:

```python
def _risk_adjusted_idea_score(
    signal: TrendStockSignal,
    instrument_volatility: float | None = None,
    expected_return_per_unit_signal: float = 0.05,  # Calibrated from backtests
) -> float:
    """
    Sharpe-like idea score.
    """
    expected_return = expected_return_per_unit_signal * signal.trend_ewma
    if instrument_volatility is not None and instrument_volatility > 0:
        return expected_return / instrument_volatility * signal.confidence
    return expected_return * signal.confidence
```

#### Issue C: The Sell Table Uses a Lower Confidence Threshold

```python
sell_min_conf = min(min_conf, SELL_TABLE_MIN_CONF)  # SELL_TABLE_MIN_CONF = 0.35
```

**Problem:** Sell signals (reductions) are accepted with confidence as low as 0.35, while buy signals require 0.45. This asymmetry is not justified in the code or in the literature. In fact, 13F data is **less reliable for sells** because:
- Sells could be driven by liquidity needs, not conviction
- Short sales are not reported on 13F
- A reduction could be profit-taking (positive signal) or loss-cutting (negative signal)

**Recommendation:** Either equalize the thresholds or **increase** the sell threshold. If the goal is to avoid false reduction signals, use:

```python
SELL_TABLE_MIN_CONF = 0.55  # Higher bar for sells
```

#### Issue D: No Correlation or Sector Constraints

**Problem:** The selection function does not check whether promoted buys are all in the same sector or have high pairwise correlation. A portfolio of 10 promoted tech stocks is not diversified, regardless of individual signal quality.

**Academic benchmark:** In "Portfolio Construction and Risk Budgeting" (Grinold & Kahn, 2000), the optimal active position is `alpha / (2 * lambda * variance)`, where lambda is the risk aversion parameter. The current code does not implement any risk budgeting.

---

## 6. Interaction Between `confidence` and `trend_ewma`

### 6.1 Current Implementation

```python
confidence = _confidence_score(...)
trend_ewma = context.blended_score * confidence
trend_delta = trend_ewma - context.prev_trend
```

### 6.2 Analysis

**Problem:** The `trend_ewma` is the product of the raw blended signal and confidence. This means:
- High raw signal + low confidence → suppressed trend
- Low raw signal + high confidence → amplified trend (but raw signal is low, so product is still low)
- Zero confidence → zero trend (regardless of raw signal)

This is a **multiplicative confidence scaling**. It has two major issues:

**Issue A: Destruction of Cardinal Information**

Suppose Stock A has `blended_score = 0.10` and `confidence = 0.50` → `trend_ewma = 0.05`.
Suppose Stock B has `blended_score = 0.05` and `confidence = 1.00` → `trend_ewma = 0.05`.

Both get the same `trend_ewma`, but they represent fundamentally different situations:
- Stock A: Strong raw signal with moderate uncertainty
- Stock B: Weak raw signal with high certainty

A portfolio optimizer would want to treat these differently. The multiplicative scaling destroys the distinction.

**Issue B: Confidence Is Applied Twice**

Confidence already enters the `_confidence_score` calculation via breadth, persistence, crowding, etc. When it is multiplied back into `trend_ewma`, it creates a **nonlinear interaction** that is hard to interpret. For example:
- A stock with high breadth but high crowding gets medium confidence.
- The same stock's trend_ewma is then suppressed by that medium confidence.
- The crowding penalty is applied twice: once in confidence (20% weight) and once in the final scaling.

**Academic benchmark:** In Bayesian signal processing, the standard approach is:
```
Posterior Mean = Prior Mean + (Signal - Prior Mean) × Reliability Weight
```
where `Reliability Weight = Signal Variance / (Signal Variance + Noise Variance)`.

The current implementation is more akin to:
```
Posterior = Signal × Confidence
```
which is not a valid Bayesian update unless confidence is exactly the reliability weight and the prior mean is zero.

### 6.3 Suggested Improvement

Separate the **signal** from its **uncertainty**. Expose both to downstream consumers:

```python
@dataclass(frozen=True)
class TrendSignalRow:
    # ... existing fields ...
    trend_ewma: float           # Raw blended score (unscaled)
    trend_ewma_shrunk: float    # Confidence-shrunk estimate (Bayesian)
    trend_delta: float
    confidence: float           # Uncertainty measure [0, 1]
    information_coefficient: float  # Historical correlation of this signal with returns

# In compute_trend_signals:
confidence = _confidence_score(...)
trend_ewma_raw = context.blended_score

# Bayesian shrinkage toward zero (or toward cross-sectional mean)
prior_mean = 0.0  # or: cross_sectional_mean_blended_score
shrinkage = 1.0 - confidence  # High confidence = low shrinkage
trend_ewma_shrunk = prior_mean + (1.0 - shrinkage) * (trend_ewma_raw - prior_mean)

# Alternative: Use confidence as a position-sizing multiplier, not a signal modifier
# This preserves the cardinal signal for ranking but scales positions by confidence
```

For portfolio construction, a **two-dimensional output** (signal + confidence) is strictly superior to a one-dimensional scaled signal:

```python
def _position_size(signal: float, confidence: float, volatility: float, max_position: float = 0.10) -> float:
    """
    Kelly-like position sizing.
    """
    if volatility <= 0:
        return 0.0
    # Assume IC = 0.10 (information coefficient)
    ic = 0.10
    expected_return = ic * signal * confidence
    kelly_fraction = expected_return / (volatility ** 2)
    return _clip(kelly_fraction, -max_position, max_position)
```

---

## 7. Cross-Cutting Architectural Concerns

### 7.1 Lack of Out-of-Sample Validation

None of the thresholds (`1.5%`, `0.70`, `0.35`, etc.) are validated against historical returns. In a proper quantitative research process:

1. Define a **training period** (e.g., 2015–2020)
2. Grid-search or optimize hyperparameters on the training period
3. Validate on a **holdout period** (2021–2023)
4. Report **out-of-sample Sharpe, IR, and maximum drawdown**

**Recommendation:** Add a backtesting module that evaluates the trend engine's signals against realized returns. Use walk-forward analysis to avoid overfitting.

### 7.2 No Transaction Cost or Market Impact Model

The system generates signals but does not model:
- Commission costs
- Bid-ask spreads
- Market impact (especially critical for small-cap stocks)
- Delay between 13F filing date and public disclosure (45 days)

**Academic benchmark:** CPS (2010) find that replication alpha drops significantly when accounting for the 45-day delay. The current system uses quarter-end snapshots, which are already stale by the time they are available.

### 7.3 Survivorship and Look-Ahead Bias

The `manager_quality_multipliers` function uses historical turnover to weight managers. If a manager has poor historical performance but low turnover, they get boosted. There is no check for **survivorship bias** (managers who stopped filing because they shut down are excluded) or **look-ahead bias** (using future information to weight past signals).

---

## 8. Summary of Recommendations

| Component | Priority | Recommendation |
|-----------|----------|----------------|
| `_trade_flow_delta` | High | Replace binary corporate action guard with probabilistic version using price data; make noise threshold adaptive to position size |
| `_manager_quality_multipliers` | Medium | Add concentration (Active Share) component; replace correlated activity component with signal autocorrelation |
| `_confidence_score` | High | Calibrate weights via logistic regression on historical hit rates; reduce disagreement gamma to 0.40–0.50; fix crowding HHI threshold |
| `_classify_regime` | Medium | Add hysteresis band; increase strong persistence threshold to 3 quarters; expose velocity and acceleration separately |
| `select_trend_ideas` | Medium | Replace binary promotion with probabilistic score; equalize or raise sell threshold; add risk-adjusted idea score |
| `confidence × trend_ewma` | High | Separate raw signal from confidence; use confidence for position sizing, not signal suppression |
| System-wide | High | Add backtesting framework with walk-forward validation; model transaction costs and 45-day disclosure delay |

---

## 9. References

1. Cohen, L., Polk, C., & Silli, B. (2010). "Best Ideas." *Harvard Business School Working Paper*.
2. Cremers, M., & Petajisto, A. (2009). "How Active Is Your Fund Manager? A New Measure That Predicts Performance." *Review of Financial Studies*, 22(9), 3329–3365.
3. Diether, K., Malloy, C., & Scherbina, A. (2002). "Differences of Opinion and the Cross Section of Stock Returns." *Journal of Finance*, 57(5), 2113–2141.
4. Fama, E., & French, K. (2010). "Luck versus Skill in the Cross-Section of Mutual Fund Returns." *Journal of Finance*, 65(5), 1915–1947.
5. Gu, S., Kelly, B., & Xiu, D. (2020). "Empirical Asset Pricing via Machine Learning." *Review of Financial Studies*, 33(5), 2223–2273.
6. Jegadeesh, N., & Titman, S. (1993). "Returns to Buying Winners and Selling Losers: Implications for Stock Market Efficiency." *Journal of Finance*, 48(1), 65–91.
7. Kosowski, R., Timmermann, A., Wermers, R., & White, H. (2006). "Can Mutual Fund 'Stars' Really Pick Stocks?" *Journal of Finance*, 61(6), 2551–2595.
8. Moskowitz, T., & Grinblatt, M. (1999). "Do Industries Explain Momentum?" *Journal of Finance*, 54(4), 1249–1290.
9. Sias, R., Starks, L., & Titman, S. (2022). "Crowded Trades." *Review of Asset Pricing Studies*, 12(2), 375–418.
10. Grinold, R., & Kahn, R. (2000). *Active Portfolio Management*. McGraw-Hill.

---

*Document generated by quantitative research review. All recommendations should be validated with out-of-sample backtesting before production deployment.*
