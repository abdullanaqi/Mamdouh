"""Central configuration for the halal 9:35->15:55 research pipeline.

Every constant that affects correctness or leakage-safety lives here so it
is defined exactly once and can be audited in one place.
"""
from __future__ import annotations

import logging
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

                                                                             
                                                                          
                                                                            
                                                                          
                                                                     
SOURCE_DB_PATH = os.path.join(BASE_DIR, "market_data.duckdb")
OUTPUT_DB_PATH = os.path.join(BASE_DIR, "halal_research.duckdb")
HALAL_TICKERS_PATH = os.path.join(BASE_DIR, "halal_tickers.json")

REPORT_DIR = os.path.join(BASE_DIR, "reports")
REPORT_PATH = os.path.join(REPORT_DIR, "report_halal_backtest.md")
RULES_REPORT_PATH = os.path.join(REPORT_DIR, "rules_and_warnings.md")

                                                                              
                                                                         
                                                                          
                                                                         
                                                    
 
                                                                      
                                                                     
                                                                          
                                                                          
SESSION_START = "04:00:00"                                         
SESSION_END = "16:00:00"                                                        
PREMARKET_START = "04:00:00"
PREMARKET_END = "09:29:00"                                                     
REGULAR_OPEN = "09:30:00"
REGULAR_CLOSE_BAR = "15:59:00"                                                     
ENTRY_TIME = "09:35:00"                                
EXIT_TIME = "15:55:00"                                                      
PRE_ENTRY_START = "09:30:00"
PRE_ENTRY_END = "09:34:00"                                              
FIRST_5MIN_END = "09:40:00"                                                     

MIN_ENTRY_PRICE = 5.0

                                                                             
TOP_5_PCT_CUTOFF = 0.95                                                           
TOP_10_PCT_CUTOFF = 0.90

                                                                              
                                                                       
                                                                          
                                                                            
                                                                         
                                                            
MOMENTUM_TOP_QUINTILE = 0.80
MOMENTUM_TOP_DECILE = 0.90
VOLUME_TOP_QUINTILE = 0.80
LIQUIDITY_BOTTOM_QUINTILE = 0.20
RETURN_SMALL_ABS_TERCILE = 0.33                                                 

                                                                              
                                                                      
                                                                       
                                                                     
                                                                    
                                                                     
                                                                       
MIN_TRAIN_MONTHS = 2                                                       

                                                                              
TOP_N_VARIANTS = [1, 2, 3, 5, 10]

                                                                              
                                                                              
                                                                            
                               
                                                                   
DYNAMIC_VOL_FLOOR = 0.003
DYNAMIC_VOL_CAP = 0.10
DYNAMIC_TP_VOL_MULT = 3.0
DYNAMIC_SL_VOL_MULT = 2.0


def dynamic_vol_proxy(
    entry_price: float,
    high_0930_to_before_entry: float | None,
    low_0930_to_before_entry: float | None,
    premarket_high: float | None,
    premarket_low: float | None,
) -> float:
    """Leakage-safe volatility proxy for dynamic intraday exit levels."""
    if entry_price is None or entry_price <= 0:
        return DYNAMIC_VOL_FLOOR

    opening_range = 0.0
    if high_0930_to_before_entry is not None and low_0930_to_before_entry is not None:
        opening_range = max((high_0930_to_before_entry - low_0930_to_before_entry) / entry_price, 0.0)

    premarket_range = 0.0
    if premarket_high is not None and premarket_low is not None:
        premarket_range = max(0.5 * (premarket_high - premarket_low) / entry_price, 0.0)

    return min(max(max(opening_range, premarket_range), DYNAMIC_VOL_FLOOR), DYNAMIC_VOL_CAP)


def dynamic_tp_sl_prices(
    entry_price: float,
    high_0930_to_before_entry: float | None,
    low_0930_to_before_entry: float | None,
    premarket_high: float | None,
    premarket_low: float | None,
    tp_mult: float = DYNAMIC_TP_VOL_MULT,
    sl_mult: float = DYNAMIC_SL_VOL_MULT,
) -> tuple[float, float, float]:
    """Return (tp_price, sl_price, vol_proxy) for a long position."""
    vol = dynamic_vol_proxy(
        entry_price,
        high_0930_to_before_entry,
        low_0930_to_before_entry,
        premarket_high,
        premarket_low,
    )
    return entry_price * (1 + tp_mult * vol), entry_price * (1 - sl_mult * vol), vol


def dynamic_vol_proxy_sql(entry_col: str = "entry_price_0935") -> str:
    return f"""
        LEAST(GREATEST(
            GREATEST(
                COALESCE((high_0930_to_before_entry - low_0930_to_before_entry) / {entry_col}, 0),
                COALESCE(0.5 * (premarket_high - premarket_low) / {entry_col}, 0)
            ), {DYNAMIC_VOL_FLOOR}), {DYNAMIC_VOL_CAP})
    """


                                                                             
                                                                            
                                                                       
                                                                      
                                                                         
                                                    
COST_BPS_BY_PRICE_TIER = [
    (10.0, 30.0),                                            
    (50.0, 15.0),                                                  
    (float("inf"), 8.0),                                          
]


def cost_bps_for_price(price: float) -> float:
    for upper, bps in COST_BPS_BY_PRICE_TIER:
        if price < upper:
            return bps
    return COST_BPS_BY_PRICE_TIER[-1][1]

                                                                             
                                                                       
                                                                        
                                                          
PRE_ENTRY_FEATURE_COLUMNS = [
    "open_0930",
    "high_0930_to_before_entry",
    "low_0930_to_before_entry",
    "return_0930_to_entry",
    "volume_0930_to_entry",
    "transactions_0930_to_entry",
    "dollar_volume_0930_to_entry",
    "average_volume_per_minute_0930_to_entry",
    "average_transactions_per_minute_0930_to_entry",
    "close_position_inside_pre_entry_range",
    "pre_entry_momentum_percentile",
    "pre_entry_volume_percentile",
    "pre_entry_dollar_volume_percentile",
    "pre_entry_transactions_percentile",
    "premarket_open",
    "premarket_last_price",
    "premarket_high",
    "premarket_low",
    "premarket_volume",
    "premarket_transactions",
    "premarket_dollar_volume",
    "premarket_bar_count",
    "premarket_range_position",
    "gap_pct_premarket_vs_prior_close",
    "gap_pct_open_vs_prior_close",
    "premarket_volume_percentile",
    "premarket_dollar_volume_percentile",
    "premarket_gap_percentile",
    "has_premarket_data",
    "news_count_before_entry",
    "positive_news_count_before_entry",
    "negative_news_count_before_entry",
    "neutral_news_count_before_entry",
    "minutes_between_latest_news_and_entry",
    "has_news_before_entry",
    "has_positive_news_before_entry",
    "has_negative_news_before_entry",
    "duplicate_news_id_count_across_tickers",
    "is_broad_news",
    "entry_price_0935",
]

FORBIDDEN_COLUMNS = {
    "return_0935_to_1555",
    "exit_price_1555",
    "daily_rank_by_return",
    "daily_percentile_by_return",
    "is_top_5_percent",
    "is_top_10_percent",
    "is_winner",
    "is_loser",
    "max_favorable_move_after_entry",
    "max_adverse_move_after_entry",
    "first_5min_return_after_entry",
    "looked_strong_before_entry_but_lost",
    "high_pre_entry_momentum_then_negative_return",
    "positive_news_but_negative_return",
    "high_volume_before_entry_but_failed",
    "early_spike_then_fade",
    "large_gain_given_back_after_entry",
    "green_at_entry_red_by_exit",
    "strong_first_5min_but_closed_weak",
    "low_liquidity_false_move",
    "repeated_news_but_no_follow_through",
    "broad_news_misleading_ticker_reaction",
    "possible_pump_and_fade_pattern",
    "possible_buyer_trap_pattern",
}

assert not (set(PRE_ENTRY_FEATURE_COLUMNS) & FORBIDDEN_COLUMNS), (
    "A forbidden outcome/label column leaked into PRE_ENTRY_FEATURE_COLUMNS"
)


def setup_logging(name: str) -> logging.Logger:
    """Consistent logging config shared by every stage module."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%H:%M:%S")
        )
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger
