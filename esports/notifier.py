"""Local desktop notifier for esports sharp moves — popup + sound.

A standalone process (run via notifier.bat) that watches the local esports DB
the tracker writes and fires an always-on-top, NON-focus-stealing popup (plus a
short sound) the moment a tracked sharp move matches a trigger:

  • Big position : a FOLLOW wallet enters with notional >= $5k
  • Burst        : >= 3 distinct sharps BUY the same market within 5 min
  • Exit         : >= 2 sharps SELL a market (before it resolves)

Design:
  - Reads the SQLite DB read-only (mode=ro) — never contends with the tracker.
  - Baselines on startup (max id) so it only alerts on moves from launch onward.
  - The popup is WS_EX_NOACTIVATE on Windows, so it draws on top WITHOUT stealing
    keyboard/mouse focus — it won't interrupt a game's input or alt-tab you out.
    It appears over normal windows + browser-fullscreen + BORDERLESS games; it
    can't draw over a game in true EXCLUSIVE fullscreen (Windows limitation) —
    that's what the sound is for (sound always gets through).

Trigger detection (detect_alerts) is pure-ish over the DB so it's testable
headlessly; the Tk popup only runs in main().

Run:  notifier.bat     (or:  python -m esports.notifier)
"""

from __future__ import annotations

import os
import sqlite3
import sys
import threading
import time

from esports.consensus import market_label, match_of
from esports.db import DEFAULT_DB

sys.stdout.reconfigure(encoding="utf-8")

CFG = {
    "big_usd": float(os.getenv("ESPORTS_ALERT_BIG_USD", "5000")),
    "burst_wallets": int(os.getenv("ESPORTS_ALERT_BURST_WALLETS", "3")),
    "burst_window_min": float(os.getenv("ESPORTS_ALERT_BURST_WINDOW_MIN", "5")),
    "exit_count": int(os.getenv("ESPORTS_ALERT_EXIT_COUNT", "2")),
    "rearm_s": float(os.getenv("ESPORTS_ALERT_REARM_SECONDS", "900")),  # don't re-alert a market for 15 min
    "poll_s": float(os.getenv("ESPORTS_ALERT_POLL_SECONDS", "4")),
    "sound": os.getenv("ESPORTS_ALERT_SOUND", "1") != "0",
    "popup_seconds": float(os.getenv("ESPORTS_ALERT_POPUP_SECONDS", "11")),
}


# --------------------------- data + detection ---------------------------

def _ro_conn() -> sqlite3.Connection | None:
    if not DEFAULT_DB.exists():
        return None
    conn = sqlite3.connect(f"file:{DEFAULT_DB}?mode=ro", uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def _notional(r) -> float | None:
    if r["usdc_size"] is not None:
        return r["usdc_size"]
    if r["size"] is not None and r["their_price"] is not None:
        return r["size"] * r["their_price"]
    return None


def _fmt_usd(n: float | None) -> str:
    if n is None:
        return "—"
    a = abs(n)
    if a >= 1_000_000:
        return f"${a/1_000_000:.2f}M"
    if a >= 1_000:
        return f"${a/1000:.1f}k"
    return f"${a:.0f}"


def _teams(title: str | None) -> str:
    _, disp = match_of(title)
    return disp or (title or "")[:40]


def _distinct_recent(conn, cid: str, side: str, window_s: float) -> int:
    cutoff = time.time() - window_s
    row = conn.execute(
        "SELECT COUNT(DISTINCT wallet) c FROM esports_sharp_actions "
        "WHERE condition_id=? AND side=? AND traded_at>=?",
        (cid, side, cutoff),
    ).fetchone()
    return row["c"] if row else 0


def _is_resolved(conn, cid: str) -> bool:
    row = conn.execute(
        "SELECT resolved_outcome FROM esports_markets WHERE condition_id=?", (cid,)
    ).fetchone()
    return bool(row and row["resolved_outcome"])


def _rearm(store: dict, key: str, now: float, rearm_s: float) -> bool:
    if key not in store or (now - store[key]) > rearm_s:
        store[key] = now
        return True
    return False


def max_action_id(conn) -> int:
    row = conn.execute("SELECT COALESCE(MAX(id), 0) m FROM esports_sharp_actions").fetchone()
    return row["m"] if row else 0


def detect_alerts(conn, last_id: int, state: dict, cfg: dict) -> tuple[list[dict], int]:
    """Return (alerts, new_last_id) for actions with id > last_id."""
    rows = conn.execute(
        """SELECT a.*, s.name, s.follow FROM esports_sharp_actions a
           LEFT JOIN esports_sharps s ON s.wallet = a.wallet
           WHERE a.id > ? ORDER BY a.id""",
        (last_id,),
    ).fetchall()
    alerts: list[dict] = []
    buys: dict[str, sqlite3.Row] = {}
    sells: dict[str, sqlite3.Row] = {}
    for r in rows:
        last_id = r["id"]
        n = _notional(r)
        name = r["name"] or (r["wallet"][:8] + "…")
        if r["side"] == "BUY" and r["follow"] and n is not None and n >= cfg["big_usd"]:
            alerts.append({
                "kind": "big", "accent": "#21d07a",
                "title": f"💰 Big position · {_fmt_usd(n)}",
                "lines": [f"{name} bought {r['outcome'] or '?'}",
                          f"{_teams(r['title'])} · {market_label(r['title'], r['market_type'])}",
                          f"@ {r['their_price']:.2f}" if r["their_price"] is not None else ""],
            })
        if r["side"] == "BUY" and r["condition_id"]:
            buys[r["condition_id"]] = r
        elif r["side"] == "SELL" and r["condition_id"]:
            sells[r["condition_id"]] = r

    now = time.time()
    win = cfg["burst_window_min"] * 60
    for cid, r in buys.items():
        c = _distinct_recent(conn, cid, "BUY", win)
        if c >= cfg["burst_wallets"] and _rearm(state["burst"], cid, now, cfg["rearm_s"]):
            name = r["name"] or (r["wallet"][:8] + "…")
            alerts.append({
                "kind": "burst", "accent": "#21d07a",
                "title": f"🔥 {c} sharps piling in",
                "lines": [_teams(r["title"]),
                          f"{market_label(r['title'], r['market_type'])} · on {r['outcome'] or '?'}",
                          f"latest: {name} @ {r['their_price']:.2f}" if r["their_price"] is not None else f"latest: {name}"],
            })
    for cid, r in sells.items():
        if _is_resolved(conn, cid):
            continue
        c = _distinct_recent(conn, cid, "SELL", win)
        if c >= cfg["exit_count"] and _rearm(state["exit"], cid, now, cfg["rearm_s"]):
            alerts.append({
                "kind": "exit", "accent": "#f0506e",
                "title": f"⚠ {c} sharps exiting",
                "lines": [_teams(r["title"]),
                          f"{market_label(r['title'], r['market_type'])} · {r['outcome'] or '?'}",
                          "before resolution"],
            })
    return alerts, last_id


# --------------------------- sound + popup ---------------------------

def _play_sound(kind: str) -> None:
    try:
        import winsound
        if kind == "exit":
            winsound.Beep(660, 150); winsound.Beep(440, 200)
        else:
            winsound.Beep(880, 120); winsound.Beep(1175, 170)
    except Exception:  # noqa: BLE001 — non-Windows or no audio; popup still works
        pass


def _make_noactivate(win) -> None:
    """Windows: mark the popup WS_EX_NOACTIVATE + tool-window so it shows on top
    without stealing focus or appearing in the taskbar/alt-tab."""
    try:
        import ctypes
        GWL_EXSTYLE = -20
        WS_EX_NOACTIVATE = 0x08000000
        WS_EX_TOOLWINDOW = 0x00000080
        WS_EX_TOPMOST = 0x00000008
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(
            hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST)
    except Exception:  # noqa: BLE001
        pass


class Notifier:
    """Tk-based popup stack in the top-right corner."""

    WIDTH = 320
    MARGIN = 16
    GAP = 10

    def __init__(self, cfg: dict):
        import tkinter as tk
        self.tk = tk
        self.cfg = cfg
        self.root = tk.Tk()
        self.root.withdraw()  # hidden controller window
        self.active: list = []

    def _reflow(self) -> None:
        y = self.MARGIN
        sw = self.root.winfo_screenwidth()
        x = sw - self.WIDTH - self.MARGIN
        for top in self.active:
            try:
                top.geometry(f"+{x}+{y}")
                y += top.winfo_height() + self.GAP
            except Exception:  # noqa: BLE001
                pass

    def show(self, alert: dict) -> None:
        tk = self.tk
        top = tk.Toplevel(self.root)
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        top.configure(bg="#23262d")
        frame = tk.Frame(top, bg="#16181d", padx=0, pady=0)
        frame.pack(fill="both", expand=True, padx=1, pady=1)
        # accent stripe
        tk.Frame(frame, bg=alert["accent"], width=4).pack(side="left", fill="y")
        body = tk.Frame(frame, bg="#16181d", padx=12, pady=10)
        body.pack(side="left", fill="both", expand=True)
        tk.Label(body, text=alert["title"], bg="#16181d", fg=alert["accent"],
                 font=("Segoe UI", 11, "bold"), anchor="w", justify="left").pack(anchor="w")
        for ln in alert["lines"]:
            if not ln:
                continue
            tk.Label(body, text=ln, bg="#16181d", fg="#d6d9e0",
                     font=("Segoe UI", 9), anchor="w", justify="left",
                     wraplength=self.WIDTH - 30).pack(anchor="w")

        def close(_=None):
            if top in self.active:
                self.active.remove(top)
            try:
                top.destroy()
            except Exception:  # noqa: BLE001
                pass
            self._reflow()

        top.bind("<Button-1>", close)
        for child in (frame, body):
            child.bind("<Button-1>", close)
        self.active.append(top)
        top.update_idletasks()
        _make_noactivate(top)
        self._reflow()
        top.after(int(self.cfg["popup_seconds"] * 1000), close)
        if self.cfg["sound"]:
            threading.Thread(target=_play_sound, args=(alert["kind"],), daemon=True).start()

    def poll(self, st: dict) -> None:
        try:
            conn = _ro_conn()
            if conn is not None:
                try:
                    alerts, st["last_id"] = detect_alerts(conn, st["last_id"], st["state"], self.cfg)
                finally:
                    conn.close()
                for a in alerts:
                    self.show(a)
        except Exception as e:  # noqa: BLE001 — never let the loop die
            print(f"  ! poll error: {type(e).__name__}: {str(e)[:90]}")
        self.root.after(int(self.cfg["poll_s"] * 1000), self.poll, st)

    def demo(self) -> None:
        """Fire one sample of each alert kind so you can check the look, the
        sound, and whether it draws over your game (run with --test)."""
        samples = [
            {"kind": "burst", "accent": "#21d07a", "title": "🔥 3 sharps piling in",
             "lines": ["ThunderTalk Gaming vs LGD Gaming", "Game 4 winner · on LGD Gaming",
                       "latest: ColinHe @ 0.43"]},
            {"kind": "big", "accent": "#21d07a", "title": "💰 Big position · $35.4k",
             "lines": ["Zywoo123 bought LGD Gaming", "ThunderTalk Gaming vs LGD Gaming · Match winner · BO5",
                       "@ 0.55"]},
            {"kind": "exit", "accent": "#f0506e", "title": "⚠ 2 sharps exiting",
             "lines": ["M80 vs Lynn Vision", "Match winner · BO1 · Lynn Vision", "before resolution"]},
        ]
        for i, a in enumerate(samples):
            self.root.after(700 + i * 1200, self.show, a)

    def run(self, test: bool = False) -> None:
        # Baseline so we only alert on moves from launch onward.
        conn = _ro_conn()
        base = max_action_id(conn) if conn is not None else 0
        if conn is not None:
            conn.close()
        st = {"last_id": base, "state": {"burst": {}, "exit": {}}}
        print(f"esports notifier: watching {DEFAULT_DB} from action id {base} "
              f"| big>={_fmt_usd(self.cfg['big_usd'])}, burst>={self.cfg['burst_wallets']}/"
              f"{self.cfg['burst_window_min']:g}min, exits>={self.cfg['exit_count']} "
              f"| sound={'on' if self.cfg['sound'] else 'off'}")
        if test:
            print("--test: firing sample popups…")
            self.demo()
        self.root.after(500, self.poll, st)
        self.root.mainloop()


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="Esports sharp-move desktop notifier.")
    ap.add_argument("--test", action="store_true", help="fire sample popups on start")
    args = ap.parse_args()
    try:
        Notifier(CFG).run(test=args.test)
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
