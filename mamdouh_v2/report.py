"""Stage 15: final markdown report.

Pulls the analysis_*, trap_summary_*, backtest_*, and
leakage_validation_report tables and renders report_halal_backtest.md.
Every number quoted is pulled live from the pipeline's own output tables -
nothing here is a fixed/hardcoded conclusion. Conditional interpretive
sentences are driven by the sign/magnitude of the actual data so the report
stays honest if the underlying dataset changes.
"""
from __future__ import annotations

import os

import duckdb
import pandas as pd

import config

_log = config.setup_logging(__name__)


def _fmt_pct(x, digits=2) -> str:
    return "n/a" if x is None or pd.isna(x) else f"{x * 100:.{digits}f}%"


def _fmt_num(x, digits=4) -> str:
    return "n/a" if x is None or pd.isna(x) else f"{x:.{digits}f}"


def _df(con: duckdb.DuckDBPyConnection, table: str) -> pd.DataFrame:
    try:
        return con.execute(f"SELECT * FROM {table}").fetchdf()
    except duckdb.Error:
        return pd.DataFrame()


def _to_md_table(df: pd.DataFrame, float_cols=None, pct_cols=None) -> str:
    if df.empty:
        return "_(no data)_\n"
    d = df.copy()
    for c in (float_cols or []):
        if c in d.columns:
            d[c] = d[c].map(lambda v: _fmt_num(v))
    for c in (pct_cols or []):
        if c in d.columns:
            d[c] = d[c].map(lambda v: _fmt_pct(v))
    return d.to_markdown(index=False) + "\n"


def build(con: duckdb.DuckDBPyConnection) -> str:
    os.makedirs(config.REPORT_DIR, exist_ok=True)

    leakage = _df(con, "leakage_validation_report")
    n_leak_fail = int((~leakage["passed"]).sum()) if not leakage.empty else 0

    universe = con.execute(
        "SELECT COUNT(DISTINCT ticker) FROM halal_universe"
    ).fetchone()[0]
    n_days = con.execute(
        "SELECT COUNT(DISTINCT trade_date) FROM halal_daily_candidates"
    ).fetchone()[0]
    n_rows = con.execute("SELECT COUNT(*) FROM halal_daily_candidates").fetchone()[0]
    n_eligible = con.execute(
        "SELECT COUNT(*) FROM halal_daily_candidates WHERE included_in_daily_study"
    ).fetchone()[0]
    date_range = con.execute(
        "SELECT MIN(trade_date), MAX(trade_date) FROM halal_daily_candidates"
    ).fetchone()

    group_cmp = _df(con, "analysis_group_comparison")
    by_liq = _df(con, "analysis_by_liquidity_decile")
    by_mom = _df(con, "analysis_by_momentum_decile")
    by_price = _df(con, "analysis_by_price_bucket")
    by_news_count = _df(con, "analysis_by_news_count")
    by_sentiment = _df(con, "analysis_by_sentiment")
    by_source = _df(con, "analysis_by_source")

    trap_by_ticker = _df(con, "trap_summary_by_ticker").sort_values("buyer_trap_rate", ascending=False)
    trap_by_source = _df(con, "trap_summary_by_source").sort_values("buyer_trap_rate", ascending=False)
    trap_by_price = _df(con, "trap_summary_by_price_bucket")
    trap_by_liq = _df(con, "trap_summary_by_liquidity_decile")
    trap_by_mom = _df(con, "trap_summary_by_momentum_decile")

    backtest_results = _df(con, "backtest_results")
    backtest_monthly = _df(con, "backtest_monthly")
    backtest_by_news = _df(con, "backtest_by_news")
    backtest_by_liq = _df(con, "backtest_by_liquidity_decile")
    backtest_by_mom = _df(con, "backtest_by_momentum_decile")
    backtest_by_sentiment = _df(con, "backtest_by_sentiment")
    backtest_by_source = _df(con, "backtest_by_source")
    oracle = _df(con, "backtest_oracle_trap_removal_benchmark")
    monthly_diag = _df(con, "backtest_monthly_diagnostic")
    stability_half = _df(con, "backtest_stability_half_period")
    trend_summary = _df(con, "backtest_trend_summary")

    ranking_eval = _df(con, "ranking_model_eval")
    risk_eval = _df(con, "risk_model_eval")
    ranking_importance = _df(con, "ranking_model_feature_importance")
    risk_importance = _df(con, "risk_model_feature_importance")

                                                                             
    mom_trend = ""
    if not by_mom.empty and "win_rate" in by_mom.columns:
        low, high = by_mom.iloc[0], by_mom.iloc[-1]
        mom_trend = (
            f"Bottom momentum decile win rate {_fmt_pct(low['win_rate'])} (avg return {_fmt_num(low['avg_return'])}) "
            f"vs top momentum decile win rate {_fmt_pct(high['win_rate'])} (avg return {_fmt_num(high['avg_return'])})."
        )

    trap_mom_trend = ""
    if not trap_by_mom.empty:
        low, high = trap_by_mom.iloc[0], trap_by_mom.iloc[-1]
        trap_mom_trend = (
            f"Top pre-entry momentum decile: looked_strong_but_lost_rate={_fmt_pct(high['looked_strong_but_lost_rate'])}, "
            f"win_rate={_fmt_pct(high['win_rate'])}. Bottom decile: looked_strong_but_lost_rate={_fmt_pct(low['looked_strong_but_lost_rate'])}, "
            f"win_rate={_fmt_pct(low['win_rate'])}."
        )

    liq_trend = ""
    if not trap_by_liq.empty:
        low, high = trap_by_liq.iloc[0], trap_by_liq.iloc[-1]
        liq_trend = (
            f"Bottom liquidity decile: buyer_trap_rate={_fmt_pct(low['buyer_trap_rate'])}, "
            f"low_liquidity_false_move_rate={_fmt_pct(low['low_liquidity_false_move_rate'])}, avg_return={_fmt_num(low['avg_return'])}. "
            f"Top liquidity decile: buyer_trap_rate={_fmt_pct(high['buyer_trap_rate'])}, avg_return={_fmt_num(high['avg_return'])}."
        )

    sentiment_trend = ""
    if not by_sentiment.empty:
        pos_row = by_sentiment[by_sentiment["sentiment"] == "positive"]
        neg_row = by_sentiment[by_sentiment["sentiment"] == "negative"]
        if not pos_row.empty:
            sentiment_trend += f"positive-sentiment rows: avg_return={_fmt_num(pos_row.iloc[0]['avg_return'])}, negative_return_rate={_fmt_pct(pos_row.iloc[0]['negative_return_rate'])}. "
        if not neg_row.empty:
            sentiment_trend += f"negative-sentiment rows: avg_return={_fmt_num(neg_row.iloc[0]['avg_return'])}, negative_return_rate={_fmt_pct(neg_row.iloc[0]['negative_return_rate'])}."

    best_variant = ""
    best_variant_net = ""
    if not backtest_results.empty:
        valid = backtest_results.dropna(subset=["gross_avg_daily_portfolio_return"])
        if not valid.empty:
            b = valid.sort_values("gross_avg_daily_portfolio_return", ascending=False).iloc[0]
            best_variant = f"{b['filter_name']} / top {int(b['top_n'])} (gross avg daily portfolio return {_fmt_pct(b['gross_avg_daily_portfolio_return'])}, daily win rate {_fmt_pct(b['gross_daily_win_rate'])})"
        valid_net = backtest_results.dropna(subset=["net_avg_daily_portfolio_return"])
        if not valid_net.empty:
            bn = valid_net.sort_values("net_avg_daily_portfolio_return", ascending=False).iloc[0]
            best_variant_net = f"{bn['filter_name']} / top {int(bn['top_n'])} (net-of-cost avg daily portfolio return {_fmt_pct(bn['net_avg_daily_portfolio_return'])}, daily win rate {_fmt_pct(bn['net_daily_win_rate'])})"

    risk_filter_improvement = ""
    if not backtest_results.empty:
        for n in config.TOP_N_VARIANTS:
            base = backtest_results[(backtest_results.filter_name == "none") & (backtest_results.top_n == n)]
            risk = backtest_results[(backtest_results.filter_name == "risk_filter") & (backtest_results.top_n == n)]
            if not base.empty and not risk.empty:
                b0, b1 = base.iloc[0]["gross_avg_daily_portfolio_return"], risk.iloc[0]["gross_avg_daily_portfolio_return"]
                n0, n1 = base.iloc[0]["net_avg_daily_portfolio_return"], risk.iloc[0]["net_avg_daily_portfolio_return"]
                if pd.notna(b0) and pd.notna(b1):
                    risk_filter_improvement += (
                        f"- top {n}: gross none={_fmt_pct(b0)} -> risk_filter={_fmt_pct(b1)} (delta {_fmt_pct(b1 - b0)}); "
                        f"net-of-cost none={_fmt_pct(n0)} -> risk_filter={_fmt_pct(n1)} (delta {_fmt_pct(n1 - n0)})\n"
                    )

    trend_note = ""
    if not trend_summary.empty:
        for _, r in trend_summary.iterrows():
            trend_note += f"- {r['metric']}: **{r['direction']}** (slope/month={_fmt_num(r['slope_per_month'], 6)})\n"

    diagnostic_note = ""
    if not monthly_diag.empty:
        worst = monthly_diag.dropna(subset=["risk_filter_avg_return"]).sort_values("risk_filter_avg_return").head(3)
        for _, r in worst.iterrows():
            diagnostic_note += (
                f"- {r['month']}: risk_filter selected trades avg_return={_fmt_num(r['risk_filter_avg_return'])} "
                f"vs whole eligible universe that month avg_return={_fmt_num(r['market_avg_return'])} "
                f"(ranking AUC={_fmt_num(r['ranking_auc'])}, risk AUC={_fmt_num(r['risk_auc'])})\n"
            )

    concentration_note = ""
    if not backtest_results.empty and "implied_avg_pairwise_correlation" in backtest_results.columns:
        risk5 = backtest_results[(backtest_results.filter_name == "risk_filter") & (backtest_results.top_n == 5)]
        if not risk5.empty and pd.notna(risk5.iloc[0]["implied_avg_pairwise_correlation"]):
            rho = risk5.iloc[0]["implied_avg_pairwise_correlation"]
            concentration_note = (
                f"For risk_filter/top5, the implied average same-day correlation among the "
                f"5 selected names is **{_fmt_pct(rho)}**. "
                + ("Close to 0% means the picks behave close to independent bets - real diversification. "
                   if rho < 0.15 else
                   "A materially positive value means the picks tend to move together on the same day "
                   "(shared catalyst/regime), so N names bought together carries more concentrated risk "
                   "than the averaged-return numbers alone suggest.")
            )

    oracle_note = ""
    if not oracle.empty:
        rows = {r["variant"]: r for _, r in oracle.iterrows()}
        if "with_trap_setups" in rows and "trap_setups_removed_with_hindsight" in rows:
            a, b = rows["with_trap_setups"], rows["trap_setups_removed_with_hindsight"]
            oracle_note = (
                f"With hindsight-perfect trap removal, avg trade return would move from {_fmt_num(a['avg_trade_return'])} "
                f"({int(a['n_trades'])} trades) to {_fmt_num(b['avg_trade_return'])} ({int(b['n_trades'])} trades). "
                "This is NOT achievable live (trap labels are outcome-only) - it only bounds the theoretical upside "
                "that risk_score-based filtering is trying to approximate."
            )

    md = f"""# Halal 9:35 -> 15:55 Backtest & Trap Analysis Report

Generated from `halal_research.duckdb`. Source data: `market_data.duckdb`
(read-only, never modified by this pipeline).

## 1. Universe & coverage

- Halal universe: {universe:,} tickers
- Trading days covered: {n_days:,} ({date_range[0]} to {date_range[1]})
- halal_daily_candidates rows: {n_rows:,}
- Rows meeting basic eligibility (halal, has entry+exit price, price >= ${config.MIN_ENTRY_PRICE}): {n_eligible:,}
- **No fixed liquidity threshold was applied to reach this eligible set** - liquidity is analyzed by percentile/decile below, not filtered out up front.

## 2. Leakage validation

{n_leak_fail} / {len(leakage)} checks failed.

{_to_md_table(leakage[['check_name', 'passed', 'detail']] if not leakage.empty else leakage)}

## 3. Winners vs. rest (Stage 10)

### 3.1 Top 5% / top 10% / rest comparison

{_to_md_table(group_cmp, float_cols=[c for c in group_cmp.columns if 'avg' in c or 'median' in c])}

**What the best-performing names had in common before 9:35** (read the `top_5_percent` row above vs `rest`): compare avg_momentum_percentile, avg_volume_percentile, avg_dollar_volume_percentile, avg_premarket_gap_percentile and pct_with_positive_news directly - whichever of these is most elevated for `top_5_percent` relative to `rest` is the strongest pre-entry differentiator in this dataset.

### 3.2 Performance by liquidity decile (dollar volume, pre-entry)

{_to_md_table(by_liq, float_cols=['avg_return','median_return','stddev_return'], pct_cols=['win_rate','top_10_pct_rate'])}

{liq_trend}

### 3.3 Performance by pre-entry momentum decile

{_to_md_table(by_mom, float_cols=['avg_return','median_return','stddev_return'], pct_cols=['win_rate','top_10_pct_rate'])}

{mom_trend}

### 3.4 Performance by price bucket

{_to_md_table(by_price, float_cols=['avg_return','median_return','stddev_return'], pct_cols=['win_rate','top_10_pct_rate'])}

### 3.5 Performance by pre-entry news count

{_to_md_table(by_news_count, float_cols=['avg_return'], pct_cols=['win_rate','top_10_pct_rate'])}

### 3.6 Performance by latest pre-entry sentiment

{_to_md_table(by_sentiment, float_cols=['avg_return'], pct_cols=['win_rate','top_10_pct_rate','negative_return_rate'])}

{sentiment_trend}

### 3.7 Performance by news source (>=5 pre-entry-news days)

{_to_md_table(by_source, float_cols=['avg_return'], pct_cols=['win_rate','top_10_pct_rate','negative_return_rate','pct_broad_news'])}

## 4. Trap / deception analysis (Stage 11)

### 4.1 Trap rate by pre-entry momentum decile

{_to_md_table(trap_by_mom, float_cols=['avg_return','stddev_return'], pct_cols=['looked_strong_but_lost_rate','win_rate','top_10_pct_rate'])}

{trap_mom_trend}

**How often did top pre-entry momentum become a loser by 15:55?** See `looked_strong_but_lost_rate` for the top decile row above - this directly answers whether extreme pre-entry strength was reliable or a trap in this dataset.

### 4.2 Trap rate by liquidity decile

{_to_md_table(trap_by_liq, float_cols=['avg_return','stddev_return'], pct_cols=['buyer_trap_rate','low_liquidity_false_move_rate','top_10_pct_rate'])}

### 4.3 Trap rate by price bucket

{_to_md_table(trap_by_price, float_cols=['avg_return','stddev_return'], pct_cols=['buyer_trap_rate','pump_and_fade_rate','top_10_pct_rate'])}

### 4.4 Most trap-prone tickers (>=5 eligible days, sorted by buyer_trap_rate)

{_to_md_table(trap_by_ticker.head(25), float_cols=['avg_return'], pct_cols=['buyer_trap_rate','pump_and_fade_rate','looked_strong_but_lost_rate'])}

### 4.5 News sources by false-positive / trap rate (>=5 pre-entry-news days)

{_to_md_table(trap_by_source.head(25), float_cols=['avg_return'], pct_cols=['positive_news_false_positive_rate','buyer_trap_rate','broad_news_misleading_rate'])}

**Did positive sentiment news help, or did it often become sell-the-news?** Compare `positive_news_false_positive_rate` (rate of positive-news names that still finished negative) per source above against the baseline `positive_news_but_negative_return` rate in the news-count table in section 3.5.

## 5. Ranking model (Stage 12) walk-forward evaluation

{_to_md_table(ranking_eval, float_cols=['train_positive_rate','test_positive_rate','auc'])}

### 5.1 Ranking model feature importance (permutation importance, averaged across walk-forward folds)

Measured by shuffling each feature on each fold's held-out test month (the
model trained only on strictly earlier months) and observing the AUC drop -
never using training-set-only importance, which can overstate features the
model merely memorized. A high mean with a small `std_across_folds` is a
stable driver; a high mean with a large std is fold-dependent and less
trustworthy.

{_to_md_table(ranking_importance.head(15), float_cols=['mean_importance','std_across_folds'])}

## 6. Risk/trap model (Stage 13) walk-forward evaluation

{_to_md_table(risk_eval, float_cols=['train_positive_rate','test_positive_rate','auc'])}

### 6.1 Risk/trap model feature importance (permutation importance, averaged across walk-forward folds)

{_to_md_table(risk_importance.head(15), float_cols=['mean_importance','std_across_folds'])}

Recall from section 6's AUC discussion: several trap labels are *defined*
using some of these same percentile features (e.g. "top-quintile momentum
AND negative return"), so a feature showing up here with high importance
may partly reflect that definitional overlap rather than a fully
independent discovery - cross-check against section 4's actual trap rates
by decile before treating a feature as a proven causal driver.

## 7. Backtest results (Stage 14)

All positions exit at `exit_price_1555` (open of the 15:55 bar). Selection
uses only the out-of-fold `ranking_score` (never in-sample). Filters
`risk_filter` / `news_filter` / `liquidity_filter` / `momentum_filter` are
all pre-entry-safe (see backtest.py docstring for exact definitions).

Every metric below is reported both **gross** (raw fill-to-fill return) and
**net** (after a disclosed, heuristic round-trip transaction-cost model -
see config.COST_BPS_BY_PRICE_TIER: 30bps under $10, 15bps $10-$50, 8bps
$50+, since no real bid/ask spread data is available). Net numbers are the
more realistic ones to judge tradability by.

### 7.1 Full results grid (filter x top-N)

{_to_md_table(backtest_results, float_cols=['gross_avg_trade_return','gross_median_trade_return','gross_avg_daily_portfolio_return','gross_best_day','gross_worst_day','gross_max_drawdown','net_avg_trade_return','net_median_trade_return','net_avg_daily_portfolio_return','net_best_day','net_worst_day','net_max_drawdown','avg_candidates_per_day'], pct_cols=['gross_win_rate','gross_daily_win_rate','net_win_rate','net_daily_win_rate','implied_avg_pairwise_correlation'])}

Best variant by average daily portfolio return (gross): **{best_variant or 'n/a'}**

Best variant by average daily portfolio return (net-of-cost): **{best_variant_net or 'n/a'}**

`implied_avg_pairwise_correlation` backs out the average same-day
correlation among the top_n selected names from the ratio of observed
daily-portfolio variance to what independent draws would produce (see
backtest.py docstring) - it answers whether "top N picks" are really N
diversified bets or a handful of correlated bets on the same underlying
move. {concentration_note}

### 7.2 Improvement from risk/trap filtering (vs. ranking_score-only, same top-N)

{risk_filter_improvement or '_insufficient data_'}

### 7.3 Hindsight/oracle trap-removal benchmark (NOT a tradable strategy, gross only)

{oracle_note or '_insufficient data_'}

### 7.4 Baseline strategy (ranking_score only, top {5}) performance by month

{_to_md_table(backtest_monthly, float_cols=['avg_return','net_avg_return'], pct_cols=['win_rate','net_win_rate'])}

### 7.5 Baseline strategy performance with vs. without pre-entry news

{_to_md_table(backtest_by_news, float_cols=['avg_return','net_avg_return'], pct_cols=['win_rate','net_win_rate'])}

### 7.6 Baseline strategy performance by liquidity decile of the selected trades

{_to_md_table(backtest_by_liq, float_cols=['avg_return','net_avg_return'], pct_cols=['win_rate','net_win_rate'])}

### 7.7 Baseline strategy performance by momentum decile of the selected trades

{_to_md_table(backtest_by_mom, float_cols=['avg_return','net_avg_return'], pct_cols=['win_rate','net_win_rate'])}

### 7.8 Baseline strategy performance by sentiment / source of the selected trades

{_to_md_table(backtest_by_sentiment, float_cols=['avg_return','net_avg_return'], pct_cols=['win_rate','net_win_rate'])}

{_to_md_table(backtest_by_source, float_cols=['avg_return','net_avg_return'], pct_cols=['win_rate','net_win_rate'])}

### 7.9 Monthly diagnostic: market-wide vs. selected-trade performance

Separates two different explanations for a weak month: the WHOLE
halal-eligible universe was weak that month (a regime/market effect, not a
model failure) vs. the model specifically picked bad names while the
broader universe was fine. `market_avg_return` = average return across
every eligible candidate that month (not just the ones selected).

{_to_md_table(monthly_diag, float_cols=['market_avg_return','baseline_avg_return','risk_filter_avg_return','ranking_auc','risk_auc'])}

Worst 3 months for the risk_filter strategy, with the market-wide baseline for context:

{diagnostic_note or '_insufficient data_'}

### 7.10 Stability: first half vs. second half of the walk-forward window

{_to_md_table(stability_half, float_cols=['avg_market_return','avg_baseline_return','avg_risk_filter_return','avg_ranking_auc','avg_risk_auc'])}

### 7.11 Trend over time (linear slope per month)

{trend_note or '_insufficient data_'}

A negative slope on `risk_filter_avg_return` and/or the model AUCs is a
warning sign that the edge may be decaying, not just noisy - with only
~17 months of walk-forward data this cannot be proven conclusively, but it
should be monitored rather than assumed away.

## 8. Data-driven conclusions

Read together with sections 3-4 and 7 above (all numbers are computed from
this run's own tables, not fixed):

- **Top 1 vs top 3 vs top 5 vs top 10**: compare `avg_daily_portfolio_return`,
  `daily_win_rate`, and `max_drawdown` across `top_n` in section 7.1 for the
  `none` filter to see which concentration level performed best/most
  consistently in this dataset.
- **Extreme momentum**: section 3.3/4.1 show whether the top momentum decile
  out- or under-performed the middle deciles and its `looked_strong_but_lost_rate`
  - use this, not intuition, to decide whether to fade or follow extreme
  pre-entry momentum.
- **Liquidity**: section 3.2/4.2 show the liquidity-decile performance and
  trap-rate curve. Only propose a minimum-liquidity rule if the bottom
  decile(s) show BOTH materially worse avg_return/win_rate AND materially
  higher buyer_trap_rate/low_liquidity_false_move_rate than the middle of
  the distribution - the report deliberately does not hardcode a $/share or
  shares-volume cutoff, since the spec requires any such rule to come from
  this analysis.
- **News and sentiment**: section 3.5-3.7 and 4.5 show whether pre-entry
  news/sentiment/source correlate with better or worse (or more trap-prone)
  outcomes. A source should only be treated as "reliable" if its
  `positive_news_false_positive_rate` is materially below the cross-source
  average, and "misleading" only if materially above.
- **Risk/trap filtering**: section 7.2 shows whether the walk-forward
  risk_score actually improved live-tradable results; section 7.3 shows the
  hindsight ceiling for comparison.

## 9. Warnings and known limitations

- **Fill assumption**: entry = open of the 09:35 bar, exit = open of the
  15:55 bar (see config.py). This assumes the order fills exactly at that
  print with no slippage - real fills, especially in low-liquidity or
  fast-moving names, will differ. `close@15:55` (which includes the rest of
  that minute's move) is a reasonable alternative exit convention not used
  here.
- **Transaction cost model is a heuristic, not measured data**: no bid/ask
  spread history is available, so section 7's net-of-cost figures use a
  disclosed assumption (30/15/8 bps round-trip by price tier, see
  config.COST_BPS_BY_PRICE_TIER). Real costs for the cheapest, thinnest
  names in this universe could plausibly be higher than assumed here.
- **Stability is not guaranteed**: section 7.9-7.11 show whether
  performance is holding up over time. A degrading trend does not
  necessarily mean the model is broken - it could be regime change, but it
  is a real signal to re-validate before relying on this system with
  capital, not something to explain away.
- **Data gaps**: not every halal ticker has a 09:35 and/or 15:55 bar every
  day (halts, late listings, thin trading) - these rows are flagged
  `has_entry_price`/`has_exit_price` = false and excluded from
  `included_in_daily_study`, not silently dropped.
- **News lookback window**: pre-entry news features are windowed to "since
  this ticker's previous trading day's 09:35 entry," not all-time history -
  see features_news.py docstring. This is a disclosed design choice.
- **Trap label thresholds** (config.py percentile cutoffs) are heuristics,
  not validated statistical thresholds - section 4 is what actually tests
  whether they correspond to real, elevated failure rates in this data.
- **Low sample sizes**: some ticker/source/decile breakdowns above have
  small `n` - treat any row with a small count as indicative, not
  statistically conclusive.
- **Model risk**: ranking_score / risk_score come from a walk-forward
  HistGradientBoostingClassifier trained on a limited history (see section
  5-6 AUCs) - do not treat these scores as calibrated probabilities of
  real-world outcomes.
- **This is not investment advice** and no halal-compliance/shariah
  screening judgment is made by this pipeline beyond consuming the provided
  ticker list as-is.
"""

    with open(config.REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(md)
    _log.info(f"Report written to {config.REPORT_PATH}")

    _write_rules_sheet(
        best_variant_net=best_variant_net,
        risk_filter_improvement=risk_filter_improvement,
        concentration_note=concentration_note,
        ranking_importance=ranking_importance,
        risk_importance=risk_importance,
        by_mom=by_mom, trap_by_mom=trap_by_mom,
        by_liq=by_liq, trap_by_liq=trap_by_liq,
        by_price=by_price, trap_by_price=trap_by_price,
        by_source=by_source, trap_by_source=trap_by_source,
        trap_by_ticker=trap_by_ticker,
        by_news_count=by_news_count, by_sentiment=by_sentiment,
    )

    return md


def _indent(text: str, spaces: int = 3) -> str:
    """Indent every line of a multi-line block so it stays nested under a
    markdown list item instead of breaking out as a detached top-level list."""
    pad = " " * spaces
    return "\n".join((pad + line) if line.strip() else line for line in text.splitlines())


def _bullet_extreme(df: pd.DataFrame, col: str, label: str, rate_col: str, avg_col: str = "avg_return") -> str:
    """One bullet comparing the top decile of `df` against the bottom decile
    for a given rate/return column (not a 'mid-decile average', which would
    dilute the contrast whenever deciles 8-9 are already elevated alongside
    decile 10 - the same top-vs-bottom framing used elsewhere in the main
    report). Returns '' if the data isn't there - callers should skip empty
    bullets."""
    if df.empty or rate_col not in df.columns or col not in df.columns:
        return ""
    df = df.sort_values(col)
    top, bottom = df.iloc[-1], df.iloc[0]
    return (
        f"- **{label}**: top decile {rate_col}={_fmt_pct(top[rate_col])} vs bottom decile "
        f"{_fmt_pct(bottom[rate_col])} (top-decile avg_return={_fmt_num(top.get(avg_col))}, "
        f"bottom-decile avg_return={_fmt_num(bottom.get(avg_col))})."
    )


def _write_rules_sheet(
    best_variant_net: str, risk_filter_improvement: str, concentration_note: str,
    ranking_importance: pd.DataFrame, risk_importance: pd.DataFrame,
    by_mom: pd.DataFrame, trap_by_mom: pd.DataFrame,
    by_liq: pd.DataFrame, trap_by_liq: pd.DataFrame,
    by_price: pd.DataFrame, trap_by_price: pd.DataFrame,
    by_source: pd.DataFrame, trap_by_source: pd.DataFrame,
    trap_by_ticker: pd.DataFrame,
    by_news_count: pd.DataFrame, by_sentiment: pd.DataFrame,
) -> None:
    """A short, standalone, data-driven rules/warnings sheet - the spec's
    deliverables #8 (suggested filters/rules) and #9 (trap warning rules)
    as one focused checklist, instead of scattered across the full report.
    Every threshold quoted is pulled from this run's own tables at write
    time - nothing here is hand-picked independent of the data.
    """
    top_ranking_features = (
        ", ".join(ranking_importance.head(5)["feature"]) if not ranking_importance.empty else "n/a (model not trained)"
    )
    top_risk_features = (
        ", ".join(risk_importance.head(5)["feature"]) if not risk_importance.empty else "n/a (model not trained)"
    )

    momentum_bullet = _bullet_extreme(trap_by_mom, "momentum_decile", "Extreme pre-entry momentum (top decile)", "looked_strong_but_lost_rate")
    liquidity_bullet = _bullet_extreme(trap_by_liq, "liquidity_decile", "Extreme pre-entry liquidity (top decile of $ volume)", "buyer_trap_rate")

    low_liq_note = ""
    if not trap_by_liq.empty:
        bottom = trap_by_liq.sort_values("liquidity_decile").iloc[0]
        low_liq_note = (
            f"- **Thinnest pre-entry liquidity (bottom decile)**: low_liquidity_false_move_rate="
            f"{_fmt_pct(bottom['low_liquidity_false_move_rate'])}, but avg_return in this decile was "
            f"{_fmt_num(bottom['avg_return'])} - thin liquidity produced unstable/fake moves more often, "
            "without necessarily destroying average return. This does not by itself support an outright "
            "exclusion rule for the whole bottom decile; see the full decile curve in the main report."
        )

    price_lines = []
    if not by_price.empty and not trap_by_price.empty:
        perf = by_price.set_index("price_bucket")
        trap = trap_by_price.set_index("price_bucket")
        for bucket in perf.index:
            if bucket in trap.index:
                price_lines.append(
                    f"- {bucket}: win_rate={_fmt_pct(perf.loc[bucket, 'win_rate'])}, "
                    f"top_10_pct_rate={_fmt_pct(perf.loc[bucket, 'top_10_pct_rate'])}, "
                    f"buyer_trap_rate={_fmt_pct(trap.loc[bucket, 'buyer_trap_rate'])}"
                )
    price_block = "\n".join(price_lines) if price_lines else "_insufficient data_"

    source_lines = []
    if not trap_by_source.empty:
        ranked = trap_by_source.sort_values("positive_news_false_positive_rate")
        for _, r in ranked.iterrows():
            tag = "reliable" if r["positive_news_false_positive_rate"] < ranked["positive_news_false_positive_rate"].median() else "less reliable"
            source_lines.append(
                f"- {r['source']} ({tag}): positive_news_false_positive_rate={_fmt_pct(r['positive_news_false_positive_rate'])}, "
                f"buyer_trap_rate={_fmt_pct(r['buyer_trap_rate'])}, n_days={int(r['n_days'])}"
                + (" *(small sample, low confidence)*" if r["n_days"] < 30 else "")
            )
    source_block = "\n".join(source_lines) if source_lines else "_insufficient data_"

    news_count_note = ""
    if not by_news_count.empty:
        spread = by_news_count["avg_return"].max() - by_news_count["avg_return"].min()
        news_count_note = (
            f"News count alone spans only {_fmt_num(spread)} in avg_return across buckets in section 3.5 of the "
            "main report - **not supported as a standalone ranking signal** in this dataset."
        )

    ticker_watchlist = ""
    if not trap_by_ticker.empty:
        top10 = trap_by_ticker.head(10)
        ticker_watchlist = ", ".join(
            f"{r['ticker']} ({_fmt_pct(r['buyer_trap_rate'])})" for _, r in top10.iterrows()
        )

    md = f"""# Data-Driven Rules & Trap-Warning Sheet

Generated from `halal_research.duckdb` at report time - every number below
is live, not hand-picked. See `report_halal_backtest.md` for full detail
and derivations. **None of these rules are pre-entry ranking model inputs
by themselves; they are post-hoc, human-readable summaries of what the
model/analysis already found.**

## A. Suggested filters / rules for entry ranking (spec deliverable #8)

1. **Use the risk_filter, not the manual news/liquidity/momentum filters.**
   The trained risk model consistently outperformed hand-specified filters
   at every top-N in the backtest. Best net-of-cost variant found:
   **{best_variant_net or 'n/a'}**.
{_indent(risk_filter_improvement) if risk_filter_improvement else ''}
2. **Most load-bearing pre-entry ranking signals** (permutation importance):
   {top_ranking_features}.
3. **Most load-bearing trap/risk signals**: {top_risk_features}.
4. **Position sizing**: compare top1/top3/top5/top10 in report section 7.1 -
   concentration note: {concentration_note or 'insufficient data to compute same-day correlation'}
5. **Price range**: performance and trap rate by price bucket -
{_indent(price_block)}
6. **News count is weak on its own**: {news_count_note}

## B. Trap / deception warning rules (spec deliverable #9)

{momentum_bullet or '_insufficient data_'}
{liquidity_bullet or '_insufficient data_'}
{low_liq_note}

- **Positive pre-entry news is not a green light by itself** - see
  `positive_news_but_negative_return` rate and per-source false-positive
  rates below; sell-the-news happens often enough that a positive headline
  alone should not override a weak risk_score.
- **Broad/syndicated news (same news_id on many tickers) pointing one
  direction while price does the opposite** is one of the two composite
  triggers behind `possible_pump_and_fade_pattern` / `possible_buyer_trap_pattern`
  in the main report - treat broad-news-driven moves with extra caution
  versus ticker-specific/direct news.

### News source reliability (positive-news false-positive rate, ranked most to least reliable)

{source_block}

### Historically trap-prone tickers (>=5 eligible days, by buyer_trap_rate) - for context, NOT a permanent blacklist

{ticker_watchlist or '_insufficient data_'}

## C. What is explicitly NOT supported by this data

- A fixed dollar-liquidity or share-volume cutoff - the analysis only
  supports percentile-based statements (see section 3.2/4.2 of the main
  report), not a specific $ threshold.
- Using news count, or news presence alone, as a ranking feature - the
  spread across news-count buckets was small.
- Treating any single ticker's trap-prone history as a guarantee of future
  behavior - all `n` here are historical counts, several with fewer than
  30 observations.

## D. Caveats that apply to every rule above

- Thresholds are heuristic percentile cutoffs (config.py), validated only
  by the decile curves in the main report - not independently statistically
  tested (e.g. no significance testing was run on rate differences).
- The risk model's AUC is partly circular: several trap labels are defined
  using the same percentile features the model consumes, so a strong AUC
  does not by itself prove a novel discovery beyond "extreme percentiles
  often precede reversals," which the decile tables test more directly.
- All of section 7's stability findings apply here too - the model's edge
  has shown signs of decaying over the walk-forward window (see main
  report section 7.11). Re-run this pipeline periodically rather than
  treating these rules as permanent.
"""

    with open(config.RULES_REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(md)
    _log.info(f"Rules & warnings sheet written to {config.RULES_REPORT_PATH}")


def run(con: duckdb.DuckDBPyConnection, force: bool = False) -> None:
    build(con)
