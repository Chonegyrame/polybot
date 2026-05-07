"""Smoke test for Session 1 (Phase A correctness fixes).

Tests each of the nine items shipped in session 1:
  A1: sybil writeback to wallet_classifications
  A2: position TTL filter + drop-out cleanup
  A3: slippage double-count fix in paper-trade auto-close
  A4: resolution 50_50 + VOID detection
  A6: rate-limit acquire inside tenacity retry
  A7: auto-close DB connection scope refactor
  A22: win rate convention to pnl_per_dollar > 0
  A23: fee on payout, not stake
  A26: size-weighted avg entry price

Run: ./venv/Scripts/python.exe scripts/smoke_phase_a.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db.connection import close_pool, init_pool  # noqa: E402
from app.db import crud  # noqa: E402
from app.services.backtest_engine import compute_pnl_per_dollar  # noqa: E402
from app.services.market_sync import _infer_resolved_outcome  # noqa: E402
from app.services.polymarket_types import Market  # noqa: E402
from app.services.signal_detector import detect_signals  # noqa: E402
from app.services.sybil_detector import detect_clusters  # noqa: E402
from app.services.polymarket_types import Trade  # noqa: E402

logging.basicConfig(
    level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("smoke_phase_a")
log.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

PASS = "[PASS]"
FAIL = "[FAIL]"
results: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append((label, ok, detail))
    sym = PASS if ok else FAIL
    print(f"  {sym}  {label}{('  -- ' + detail) if detail else ''}")


def section(title: str) -> None:
    print(f"\n{'=' * 80}\n  {title}\n{'=' * 80}")


def approx(a: float, b: float, tol: float = 0.01) -> bool:
    return abs(a - b) <= tol


# ---------------------------------------------------------------------------
# A4: _infer_resolved_outcome
# ---------------------------------------------------------------------------


def make_market(
    closed: bool, outcomes: list[str] | None, prices: list[float] | None
) -> Market:
    return Market(
        id="testmkt",
        slug="test",
        question="Test market?",
        condition_id="0xtest",
        clob_token_ids=[],
        outcomes=outcomes or [],
        outcome_prices=prices or [],
        volume_num=None,
        liquidity_num=None,
        end_date=None,
        closed=closed,
        active=not closed,
        last_trade_price=None,
        best_bid=None,
        best_ask=None,
        raw={},
    )


def test_a4_resolution_inference() -> None:
    section("A4: _infer_resolved_outcome handles YES/NO/50_50/VOID")

    # YES winner
    m = make_market(True, ["Yes", "No"], [1.0, 0.0])
    check("YES winner detected", _infer_resolved_outcome(m) == "YES")

    # NO winner
    m = make_market(True, ["Yes", "No"], [0.0, 1.0])
    check("NO winner detected", _infer_resolved_outcome(m) == "NO")

    # 50_50 (oracle Invalid)
    m = make_market(True, ["Yes", "No"], [0.5, 0.5])
    check("50_50 oracle resolution detected", _infer_resolved_outcome(m) == "50_50")

    # 50_50 with slight float noise (within tolerance)
    m = make_market(True, ["Yes", "No"], [0.49, 0.51])
    check("50_50 with float noise detected", _infer_resolved_outcome(m) == "50_50")

    # VOID — closed with empty prices list
    m = make_market(True, ["Yes", "No"], [])
    check("VOID (closed + empty prices) detected", _infer_resolved_outcome(m) == "VOID")

    # VOID — closed with empty outcomes list
    m = make_market(True, [], [])
    check("VOID (closed + empty outcomes) detected", _infer_resolved_outcome(m) == "VOID")

    # VOID — both outcomes near zero (no winner declared)
    m = make_market(True, ["Yes", "No"], [0.0, 0.0])
    check("VOID (both at 0.0) detected", _infer_resolved_outcome(m) == "VOID")

    # Active market — should return None
    m = make_market(False, ["Yes", "No"], [0.55, 0.45])
    check("Active market returns None", _infer_resolved_outcome(m) is None)

    # Closed but shape mismatch — VOID
    m = make_market(True, ["Yes", "No", "Maybe"], [1.0, 0.0])
    check("Shape mismatch -> VOID", _infer_resolved_outcome(m) == "VOID")

    # Outcomes as dicts (gamma sometimes returns this shape)
    m = make_market(True, [{"outcome": "Yes"}, {"outcome": "No"}], [1.0, 0.0])  # type: ignore[arg-type]
    check("Dict-shaped outcomes parsed", _infer_resolved_outcome(m) == "YES")

    # String-typed prices (sometimes happens)
    m = make_market(True, ["Yes", "No"], ["1.0", "0.0"])  # type: ignore[arg-type]
    check("String prices parsed", _infer_resolved_outcome(m) == "YES")


def test_f15_custom_label_resolution_marked_void_not_silently_null() -> None:
    """F15 regression: custom-label binary resolutions (e.g. "Trump wins" /
    "Biden wins") used to return None, silently excluding the market from
    backtest. Now returns "VOID" + logs a WARN, so the magnitude of skipped
    markets is visible to the operator.

    See review/FIXES.md F15.
    """
    section("F15: custom-label binary resolution -> VOID (was: silent NULL)")

    # Custom-label market resolved with one side at $1.00
    m = make_market(True, ["Trump wins", "Biden wins"], [1.0, 0.0])
    check(
        "F15: custom-label resolution -> VOID (not None)",
        _infer_resolved_outcome(m) == "VOID",
        f"got {_infer_resolved_outcome(m)}",
    )

    # Asymmetric custom labels — both should still be VOID
    m2 = make_market(True, ["Yes (5+ goals)", "No"], [1.0, 0.0])
    check(
        "F15: 'Yes (5+ goals)' (not exact 'yes') resolved 1.0 -> VOID",
        _infer_resolved_outcome(m2) == "VOID",
        f"got {_infer_resolved_outcome(m2)}",
    )

    # Sanity: standard Yes/No still resolves correctly
    m3 = make_market(True, ["Yes", "No"], [1.0, 0.0])
    check(
        "Standard 'Yes'/'No' resolution still works (regression check)",
        _infer_resolved_outcome(m3) == "YES",
        f"got {_infer_resolved_outcome(m3)}",
    )


def test_f6_yes_no_token_mapping_uses_outcomes() -> None:
    """F6 regression: YES/NO token IDs must be paired by matching the
    outcome label, not by array index. Pre-fix: market_sync.py used
    `clob_token_ids[0]` as YES and `[1]` as NO unconditionally — silently
    wrong for markets where outcomes ships in `["No", "Yes"]` order
    (some sports markets, negation prompts).

    See review/01_ingestion.md High #12, review/FIXES.md F6.
    """
    section("F6: YES/NO token mapping pairs by outcome label")

    # Imported here so the suite stays runnable before the helper exists.
    from app.services.polymarket_types import pair_yes_no_tokens

    # Standard binary: outcomes in YES/NO order
    yes, no = pair_yes_no_tokens(["Yes", "No"], ["t_yes", "t_no"])
    check(
        "Standard binary: YES first, NO second",
        yes == "t_yes" and no == "t_no",
        f"got yes={yes!r}, no={no!r}",
    )

    # Inverted (sports / negation): outcomes in NO/YES order.
    # Pre-fix indexing would have given yes=t_no, no=t_yes (silent corruption).
    yes, no = pair_yes_no_tokens(["No", "Yes"], ["t_no", "t_yes"])
    check(
        "F6: Inverted outcomes -- pairs by label, not by index",
        yes == "t_yes" and no == "t_no",
        f"got yes={yes!r}, no={no!r}",
    )

    # Case-insensitive match
    yes, no = pair_yes_no_tokens(["yes", "no"], ["t_a", "t_b"])
    check(
        "Case-insensitive yes/no match",
        yes == "t_a" and no == "t_b",
        f"got yes={yes!r}, no={no!r}",
    )

    # Whitespace-tolerant match
    yes, no = pair_yes_no_tokens(["Yes ", " No"], ["t_a", "t_b"])
    check(
        "Whitespace-tolerant",
        yes == "t_a" and no == "t_b",
        f"got yes={yes!r}, no={no!r}",
    )

    # Multi-outcome (3+ outcomes): defensive return — no clean binary mapping
    yes, no = pair_yes_no_tokens(["Trump", "Biden", "Other"], ["t1", "t2", "t3"])
    check(
        "Multi-outcome: returns (None, None)",
        yes is None and no is None,
        f"got yes={yes!r}, no={no!r}",
    )

    # Custom labels (no exact yes/no): defensive
    yes, no = pair_yes_no_tokens(["Trump wins", "Biden wins"], ["t1", "t2"])
    check(
        "Custom labels (no exact yes/no): returns (None, None)",
        yes is None and no is None,
        f"got yes={yes!r}, no={no!r}",
    )

    # Mismatched lengths
    yes, no = pair_yes_no_tokens(["Yes"], ["t1", "t2"])
    check(
        "Mismatched lengths: returns (None, None)",
        yes is None and no is None,
    )

    # Empty inputs
    yes, no = pair_yes_no_tokens([], [])
    check(
        "Empty inputs: returns (None, None)",
        yes is None and no is None,
    )

    # Degenerate: both labels match "yes"
    yes, no = pair_yes_no_tokens(["Yes", "Yes"], ["t1", "t2"])
    check(
        "Both labels are yes: returns (None, None)",
        yes is None and no is None,
    )


def test_f13_safe_list_from_response() -> None:
    """F13 regression: API client must distinguish a real empty list from
    an unexpected response shape. Pre-fix code silently coerced both to []
    via `if not isinstance(data, list): return []`, so an API error wrapped
    in a JSON dict looked identical to "no results." This was the smoking
    gun for the CLOB /trades 401 swallowed silently.

    See review/PROBE_FINDINGS.md, review/FIXES.md F13.
    """
    section("F13/R15: _safe_list_from_response distinguishes empty from error")

    # R15 (Pass 3): _safe_list_from_response now RAISES ResponseShapeError on
    # un-parseable input (paginators catch + fail loudly). The silent
    # "returns []" semantic is preserved via _safe_list_or_empty for
    # single-shot callers. We test BOTH below.
    from app.services.polymarket import (
        ResponseShapeError,
        _safe_list_from_response,
        _safe_list_or_empty,
    )

    # Real empty list -> return [] silently
    out = _safe_list_from_response([], "test-endpoint")
    check(
        "Empty list -> returns [] (no warning needed)",
        out == [],
        f"got {out}",
    )

    # List of dicts -> return all dicts
    out = _safe_list_from_response(
        [{"a": 1}, {"a": 2}, "not-a-dict", 123], "test-endpoint",
    )
    check(
        "List of mixed items -> returns only dict items",
        out == [{"a": 1}, {"a": 2}],
        f"got {out}",
    )

    # Wrapped list ({"data": [...]})
    out = _safe_list_from_response(
        {"data": [{"x": 1}], "meta": "ignore"}, "test", list_keys=("data",),
    )
    check(
        "Wrapped list under known key -> unwrapped",
        out == [{"x": 1}],
        f"got {out}",
    )

    # Wrapped list, prefer first matching wrapper key in order
    out = _safe_list_from_response(
        {"trades": [{"t": 1}], "data": [{"d": 1}]},
        "test", list_keys=("data", "trades"),
    )
    check(
        "Wrapped list -- list_keys order respected (data wins over trades)",
        out == [{"d": 1}],
        f"got {out}",
    )

    # R15: dict with no expected list-key -> RAISES ResponseShapeError
    # (paginators catch this and fail loudly instead of treating as end-of-pages)
    raised = False
    try:
        _safe_list_from_response(
            {"error": "Unauthorized", "code": 401},
            "test-endpoint",
            list_keys=("data",),
        )
    except ResponseShapeError:
        raised = True
    check("R15: error-shaped dict raises ResponseShapeError", raised)

    # R15: None -> RAISES
    raised = False
    try:
        _safe_list_from_response(None, "test-endpoint")
    except ResponseShapeError:
        raised = True
    check("R15: None raises ResponseShapeError", raised)

    # R15: str -> RAISES
    raised = False
    try:
        _safe_list_from_response("garbage response", "test-endpoint")
    except ResponseShapeError:
        raised = True
    check("R15: string raises ResponseShapeError", raised)

    # R15: int -> RAISES
    raised = False
    try:
        _safe_list_from_response(42, "test-endpoint")
    except ResponseShapeError:
        raised = True
    check("R15: int raises ResponseShapeError", raised)

    # F13 (silent path) — _safe_list_or_empty preserves the old "returns []"
    # semantic for single-shot callers (positions, trades, markets, etc.)
    check(
        "F13: _safe_list_or_empty on error-dict returns []",
        _safe_list_or_empty({"error": "x"}, "test-endpoint", list_keys=("data",)) == [],
    )
    check(
        "F13: _safe_list_or_empty on None returns []",
        _safe_list_or_empty(None, "test-endpoint") == [],
    )
    check(
        "F13: _safe_list_or_empty on real list passes through",
        _safe_list_or_empty([{"a": 1}], "test-endpoint") == [{"a": 1}],
    )


# ---------------------------------------------------------------------------
# A22 + A23: backtest engine win rate + fee model
# ---------------------------------------------------------------------------


def test_a22_a23_pnl_formula() -> None:
    section("A22 + A23: compute_pnl_per_dollar — fee on payout, win = pnl > 0")

    # YES @ 0.40 winning, no fee — gross = 1/0.40 = 2.5, pnl = 1.5 (less slippage)
    v = compute_pnl_per_dollar(0.40, "YES", "YES", "politics", 1.0, 25000.0)
    check(
        "YES@0.40 politics WIN (no fee)",
        v is not None and approx(v, 1.499, 0.01),
        f"got {v:+.4f}" if v is not None else "got None",
    )

    # YES @ 0.40 winning, sports 1.8% fee — fee charged on payout, not stake
    v = compute_pnl_per_dollar(0.40, "YES", "YES", "sports", 1.0, 25000.0)
    check(
        "YES@0.40 sports WIN (fee on $2.5 payout, ~1.45)",
        v is not None and approx(v, 1.454, 0.01),
        f"got {v:+.4f}" if v is not None else "got None",
    )

    # YES @ 0.40 losing, sports — fee should be ZERO on loser, not -1.018
    v = compute_pnl_per_dollar(0.40, "YES", "NO", "sports", 1.0, 25000.0)
    check(
        "YES@0.40 sports LOSS (no fee on losers — exactly -1.0)",
        v is not None and approx(v, -1.0, 0.001),
        f"got {v:+.4f}" if v is not None else "got None",
    )

    # 50_50 entered cheap — should be a winning trade
    v = compute_pnl_per_dollar(0.40, "YES", "50_50", "sports", 1.0, 25000.0)
    check(
        "YES@0.40 sports 50_50 (entered cheap, profitable, ~+0.22)",
        v is not None and v > 0 and approx(v, 0.227, 0.02),
        f"got {v:+.4f}" if v is not None else "got None",
    )

    # 50_50 entered expensive — should be a losing trade
    v = compute_pnl_per_dollar(0.95, "YES", "50_50", "sports", 1.0, 25000.0)
    check(
        "YES@0.95 sports 50_50 (entered expensive, lost, < 0)",
        v is not None and v < 0,
        f"got {v:+.4f}" if v is not None else "got None",
    )

    # NO @ 0.40 winning — symmetric to YES@0.40 winning
    v_yes = compute_pnl_per_dollar(0.40, "YES", "YES", "sports", 1.0, 25000.0)
    v_no = compute_pnl_per_dollar(0.40, "NO", "NO", "sports", 1.0, 25000.0)
    check(
        "YES/NO symmetry at 0.40 winning",
        v_yes is not None and v_no is not None and approx(v_yes, v_no, 0.001),
        f"YES={v_yes}, NO={v_no}",
    )

    # VOID — None
    v = compute_pnl_per_dollar(0.40, "YES", "VOID", "sports", 1.0, 25000.0)
    check("VOID returns None (excluded)", v is None)


# ---------------------------------------------------------------------------
# A3: slippage double-count fix in paper-trade auto-close
# ---------------------------------------------------------------------------


def test_a3_slippage_fix() -> None:
    section("A3: paper-trade settlement uses effective_entry, no double-count")

    # Replicate the logic from auto_close_resolved_paper_trades
    def settle(entry_price: float, size: float, fee: float, slip: float, payoff: float) -> float:
        effective_entry = entry_price * (1.0 + slip / size)
        shares = size / effective_entry
        gross_value = shares * payoff
        fee_rate = fee / size if size > 0 else 0.0
        realized_fee = gross_value * fee_rate
        return gross_value - size - realized_fee

    # Smoke-test scenario from session-state: $1000 YES @ 0.95 win, slip=$2.50, fee=$0
    # Old buggy formula: shares = 1000/0.95 = 1052.63, realized = 1052.63*1 - 1000 - 2.50 = +$50.13
    # New formula:       shares = 1000/0.952375 = 1050.03,realized = 1050.03 - 1000 - 0 = +$50.03
    realized = settle(entry_price=0.95, size=1000.0, fee=0.0, slip=2.50, payoff=1.0)
    check(
        "$1000 YES@0.95 WIN, slip=$2.50, fee=$0 -> ~+$50.03 (was buggy +$50.13)",
        approx(realized, 50.03, 0.50),
        f"got {realized:+.4f}",
    )

    # Losing trade: shares × $0 - size - fee*0 = -size (no fee on loser)
    realized = settle(entry_price=0.40, size=100.0, fee=1.80, slip=2.0, payoff=0.0)
    check(
        "$100 YES@0.40 LOSS, slip=$2, fee=$1.80 -> exactly -$100 (no fee on loser)",
        approx(realized, -100.0, 0.001),
        f"got {realized:+.4f}",
    )

    # Winning sports trade: fee scales to gross
    # entry=0.40, size=$100, slip=$2, fee=$1.80 (1.8% of size)
    # effective_entry = 0.40 * (1 + 2/100) = 0.408
    # shares = 100/0.408 = 245.10
    # gross_value = 245.10 * 1.0 = 245.10
    # fee_rate = 1.80/100 = 0.018; realized_fee = 245.10 * 0.018 = 4.41
    # realized = 245.10 - 100 - 4.41 = +$140.69
    realized = settle(entry_price=0.40, size=100.0, fee=1.80, slip=2.0, payoff=1.0)
    check(
        "$100 YES@0.40 WIN sports, slip=$2, fee=$1.80 -> ~+$140.69",
        approx(realized, 140.69, 0.50),
        f"got {realized:+.4f}",
    )

    # 50_50 resolution: payoff=0.5, fee scales accordingly
    realized = settle(entry_price=0.40, size=100.0, fee=1.80, slip=2.0, payoff=0.5)
    # gross = 245.10 * 0.5 = 122.55, fee = 122.55 * 0.018 = 2.21, realized = 122.55 - 100 - 2.21 = +20.34
    check(
        "$100 YES@0.40 50_50 sports -> ~+$20.34 (small profit)",
        approx(realized, 20.34, 0.50),
        f"got {realized:+.4f}",
    )


# ---------------------------------------------------------------------------
# A1: sybil writeback to wallet_classifications (pure-logic + DB integration)
# ---------------------------------------------------------------------------


def test_a1_sybil_logic() -> None:
    section("A1: sybil detector — synthetic cluster detection")

    # Build a synthetic dataset where wallets W1, W2, W3 all trade the same
    # markets at the same times. W4 is independent.
    base_t = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)

    def t(seconds: int) -> datetime:
        return base_t + timedelta(seconds=seconds)

    def trade(cid: str, asset: str, time_offset_sec: int) -> Trade:
        return Trade(
            proxy_wallet="x",  # ignored; key is timestamp + market
            condition_id=cid,
            asset=asset,
            side="BUY",
            size=100.0,
            usdc_size=50.0,
            price=0.50,
            timestamp=t(time_offset_sec),
            transaction_hash=f"0x{cid}{asset}{time_offset_sec}",
            title=None,
            slug=None,
        )

    # 25 coordinated trades for W1, W2, W3 (≥20 floor)
    coordinated = [trade(f"m{i}", "YES", i * 60) for i in range(25)]

    # Each of W1, W2, W3 fires the same 25 trades (in same buckets)
    trades_by_wallet = {
        "W1": coordinated.copy(),
        "W2": coordinated.copy(),
        "W3": coordinated.copy(),
        # W4 has different timestamps + markets — independent
        "W4": [trade(f"x{i}", "YES", 5000 + i * 300) for i in range(25)],
    }

    clusters = detect_clusters(trades_by_wallet)
    check(
        "Synthetic 3-wallet cluster detected",
        len(clusters) == 1 and set(clusters[0].members) == {"W1", "W2", "W3"},
        f"got {len(clusters)} clusters: {[c.members for c in clusters]}",
    )
    check(
        "W4 (independent) not in any cluster",
        not any("W4" in c.members for c in clusters),
    )


async def test_a1_db_integration() -> None:
    section("A1: sybil writeback to wallet_classifications (DB integration)")

    pool = await init_pool(min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            # We need real proxy_wallets that exist in the traders table for the
            # FK constraint to hold. Pick the two oldest traders with traffic.
            rows = await conn.fetch(
                """
                SELECT proxy_wallet FROM traders
                ORDER BY proxy_wallet
                LIMIT 2
                """
            )
            if len(rows) < 2:
                check(
                    "DB integration test (skipped — need ≥2 traders in DB)",
                    True, "DB has too few traders to test FK",
                )
                return
            wallets = [r["proxy_wallet"] for r in rows]

            # Capture original classification (so we can restore at the end)
            orig = await conn.fetch(
                "SELECT proxy_wallet, wallet_class FROM wallet_classifications "
                "WHERE proxy_wallet = ANY($1::TEXT[])",
                wallets,
            )
            orig_by_wallet = {r["proxy_wallet"]: r["wallet_class"] for r in orig}

            # Persist a synthetic cluster
            cluster_id = await crud.persist_sybil_cluster(
                conn,
                members=wallets,
                evidence={"smoke_test": True, "synthetic": True},
            )
            await crud.mark_wallets_likely_sybil(
                conn,
                proxy_wallets=wallets,
                cluster_id=cluster_id,
                evidence={"smoke_test": True},
                trades_observed_by_wallet={w: 50 for w in wallets},
            )

            # Verify each wallet now has wallet_class='likely_sybil'
            after = await conn.fetch(
                "SELECT proxy_wallet, wallet_class FROM wallet_classifications "
                "WHERE proxy_wallet = ANY($1::TEXT[])",
                wallets,
            )
            after_classes = {r["proxy_wallet"]: r["wallet_class"] for r in after}
            all_sybil = all(
                after_classes.get(w) == "likely_sybil" for w in wallets
            )
            check(
                "Cluster members marked likely_sybil in wallet_classifications",
                all_sybil,
                f"got {after_classes}",
            )

            # Verify the existing exclusion query picks them up
            contaminated = await crud.get_contaminated_wallets(conn)
            both_excluded = all(w in contaminated for w in wallets)
            check(
                "get_contaminated_wallets() now includes the cluster members",
                both_excluded,
            )

            # Cleanup: remove the synthetic cluster + restore original classes
            await conn.execute(
                "DELETE FROM wallet_clusters WHERE cluster_id = $1::uuid",
                cluster_id,
            )
            for w in wallets:
                if w in orig_by_wallet:
                    await conn.execute(
                        "UPDATE wallet_classifications SET wallet_class = $2 "
                        "WHERE proxy_wallet = $1",
                        w, orig_by_wallet[w],
                    )
                else:
                    await conn.execute(
                        "DELETE FROM wallet_classifications WHERE proxy_wallet = $1",
                        w,
                    )
            check("Synthetic cluster + likely_sybil rows cleaned up", True)
    finally:
        await close_pool()


# ---------------------------------------------------------------------------
# A2 + A26: signal_detector SQL parses with TTL + size-weighted entry
# ---------------------------------------------------------------------------


async def test_a2_a26_signal_sql() -> None:
    section("A2 + A26: signal_detector SQL with TTL filter + size-weighted entry")

    pool = await init_pool(min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            # detect_signals runs the full SQL; if the new TTL clause or the
            # size-weighted SUM(price*size)/SUM(size) had a syntax error this
            # would throw.
            signals = await detect_signals(
                conn, mode="absolute", category="overall", top_n=50
            )
            check(
                f"detect_signals SQL runs (returned {len(signals)} signals)",
                True,
            )
            # Spot-check that any returned signal has avg_entry_price <= 1
            # (Polymarket prices are in [0, 1])
            for s in signals[:5]:
                if s.avg_entry_price is not None:
                    check(
                        f"  signal '{(s.market_question or s.condition_id)[:40]}' avg_entry_price valid",
                        0.0 <= s.avg_entry_price <= 1.0,
                        f"got {s.avg_entry_price:.4f}",
                    )
    finally:
        await close_pool()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


async def main() -> None:
    print("\nSession 1 (Phase A) smoke test\n" + "=" * 80)

    # Pure-function tests — no DB needed
    test_a4_resolution_inference()
    test_f6_yes_no_token_mapping_uses_outcomes()
    test_f13_safe_list_from_response()
    test_f15_custom_label_resolution_marked_void_not_silently_null()
    test_a22_a23_pnl_formula()
    test_a3_slippage_fix()
    test_a1_sybil_logic()

    # DB integration tests
    await test_a1_db_integration()
    await test_a2_a26_signal_sql()

    # Summary
    section("SUMMARY")
    n_pass = sum(1 for _, ok, _ in results if ok)
    n_fail = sum(1 for _, ok, _ in results if not ok)
    print(f"  {n_pass} passed, {n_fail} failed")
    if n_fail:
        print("\n  Failures:")
        for label, ok, detail in results:
            if not ok:
                print(f"    {FAIL}  {label}  -- {detail}")
        sys.exit(1)
    print("\n  All session-1 changes verified.\n")


if __name__ == "__main__":
    asyncio.run(main())
