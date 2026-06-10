"""
Discord auto-reply controller -- Issue #177 (slice 2), ADR-0006.

Sits on the Cerebral side of the ``set_draft_callback`` seam exposed
by ``plugins/discord_user.py``. Every inbound Discord DM lands here:

  - sender NOT on the per-profile allowlist  --> dropped silently.
    Inbound DMs are NOT queued; the action queue is for outbound
    proposals Felix wants to take (each row carries a tool_name +
    tool_args so Approve can dispatch), not a notification stream.
  - sender ON the allowlist  --> the controller below runs the
    detection-mitigation gauntlet, then drives Cerebral's LLM pipeline
    and emits a real reply via the plugin's internal send path. Any
    gauntlet failure (sleep-hours window, per-channel rate-limit
    budget exhausted) drops the event rather than queueing -- queueing
    invites burst-reply detection.

Detection-mitigation defaults (all on by default per ADR-0006):

  - **Per-channel serialisation** via an ``asyncio.Lock`` keyed by
    channel id, so two near-simultaneous DMs on the same channel run
    in order rather than in parallel.
  - **Per-channel rate-limit** (token-bucket-ish: a rolling window of
    timestamps, oldest evicted when the window closes). Default budget:
    3 replies per 60 s window.
  - **Reply-delay distribution** sampled from a clamped log-normal.
    Default: mean ~5 s, sigma 0.5 (in log-space), clamped to [2, 15] s.
    Mocked-clock tests assert the sampler actually varies; the live-
    verify gate proves the wall-clock shape feels human.
  - **Typing indicator** emitted before sending -- one POST to
    ``/channels/{id}/typing`` per reply (Discord's typing indicator
    naturally decays after ~10 s, which is inside our delay clamp).
  - **Sleep-hours window** -- when configured AND the current local
    time is inside the window, even allowlisted senders fall through
    to draft surfacing. Window is configurable per profile, off by
    default (matching the acceptance criterion: defaults all "on"
    EXCEPT sleep-hours).

Settings live in ``profiles.discord_user_settings``; defaults below
apply when no override is recorded. The ``DiscordAutoReplyController``
constructor takes seams for the clock + RNG + LLM pipeline so the test
suite drives the whole thing deterministically without touching the
network.
"""
from __future__ import annotations

import asyncio
import logging
import math
import random
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional, Protocol

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Settings -- defaults + per-profile overrides
# ---------------------------------------------------------------------------

SETTING_DELAY_MIN_S = "delay_min_s"
SETTING_DELAY_MAX_S = "delay_max_s"
SETTING_DELAY_MEAN_S = "delay_mean_s"
SETTING_DELAY_SIGMA = "delay_sigma"
SETTING_RATE_LIMIT_MAX = "rate_limit_max"
SETTING_RATE_LIMIT_WINDOW_S = "rate_limit_window_s"
SETTING_TYPING_INDICATOR = "typing_indicator"
SETTING_SLEEP_START_HOUR = "sleep_start_hour"
SETTING_SLEEP_END_HOUR = "sleep_end_hour"

_DEFAULTS: dict[str, str] = {
    SETTING_DELAY_MIN_S: "2.0",
    SETTING_DELAY_MAX_S: "15.0",
    SETTING_DELAY_MEAN_S: "5.0",
    SETTING_DELAY_SIGMA: "0.5",
    SETTING_RATE_LIMIT_MAX: "3",
    SETTING_RATE_LIMIT_WINDOW_S: "60",
    SETTING_TYPING_INDICATOR: "1",
    SETTING_SLEEP_START_HOUR: "",  # empty = off
    SETTING_SLEEP_END_HOUR: "",
}

ALLOWED_SETTING_KEYS: frozenset[str] = frozenset(_DEFAULTS)


@dataclass(frozen=True)
class AutoReplySettings:
    delay_min_s: float
    delay_max_s: float
    delay_mean_s: float
    delay_sigma: float
    rate_limit_max: int
    rate_limit_window_s: float
    typing_indicator: bool
    sleep_start_hour: Optional[int]   # 0..23, None = sleep-hours off
    sleep_end_hour: Optional[int]


def _parse_float(raw: str, fallback: float) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return fallback


def _parse_int(raw: str, fallback: int) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return fallback


def _parse_bool(raw: str, fallback: bool) -> bool:
    if raw is None:
        return fallback
    s = str(raw).strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off"):
        return False
    return fallback


def _parse_hour(raw: str) -> Optional[int]:
    """Parse a 0..23 hour, or None for "off" / unset / garbage."""
    if raw is None or raw == "":
        return None
    try:
        h = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    if 0 <= h <= 23:
        return h
    return None


def settings_from_overrides(overrides: dict[str, str]) -> AutoReplySettings:
    """Merge persisted overrides with module defaults into a typed view."""
    def g(k: str) -> str:
        return overrides.get(k, _DEFAULTS[k])

    return AutoReplySettings(
        delay_min_s=_parse_float(g(SETTING_DELAY_MIN_S), 2.0),
        delay_max_s=_parse_float(g(SETTING_DELAY_MAX_S), 15.0),
        delay_mean_s=_parse_float(g(SETTING_DELAY_MEAN_S), 5.0),
        delay_sigma=_parse_float(g(SETTING_DELAY_SIGMA), 0.5),
        rate_limit_max=_parse_int(g(SETTING_RATE_LIMIT_MAX), 3),
        rate_limit_window_s=_parse_float(g(SETTING_RATE_LIMIT_WINDOW_S), 60.0),
        typing_indicator=_parse_bool(g(SETTING_TYPING_INDICATOR), True),
        sleep_start_hour=_parse_hour(g(SETTING_SLEEP_START_HOUR)),
        sleep_end_hour=_parse_hour(g(SETTING_SLEEP_END_HOUR)),
    )


# ---------------------------------------------------------------------------
# Seams the controller leans on
# ---------------------------------------------------------------------------

class DiscordSender(Protocol):
    """Minimal slice of the slice-1 plugin's internal send surface."""

    async def trigger_typing(self, channel_id: str) -> None: ...
    async def send_dm(self, channel_id: str, content: str) -> bool: ...


# Per-event LLM pipeline driver. Returns the reply text, or "" to skip.
ReplyGenerator = Callable[[dict], Awaitable[str]]
# Returns (allowlist_hit?, settings) for the active profile, or (False, defaults)
# when no profile is active. The controller never reaches into ProfileManager
# directly -- main.py supplies this binding.
AllowlistLookup = Callable[[str], bool]
SettingsLookup = Callable[[], AutoReplySettings]
# Local-time supplier (returns an hour-of-day 0..23). Tests inject a fixed value.
LocalHourFn = Callable[[], int]
# Monotonic clock + sleep -- both injectable for deterministic tests.
ClockFn = Callable[[], float]
SleepFn = Callable[[float], Awaitable[None]]
# Per-channel mutex factory -- injectable so tests can observe contention.
LockFactory = Callable[[], asyncio.Lock]
# Random source (0.0..1.0 uniform; we transform downstream).
RandomFn = Callable[[], float]


# ---------------------------------------------------------------------------
# The controller
# ---------------------------------------------------------------------------

class DiscordAutoReplyController:
    """Orchestrates the slice-2 auto-reply path.

    The plugin (slice 1) is upstream of this controller and owns the
    Discord wire protocol. The controller's job is everything BETWEEN
    "an inbound DM landed" and "a reply went out":

      1. Is the sender on the allowlist?    -- no  -> caller drops.
      2. Are we inside sleep-hours?          -- yes -> caller drops.
      3. Per-channel rate-limit budget?      -- empty -> caller drops.
      4. Serialise per-channel (asyncio.Lock so a second inbound on the
         same channel queues behind the first one's reply rather than
         racing).
      5. Sample reply-delay from the configured log-normal, sleep it.
      6. Emit typing indicator (unless disabled).
      7. Drive the LLM pipeline; bail if it returns empty.
      8. Send the reply.

    Step 1 alone enforces the conservative default: empty allowlist
    means step-1 always says "no", so this controller never runs the
    rest of the gauntlet and inbound DMs are dropped silently. Inbound
    DMs do NOT enter the action queue -- the queue is for outbound
    proposals carrying a tool_name + tool_args, not a notification
    stream.
    """

    def __init__(
        self,
        *,
        sender: DiscordSender,
        reply_generator: ReplyGenerator,
        is_allowlisted: AllowlistLookup,
        get_settings: SettingsLookup,
        local_hour: LocalHourFn,
        clock: ClockFn = None,
        sleep: SleepFn = None,
        lock_factory: LockFactory = None,
        random_fn: RandomFn = None,
    ) -> None:
        self._sender = sender
        self._reply_generator = reply_generator
        self._is_allowlisted = is_allowlisted
        self._get_settings = get_settings
        self._local_hour = local_hour
        self._clock = clock or asyncio.get_event_loop().time
        self._sleep = sleep or asyncio.sleep
        self._lock_factory = lock_factory or asyncio.Lock
        self._random_fn = random_fn or random.random

        # Per-channel mutexes + rate-limit ring buffers, both lazily-
        # created on first use so we don't accumulate empty state for
        # every channel the user has ever seen.
        self._channel_locks: dict[str, asyncio.Lock] = {}
        self._rate_history: dict[str, list[float]] = {}

    # ------------------------------------------------------------------
    # Public entry -- main.py calls this from the seam
    # ------------------------------------------------------------------

    async def handle_inbound(self, event: dict) -> bool:
        """Try to auto-reply to this inbound DM event.

        Returns True iff the controller actually engaged with the event
        (auto-reply attempted, regardless of whether the network send
        ultimately succeeded). Returns False when the controller
        declined -- sender not on the allowlist, sleep-hours in effect,
        per-channel rate budget exhausted, or required fields missing.
        The caller drops the event in that case; nothing is queued.
        """
        author_id = str(event.get("author_id") or "")
        channel_id = str(event.get("channel_id") or "")
        if not author_id or not channel_id:
            return False
        if not self._is_allowlisted(author_id):
            return False

        settings = self._get_settings()
        if _hour_in_sleep_window(
            self._local_hour(),
            settings.sleep_start_hour,
            settings.sleep_end_hour,
        ):
            logger.info(
                "[discord_auto_reply] inside sleep-hours window -- "
                "falling back to draft for author=%s channel=%s",
                author_id, channel_id,
            )
            return False

        now = self._clock()
        if not self._consume_rate_budget(channel_id, settings, now):
            logger.info(
                "[discord_auto_reply] rate-limit exhausted -- falling "
                "back to draft for channel=%s (budget=%d/%.0fs)",
                channel_id, settings.rate_limit_max,
                settings.rate_limit_window_s,
            )
            return False

        # Serialise per-channel from here on -- two near-simultaneous
        # DMs on the same channel queue behind one another.
        lock = self._channel_locks.setdefault(channel_id, self._lock_factory())
        async with lock:
            await self._sleep(self._sample_delay(settings))
            if settings.typing_indicator:
                try:
                    await self._sender.trigger_typing(channel_id)
                except Exception:  # pragma: no cover -- plugin already scrubs
                    logger.debug(
                        "[discord_auto_reply] trigger_typing raised -- "
                        "continuing without indicator",
                        exc_info=True,
                    )

            try:
                reply = await self._reply_generator(event)
            except Exception:
                logger.exception(
                    "[discord_auto_reply] reply generator raised -- "
                    "skipping send",
                )
                return True  # engaged: caller still drops the event

            if not reply or not reply.strip():
                logger.info(
                    "[discord_auto_reply] empty LLM reply -- nothing sent",
                )
                return True

            try:
                await self._sender.send_dm(channel_id, reply)
            except Exception:
                logger.exception(
                    "[discord_auto_reply] send_dm raised -- reply lost",
                )
        return True

    # ------------------------------------------------------------------
    # Internals (exposed for direct unit testing)
    # ------------------------------------------------------------------

    def _consume_rate_budget(
        self, channel_id: str, settings: AutoReplySettings, now: float,
    ) -> bool:
        """Token-bucket-ish: keep the last N timestamps within the
        window, evict older ones, accept a new send iff the live count
        is under the configured max."""
        if settings.rate_limit_max <= 0:
            return False
        history = self._rate_history.setdefault(channel_id, [])
        cutoff = now - max(settings.rate_limit_window_s, 0.0)
        # Drop expired entries.
        kept: list[float] = [t for t in history if t > cutoff]
        if len(kept) >= settings.rate_limit_max:
            self._rate_history[channel_id] = kept
            return False
        kept.append(now)
        self._rate_history[channel_id] = kept
        return True

    def _sample_delay(self, settings: AutoReplySettings) -> float:
        """Sample a log-normal reply delay, clamped to [min, max].

        Using ``random.random`` + ``_inverse_lognormal`` rather than
        ``random.lognormvariate`` keeps the RNG seam single-purpose --
        tests inject one uniform supplier and we transform.
        """
        u = max(1e-9, min(1.0 - 1e-9, float(self._random_fn())))
        sample = _inverse_lognormal(u, settings.delay_mean_s, settings.delay_sigma)
        lo, hi = settings.delay_min_s, settings.delay_max_s
        if hi < lo:
            hi = lo
        return max(lo, min(hi, sample))


def _inverse_lognormal(u: float, mean_s: float, sigma: float) -> float:
    """Map a U(0,1) draw onto a log-normal with the given centre+sigma.

    We parameterise on the *median* (i.e. ``mean_s`` is the geometric
    mean / log-space mean). That's the intuitive knob for "a reply
    typically lands ~5 seconds after the inbound" -- the user doesn't
    think in moments of a distribution.
    """
    # Standard normal quantile via the rational approximation; we use
    # ``math.erfcinv``'s equivalent via a Beasley-Springer fallback for
    # portability.
    z = _phi_inv(u)
    if mean_s <= 0:
        return 0.0
    return math.exp(math.log(mean_s) + sigma * z)


def _phi_inv(p: float) -> float:
    """Standard normal inverse CDF -- Beasley-Springer-Moro coefficients."""
    # Source: Moro (1995). Accurate to ~10^-9, plenty for delay sampling.
    a = (
        -3.969683028665376e+01,  2.209460984245205e+02,
        -2.759285104469687e+02,  1.383577518672690e+02,
        -3.066479806614716e+01,  2.506628277459239e+00,
    )
    b = (
        -5.447609879822406e+01,  1.615858368580409e+02,
        -1.556989798598866e+02,  6.680131188771972e+01,
        -1.328068155288572e+01,
    )
    c = (
        -7.784894002430293e-03, -3.223964580411365e-01,
        -2.400758277161838e+00, -2.549732539343734e+00,
         4.374664141464968e+00,  2.938163982698783e+00,
    )
    d = (
         7.784695709041462e-03,  3.224671290700398e-01,
         2.445134137142996e+00,  3.754408661907416e+00,
    )
    p_low = 0.02425
    p_high = 1.0 - p_low
    if p < p_low:
        q = math.sqrt(-2.0 * math.log(p))
        return (
            ((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]
        ) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0
        )
    if p <= p_high:
        q = p - 0.5
        r = q * q
        return (
            ((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]
        ) * q / (
            ((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0
        )
    q = math.sqrt(-2.0 * math.log(1.0 - p))
    return -(
        ((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]
    ) / (
        (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0
    )


def _hour_in_sleep_window(
    now_hour: int, start: Optional[int], end: Optional[int],
) -> bool:
    """Is ``now_hour`` inside the sleep-hours window?

    Either bound being ``None`` disables the window. We support
    wrap-around (e.g. start=22, end=7 means 22:00..23:59 + 00:00..06:59
    is "asleep").
    """
    if start is None or end is None:
        return False
    if start == end:
        return False
    if start < end:
        return start <= now_hour < end
    # wrap-around midnight
    return now_hour >= start or now_hour < end
