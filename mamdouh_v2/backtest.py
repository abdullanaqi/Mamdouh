"""Stage 14: backtest daily selection strategies.

All strategies rank by ranking_score (Stage 12, pre-entry-only model output)
among included_in_daily_study rows. Every position is exited at
exit_price_1555 (open of the 15:55 bar) - see config.py for the fill
convention and report.py for the realism caveats.

Filter variants (all built from PRE-ENTRY-SAFE signals - either the
pre-entry risk_score model, or raw pre-entry features/news; never from
outcome/trap labels, which would be look-ahead bias in a live strategy):
  none            ranking_score only
  risk_filter     drop the riskiest quartile of risk_score (Stage 13 model) that day
  news_filter     drop names with negative news before entry
  liquidity_filter drop bottom-quintile pre-entry dollar-volume names (config.LIQUIDITY_BOTTOM_QUINTILE)
  momentum_filter drop top-decile pre-entry momentum names (config.MOMENTUM_TOP_DECILE) - the
                  "extreme momentum often reverses" hypothesis, tested rather than assumed

A separate, clearly-labeled `backtest_oracle_trap_removal_benchmark` uses
the ACTUAL (outcome) trap labels to show the theoretical upside of perfect
trap foresight. This is a hindsight/oracle benchmark for research context
only - it is NOT a tradable strategy and must never be confused with the
risk_filter variant above.
"""
from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

import config
from db import skip_if_exists

_log = config.setup_logging(__name__)

BASELINE_TOP_N = 5                                                                                   

FILTER_SQL = {
    "none": "TRUE",
    "risk_filter": "(risk_score IS NULL OR risk_percentile < 0.75)",
    "news_filter": "(NOT has_negative_news_before_entry)",
    "liquidity_filter": f"(pre_entry_dollar_volume_percentile IS NOT NULL AND pre_entry_dollar_volume_percentile >= {config.LIQUIDITY_BOTTOM_QUINTILE})",
    "momentum_filter": f"(pre_entry_momentum_percentile IS NULL OR pre_entry_momentum_percentile < {config.MOMENTUM_TOP_DECILE})",
}


def build_candidates_scored(con: duckdb.DuckDBPyConnection, force: bool = False) -> None:
    if skip_if_exists(con, "candidates_scored", force, "Stage 14a"):
        return

    con.execute(
        """
        CREATE OR REPLACE TABLE candidates_scored AS
        SELECT
            c.*,
            rm.ranking_score,
            rk.risk_score,
            PERCENT_RANK() OVER (PARTITION BY c.trade_date ORDER BY rk.risk_score) AS risk_percentile
        FROM halal_daily_candidates c
        LEFT JOIN ranking_model_scores rm ON rm.ticker = c.ticker AND rm.trade_date = c.trade_date
        LEFT JOIN risk_model_scores rk ON rk.ticker = c.ticker AND rk.trade_date = c.trade_date
        WHERE c.included_in_daily_study
        """
    )
    n = con.execute("SELECT COUNT(*) FROM candidates_scored WHERE ranking_score IS NOT NULL").fetchone()[0]
    _log.info(f"candidates_scored: {n:,} rows have a ranking_score (walk-forward warm-up excluded the rest)")


def _fetch_filter_frame(con: duckdb.DuckDBPyConnection, filter_sql: str) -> pd.DataFrame:
    df = con.execute(
        f"""
        SELECT
            ticker, trade_date, return_0935_to_1555, ranking_score, entry_price_0935,
            pre_entry_dollar_volume_percentile, pre_entry_momentum_percentile,
            has_news_before_entry, latest_news_sentiment_before_entry, latest_news_source_before_entry,
            ROW_NUMBER() OVER (PARTITION BY trade_date ORDER BY ranking_score DESC) AS rn
        FROM candidates_scored
        WHERE ranking_score IS NOT NULL AND {filter_sql}
        """
    ).fetchdf()
    df["cost_frac"] = df["entry_price_0935"].map(config.cost_bps_for_price) / 10000.0
    df["net_return"] = df["return_0935_to_1555"] - df["cost_frac"]
    return df


def _metrics_for_column(trades: pd.DataFrame, return_col: str) -> dict:
    daily = trades.groupby("trade_date")[return_col].mean().sort_index()
    equity = (1 + daily).cumprod()
    running_max = equity.cummax()
    drawdown = equity / running_max - 1
    return {
        "avg_trade_return": float(trades[return_col].mean()),
        "median_trade_return": float(trades[return_col].median()),
        "avg_daily_portfolio_return": float(daily.mean()),
        "win_rate": float((trades[return_col] > 0).mean()),
        "daily_win_rate": float((daily > 0).mean()),
        "best_day": float(daily.max()),
        "worst_day": float(daily.min()),
        "max_drawdown": float(drawdown.min()),
    }


_EMPTY_METRICS = {
    "avg_trade_return": None, "median_trade_return": None, "avg_daily_portfolio_return": None,
    "win_rate": None, "daily_win_rate": None, "best_day": None, "worst_day": None, "max_drawdown": None,
}


def _implied_avg_pairwise_correlation(trades: pd.DataFrame, top_n: int) -> float | None:
    """Backs out the average same-day correlation among the top_n selected
    names from the ratio of observed daily-portfolio variance to the
    variance you'd expect if the top_n picks were independent draws each
    day: Var(mean of N) = Var(trade)/N * (1 + (N-1)*rho). Answers "are the
    stocks picked on the same day really diversified, or do they tend to
    move together" - something the average-return backtest numbers alone
    cannot show. Undefined for top_n == 1 (no cross-sectional averaging)."""
    if top_n <= 1:
        return None
    var_trade = trades["return_0935_to_1555"].var(ddof=1)
    daily = trades.groupby("trade_date")["return_0935_to_1555"].mean()
    var_daily = daily.var(ddof=1)
    if not var_trade or pd.isna(var_trade) or pd.isna(var_daily):
        return None
    ratio = var_daily / (var_trade / top_n)
    rho = (ratio - 1) / (top_n - 1)
    return float(rho)


def _compute_metrics(trades: pd.DataFrame, n_days_total: int, top_n: int) -> dict:
    """Returns gross (no transaction cost) AND net (cost-adjusted, see
    config.COST_BPS_BY_PRICE_TIER) metrics side by side, since no real
    bid/ask spread data is available - net_* uses a disclosed heuristic
    cost model, not a measured one."""
    if trades.empty:
        out = {f"gross_{k}": v for k, v in _EMPTY_METRICS.items()}
        out.update({f"net_{k}": v for k, v in _EMPTY_METRICS.items()})
        out["n_trades"] = 0
        out["avg_candidates_per_day"] = 0.0
        out["implied_avg_pairwise_correlation"] = None
        return out

    gross = _metrics_for_column(trades, "return_0935_to_1555")
    net = _metrics_for_column(trades, "net_return")
    n_days = trades["trade_date"].nunique()
    out = {f"gross_{k}": v for k, v in gross.items()}
    out.update({f"net_{k}": v for k, v in net.items()})
    out["n_trades"] = int(len(trades))
    out["avg_candidates_per_day"] = float(len(trades) / n_days) if n_days else 0.0
    out["implied_avg_pairwise_correlation"] = _implied_avg_pairwise_correlation(trades, top_n)
    return out


def build_backtest_results(con: duckdb.DuckDBPyConnection, force: bool = False) -> None:
    if skip_if_exists(con, "backtest_results", force, "Stage 14b"):
        return

    _log.info("Running backtest across filter variants x top-N variants")
    rows = []
    frames_by_filter = {}
    for filter_name, filter_sql in FILTER_SQL.items():
        frame = _fetch_filter_frame(con, filter_sql)
        frames_by_filter[filter_name] = frame
        n_days = frame["trade_date"].nunique()
        for top_n in config.TOP_N_VARIANTS:
            trades = frame[frame["rn"] <= top_n]
            m = _compute_metrics(trades, n_days, top_n)
            m.update({"filter_name": filter_name, "top_n": top_n})
            rows.append(m)
            _log.info(
                f"[{filter_name} top{top_n}] trades={m['n_trades']:,} "
                f"gross_avg_trade_return={m['gross_avg_trade_return']} net_avg_trade_return={m['net_avg_trade_return']}"
            )

    results_df = pd.DataFrame(rows)[[
        "filter_name", "top_n", "n_trades",
        "gross_avg_trade_return", "gross_median_trade_return", "gross_avg_daily_portfolio_return",
        "gross_win_rate", "gross_daily_win_rate", "gross_best_day", "gross_worst_day", "gross_max_drawdown",
        "net_avg_trade_return", "net_median_trade_return", "net_avg_daily_portfolio_return",
        "net_win_rate", "net_daily_win_rate", "net_best_day", "net_worst_day", "net_max_drawdown",
        "avg_candidates_per_day", "implied_avg_pairwise_correlation",
    ]]
    con.execute("CREATE OR REPLACE TABLE backtest_results AS SELECT * FROM results_df")
    _log.info(f"backtest_results: {len(results_df)} filter x top-N rows (gross and cost-adjusted net columns)")

    _build_breakdowns(con, frames_by_filter["none"])
    _build_oracle_benchmark(con)
    build_stability_analysis(con)


def _build_breakdowns(con: duckdb.DuckDBPyConnection, baseline_frame: pd.DataFrame) -> None:
    """Cuts requested by the spec, computed on the baseline strategy
    (ranking_score only, top BASELINE_TOP_N) so they are comparable to each
    other."""
    trades = baseline_frame[baseline_frame["rn"] <= BASELINE_TOP_N].copy()
    trades["month"] = pd.to_datetime(trades["trade_date"]).dt.to_period("M").astype(str)

    def _grouped(frame: pd.DataFrame, col) -> pd.DataFrame:
        g = frame.groupby(col).agg(
            n=("return_0935_to_1555", "count"),
            avg_return=("return_0935_to_1555", "mean"),
            win_rate=("return_0935_to_1555", lambda s: (s > 0).mean()),
            net_avg_return=("net_return", "mean"),
            net_win_rate=("net_return", lambda s: (s > 0).mean()),
        ).reset_index()
        return g

    monthly = _grouped(trades, "month")
    con.execute("CREATE OR REPLACE TABLE backtest_monthly AS SELECT * FROM monthly")

    news_df = trades.copy()
    news_df["has_news_before_entry"] = news_df["has_news_before_entry"].astype(bool)
    by_news = _grouped(news_df, "has_news_before_entry")
    con.execute("CREATE OR REPLACE TABLE backtest_by_news AS SELECT * FROM by_news")

    liq = trades.dropna(subset=["pre_entry_dollar_volume_percentile"]).copy()
    liq["liquidity_decile"] = np.floor(liq["pre_entry_dollar_volume_percentile"] * 10).astype(int)
    by_liquidity = _grouped(liq, "liquidity_decile")
    con.execute("CREATE OR REPLACE TABLE backtest_by_liquidity_decile AS SELECT * FROM by_liquidity")

    mom = trades.dropna(subset=["pre_entry_momentum_percentile"]).copy()
    mom["momentum_decile"] = np.floor(mom["pre_entry_momentum_percentile"] * 10).astype(int)
    by_momentum = _grouped(mom, "momentum_decile")
    con.execute("CREATE OR REPLACE TABLE backtest_by_momentum_decile AS SELECT * FROM by_momentum")

    sentiment_df = trades.copy()
    sentiment_df["latest_news_sentiment_before_entry"] = sentiment_df["latest_news_sentiment_before_entry"].fillna("no_news")
    by_sentiment = _grouped(sentiment_df, "latest_news_sentiment_before_entry").rename(columns={"latest_news_sentiment_before_entry": "sentiment"})
    con.execute("CREATE OR REPLACE TABLE backtest_by_sentiment AS SELECT * FROM by_sentiment")

    src = trades.dropna(subset=["latest_news_source_before_entry"])
    by_source = _grouped(src, "latest_news_source_before_entry").rename(columns={"latest_news_source_before_entry": "source"})
    con.execute("CREATE OR REPLACE TABLE backtest_by_source AS SELECT * FROM by_source")

    _log.info(f"backtest breakdowns built on baseline strategy (ranking_score only, top {BASELINE_TOP_N})")


def _build_oracle_benchmark(con: duckdb.DuckDBPyConnection) -> None:
    """HINDSIGHT / ORACLE benchmark - uses the actual outcome trap labels to
    show the theoretical upside of perfect trap foresight. NOT a tradable
    strategy: possible_buyer_trap_pattern / possible_pump_and_fade_pattern
    are only known after 15:55. For research context only."""
    con.execute(
        f"""
        CREATE OR REPLACE TABLE backtest_oracle_trap_removal_benchmark AS
        WITH ranked AS (
            SELECT
                ticker, trade_date, return_0935_to_1555,
                possible_buyer_trap_pattern, possible_pump_and_fade_pattern,
                ROW_NUMBER() OVER (PARTITION BY trade_date ORDER BY ranking_score DESC) AS rn
            FROM candidates_scored
            WHERE ranking_score IS NOT NULL
        ),
        selected AS (SELECT * FROM ranked WHERE rn <= {BASELINE_TOP_N})
        SELECT
            'with_trap_setups' AS variant, COUNT(*) AS n_trades, AVG(return_0935_to_1555) AS avg_trade_return
        FROM selected
        UNION ALL
        SELECT
            'trap_setups_removed_with_hindsight' AS variant, COUNT(*) AS n_trades, AVG(return_0935_to_1555) AS avg_trade_return
        FROM selected
        WHERE NOT (possible_buyer_trap_pattern OR possible_pump_and_fade_pattern)
        """
    )
    _log.info("backtest_oracle_trap_removal_benchmark built (hindsight-only, not a tradable strategy)")


def build_stability_analysis(con: duckdb.DuckDBPyConnection) -> None:
    """Answers: is the edge stable over time, or drifting/degrading?

    backtest_monthly_diagnostic separates two possible explanations for a
    weak month: (a) the whole halal-eligible universe was weak that month
    (a market/regime effect, not a model failure), vs (b) the model
    specifically picked bad names while the universe overall was fine.

    backtest_stability_half_period and backtest_trend_summary split the
    walk-forward test window in half and fit a simple linear trend to
    detect whether performance/AUC is improving or degrading over time -
    this cannot prove causality with only 17 months, but a clear monotonic
    slide is a real warning sign that a k-fold or single-split evaluation
    would have hidden.
    """
    diag = con.execute(
        f"""
        WITH universe AS (
            SELECT strftime(trade_date, '%Y-%m') AS month, return_0935_to_1555
            FROM candidates_scored WHERE ranking_score IS NOT NULL
        ),
        market AS (
            SELECT month, AVG(return_0935_to_1555) AS market_avg_return, COUNT(*) AS market_n
            FROM universe GROUP BY month
        ),
        ranked AS (
            SELECT
                strftime(trade_date, '%Y-%m') AS month, return_0935_to_1555,
                (risk_score IS NULL OR risk_percentile < 0.75) AS passes_risk_filter,
                ROW_NUMBER() OVER (PARTITION BY trade_date ORDER BY ranking_score DESC) AS rn
            FROM candidates_scored WHERE ranking_score IS NOT NULL
        ),
        baseline_agg AS (
            SELECT month, AVG(return_0935_to_1555) AS baseline_avg_return
            FROM ranked WHERE rn <= {BASELINE_TOP_N} GROUP BY month
        ),
        risk_agg AS (
            SELECT month, AVG(return_0935_to_1555) AS risk_filter_avg_return
            FROM (
                SELECT
                    strftime(trade_date, '%Y-%m') AS month, return_0935_to_1555,
                    ROW_NUMBER() OVER (PARTITION BY trade_date ORDER BY ranking_score DESC) AS rn
                FROM candidates_scored
                WHERE ranking_score IS NOT NULL AND (risk_score IS NULL OR risk_percentile < 0.75)
            ) WHERE rn <= {BASELINE_TOP_N}
            GROUP BY month
        )
        SELECT m.month, m.market_avg_return, m.market_n, b.baseline_avg_return, r.risk_filter_avg_return
        FROM market m
        LEFT JOIN baseline_agg b ON b.month = m.month
        LEFT JOIN risk_agg r ON r.month = m.month
        ORDER BY m.month
        """
    ).fetchdf()

    ranking_eval = con.execute("SELECT test_month, auc AS ranking_auc FROM ranking_model_eval").fetchdf()
    risk_eval = con.execute("SELECT test_month, auc AS risk_auc FROM risk_model_eval").fetchdf()
    diag = diag.merge(ranking_eval, left_on="month", right_on="test_month", how="left").drop(columns=["test_month"])
    diag = diag.merge(risk_eval, left_on="month", right_on="test_month", how="left").drop(columns=["test_month"])
    diag = diag.sort_values("month").reset_index(drop=True)
    con.execute("CREATE OR REPLACE TABLE backtest_monthly_diagnostic AS SELECT * FROM diag")

                                                                            
                                                             
    n = len(diag)
    half = n // 2
    diag["period"] = ["first_half"] * half + ["second_half"] * (n - half)
    half_summary = diag.groupby("period").agg(
        n_months=("month", "count"),
        avg_market_return=("market_avg_return", "mean"),
        avg_baseline_return=("baseline_avg_return", "mean"),
        avg_risk_filter_return=("risk_filter_avg_return", "mean"),
        avg_ranking_auc=("ranking_auc", "mean"),
        avg_risk_auc=("risk_auc", "mean"),
    ).reset_index()
    con.execute("CREATE OR REPLACE TABLE backtest_stability_half_period AS SELECT * FROM half_summary")

                                                                             
    x = np.arange(n)

    def _slope(y: pd.Series) -> float | None:
        mask = y.notna()
        if mask.sum() < 3:
            return None
        return float(np.polyfit(x[mask.values], y[mask.values], 1)[0])

    def _direction(slope: float | None) -> str:
        if slope is None:
            return "insufficient data"
        if slope > 1e-5:
            return "improving over time"
        if slope < -1e-5:
            return "degrading over time"
        return "flat / no clear trend"

    trend_rows = []
    for label, col in [
        ("baseline_avg_return (top5, no filter)", "baseline_avg_return"),
        ("risk_filter_avg_return (top5, risk filter)", "risk_filter_avg_return"),
        ("market_avg_return (whole eligible universe)", "market_avg_return"),
        ("ranking_model_auc", "ranking_auc"),
        ("risk_model_auc", "risk_auc"),
    ]:
        slope = _slope(diag[col])
        trend_rows.append({"metric": label, "slope_per_month": slope, "direction": _direction(slope)})
    trend_df = pd.DataFrame(trend_rows)
    con.execute("CREATE OR REPLACE TABLE backtest_trend_summary AS SELECT * FROM trend_df")

    _log.info(f"backtest_monthly_diagnostic / _stability_half_period / _trend_summary built ({n} months)")
    for _, r in trend_df.iterrows():
        _log.info(f"  trend: {r['metric']}: {r['direction']} (slope/month={r['slope_per_month']})")


def run(con: duckdb.DuckDBPyConnection, force: bool = False) -> None:
    build_candidates_scored(con, force)
    build_backtest_results(con, force)
