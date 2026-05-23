# Algorithmic Audit Report: Hedge Fund 13F Trend Engine

**Date:** 2026-05-22
**Scope:** Core trend signal computation, confidence model, regime classification, and idea selection algorithms
**Data:** 15 quarters of trend signals (2022Q2–2025Q4) backtested against Yahoo Finance price data
**Benchmark:** SPY (S&P 500 ETF)

---

## Executive Summary

| Component | Verdict | Key Finding |
|---|---|---|
| **Trade Flow Construction** | ✅ Sound | Shares-aware delta with corporate-action guard is professionally rigorous |
| **Manager Quality Adjustment** | ⚠️ Caution | Turnover-based quality may reward closet indexers; functional form is unusual |
| **EWMA Blend Model** | ✅ Sound | 1Q/3Q half-lives and 60/40 tactical blend are standard for quarterly rebalancing |
| **Confidence Score** | ⚠️ Caution | 4-factor structure is correct, but weights appear uncalibrated; disagreement penalty may be too aggressive |
| **Regime Classification** | ✅ Sound | State machine is logical; reversal detection is conservative |
| **Idea Selection** | 🔴 Critical | `promoted` strategy underperforms random selection by 5.5pp and lags SPY by 5.14% |
| **Evaluation Methodology** | 🔴 Critical | Existing evaluation only measures *coverage*, not *performance* |

**Bottom line:** The signal construction (flow → EWMA → trend) is fundamentally sound and produces alpha (+1.82% excess vs SPY when buying all positive signals). However, the **idea selection layer destroys value**. The promotion criteria filter out the highest-momentum names and retain lower-conviction positions.

---

## 1. Trade Flow & Signal Construction

### What's Implemented
```python
signal_value = manager_weight_effective 
    * position_signal_weight(max_weight) 
    * flow_participation 
    * tanh(z_score / 2)
```

- **Trade flow delta** (`_trade_flow_delta`): Uses shares direction when available, falls back to weight delta. Ignores noise below 1.5% shares change. Guards against corporate actions (splits) via weight-delta check.
- **Robust normalization**: MAD-based sigma with 5-sigma clip and 2-sigma tanh scaling.
- **Position weight**: `sqrt(max_weight / 5%)` — smaller positions get reduced influence.
- **Entry impulse multiplier**: 2.0–2.5× boost for new entries >3% weight.

### Professional Comparison
This closely resembles **Cohen-Polk-Silli (CPS) abnormal weight change** measures used in academic 13F replication studies. The shares-aware delta is *superior* to pure weight delta because it disambiguates price-driven weight changes from active trading. The corporate-action guard is a professional-grade feature rarely seen in open-source implementations.

### Finding: ✅ Sound
No material issues. The signal construction is the strongest part of the pipeline.

---

## 2. Manager Quality Adjustment

### What's Implemented
```python
turnover_component = sqrt(global_median / manager_median)
activity_component = 0.85 + (0.30 * activity_ratio)
quality = clip(turnover_component * activity_component, 0.75, 1.25)
```

- **Turnover component**: Low-turnover managers get boosted (up to 1.25×), high-turnover managers get penalized (down to 0.75×).
- **Activity component**: Managers active in more periods get boosted.

### Professional Comparison
Institutional practice varies:
- **Goldman Sachs VIP list** and **Morgan Stanley Hedge Fund Positions** use *persistence* (how many consecutive quarters a manager holds a name) as the primary quality signal, not turnover.
- **Academic literature** (e.g., "The Investment Value of 13F Disclosures") finds that *low-turnover, concentrated* managers have the highest replication alpha. However, the functional form is typically linear or rank-based, not `sqrt(global/median)`.

### Finding: ⚠️ Caution
1. **Turnover component may reward closet indexers**: A manager who holds 500 stocks with minimal turnover will get a high quality score despite having no edge. The current code does not measure *concentration* (HHI of manager's own portfolio).
2. **Activity component conflates two things**: High activity could mean "trades a lot" (bad, noisy) or "holds positions through multiple quarters" (good, persistent). The current metric doesn't distinguish.

**Recommendation:** Add a concentration quality factor (manager-level HHI) and separate activity into "persistence ratio" (holds for N consecutive quarters) vs. "turnover ratio."

---

## 3. EWMA Memory & Blend Model

### What's Implemented
- **Impulse half-life**: 1 quarter (`decay = 0.5`)
- **Accumulation half-life**: 3 quarters (`decay = 0.794`)
- **Tactical blend**: 60% impulse / 40% accumulation
- **Portfolio blend**: 35% impulse / 65% accumulation

### Professional Comparison
- **Quarterly rebalancing strategies** (e.g., AQR's 13F replication) typically use 4–8 quarter lookbacks with exponential decay.
- **CTA/trend-following** uses much shorter half-lives (days to weeks) on daily data. On quarterly 13F data, 1Q/3Q is reasonable.
- The 60/40 tactical vs. 35/65 portfolio split is a sensible design: tactical emphasizes recent signal changes, portfolio emphasizes sustained conviction.

### Finding: ✅ Sound
The memory model is well-specified for the data frequency. No changes recommended.

---

## 4. Confidence Score

### What's Implemented
```python
base = (0.40 * breadth) + (0.25 * persistence) + (0.20 * crowding) + (0.15 * magnitude)
confidence = base * (1 - 0.70 * disagreement) * freshness
```

- **Breadth**: Count + weight of directional managers
- **Persistence**: Consecutive quarters in same direction (capped at 3)
- **Crowding**: `1 - (HHI - 0.35) / 0.65` — penalizes concentrated bets
- **Magnitude**: `|np_adj| / 90th percentile` — scales by quarter norm
- **Disagreement**: Opposite weight / total weight, scaled by gamma = 0.70
- **Freshness**: Decay based on live price drift vs. quarter-end price

### Professional Comparison
This is a **well-structured multi-factor confidence model** comparable to institutional implementations. However:

1. **Weights are likely uncalibrated**: The 40/25/20/15 split appears arbitrary. In professional quant systems, these weights are optimized via cross-validation or information ratio maximization.
2. **Disagreement gamma = 0.70 is aggressive**: If 50% of directional weight is opposed, confidence drops by 35%. This may be appropriate for long/short signals but is very conservative for directional equity selection.
3. **Magnitude uses NP rather than blended score**: The 15% magnitude weight uses `np_adj` (unblended net positioning) rather than `blended_score` (EWMA output). This means the confidence doesn't fully reflect the signal's memory-adjusted strength.

### Finding: ⚠️ Caution
The structure is correct but the weights and disagreement penalty appear heuristic rather than calibrated. The model would benefit from an optimization pass using historical forward returns as the target.

---

## 5. Regime Classification

### What's Implemented
State machine based on `trend_ewma` sign, `trend_delta`, and persistence:
- `STRONG_BUY/SELL`: persistence ≥ 2 + gate
- `EMERGING_BUY/SELL`: trend_delta aligned with sign + gate
- `WEAKENING_BUY/SELL`: trend_delta opposed to sign + gate
- `REVERSAL_BUY/SELL`: sign flip from previous quarter + gate

### Professional Comparison
- Similar to **turtle trading** phase logic (entry → continuation → exit).
- The reversal detection is conservative (requires sign flip), which is appropriate given 13F quarterly staleness.
- **Buy/sell gates** (min managers or min weight) prevent weak signals from getting classified.

### Finding: ✅ Sound
No material issues. The state machine is logical and conservative.

---

## 6. Idea Selection — CRITICAL FINDING

### What's Implemented
```python
def _idea_score(signal):
    return abs(signal.accumulation_score) * signal.confidence

def _decision_for_signal(signal):
    direction = _direction_for_signal(signal)
    if directional_managers >= 2:
        return PROMOTED  # multi-manager support
    if directional_persistence >= 2:
        return PROMOTED  # persistence
    return MONITOR
```

Promotion requires:
1. Directional regime (BUY/SELL, not REVERSAL or NONE)
2. Confidence ≥ 0.45 (BUY) or ≥ 0.35 (SELL)
3. **Multi-manager support (≥2) OR persistence (≥2)**

### Empirical Evidence

| Strategy | Mean Return | Hit Rate | Excess vs SPY | N |
|---|---|---|---|---|
| **promoted** | **+1.60%** | **52%** | **-5.14%** | 61 |
| top_trend_ewma | +8.51% | 67% | +1.69% | 89 |
| random | +7.11% | 68% | +0.69% | 99 |
| all_buy | +7.96% | 61% | +1.82% | 948 |

### Analysis

**The promotion criteria destroy alpha.** This happens for two reasons:

1. **`_idea_score` uses `accumulation_score` instead of `trend_ewma`**: 
   - `accumulation_score` is the slow-moving (3Q half-life) component.
   - `trend_ewma` is `blended_score * confidence`, which already incorporates both speed and conviction.
   - By using `accumulation_score`, the idea score **overweights stale, slow-moving positions** and underweights fresh momentum.

2. **Promotion gates are too restrictive and misaligned**:
   - Requiring ≥2 managers eliminates strong single-manager convictions (e.g., Pershing Square's concentrated bets).
   - Requiring persistence ≥2 eliminates new entries, which is where 13F replication alpha is historically highest (CPS new entry effect).
   - The `top_trend_ewma` strategy (which ignores promotion gates and simply picks highest `trend_ewma`) outperforms by **6.9 percentage points**.

### Professional Comparison
- **Goldman Sachs VIP list** uses *any* top-10 holding from a hedge fund, regardless of multi-manager consensus.
- **Morgan Stanley HF Positioning** uses *aggregate weight* and *directional intensity*, not manager count.
- **Academic 13F replication** (CPS) finds the highest alpha comes from **new entries** and **large weight increases** — exactly what your promotion gates filter out.

### Finding: 🔴 Critical
**The idea selection layer should be rethought.** Current promotion logic is worse than random. Two specific fixes:

1. **Change `_idea_score` to use `trend_ewma`**:
   ```python
   def _idea_score(signal):
       return abs(signal.trend_ewma)  # or signal.trend_ewma * signal.confidence
   ```

2. **Relax or restructure promotion gates**:
   - Option A: Promote top-N by `trend_ewma` directly (simplest, empirically best).
   - Option B: Promote if `confidence ≥ 0.60` OR `trend_ewma ≥ 90th percentile` (signal-strength based).
   - Option C: Keep multi-manager/persistence as *bonus points* in the sort key, not hard gates.

---

## 7. Evaluation Methodology — CRITICAL FINDING

### What's Implemented
The existing `evaluate_trend_ideas.py` computes:
- How many promoted ideas have price data (coverage)
- How many are mapped to tickers
- Regime and state distributions

**It does NOT compute:**
- Forward returns
- Hit rates
- Sharpe ratios
- Benchmark-relative alphas
- Confidence calibration (does high confidence → high return?)

### Finding: 🔴 Critical
Without forward-return analysis, there is no way to validate whether the algorithm works. The coverage metrics are necessary but insufficient. The backtest script created for this audit (`scripts/backtest_trend_signals.py`) should become a permanent part of the evaluation pipeline.

---

## Recommendations Summary

### Immediate (High Impact, Low Effort)
1. **Replace `_idea_score` with `abs(trend_ewma)`** — expected improvement: +5-7pp mean return.
2. **Replace promotion hard gates with top-N by `trend_ewma`** — expected improvement: +5-7pp mean return.
3. **Integrate `backtest_trend_signals.py` into CI** — run quarterly to validate that promoted ideas beat random.

### Short-term (Medium Effort)
4. **Calibrate confidence weights** using forward-return optimization on historical data.
5. **Add manager concentration quality factor** to prevent closet indexer boost.
6. **Test regime-conditioned performance** — does `STRONG_BUY` outperform `EMERGING_BUY`? (Current data suggests yes, but sample is small.)

### Long-term (High Effort)
7. **Add sector/neutralization layer** — the current signal is sector-agnostic. Professional 13F replication typically neutralizes sector exposure.
8. **Add risk-adjusted position sizing** — current idea selection is equal-weight. Size by conviction or inverse volatility.

---

## Appendix: Quarter-by-Quarter Promoted Performance

| Quarter | Availability | N | Mean Return | Excess vs SPY |
|---|---|---|---|---|
| 2022Q2 | 2022-08-15 | 1 | +2.33% | +5.22% |
| 2022Q3 | 2022-11-14 | 0 | — | — |
| 2022Q4 | 2023-05-17 | 0 | — | — |
| 2023Q1 | 2023-05-15 | 2 | -3.15% | -10.50% |
| 2023Q2 | 2023-08-14 | 3 | +23.95% | +11.30% |
| 2023Q3 | 2024-05-15 | 2 | +17.82% | +4.09% |
| 2023Q4 | 2024-08-23 | 5 | +3.91% | -5.83% |
| 2024Q1 | 2024-08-20 | 4 | +9.53% | -0.63% |
| 2024Q2 | 2024-08-14 | 7 | +5.83% | -6.12% |
| 2024Q3 | 2024-11-14 | 5 | +1.57% | +2.03% |
| 2024Q4 | 2025-08-12 | 3 | -1.57% | -10.17% |
| 2025Q1 | 2025-08-14 | 13 | -4.12% | -12.05% |
| 2025Q2 | 2025-08-15 | 10 | +1.55% | -6.61% |
| 2025Q3 | 2025-12-29 | 6 | -11.61% | -3.74% |
| 2025Q4 | 2026-05-15 | 10 | — | — |

**Note:** 2025Q4 has no forward data yet (availability date is 2026-05-15). 2022Q3/Q4 had no promoted ideas due to insufficient history or signal strength.
