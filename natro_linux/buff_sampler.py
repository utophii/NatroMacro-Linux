"""Read movement buffs without blocking key-release deadlines."""
import threading
import time


class BuffSampler:
    def __init__(self, reader, initial, interval=.25, max_age=2., clock=time.monotonic):
        self.reader, self.interval, self.max_age, self.clock = reader, interval, max_age, clock
        self._sample = (initial, clock(), None)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name='natro-buffs', daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            started = self.clock()
            try:
                value, error = self.reader(), None
            except Exception as exc:
                value, error = None, exc
            with self._lock:
                # Age includes capture latency, not just time since completion.
                self._sample = value, started, error
            if self._stop.wait(self.interval):
                return

    def get(self):
        with self._lock:
            value, timestamp, error = self._sample
        if error is not None:
            raise error
        if self.clock()-timestamp > self.max_age:
            raise RuntimeError('Снимки баффов устарели. Движение остановлено, проверь скорость захвата Niri.')
        return value

    def close(self):
        self._stop.set()
        # A pending read can finish independently, it never accesses input devices.
        self._thread.join(timeout=.5)
