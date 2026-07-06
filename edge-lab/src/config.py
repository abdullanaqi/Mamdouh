"""Central configuration for edge-lab.

All tunable assumptions live here so they are visible, versioned, and
stress-testable. Nothing in this file may encode future information.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:  # optional
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Paths:
    root: Path = REPO_ROOT
    raw: Path = REPO_ROOT / "data" / "raw"
    minute: Path = REPO_ROOT / "data" / "processed" / "minute"
    daily: Path = REPO_ROOT / "data" / "processed" / "daily"
    features: Path = REPO_ROOT / "data" / "features"
    universe: Path = REPO_ROOT / "data" / "universe"
    cache: Path = REPO_ROOT / "data" / "cache"
    experiments: Path = REPO_ROOT / "research" / "experiments"
    failed_ideas: Path = REPO_ROOT / "research" / "failed_ideas"
    accepted_edges: Path = REPO_ROOT / "research" / "accepted_edges"
    backtests: Path = REPO_ROOT / "results" / "backtests"
    walk_forward: Path = REPO_ROOT / "results" / "walk_forward"
    daily_picks: Path = REPO_ROOT / "results" / "daily_picks"
    reports: Path = REPO_ROOT / "reports"
    logs: Path = REPO_ROOT / "logs"
    models_candidates: Path = REPO_ROOT / "models" / "candidates"
    models_production: Path = REPO_ROOT / "models" / "production"


@dataclass(frozen=True)
class MassiveConfig:
    """Massive (massive.com) flat-file S3 access. Credentials come from env only.

    Flat files are published end-of-day (T+1 for research). Live intraday
    selection requires the Massive REST/WebSocket API, which is NOT wired up
    yet (see src/data/massive_rest_client.py).
    """
    s3_endpoint: str = os.environ.get("MASSIVE_S3_ENDPOINT", "https://files.massive.com")
    s3_bucket: str = os.environ.get("MASSIVE_S3_BUCKET", "flatfiles")
    access_key_id: str = os.environ.get("MASSIVE_ACCESS_KEY_ID", "")
    secret_access_key: str = os.environ.get("MASSIVE_SECRET_ACCESS_KEY", "")
    # Prefixes below follow the documented Massive/Polygon flat-file layout.
    # VERIFY against your account's file browser before large downloads.
    minute_prefix: str = os.environ.get("MASSIVE_MINUTE_PREFIX", "us_stocks_sip/minute_aggs_v1")
    day_prefix: str = os.environ.get("MASSIVE_DAY_PREFIX", "us_stocks_sip/day_aggs_v1")


@dataclass(frozen=True)
class Costs:
    """Execution cost assumptions. Deliberately conservative; stress-tested
    at multiples in overfit_tests.py."""
    fee_round_trip_usd: float = 2.00
    # Base one-way slippage in bps by (price bucket). Applied adversely on
    # BOTH entry and exit. These are assumptions, not measured microstructure
    # -- documented in README_TRUTH.md. Quote data would allow real spreads.
    slippage_bps_by_price: tuple = ((2.0, 25.0), (5.0, 15.0), (15.0, 8.0), (50.0, 5.0), (1e9, 3.0))
    # Extra slippage added when order is large vs. liquidity (per unit of
    # participation sqrt): slip += impact_k * sqrt(order_$ / bar_$vol) bps
    impact_k_bps: float = 8.0
    stop_extra_slippage_bps: float = 5.0  # stops are market orders; they slip more

    def one_way_bps(self, price: float, order_usd: float = 0.0, bar_dollar_vol: float = float("inf")) -> float:
        base = self.slippage_bps_by_price[-1][1]
        for cutoff, bps in self.slippage_bps_by_price:
            if price < cutoff:
                base = bps
                break
        impact = 0.0
        if order_usd > 0 and bar_dollar_vol and bar_dollar_vol > 0 and bar_dollar_vol != float("inf"):
            impact = self.impact_k_bps * (order_usd / bar_dollar_vol) ** 0.5
        return base + impact


@dataclass(frozen=True)
class Session:
    tz: str = "America/New_York"
    premarket_open: str = "04:00"
    rth_open: str = "09:30"
    rth_close: str = "16:00"
    last_entry: str = "15:30"
    default_time_stop: str = "15:55"  # market-out time if nothing else triggered


@dataclass(frozen=True)
class UniverseFilters:
    """Point-in-time universe gates, computed from data <= prior day only."""
    min_price: float = 1.00
    max_price: float = 2000.0
    min_median_dollar_vol_20d: float = 2_000_000.0
    min_prev_day_dollar_vol: float = 1_000_000.0
    max_spread_proxy_bps: float = 60.0  # trailing daily (H-L)/C proxy gate


@dataclass(frozen=True)
class Sizing:
    account_equity: float = float(os.environ.get("ACCOUNT_EQUITY", "10000"))
    risk_per_trade_frac: float = 0.005        # 0.5% of equity risked to stop
    max_position_frac: float = 0.25           # never > 25% of equity in one name
    max_participation_of_minute_vol: float = 0.02  # <=2% of entry-minute $ volume


@dataclass(frozen=True)
class WalkForwardConfig:
    train_months: int = 12
    val_months: int = 3
    test_months: int = 3
    step_months: int = 3
    embargo_days: int = 5


@dataclass(frozen=True)
class SelectorWeights:
    """Ranking score per spec section 8. Score is used to ORDER candidates;
    the EV gate decides whether the pick is a real recommendation or a
    FORCED TRADE."""
    ev: float = 1.0
    p_win: float = 0.5
    liquidity: float = 0.2
    stability: float = 0.2
    tail_risk: float = -0.4
    slippage_risk: float = -0.3
    overfit_penalty: float = -0.3


@dataclass(frozen=True)
class LLMConfig:
    """Anthropic API is used ONLY for research planning / auditing / reports.
    It never selects trades. Docs: https://docs.claude.com/en/api/overview"""
    api_key: str = os.environ.get("ANTHROPIC_API_KEY", "")
    model: str = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    max_tokens: int = 2000


@dataclass(frozen=True)
class Config:
    paths: Paths = field(default_factory=Paths)
    massive: MassiveConfig = field(default_factory=MassiveConfig)
    costs: Costs = field(default_factory=Costs)
    session: Session = field(default_factory=Session)
    universe: UniverseFilters = field(default_factory=UniverseFilters)
    sizing: Sizing = field(default_factory=Sizing)
    wf: WalkForwardConfig = field(default_factory=WalkForwardConfig)
    selector_weights: SelectorWeights = field(default_factory=SelectorWeights)
    llm: LLMConfig = field(default_factory=LLMConfig)


CFG = Config()
