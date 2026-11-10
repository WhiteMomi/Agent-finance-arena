"""试验结束后,用 CLI 查看所有 LLM 会话的实验结果(无需 GUI)。"""

from __future__ import annotations

import argparse
from pathlib import Path

from config import Config
from data.engine import TushareDataEngine
from data.store import JsonStore
from trade.models import Account


def _fmt_money(value: float) -> str:
    return f"{value:,.2f}"


def _sessions(store: JsonStore) -> list[str]:
    state = store.base_dir / "state"
    if not state.exists():
        return []
    return sorted(
        item.name for item in state.iterdir() if item.is_dir()
    )


def _prices(engine: TushareDataEngine, symbols: list[str]) -> dict[str, float]:
    prices: dict[str, float] = {}
    for symbol in symbols:
        bar = engine.latest_bar(symbol)
        if bar is not None:
            prices[symbol] = bar.close
    return prices


def main() -> None:
    parser = argparse.ArgumentParser(description="展示所有 LLM 会话的实验结果")
    parser.add_argument("--session", default=None, help="只看某一个会话")
    parser.add_argument("--full", action="store_true", help="输出全部每日决策,否则只输出最近 5 条")
    args = parser.parse_args()

    cfg = Config.load()
    store = JsonStore(cfg.data_dir)
    engine = TushareDataEngine(store)
    session_names = [args.session] if args.session else _sessions(store)

    if not session_names:
        print("没有找到任何会话,请先运行 main.py run。")
        return

    print("=" * 100)
    for session in session_names:
        raw = store.load(f"state/{session}/account.json")
        if not raw:
            print(f"[{session}] 没有 account.json")
            continue
        account = Account.from_dict(raw)
        symbols = list(account.positions.keys()) or list(cfg.symbols)
        prices = _prices(engine, symbols)
        equity = account.equity(prices, cfg.contracts) if prices else account.balance
        available = account.available(prices, cfg.contracts) if prices else account.balance
        margin = account.margin_used(prices, cfg.contracts) if prices else 0.0
        risk = account.risk_ratio(prices, cfg.contracts) if prices else 0.0

        orders = store.load(f"state/{session}/orders.json", default={})
        order_list = orders.get("orders", []) if isinstance(orders, dict) else []
        trade_list = orders.get("trades", []) if isinstance(orders, dict) else []
        decisions = store.load(f"state/{session}/decisions.json", default=[])

        print(f"会话: {session}")
        print(f"  权益 {_fmt_money(equity)}  现金 {_fmt_money(account.balance)}")
        print(f"  可用 {_fmt_money(available)}  占用保证金 {_fmt_money(margin)}  风险度 {risk:.2%}")
        print(
            f"  已实现盈亏 {_fmt_money(account.realized_pnl)}  "
            f"手续费 {_fmt_money(account.fees_paid)}"
        )
        print(
            f"  订单 {len(order_list)} 笔,成交 {len(trade_list)} 笔,"
            f"决策记录 {len(decisions)} 天"
        )
        if account.positions:
            pos_text = ", ".join(
                f"{sym} 多{p.long_qty}/空{p.short_qty}"
                for sym, p in account.positions.items()
            )
            print(f"  持仓: {pos_text}")
        else:
            print("  持仓: 空仓")

        shown = decisions if args.full else decisions[-5:]
        if shown:
            print("  最近决策:")
            for item in shown:
                print(f"    {item['date']} | {item.get('decision', '')}")
        print("-" * 100)


if __name__ == "__main__":
    main()
