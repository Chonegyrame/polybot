"""Fair-value model for a BTC up/down window.

A window resolves "Up" if the BTC price at the close is >= the price at the
open. With the open price fixed at candle start, the only question is where the
price lands at expiry relative to that line. Over a 5-15 minute horizon BTC is
very close to a driftless random walk, so model log-returns as

    ln(S_T / S) ~ Normal(0, sigma^2 * tau)

where S is the current spot, tau is seconds left, and sigma is volatility per
sqrt-second. Then

    P(up) = P(S_T >= O)
          = Phi( ln(S / O) / (sigma * sqrt(tau)) )

The drift / -0.5*sigma^2*tau convexity term is ~1e-6 over five minutes, far
below the order-book tick, so we drop it. (Resolution counts ties as "Up", so
at expiry S == O resolves Up — handled in the tau<=0 limit.)
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

SECONDS_PER_YEAR = 365.0 * 24.0 * 3600.0


def _phi(x: float) -> float:
    """Standard normal CDF."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def annualized_to_per_sqrt_sec(annual_vol: float) -> float:
    """Convert an annualized vol (e.g. 0.70 = 70%) to per-sqrt-second."""
    return annual_vol / math.sqrt(SECONDS_PER_YEAR)


# Default anchor: ~70% annualized, a reasonable resting BTC vol. The runner
# overrides this with a live realized estimate; this is only the cold-start seed.
DEFAULT_SIGMA_PER_SQRT_SEC = annualized_to_per_sqrt_sec(0.70)


def fair_prob_up(
    spot: float,
    open_price: float,
    seconds_left: float,
    sigma_per_sqrt_sec: float = DEFAULT_SIGMA_PER_SQRT_SEC,
) -> float:
    """P(window resolves Up) given live spot, the fixed open, and time left."""
    if open_price <= 0 or spot <= 0:
        return 0.5
    if seconds_left <= 0:
        # At expiry, ">=" means a tie resolves Up.
        return 1.0 if spot >= open_price else 0.0
    sigma = max(sigma_per_sqrt_sec, 1e-12)
    z = math.log(spot / open_price) / (sigma * math.sqrt(seconds_left))
    return _phi(z)


@dataclass
class RollingVol:
    """EWMA estimate of BTC volatility per sqrt-second from a tick stream.

    Each update folds the latest per-second return variance (r^2 / dt) into an
    exponentially-weighted mean, so the estimate adapts to regime changes
    (quiet vs. volatile) without a fixed lookback window.
    """

    halflife_seconds: float = 120.0
    _var_per_sec: float | None = None
    _last_price: float | None = None
    _last_ts: float | None = None

    def __post_init__(self) -> None:
        if self._var_per_sec is None:
            self._var_per_sec = DEFAULT_SIGMA_PER_SQRT_SEC ** 2

    def update(self, price: float, ts: float | None = None) -> None:
        now = ts if ts is not None else time.time()
        if self._last_price is not None and self._last_ts is not None:
            dt = now - self._last_ts
            if dt > 0 and price > 0 and self._last_price > 0:
                r = math.log(price / self._last_price)
                inst_var = (r * r) / dt  # per-second variance of this step
                # EWMA weight derived from the gap so irregular tick spacing
                # is handled correctly (decay = 0.5 ** (dt / halflife)).
                decay = 0.5 ** (dt / self.halflife_seconds)
                self._var_per_sec = decay * self._var_per_sec + (1 - decay) * inst_var
        self._last_price = price
        self._last_ts = now

    @property
    def sigma_per_sqrt_sec(self) -> float:
        return math.sqrt(max(self._var_per_sec or 0.0, 1e-24))
