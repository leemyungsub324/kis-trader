"""명령줄 진입점:  python -m kis_trader <명령>

  fetch-data   백테스트용 KRX 일봉(marcap) 내려받기
  backtest     전략 백테스트
  scan         일봉 갱신 + 다음 거래일 매수 후보 계산 (장 마감 후)
  run          자동매매 실행 (장 시작 전에 켜 둔다)
  status       보유 포지션·대기 주문·후보 확인
  report       trades.csv로 전략별 실현 손익 집계
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import urllib.request
from pathlib import Path

import pandas as pd
import yaml

from .strategies import build


def load_cfg(path: str) -> dict:
    cfg = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    for k, env in (("app_key", "KIS_APP_KEY"), ("app_secret", "KIS_APP_SECRET"), ("account", "KIS_ACCOUNT")):
        if os.environ.get(env):
            cfg[k] = os.environ[env]
    cfg.setdefault("env", "mock")
    cfg.setdefault("data_dir", "data")
    return cfg


def make_engine(cfg, dry_run=False, allow_real=False, notify=False):
    from .engine import DryBroker, Engine
    from .kis_api import KisClient
    from .notify import Notifier
    for k in ("app_key", "app_secret", "account"):
        if not cfg.get(k):
            sys.exit(f"설정 누락: {k} (config.yaml 또는 환경변수 KIS_APP_KEY / KIS_APP_SECRET / KIS_ACCOUNT)")
    if cfg["env"] == "real" and not (allow_real or dry_run):
        sys.exit("env: real 입니다. 실제 돈으로 주문하려면 --real-money 를 붙이세요.")
    broker = KisClient(cfg["app_key"], cfg["app_secret"], cfg["account"], cfg["env"], token_dir=cfg["data_dir"])
    if dry_run:
        broker = DryBroker(broker)
    return Engine(broker, build(cfg.get("strategies")), cfg, data_dir=cfg["data_dir"],
                  notify=Notifier.from_cfg(cfg) if notify else None)


def universe_fn(cfg):
    from .universe import load_universe
    u = cfg.get("universe", {})
    def fn():
        if u.get("codes"):
            return pd.DataFrame({"code": [str(c).zfill(6) for c in u["codes"]]})
        return load_universe(cfg["data_dir"], min_price=u.get("min_price", 1000))
    return fn


def cmd_fetch(a, cfg):
    os.makedirs(a.dir, exist_ok=True)
    for y in range(a.start, a.end + 1):
        dst = os.path.join(a.dir, f"marcap-{y}.parquet")
        url = f"https://raw.githubusercontent.com/FinanceData/marcap/master/data/marcap-{y}.parquet"
        print("받는 중", url)
        urllib.request.urlretrieve(url, dst)


def cmd_backtest(a, cfg):
    from .backtest import Costs, Panel, run_panel
    from .data import load_marcap, market_proxy
    markets = tuple(a.markets.split(","))
    data, _ = load_marcap(a.dir, markets=markets)
    kospi = data if markets == ("KOSPI",) else load_marcap(a.dir, markets=("KOSPI",))[0]
    idx = market_proxy(kospi)["close"]
    strats = build(cfg.get("strategies") if not a.all else {k: {} for k in __import__("kis_trader.strategies", fromlist=["REGISTRY"]).REGISTRY})
    panel = Panel(data, strats, idx)
    costs = Costs(**(cfg.get("costs") or {}))
    pd.set_option("display.width", 200)
    pd.set_option("display.float_format", lambda x: f"{x:,.3f}")
    for group in [strats] + ([[s] for s in strats] if len(strats) > 1 else []):
        r = run_panel(panel, group, cash=a.cash, costs=costs, start=a.start, end=a.end)
        print("\n==", " + ".join(s.label for s in group), "==")
        print(" | ".join(f"{k} {v:.1%}" if k != "거래수" else f"{k} {v}" for k, v in r.summary().items()))
        if len(group) > 1 or len(strats) == 1:
            print(r.by_strategy().to_string())
            y = r.equity.resample("YE").last()
            yr = y.pct_change()
            yr.iloc[0] = y.iloc[0] / a.cash - 1
            print("연도별 수익률(%):", {k.year: round(v * 100, 1) for k, v in yr.items()})
            if a.out:
                r.trades.to_csv(a.out, index=False)


def cmd_scan(a, cfg):
    e = make_engine(cfg, dry_run=True)
    plans = e.eod_scan(universe_fn(cfg)())
    for p in plans[:30]:
        print(p)


def cmd_run(a, cfg):
    e = make_engine(cfg, dry_run=a.dry_run, allow_real=a.real_money, notify=True)
    logging.getLogger().info("시작: env=%s dry_run=%s 전략=%s", cfg["env"], a.dry_run, list(e.S))
    e.notify.send(f"[시작] kis-trader v2 env={cfg['env']} dry_run={a.dry_run} 전략={list(e.S)}")
    e.run(universe_fn(cfg), poll=cfg.get("poll_seconds", 20))


def cmd_status(a, cfg):
    import json
    path = os.path.join(cfg["data_dir"], "state.json")
    if not os.path.exists(path):
        sys.exit("state.json 없음 (아직 실행 전)")
    st = json.load(open(path))
    print(f"날짜 {st['date']} | 국면 {st['regime']} | 신규매수중단 {st['halt_entries']} | 후보 기준일 {st.get('plan_date')}")
    print("보유:")
    for c, p in st["positions"].items():
        print(f"  {c} {p['strat']} {p['qty']}주 평단 {p['entry']:.0f} 손절 {p.get('stop', 0):.0f} 보유 {p['days']}일")
    print("미체결:", [(o["code"], o["side"], o["qty"], o["price"]) for o in st["orders"] if o["open"]])
    print("후보:", [(p["strat"], p["code"]) for p in st.get("plans", [])][:20])


def cmd_report(a, cfg):
    path = os.path.join(cfg["data_dir"], "trades.csv")
    if not os.path.exists(path):
        sys.exit("trades.csv 없음")
    t = pd.read_csv(path, dtype={"code": str})
    fee, tax = 0.00015, 0.0020
    t["cash"] = t.apply(lambda r: -r.qty * r.price * (1 + fee) if r.side == "buy" else r.qty * r.price * (1 - fee - tax), axis=1)
    t["signed"] = t.apply(lambda r: r.qty if r.side == "buy" else -r.qty, axis=1)
    g = t.groupby(["strat", "code"]).agg(net_qty=("signed", "sum"), pnl=("cash", "sum"), buy=("cash", lambda x: -x[x < 0].sum()))
    closed = g[g.net_qty == 0]
    out = closed.groupby("strat").agg(종목수=("pnl", "size"), 실현손익=("pnl", "sum"), 매수금액=("buy", "sum"),
                                     승률=("pnl", lambda x: (x > 0).mean()))
    out["수익률"] = out["실현손익"] / out["매수금액"]
    print(out.to_string(float_format=lambda x: f"{x:,.3f}"))
    print(f"\n보유 중(미청산) {len(g) - len(closed)}건은 제외. 수수료 0.015%·거래세 0.20% 가정.")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="kis_trader", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-c", "--config", default="config.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("fetch-data"); p.add_argument("--dir", default="data/marcap")
    p.add_argument("--start", type=int, default=2019); p.add_argument("--end", type=int, default=pd.Timestamp.now().year)
    p.set_defaults(fn=cmd_fetch)
    p = sub.add_parser("backtest"); p.add_argument("--dir", default="data/marcap"); p.add_argument("--markets", default="KOSPI")
    p.add_argument("--start", default="2019-07-01"); p.add_argument("--end", default=None)
    p.add_argument("--cash", type=float, default=5_000_000); p.add_argument("--all", action="store_true", help="실험용 전략까지 전부")
    p.add_argument("--out", help="거래 내역 CSV 저장 경로"); p.set_defaults(fn=cmd_backtest)
    p = sub.add_parser("scan"); p.set_defaults(fn=cmd_scan)
    p = sub.add_parser("run"); p.add_argument("--dry-run", action="store_true", help="주문을 보내지 않고 로그만")
    p.add_argument("--real-money", action="store_true", help="env: real 일 때 실제 주문 허용"); p.set_defaults(fn=cmd_run)
    p = sub.add_parser("status"); p.set_defaults(fn=cmd_status)
    p = sub.add_parser("report"); p.set_defaults(fn=cmd_report)
    a = ap.parse_args(argv)
    cfg = load_cfg(a.config)
    os.makedirs(cfg["data_dir"], exist_ok=True)
    # v1 과 같은 로그: logs/kis-trader.log (JSON, 5MB x 5 로테이션, 비밀값 마스킹)
    from .logging_setup import register_secrets, setup_logging
    tg = cfg.get("telegram") or {}
    register_secrets([cfg.get("app_key", ""), cfg.get("app_secret", ""), str(cfg.get("account", "")).split("-")[0],
                      os.environ.get("TELEGRAM_BOT_TOKEN", ""), tg.get("bot_token", "")])
    setup_logging(Path(os.environ.get("LOG_DIR") or cfg.get("log_dir", "logs")), cfg.get("log_level", "INFO"))
    a.fn(a, cfg)


if __name__ == "__main__":
    main()
