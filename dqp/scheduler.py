"""Small background scheduler (one thread). For servers, `dqp run --every N` or cron does the same job."""
from __future__ import annotations

import threading
import time
from typing import Callable


class Scheduler:
    def __init__(self, job: Callable[[], object]):
        self.job, self.interval, self.last_error = job, 0, ""
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and not self._stop.is_set())

    def start(self, minutes: int) -> None:
        self.stop()
        self.interval = max(1, int(minutes))
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, args=(self._stop,), daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self, stop: threading.Event) -> None:
        while not stop.wait(self.interval * 60):  # first run happens after one interval
            try:
                self.job()
                self.last_error = ""
            except Exception as e:  # noqa: BLE001
                self.last_error = str(e)
