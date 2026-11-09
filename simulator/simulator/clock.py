"""Accelerated simulated clock: lets us replay a whole season in minutes.

speed = 360  ->  1 real second = 6 simulated minutes, 1 simulated day = 4 real minutes.
"""

import time
from datetime import datetime, timedelta, timezone
from typing import Callable


class SimClock:
    def __init__(self, start: datetime, speed: float, real_time: Callable[[], float] = time.time):
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        self.start = start
        self.speed = speed
        self._real_time = real_time
        self._real0 = real_time()

    def now(self) -> datetime:
        return self.start + timedelta(seconds=(self._real_time() - self._real0) * self.speed)

    def iso(self) -> str:
        return self.now().isoformat(timespec="seconds").replace("+00:00", "Z")

    def sim_hours(self, real_seconds: float) -> float:
        return real_seconds * self.speed / 3600.0
