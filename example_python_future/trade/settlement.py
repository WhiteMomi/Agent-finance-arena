"""每日无负债结算与强制平仓(简化版)。"""

from __future__ import annotations

from trade.models import Account, Bar, ContractSpec


class SettlementEngine:
    def __init__(
        self,
        specs: dict[str, ContractSpec],
        margin_call_threshold: float = 0.8,
        force_close_threshold: float = 1.0,
    ) -> None:
        self.specs = specs
        self.margin_call_threshold = margin_call_threshold
        self.force_close_threshold = force_close_threshold

    def settle(self, account: Account, bars: dict[str, Bar]) -> None:
        self._mark_to_market(account, bars)
        prices = {symbol: bar.settle for symbol, bar in bars.items()}
        ratio = account.risk_ratio(prices, self.specs)
        if ratio >= self.force_close_threshold or account.equity(prices, self.specs) <= 0:
            self._force_close_all(account, bars)

    def _mark_to_market(self, account: Account, bars: dict[str, Bar]) -> None:
        for pos in list(account.positions.values()):
            spec = self.specs[pos.symbol]
            bar = bars.get(pos.symbol)
            if bar is None:
                continue
            pnl = 0.0
            pnl += (bar.settle - pos.prev_settle) * spec.multiplier * pos.long_qty
            pnl += (pos.prev_settle - bar.settle) * spec.multiplier * pos.short_qty
            account.balance += pnl
            account.realized_pnl += pnl

            # 盯市后,成本价与基准价都重置为今日结算价。
            pos.long_avg = bar.settle if pos.long_qty else 0.0
            pos.short_avg = bar.settle if pos.short_qty else 0.0
            pos.prev_settle = bar.settle
            pos.long_today = 0
            pos.short_today = 0

    def _force_close_all(self, account: Account, bars: dict[str, Bar]) -> None:
        for symbol, pos in list(account.positions.items()):
            spec = self.specs[symbol]
            bar = bars.get(symbol)
            if bar is None:
                continue
            qty = pos.long_qty + pos.short_qty
            if qty == 0:
                account.positions.pop(symbol, None)
                continue
            fee = spec.fee_close * qty
            account.balance -= fee
            account.fees_paid += fee
            account.positions.pop(symbol, None)
