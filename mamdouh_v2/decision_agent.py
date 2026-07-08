"""LLM trading-decision agent: a Claude-powered second opinion over the
pipeline's top-ranked candidates.

What it does
------------
For a given trade date, takes the top-N candidates by walk-forward
ranking_score, packages ONLY pre-entry information (price/momentum/premarket
features, pre-entry news, model scores, and the ticker's own PRIOR history),
and asks Claude for a trade/skip decision per candidate with conviction,
red flags, and a short rationale. Decisions are stored in the
`agent_decisions` table in halal_research.duckdb.

Leakage discipline
------------------
- Everything sent to the model is available at or before 09:35 on the
  decision date. Ticker history stats are computed point-in-time (strictly
  earlier trade dates only), so evaluation over historical days does not
  feed the agent future aggregates.
- ⚠️ KNOWLEDGE-CUTOFF CONTAMINATION (evaluation mode only): the LLM's
  training data covers the backtest period, so it may implicitly "remember"
  what actually happened to a specific ticker on a specific 2025 date. The
  system prompt forbids using such knowledge, but this cannot be verified.
  Historical evaluation results are therefore INDICATIVE ONLY - the clean,
  uncontaminated use of this agent is live/forward operation on new dates.

Usage
-----
    python decision_agent.py                     # decide latest available day, top 5
    python decision_agent.py --date 2026-06-30 --top 5
    python decision_agent.py --evaluate --days 15  # run over last 15 days + score it

Requires ANTHROPIC_API_KEY in the environment or .env, and:
    pip install anthropic python-dotenv
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from typing import List, Literal

import anthropic
import duckdb
import pandas as pd
from dotenv import load_dotenv
from pydantic import BaseModel

import config
from db import get_connection

_log = config.setup_logging(__name__)

MODEL = "claude-opus-4-8"
TOP_N_DEFAULT = 5


class CandidateDecision(BaseModel):
    ticker: str
    decision: Literal["trade", "skip"]
    conviction: int                                                                  
    red_flags: List[str]
    rationale: str


class DailyAssessment(BaseModel):
    decisions: List[CandidateDecision]
    market_note: str


SYSTEM_PROMPT = """You are the risk officer for a quantitative halal-stock day-trading desk.

The desk's strategy: buy at 09:35 New York time, use dynamic volatility-scaled
TP/SL levels intraday, and sell any remaining position at 15:55 the same day.
A walk-forward gradient-boosting model has already ranked today's candidates;
you are the SECOND opinion, not the primary ranker. Your job is to catch
qualitative problems the numeric model cannot see: news that is stale,
recycled, or not really about the ticker; pump-and-fade fingerprints;
setups that historically trapped buyers.

Known findings from this desk's own research (use them):
- Extreme pre-entry momentum (top decile) historically REVERSED: 57% of
  those names finished red. Extreme strength is a warning, not a buy signal.
- Very high pre-entry dollar-volume percentile also carried elevated trap
  rates. Crowded, fast moves fade often.
- Positive headlines preceded a negative day ~36% of the time
  (sell-the-news). Broad/syndicated news (same story on many tickers) is
  less reliable than ticker-specific news.
- Thin liquidity produced unstable "false moves" but not systematically
  negative returns - flag it, don't auto-reject it.

Rules:
1. Base every judgment ONLY on the data provided in the message. You must
   NOT use any memory of what actually happened to these stocks on or after
   the decision date, even if you recognize the ticker and date. Reasoning
   that references actual future outcomes is a hard failure.
2. Skip is not free: skipping every candidate destroys the strategy. Reserve
   "skip" for candidates with concrete red flags, not generic caution.
3. Be specific in red_flags (e.g. "premarket gap 97th percentile + broad
   syndicated headline = classic pump-fade profile"), never vague ("risky").
4. conviction: 1-5 for how strongly you hold the decision you made.
5. rationale: 1-3 sentences, plain language.
"""


def _latest_scored_date(con: duckdb.DuckDBPyConnection) -> date:
    row = con.execute(
        "SELECT MAX(trade_date) FROM candidates_scored WHERE ranking_score IS NOT NULL"
    ).fetchone()
    if row is None or row[0] is None:
        raise SystemExit("candidates_scored has no scored rows - run the pipeline through stage 15 first")
    return row[0]


def fetch_candidates(con: duckdb.DuckDBPyConnection, trade_date: date, top_n: int) -> pd.DataFrame:
    """Top-N by ranking_score for the date, with point-in-time ticker history
    (strictly earlier dates only - no future aggregates leak into the prompt)."""
    return con.execute(
        f"""
        WITH day_candidates AS (
            SELECT *, ROW_NUMBER() OVER (ORDER BY ranking_score DESC) AS rn
            FROM candidates_scored
            WHERE trade_date = ? AND ranking_score IS NOT NULL
        ),
        top_c AS (SELECT * FROM day_candidates WHERE rn <= ?),
        history AS (
            SELECT
                c.ticker,
                COUNT(*) AS prior_days,
                AVG(h.return_0935_to_1555) AS prior_avg_return,
                AVG((h.return_0935_to_1555 > 0)::INT) AS prior_win_rate,
                AVG((h.possible_buyer_trap_pattern OR h.possible_pump_and_fade_pattern)::INT) AS prior_trap_rate
            FROM top_c c
            JOIN candidates_scored h ON h.ticker = c.ticker AND h.trade_date < c.trade_date
            GROUP BY c.ticker
        )
        SELECT
            t.rn, t.ticker, t.trade_date, t.entry_price_0935,
            {config.dynamic_vol_proxy_sql()} AS dynamic_vol_proxy,
            t.entry_price_0935 * (1 + {config.DYNAMIC_TP_VOL_MULT} * ({config.dynamic_vol_proxy_sql()})) AS dynamic_tp_price,
            t.entry_price_0935 * (1 - {config.DYNAMIC_SL_VOL_MULT} * ({config.dynamic_vol_proxy_sql()})) AS dynamic_sl_price,
            t.ranking_score, t.risk_score, t.risk_percentile,
            t.return_0930_to_entry,
            t.pre_entry_momentum_percentile, t.pre_entry_volume_percentile,
            t.pre_entry_dollar_volume_percentile, t.pre_entry_transactions_percentile,
            t.gap_pct_premarket_vs_prior_close, t.premarket_gap_percentile,
            t.premarket_volume_percentile, t.premarket_range_position, t.has_premarket_data,
            t.news_count_before_entry, t.latest_news_title_before_entry,
            t.latest_news_source_before_entry, t.latest_news_sentiment_before_entry,
            t.minutes_between_latest_news_and_entry,
            t.duplicate_news_id_count_across_tickers, t.is_broad_news,
            h.prior_days, h.prior_avg_return, h.prior_win_rate, h.prior_trap_rate,
            t.return_0935_to_1555  -- kept ONLY for local evaluation; never sent to the model
        FROM top_c t
        LEFT JOIN history h ON h.ticker = t.ticker
        ORDER BY t.rn
        """,
        [trade_date, top_n],
    ).fetchdf()


                                                                              
_PROMPT_EXCLUDED = {"return_0935_to_1555", "trade_date", "rn"}


def build_user_message(trade_date: date, candidates: pd.DataFrame) -> str:
    def _round(v):
        if isinstance(v, float):
            return round(v, 4)
        return v

    payload = []
    for _, r in candidates.iterrows():
        item = {
            k: _round(v)
            for k, v in r.items()
            if k not in _PROMPT_EXCLUDED and not (isinstance(v, float) and pd.isna(v))
        }
        payload.append(item)
    return (
        f"Decision date: {trade_date} (data as of 09:35 New York time).\n"
        f"Candidates (ranked by the model's ranking_score, best first):\n"
        f"{json.dumps(payload, default=str, indent=2)}\n\n"
        "Percentile fields are within-day cross-sectional percentiles (0-1). "
        "risk_percentile is the trap-model's within-day percentile (higher = riskier). "
        "prior_* fields are this ticker's own history strictly before today. "
        "Return a decision for every candidate."
    )


def assess_day(
    client: anthropic.Anthropic,
    con: duckdb.DuckDBPyConnection,
    trade_date: date,
    top_n: int,
) -> DailyAssessment | None:
    candidates = fetch_candidates(con, trade_date, top_n)
    if candidates.empty:
        _log.warning(f"No scored candidates for {trade_date} - skipping")
        return None

    response = client.messages.parse(
        model=MODEL,
        max_tokens=8000,
        thinking={"type": "adaptive"},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": build_user_message(trade_date, candidates)}],
        output_format=DailyAssessment,
    )
    assessment = response.parsed_output

    scores = candidates.set_index("ticker")[["ranking_score", "risk_score"]]
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_decisions (
            trade_date DATE, ticker VARCHAR, decision VARCHAR, conviction INT,
            red_flags VARCHAR, rationale VARCHAR, market_note VARCHAR,
            ranking_score DOUBLE, risk_score DOUBLE, model VARCHAR, created_at TIMESTAMP
        )
        """
    )
    con.execute("DELETE FROM agent_decisions WHERE trade_date = ?", [trade_date])
    for d in assessment.decisions:
        rs = scores.loc[d.ticker] if d.ticker in scores.index else None
        con.execute(
            "INSERT INTO agent_decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                trade_date, d.ticker, d.decision, d.conviction,
                json.dumps(d.red_flags), d.rationale, assessment.market_note,
                float(rs["ranking_score"]) if rs is not None else None,
                float(rs["risk_score"]) if rs is not None and pd.notna(rs["risk_score"]) else None,
                MODEL, datetime.now(),
            ],
        )
    return assessment


def print_assessment(trade_date: date, assessment: DailyAssessment) -> None:
    print(f"\n=== Agent decisions for {trade_date} (model: {MODEL}) ===")
    print(f"Market note: {assessment.market_note}\n")
    for d in assessment.decisions:
        flag_str = ("\n    red flags: " + "; ".join(d.red_flags)) if d.red_flags else ""
        print(f"  [{d.decision.upper():5s}] {d.ticker}  (conviction {d.conviction}/5){flag_str}")
        print(f"    {d.rationale}\n")


def evaluate(client: anthropic.Anthropic, con: duckdb.DuckDBPyConnection, days: int, top_n: int) -> None:
    """Run the agent over the last `days` scored trading days, then compare
    the realized returns of its trade vs skip decisions against the
    trade-everything baseline. See the module docstring's contamination
    caveat - treat these numbers as indicative, not proof."""
    dates = [
        r[0]
        for r in con.execute(
            """
            SELECT DISTINCT trade_date FROM candidates_scored
            WHERE ranking_score IS NOT NULL
            ORDER BY trade_date DESC LIMIT ?
            """,
            [days],
        ).fetchall()
    ]
    _log.info(f"Evaluating agent over {len(dates)} days x top {top_n} (one API call per day)")
    for i, d in enumerate(sorted(dates)):
        already = con.execute(
            "SELECT COUNT(*) FROM agent_decisions WHERE trade_date = ?", [d]
        ).fetchone()[0] if _table_exists(con, "agent_decisions") else 0
        if already:
            _log.info(f"[{i+1}/{len(dates)}] {d}: already assessed, skipping")
            continue
        _log.info(f"[{i+1}/{len(dates)}] assessing {d} ...")
        assess_day(client, con, d, top_n)

    summary = con.execute(
        """
        SELECT
            d.decision,
            COUNT(*) AS n,
            AVG(c.return_0935_to_1555) AS avg_return,
            MEDIAN(c.return_0935_to_1555) AS median_return,
            AVG((c.return_0935_to_1555 > 0)::INT) AS win_rate
        FROM agent_decisions d
        JOIN candidates_scored c ON c.ticker = d.ticker AND c.trade_date = d.trade_date
        GROUP BY d.decision ORDER BY d.decision
        """
    ).fetchdf()
    baseline = con.execute(
        """
        SELECT COUNT(*) AS n, AVG(c.return_0935_to_1555) AS avg_return,
               AVG((c.return_0935_to_1555 > 0)::INT) AS win_rate
        FROM agent_decisions d
        JOIN candidates_scored c ON c.ticker = d.ticker AND c.trade_date = d.trade_date
        """
    ).fetchdf()

    con.execute("CREATE OR REPLACE TABLE agent_decisions_eval AS SELECT * FROM summary")

    print("\n=== Agent evaluation (realized 09:35->15:55 returns) ===")
    print("\nBaseline - trade every top candidate:")
    print(baseline.to_string(index=False))
    print("\nSplit by agent decision:")
    print(summary.to_string(index=False))
    print(
        "\n⚠️  Contamination caveat: the LLM's training data covers this period, so it may\n"
        "implicitly know these outcomes despite being instructed not to use them.\n"
        "Treat this as indicative. The clean test is live/forward operation."
    )


def _table_exists(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    return bool(
        con.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_catalog='out' AND table_name=?",
            [name],
        ).fetchone()[0]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", type=str, help="Trade date YYYY-MM-DD (default: latest scored day)")
    parser.add_argument("--top", type=int, default=TOP_N_DEFAULT, help="Number of top-ranked candidates to assess")
    parser.add_argument("--evaluate", action="store_true", help="Run over the last --days days and score decisions vs realized returns")
    parser.add_argument("--days", type=int, default=15, help="Days to evaluate in --evaluate mode")
    args = parser.parse_args()

    load_dotenv()
    try:
        client = anthropic.Anthropic()                                   
    except anthropic.AnthropicError as exc:
        raise SystemExit(f"Could not create Anthropic client: {exc}")

    con = get_connection()
    try:
        if args.evaluate:
            evaluate(client, con, args.days, args.top)
        else:
            trade_date = date.fromisoformat(args.date) if args.date else _latest_scored_date(con)
            assessment = assess_day(client, con, trade_date, args.top)
            if assessment:
                print_assessment(trade_date, assessment)
                print("Saved to agent_decisions table in halal_research.duckdb")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
