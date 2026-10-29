"""Human-eye persistence model for LEDs and the multiplexed seven-segment display.

A real Basys 3 display shows one digit at a time; the eye integrates the
multiplexed digits into a steady picture. The simulator reports every output
change with its simulated timestamp, so we can compute, for each digit and
segment, the fraction of time it was lit over a recent window.

The window is the shorter of
  * EYE_WINDOW_PS of *simulated* time (what a person would see on hardware), and
  * the simulated time covered during the last WALL_WINDOW_S of *running*
    (so slow simulation still shows changes promptly).
When the simulation is paused nothing ages, so the picture freezes as it was.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass

EYE_WINDOW_PS = 20_000_000_000      # 20 ms of simulated time
WALL_WINDOW_S = 0.25
MAX_INTERVALS = 20_000


@dataclass
class DigitState:
    lit: bool
    segments: list[float]      # brightness 0..1 for A..G, DP


class PersistenceModel:
    def __init__(self, width: int):
        self.width = width
        self.intervals: deque = deque()  # (t0_ps, t1_ps, value, xmask)
        self.cur_time = 0
        self.cur_value = 0
        self.cur_xmask = (1 << width) - 1
        self.samples: deque = deque()    # (wall_s, sim_start_ps, sim_end_ps) per run chunk
        self.last_external: int | None = None   # time of the last input change made between runs

    def reset(self, time_ps: int, value: int, xmask: int) -> None:
        self.intervals.clear()
        self.samples.clear()
        self.last_external = None
        self.cur_time, self.cur_value, self.cur_xmask = time_ps, value, xmask

    def set_state(self, time_ps: int, value: int, xmask: int, external: bool = False) -> None:
        """Record a new output state taking effect at time_ps.

        external=True marks a change caused by a user input between run chunks: what was
        shown before it no longer belongs on the display, so the window restarts there.
        """
        if external:
            self.last_external = time_ps
        if time_ps > self.cur_time:
            self.intervals.append((self.cur_time, time_ps, self.cur_value, self.cur_xmask))
            if len(self.intervals) > MAX_INTERVALS:
                self.intervals.popleft()
            self.cur_time = time_ps
        self.cur_value, self.cur_xmask = value, xmask

    def add_run(self, start_ps: int, events, end_ps: int, end_value: int, end_xmask: int,
                wall: float | None = None) -> None:
        for t, v, x in events:
            self.set_state(t, v, x)
        self.set_state(end_ps, end_value, end_xmask)
        self.samples.append((time.monotonic() if wall is None else wall, start_ps, end_ps))
        self._trim()

    def window_start(self) -> int:
        now = self.cur_time
        start = now - EYE_WINDOW_PS
        if self.samples:
            last_wall = self.samples[-1][0]
            wall_start = self.samples[-1][1]
            for w, s0, _ in reversed(self.samples):
                if last_wall - w > WALL_WINDOW_S:
                    break
                wall_start = s0
            start = max(start, wall_start)
        if self.last_external is not None and (not self.samples or self.samples[-1][2] <= self.last_external):
            start = max(start, self.last_external)
        return start

    def _trim(self) -> None:
        horizon = self.cur_time - EYE_WINDOW_PS
        while self.intervals and self.intervals[0][1] <= horizon:
            self.intervals.popleft()
        while len(self.samples) > 1 and self.samples[0][2] <= horizon:
            self.samples.popleft()
        while len(self.samples) > 200:
            self.samples.popleft()

    def _window(self):
        """Yield (duration, value, xmask) for every interval inside the window, plus 'now'."""
        start = self.window_start()
        for t0, t1, v, x in self.intervals:
            if t1 <= start:
                continue
            yield t1 - max(t0, start), v, x

    def duty(self, bit: int | None) -> float | None:
        """Fraction of the window that `bit` was 1 (None when unmapped)."""
        if bit is None:
            return None
        total = on = 0
        for d, v, x in self._window():
            total += d
            if not (x >> bit) & 1 and (v >> bit) & 1:
                on += d
        if total == 0:
            if (self.cur_xmask >> bit) & 1:
                return 0.0
            return float((self.cur_value >> bit) & 1)
        return on / total

    def seven_segment(self, seg_bits: list[int | None], dp_bit: int | None, an_bits: list[int | None],
                      seg_active_low: bool = True, an_active_low: bool = True) -> list[DigitState]:
        """Per-digit segment brightness (digit 0 = rightmost)."""
        n = len(an_bits)
        seg_all = list(seg_bits) + [dp_bit]
        active_t = [0] * n
        seg_on = [[0] * 8 for _ in range(n)]

        def level(bit, v, x, active_low):
            if bit is None or (x >> bit) & 1:
                return False
            b = (v >> bit) & 1
            return b == 0 if active_low else b == 1

        items = list(self._window())
        if not items:
            items = [(1, self.cur_value, self.cur_xmask)]
        for d, v, x in items:
            for k, ab in enumerate(an_bits):
                if level(ab, v, x, an_active_low):
                    active_t[k] += d
                    for s, sb in enumerate(seg_all):
                        if level(sb, v, x, seg_active_low):
                            seg_on[k][s] += d
        out = []
        for k in range(n):
            if active_t[k] == 0:
                out.append(DigitState(False, [0.0] * 8))
            else:
                out.append(DigitState(True, [seg_on[k][s] / active_t[k] for s in range(8)]))
        return out
