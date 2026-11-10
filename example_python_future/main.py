"""入口:seed / sync / run / account 四个子命令。"""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta

from config import Config
from data.engine import TushareDataEngine
from data.seed import seed_demo
from data.store import JsonStore
from trade.broker import Broker
from trade.models import Account, Bar
from trade.settlement import SettlementEngine


def _iso_date(ymd: str) -> str:
    """YYYYMMDD -> YYYY-MM-DD。"""
    return f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"


def _shift_days(ymd: str, days: int) -> str:
    """把 YYYYMMDD 往前/往后移动 days 天,返回 YYYYMMDD。"""
    d = date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:8])) + timedelta(days=days)
    return d.strftime("%Y%m%d")


def _session_name(cfg: Config, args: argparse.Namespace) -> str:
    if getattr(args, "session", None):
        return args.session
    if args.model == "openai":
        return cfg.model.get("name") or "llm"
    return "rule"


def _state_dir(cfg: Config, args: argparse.Namespace) -> str:
    return f"state/{_session_name(cfg, args)}"


def _load_account(store: JsonStore, initial_balance: float, state_dir: str) -> Account:
    raw = store.load(f"{state_dir}/account.json")
    if raw:
        return Account.from_dict(raw)
    return Account(balance=initial_balance, initial_balance=initial_balance)


def _build(cfg: Config, state_dir: str):
    store = JsonStore(cfg.data_dir)
    engine = TushareDataEngine(store)
    account = _load_account(store, cfg.initial_balance, state_dir)
    broker = Broker(account, cfg.contracts, store=store, state_dir=state_dir)
    settlement = SettlementEngine(
        cfg.contracts,
        margin_call_threshold=cfg.margin_call_threshold,
        force_close_threshold=cfg.force_close_threshold,
    )
    return store, engine, broker, settlement


def _select_symbols(cfg: Config, args: argparse.Namespace) -> list[str]:
    if args.symbol:
        if args.symbol not in cfg.contracts:
            raise KeyError(f"未知合约 {args.symbol},请先在 config.json 的 contracts 中配置")
        return [args.symbol]
    return list(cfg.symbols)


def cmd_seed(cfg: Config, args: argparse.Namespace) -> None:
    store, _, _, _ = _build(cfg, _state_dir(cfg, args))
    seed_demo(store, cfg.contracts, cfg.initial_balance, state_dir=_state_dir(cfg, args))
    print(f"已生成演示数据并重置账户: {store.base_dir}")


def cmd_sync(cfg: Config, args: argparse.Namespace) -> None:
    _, engine, _, _ = _build(cfg, _state_dir(cfg, args))
    if not args.start or not args.end:
        raise SystemExit("sync 需要 --start 和 --end(YYYYMMDD)")
    fetch_start = _shift_days(args.start, -int(args.lookback_days))
    symbols = [args.symbol] if args.symbol else list(cfg.symbols)
    print(f"同步区间:{fetch_start} ~ {args.end}(含 {args.lookback_days} 天前置上下文)")
    for symbol in symbols:
        n = engine.sync(symbol, fetch_start, args.end)
        print(f"{symbol} 同步完成,共 {n} 根日线")


def cmd_account(cfg: Config, args: argparse.Namespace) -> None:
    store = JsonStore(cfg.data_dir)
    if getattr(args, "session", None):
        session = args.session
    else:
        meta = store.load("state/last_session.json", default={})
        session = meta.get("session") or cfg.model.get("name") or "rule"
    state_dir = f"state/{session}"
    _, engine, broker, _ = _build(cfg, state_dir)
    prices: dict[str, float] = {}
    for symbol in cfg.symbols:
        bar = engine.latest_bar(symbol)
        if bar is not None:
            prices[symbol] = bar.close
    account = broker.account
    print(
        json.dumps(
            {
                "session": session,
                "balance": account.balance,
                "equity": account.equity(prices, cfg.contracts) if prices else account.balance,
                "available": account.available(prices, cfg.contracts) if prices else account.balance,
                "margin_used": account.margin_used(prices, cfg.contracts) if prices else 0.0,
                "risk_ratio": account.risk_ratio(prices, cfg.contracts) if prices else 0.0,
                "realized_pnl": account.realized_pnl,
                "fees_paid": account.fees_paid,
                "positions": {sym: pos.to_dict() for sym, pos in account.positions.items()},
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def cmd_run(cfg: Config, args: argparse.Namespace) -> None:
    from agent.runner import build_agent
    from agent.tools import build_tools

    state_dir = _state_dir(cfg, args)
    store, engine, broker, settlement = _build(cfg, state_dir)
    symbols = _select_symbols(cfg, args)
    specs = cfg.contracts

    all_bars = {symbol: engine.get_bars(symbol) for symbol in symbols}
    if not any(all_bars.values()):
        print("本地无日线数据,自动生成演示数据。如需真实数据请先运行 sync。")
        seed_demo(store, specs, cfg.initial_balance, state_dir=state_dir)
        engine = TushareDataEngine(store)
        all_bars = {symbol: engine.get_bars(symbol) for symbol in symbols}

    # 回放区间:优先用 --start/--end,否则使用全部已有数据。
    if args.start:
        start_iso = _iso_date(args.start)
        all_bars = {
            sym: [b for b in bars if b.date >= start_iso]
            for sym, bars in all_bars.items()
        }
    if args.end:
        end_iso = _iso_date(args.end)
        all_bars = {
            sym: [b for b in bars if b.date <= end_iso]
            for sym, bars in all_bars.items()
        }

    dates = sorted({bar.date for bars in all_bars.values() for bar in bars})
    if args.days:
        dates = dates[-args.days :]

    tools = build_tools(broker, engine, specs)
    agent = build_agent(
        args.model,
        broker,
        engine,
        specs,
        tools,
        model_config=cfg.model,
        symbols=symbols,
    )
    decisions: list[dict] = []

    for date_str in dates:
        engine.set_current_date(date_str)
        day_bars: dict[str, Bar] = {}
        prev_settles: dict[str, float] = {}
        for symbol in symbols:
            bars = all_bars[symbol]
            history = [bar for bar in bars if bar.date <= date_str]
            if not history or history[-1].date != date_str:
                continue
            bar = history[-1]
            day_bars[symbol] = bar
            prev_settles[symbol] = (
                history[-2].settle if len(history) >= 2 else bar.settle
            )

        if not day_bars:
            continue

        broker.begin_day(day_bars, prev_settles)
        context = (
            f"今天是 {date_str},可交易合约池: {', '.join(symbols)}。"
            "请根据工具返回决定在各合约上的开平仓。"
        )
        print(f"[{date_str}] 开始决策", flush=True)
        try:
            decision = (
                agent.step(context)
                if args.model == "openai"
                else agent.step(list(day_bars.keys()))
            )
        except Exception as exc:
            decision = f"DECISION_ERROR: {exc}"
            print(f"[{date_str}] 决策失败,跳过当天: {exc}", flush=True)
        print(f"[{date_str}] 决策完成", flush=True)
        settlement.settle(broker.account, day_bars)
        broker.save_state()

        decisions.append(
            {
                "date": date_str,
                "decision": decision,
                "equity": broker.account.balance,
                "positions": {
                    sym: pos.to_dict() for sym, pos in broker.account.positions.items()
                },
            }
        )
        store.save(f"{state_dir}/decisions.json", decisions)
        store.save("state/last_session.json", {"session": _session_name(cfg, args)})

        summary = " ".join(
            f"{sym}={bar.close:,.1f}" for sym, bar in day_bars.items()
        )
        print(
            f"{date_str} equity={broker.account.balance:>12,.2f} "
            f"{summary} | {decision}",
            flush=True,
        )
    engine.set_current_date(None)


def main() -> None:
    parser = argparse.ArgumentParser(description="最小可用的期货 Agent 交易核心")
    parser.add_argument(
        "command",
        choices=["seed", "sync", "run", "account"],
        default="run",
        nargs="?",
    )
    parser.add_argument("--symbol", default=None, help="只运行/同步单个合约,如 RB2610.SHF")
    parser.add_argument("--start", default=None, help="起始日期 YYYYMMDD")
    parser.add_argument("--end", default=None, help="结束日期 YYYYMMDD")
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=30,
        help="sync 时在 --start 之前额外抓取的天数(日历天)",
    )
    parser.add_argument("--days", type=int, default=0, help="run 时最多回放的天数,0 表示全部")
    parser.add_argument("--session", default=None, help="state 下的会话名,默认取模型名")
    parser.add_argument(
        "--model",
        choices=["rule", "openai"],
        default="rule",
        help="rule=离线双均线,openai=LangChain LLM Agent",
    )
    args = parser.parse_args()

    cfg = Config.load()
    if args.command == "seed":
        cmd_seed(cfg, args)
    elif args.command == "sync":
        cmd_sync(cfg, args)
    elif args.command == "account":
        cmd_account(cfg, args)
    else:
        cmd_run(cfg, args)


if __name__ == "__main__":
    main()
