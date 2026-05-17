"""Halal filter: sector/industry/ticker exclusions per `config/halal_exclusions.yaml`."""
from __future__ import annotations

from dataclasses import dataclass

from halal_gap.utils.config import halal_exclusions


@dataclass(slots=True, frozen=True)
class HalalFilter:
    """Case-insensitive substring match against sector/industry, plus ticker blacklist."""

    excluded_sectors: tuple[str, ...]
    excluded_industries: tuple[str, ...]
    excluded_tickers: frozenset[str]

    @classmethod
    def from_config(cls) -> HalalFilter:
        cfg = halal_exclusions()
        return cls(
            excluded_sectors=tuple(s.lower() for s in cfg.get("excluded_sectors", [])),
            excluded_industries=tuple(s.lower() for s in cfg.get("excluded_industries", [])),
            excluded_tickers=frozenset(t.upper() for t in cfg.get("excluded_tickers", [])),
        )

    def is_halal(self, *, ticker: str, sector: str | None, industry: str | None) -> bool:
        """Return True iff the ticker passes all halal screens.

        Example:
            >>> f = HalalFilter(("financial services",), ("banks",), frozenset({"BAC"}))
            >>> f.is_halal(ticker="AAPL", sector="Technology", industry="Consumer Electronics")
            True
            >>> f.is_halal(ticker="BAC", sector="Financial Services", industry="Banks - Diversified")
            False
        """
        if ticker.upper() in self.excluded_tickers:
            return False
        s = (sector or "").lower()
        i = (industry or "").lower()
        for bad in self.excluded_sectors:
            if bad and bad in s:
                return False
        for bad in self.excluded_industries:
            if bad and bad in i:
                return False
        return True
