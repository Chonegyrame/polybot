"""Append-only paper-trade ledger in local SQLite.

Deliberately NOT Supabase — the whole point of this bot is to avoid adding to
the storage problem, and a local file is the right home for a paper backtest
log anyway. One row per (window, side); a unique index enforces "at most one
position per window per side" so a tight evaluation loop can't double-fill.

Settlement is on Polymarket's *actual* resolution (outcomePrices after close),
not on our own price comparison — so realized PnL is always truth.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent / "paper_ledger.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_trades (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_entry      REAL NOT NULL,
    horizon       TEXT NOT NULL,
    slug          TEXT NOT NULL,
    condition_id  TEXT,
    side          TEXT NOT NULL,            -- 'up' / 'down'
    fair_prob     REAL,
    entry_price   REAL,                     -- avg ask paid
    shares        REAL,
    cost_usd      REAL,
    fee_usd       REAL,
    net_edge      REAL,
    spot_at_entry REAL,
    open_price    REAL,
    sigma         REAL,
    seconds_left  REAL,
    status        TEXT NOT NULL DEFAULT 'open',  -- open / settled / void
    outcome       TEXT,                     -- 'up' / 'down'
    pnl_usd       REAL,
    ts_settled    REAL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_trade_window_side
    ON paper_trades(slug, side);

CREATE TABLE IF NOT EXISTS account (
    id        INTEGER PRIMARY KEY CHECK (id = 1),
    balance   REAL NOT NULL,
    starting  REAL NOT NULL,
    updated_at REAL
);

-- Per-second observations for offline research/backtesting. This is the
-- dataset every future strategy is developed and validated against.
CREATE TABLE IF NOT EXISTS market_snapshots (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         REAL NOT NULL,
    horizon    TEXT NOT NULL,
    slug       TEXT NOT NULL,
    secs_left  REAL,
    spot       REAL,
    open_price REAL,
    sigma      REAL,
    fair_up    REAL,
    up_bid     REAL,
    up_ask     REAL,
    down_bid   REAL,
    down_ask   REAL
);
CREATE INDEX IF NOT EXISTS ix_snap_slug ON market_snapshots(slug);
CREATE INDEX IF NOT EXISTS ix_snap_ts ON market_snapshots(ts);
"""


def connect(path: Path | str = DEFAULT_DB) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def ensure_account(conn: sqlite3.Connection, starting: float = 1000.0) -> None:
    """Create the paper account with `starting` bankroll if it doesn't exist."""
    row = conn.execute("SELECT 1 FROM account WHERE id=1").fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO account (id, balance, starting, updated_at) VALUES (1,?,?,?)",
            (starting, starting, time.time()),
        )
        conn.commit()


def balance(conn: sqlite3.Connection) -> float:
    row = conn.execute("SELECT balance FROM account WHERE id=1").fetchone()
    return float(row["balance"]) if row else 0.0


def _adjust_balance(conn: sqlite3.Connection, delta: float) -> None:
    conn.execute(
        "UPDATE account SET balance = balance + ?, updated_at = ? WHERE id=1",
        (delta, time.time()),
    )


def log_snapshot(
    conn: sqlite3.Connection, *, ts: float, horizon: str, slug: str,
    secs_left: float | None, spot: float | None, open_price: float | None,
    sigma: float | None, fair_up: float | None, up_bid: float | None,
    up_ask: float | None, down_bid: float | None, down_ask: float | None,
) -> None:
    """Append one per-second market observation to the research dataset."""
    conn.execute(
        """INSERT INTO market_snapshots
           (ts,horizon,slug,secs_left,spot,open_price,sigma,fair_up,
            up_bid,up_ask,down_bid,down_ask)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (ts, horizon, slug, secs_left, spot, open_price, sigma, fair_up,
         up_bid, up_ask, down_bid, down_ask),
    )
    conn.commit()


@dataclass
class TradeRecord:
    ts_entry: float
    horizon: str
    slug: str
    condition_id: str
    side: str
    fair_prob: float
    entry_price: float
    shares: float
    cost_usd: float
    fee_usd: float
    net_edge: float
    spot_at_entry: float
    open_price: float
    sigma: float
    seconds_left: float


def record_trade(conn: sqlite3.Connection, t: TradeRecord) -> int | None:
    """Insert a new paper trade and debit the bankroll for cost+fee.

    Returns the row id, or None if rejected because either:
      - a position for this (slug, side) already exists (unique-index collision), or
      - the bankroll can't cover cost+fee (paper account is busted / too small).
    """
    outlay = (t.cost_usd or 0.0) + (t.fee_usd or 0.0)
    if balance(conn) < outlay:
        return None
    try:
        cur = conn.execute(
            """INSERT INTO paper_trades
               (ts_entry,horizon,slug,condition_id,side,fair_prob,entry_price,
                shares,cost_usd,fee_usd,net_edge,spot_at_entry,open_price,sigma,
                seconds_left,status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'open')""",
            (t.ts_entry, t.horizon, t.slug, t.condition_id, t.side, t.fair_prob,
             t.entry_price, t.shares, t.cost_usd, t.fee_usd, t.net_edge,
             t.spot_at_entry, t.open_price, t.sigma, t.seconds_left),
        )
        _adjust_balance(conn, -outlay)
        conn.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError:
        conn.rollback()
        return None


def has_position(conn: sqlite3.Connection, slug: str, side: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM paper_trades WHERE slug=? AND side=? LIMIT 1", (slug, side)
    ).fetchone()
    return row is not None


def open_trades(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM paper_trades WHERE status='open'"
    ).fetchall()


def settle(conn: sqlite3.Connection, trade_id: int, outcome: str) -> float:
    """Settle an open trade against the actual winning outcome ('up'/'down').

    Each share pays $1 if its side won, else $0.
      pnl = (won ? shares - cost : -cost) - fee
    """
    row = conn.execute(
        "SELECT * FROM paper_trades WHERE id=?", (trade_id,)
    ).fetchone()
    if row is None or row["status"] != "open":
        return 0.0
    won = (outcome == row["side"])
    payout = row["shares"] if won else 0.0
    pnl = payout - row["cost_usd"] - (row["fee_usd"] or 0.0)
    conn.execute(
        "UPDATE paper_trades SET status='settled', outcome=?, pnl_usd=?, ts_settled=? WHERE id=?",
        (outcome, pnl, time.time(), trade_id),
    )
    # Credit the payout back to the bankroll. cost+fee were debited at entry,
    # so the net balance change across entry+settle equals pnl.
    _adjust_balance(conn, payout)
    conn.commit()
    return pnl


def summary(conn: sqlite3.Connection) -> dict:
    """Aggregate settled-trade performance, overall and per horizon."""
    rows = conn.execute(
        "SELECT horizon, pnl_usd, net_edge FROM paper_trades WHERE status='settled'"
    ).fetchall()
    out: dict = {"overall": _agg(rows)}
    horizons = sorted({r["horizon"] for r in rows})
    for h in horizons:
        out[h] = _agg([r for r in rows if r["horizon"] == h])
    acct = conn.execute("SELECT balance, starting FROM account WHERE id=1").fetchone()
    if acct is not None:
        out["bankroll"] = {
            "starting": acct["starting"],
            "balance": acct["balance"],
            "open_positions": len(open_trades(conn)),
            "snapshots_logged": conn.execute(
                "SELECT COUNT(*) c FROM market_snapshots").fetchone()["c"],
        }
    return out


def _agg(rows: list[sqlite3.Row]) -> dict:
    n = len(rows)
    if n == 0:
        return {"trades": 0, "pnl": 0.0, "wins": 0, "win_rate": None, "avg_edge": None}
    pnl = sum(r["pnl_usd"] or 0.0 for r in rows)
    wins = sum(1 for r in rows if (r["pnl_usd"] or 0.0) > 0)
    avg_edge = sum(r["net_edge"] or 0.0 for r in rows) / n
    return {"trades": n, "pnl": pnl, "wins": wins,
            "win_rate": wins / n, "avg_edge": avg_edge}
