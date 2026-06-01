"""Local SQLite store for the esports sharp tracker.

Deliberately NOT Supabase (that's full, costs money, and this data is tiny +
append-only — exactly what a local file is for). Three tables:

  esports_sharps         — the watchlist + each wallet's vetted recent-form stats.
  esports_sharp_actions  — one row per detected entry/exit, including the LIVE
                           book at detection (what WE would pay/receive to follow).
  tracker_cursor         — per-wallet high-water timestamp so a restart resumes
                           cleanly and never re-logs or double-logs an action.

All times are unix epoch seconds (REAL).
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent / "esports.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS esports_sharps (
    wallet         TEXT PRIMARY KEY,
    name           TEXT,
    pseudonym      TEXT,
    sectors        TEXT,                 -- csv: 'lol', 'cs', 'lol,cs'
    vet_pnl        REAL,                 -- recent-form reconstructed esports PnL
    vet_win_rate   REAL,
    vet_roi        REAL,
    vet_median_entry REAL,
    vet_markets    INTEGER,
    note           TEXT,                 -- e.g. 'maker rebate — do not mirror'
    follow         INTEGER NOT NULL DEFAULT 1,  -- 1 = mirror-worthy, 0 = watch only
    active         INTEGER NOT NULL DEFAULT 1,
    added_at       REAL
);

CREATE TABLE IF NOT EXISTS esports_sharp_actions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    wallet       TEXT NOT NULL,
    tx_hash      TEXT,
    condition_id TEXT,
    asset        TEXT,                   -- outcome token_id they traded
    title        TEXT,
    slug         TEXT,
    outcome      TEXT,                   -- 'Yes'/'No'/team label
    side         TEXT,                   -- 'BUY' / 'SELL'
    game         TEXT,                   -- 'lol' / 'cs' (from event tag)
    market_type  TEXT,                   -- winner / handicap / total / prop
    their_price  REAL,
    size         REAL,
    usdc_size    REAL,
    traded_at    REAL,                   -- their fill timestamp
    detected_at  REAL,                   -- when our loop saw it
    live_bid     REAL,                   -- our book at detection
    live_ask     REAL,
    UNIQUE(wallet, tx_hash, asset, side, traded_at)
);
CREATE INDEX IF NOT EXISTS ix_actions_wallet ON esports_sharp_actions(wallet);
CREATE INDEX IF NOT EXISTS ix_actions_detected ON esports_sharp_actions(detected_at);
CREATE INDEX IF NOT EXISTS ix_actions_cid ON esports_sharp_actions(condition_id);

-- The esports market universe: every market under an OPEN (and recently-closed)
-- LoL/CS event, refreshed periodically by esports.markets. The tracker checks
-- membership here to decide "is this trade esports?" — tag-based, so it catches
-- handicap/total/prop markets whose TITLE omits the game name (the old
-- title-keyword check missed those). game/market_type come from the sweep.
CREATE TABLE IF NOT EXISTS esports_markets (
    condition_id TEXT PRIMARY KEY,
    game         TEXT,                   -- 'lol' / 'cs'
    title        TEXT,
    market_type  TEXT,                   -- winner / handicap / total / prop / other
    closed       INTEGER NOT NULL DEFAULT 0,
    refreshed_at REAL
);

CREATE TABLE IF NOT EXISTS tracker_cursor (
    wallet     TEXT PRIMARY KEY,
    last_ts    REAL NOT NULL,           -- newest traded_at processed for this wallet
    updated_at REAL
);
"""


def connect(path: Path | str = DEFAULT_DB) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    # Idempotent migration for DBs created before game/market_type existed.
    for col in ("game TEXT", "market_type TEXT"):
        try:
            conn.execute(f"ALTER TABLE esports_sharp_actions ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.commit()
    return conn


# --------------------------- watchlist ---------------------------


def upsert_sharp(conn: sqlite3.Connection, **f) -> None:
    """Insert or update a watchlist wallet. `wallet` required; rest optional.

    Updates metadata in place but never clobbers added_at on re-seed.
    """
    conn.execute(
        """INSERT INTO esports_sharps
             (wallet,name,pseudonym,sectors,vet_pnl,vet_win_rate,vet_roi,
              vet_median_entry,vet_markets,note,follow,active,added_at)
           VALUES (:wallet,:name,:pseudonym,:sectors,:vet_pnl,:vet_win_rate,
              :vet_roi,:vet_median_entry,:vet_markets,:note,:follow,:active,:added_at)
           ON CONFLICT(wallet) DO UPDATE SET
              name=excluded.name, pseudonym=excluded.pseudonym,
              sectors=excluded.sectors, vet_pnl=excluded.vet_pnl,
              vet_win_rate=excluded.vet_win_rate, vet_roi=excluded.vet_roi,
              vet_median_entry=excluded.vet_median_entry,
              vet_markets=excluded.vet_markets, note=excluded.note,
              follow=excluded.follow, active=excluded.active""",
        {
            "wallet": f["wallet"].lower(),
            "name": f.get("name"), "pseudonym": f.get("pseudonym"),
            "sectors": f.get("sectors"), "vet_pnl": f.get("vet_pnl"),
            "vet_win_rate": f.get("vet_win_rate"), "vet_roi": f.get("vet_roi"),
            "vet_median_entry": f.get("vet_median_entry"),
            "vet_markets": f.get("vet_markets"), "note": f.get("note"),
            "follow": int(f.get("follow", 1)), "active": int(f.get("active", 1)),
            "added_at": time.time(),
        },
    )
    conn.commit()


def active_wallets(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM esports_sharps WHERE active=1 ORDER BY vet_pnl DESC"
    ).fetchall()


# --------------------------- cursor ---------------------------


def get_cursor(conn: sqlite3.Connection, wallet: str) -> float | None:
    row = conn.execute(
        "SELECT last_ts FROM tracker_cursor WHERE wallet=?", (wallet.lower(),)
    ).fetchone()
    return float(row["last_ts"]) if row else None


def set_cursor(conn: sqlite3.Connection, wallet: str, last_ts: float) -> None:
    conn.execute(
        """INSERT INTO tracker_cursor (wallet,last_ts,updated_at) VALUES (?,?,?)
           ON CONFLICT(wallet) DO UPDATE SET last_ts=excluded.last_ts,
             updated_at=excluded.updated_at""",
        (wallet.lower(), last_ts, time.time()),
    )
    conn.commit()


# --------------------------- actions ---------------------------


def log_action(conn: sqlite3.Connection, **f) -> int | None:
    """Insert a detected action; returns row id, or None if it was a duplicate."""
    f.setdefault("game", None)
    f.setdefault("market_type", None)
    try:
        cur = conn.execute(
            """INSERT INTO esports_sharp_actions
                 (wallet,tx_hash,condition_id,asset,title,slug,outcome,side,
                  game,market_type,their_price,size,usdc_size,traded_at,
                  detected_at,live_bid,live_ask)
               VALUES (:wallet,:tx_hash,:condition_id,:asset,:title,:slug,:outcome,
                  :side,:game,:market_type,:their_price,:size,:usdc_size,:traded_at,
                  :detected_at,:live_bid,:live_ask)""",
            f,
        )
        conn.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError:
        conn.rollback()
        return None


# --------------------------- market universe ---------------------------


def replace_esports_markets(conn: sqlite3.Connection, rows: list[dict]) -> int:
    """Upsert the swept esports market universe. rows: dicts with
    condition_id, game, title, market_type, closed. Returns count upserted."""
    import time as _t
    now = _t.time()
    conn.executemany(
        """INSERT INTO esports_markets
             (condition_id,game,title,market_type,closed,refreshed_at)
           VALUES (:condition_id,:game,:title,:market_type,:closed,:refreshed_at)
           ON CONFLICT(condition_id) DO UPDATE SET
             game=excluded.game, title=excluded.title,
             market_type=excluded.market_type, closed=excluded.closed,
             refreshed_at=excluded.refreshed_at""",
        [{**r, "refreshed_at": now} for r in rows],
    )
    conn.commit()
    return len(rows)


def lookup_market(conn: sqlite3.Connection, condition_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT game, market_type FROM esports_markets WHERE condition_id=?",
        (condition_id,),
    ).fetchone()


def market_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) c FROM esports_markets").fetchone()["c"]


def recent_actions(conn: sqlite3.Connection, limit: int = 50) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT a.*, s.name, s.follow FROM esports_sharp_actions a
           LEFT JOIN esports_sharps s ON s.wallet = a.wallet
           ORDER BY a.detected_at DESC LIMIT ?""",
        (limit,),
    ).fetchall()
