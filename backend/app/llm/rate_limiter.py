from __future__ import annotations

import asyncio
import math
import os
import time
from dataclasses import dataclass, field

WINDOW_SECONDS = 60.0


@dataclass
class _UsageEvent:
    at: float
    tokens: int


@dataclass
class _State:
    events: list[_UsageEvent] = field(default_factory=list)
    active: int = 0
    waiters: list[asyncio.Future] = field(default_factory=list)
    last_started_at: float = 0.0
    cooldown_until: float = 0.0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_states: dict[tuple[str, str], _State] = {}


def _env_number(name: str, fallback: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return fallback
    try:
        value = int(float(raw))
        return value if value >= 0 else fallback
    except (TypeError, ValueError):
        return fallback


def configured_rate_limits(provider: str) -> dict:
    prefix = "OPENROUTER" if provider == "openrouter" else "GEMINI"
    default_rpm = 18 if provider == "openrouter" else 6
    default_tpm = 0 if provider == "openrouter" else 180_000
    rpm = _env_number(f"{prefix}_MAX_REQUESTS_PER_MINUTE", default_rpm)
    explicit_interval = os.getenv(f"{prefix}_MIN_REQUEST_INTERVAL_MS")
    if explicit_interval is not None:
        try:
            interval = max(0, int(float(explicit_interval)))
        except ValueError:
            interval = math.ceil(60_000 / rpm) if rpm > 0 else 0
    else:
        interval = math.ceil(60_000 / rpm) if rpm > 0 else 0
    return {
        "requestsPerMinute": rpm,
        "inputTokensPerMinute": _env_number(f"{prefix}_MAX_INPUT_TOKENS_PER_MINUTE", default_tpm),
        "maxConcurrent": max(1, _env_number(f"{prefix}_MAX_CONCURRENT_REQUESTS", 1)),
        "minRequestIntervalMs": interval,
    }


def _state(provider: str, model: str) -> _State:
    key = (provider, model)
    state = _states.get(key)
    if state is None:
        state = _State()
        _states[key] = state
    return state


class Reservation:
    def __init__(self, state: _State, wait_ms: int):
        self.state = state
        self.wait_ms = wait_ms
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        state = self.state
        state.active = max(0, state.active - 1)
        while state.waiters:
            future = state.waiters.pop(0)
            if not future.done():
                future.set_result(None)
                break


async def _acquire_concurrency(state: _State, maximum: int) -> None:
    while True:
        async with state.lock:
            if state.active < maximum:
                state.active += 1
                return
            future = asyncio.get_running_loop().create_future()
            state.waiters.append(future)
        await future


def _wait_until_token_capacity(events: list[_UsageEvent], incoming: int, limit: int, now: float) -> float:
    if limit <= 0:
        return .25
    retained = sum(event.tokens for event in events)
    for event in events:
        retained -= event.tokens
        if retained + incoming <= limit:
            return max(.25, WINDOW_SECONDS - (now - event.at) + .05)
    return WINDOW_SECONDS


async def reserve_model_capacity(provider: str, model: str, estimated_tokens: int = 0) -> Reservation:
    state = _state(provider, model)
    limits = configured_rate_limits(provider)
    started = time.monotonic()
    await _acquire_concurrency(state, limits["maxConcurrent"])
    try:
        while True:
            async with state.lock:
                now = time.monotonic()
                state.events = [event for event in state.events if now - event.at < WINDOW_SECONDS]
                request_count = len(state.events)
                token_count = sum(event.tokens for event in state.events)
                cooldown_wait = max(0.0, state.cooldown_until - now)
                interval_wait = max(0.0, limits["minRequestIntervalMs"] / 1000 - (now - state.last_started_at))
                request_allowed = limits["requestsPerMinute"] <= 0 or request_count < limits["requestsPerMinute"]
                token_limit = limits["inputTokensPerMinute"]
                if token_limit <= 0:
                    tokens_allowed = True
                elif estimated_tokens > token_limit:
                    tokens_allowed = token_count == 0
                else:
                    tokens_allowed = token_count + estimated_tokens <= token_limit

                if cooldown_wait <= 0 and interval_wait <= 0 and request_allowed and tokens_allowed:
                    state.last_started_at = time.monotonic()
                    state.events.append(_UsageEvent(state.last_started_at, max(0, estimated_tokens)))
                    return Reservation(state, int((time.monotonic() - started) * 1000))

                request_wait = 0.0 if request_allowed or not state.events else max(.25, WINDOW_SECONDS - (now - state.events[0].at) + .05)
                token_wait = 0.0 if tokens_allowed else _wait_until_token_capacity(state.events, estimated_tokens, token_limit, now)
                delay = max(.05, cooldown_wait, interval_wait, request_wait, token_wait)
            await asyncio.sleep(delay)
    except BaseException:
        Reservation(state, 0).release()
        raise


def penalize_model_capacity(provider: str, model: str, wait_ms: int) -> None:
    state = _state(provider, model)
    state.cooldown_until = max(state.cooldown_until, time.monotonic() + max(0, wait_ms) / 1000)
