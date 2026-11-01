"""Keeps the earbuds' control channel on this PC.

The earbuds accept one controller at a time and the first one to connect keeps it: while the Technics app on a phone
holds the channel this PC is refused straight away, and while this PC holds it the phone app cannot take it. So the
tray holds the channel instead of letting go when idle, and while it is refused it tries again with a growing pause.
Only a read-only poll (which opens the channel when needed) is ever repeated here; a write is never resent.

Run  python tray\\keeper.py  to self-test.
"""
import logging
import threading

BACKOFF_S = (5, 10, 30, 60)   # pause after 1, 2, 3, then 4 or more failed attempts in a row
CHECK_S = 60                  # read-only check while connected, so a dropped link is noticed and taken again
IDLE_S = 60                   # how often to look again while keeping control is off or released


class Keeper:
    def __init__(self, read, on_result, wanted):
        """read(): one read-only poll that opens the channel if needed and returns a result dict ({"error": ...} on failure).
        on_result(res): hands every result to the tray. wanted(): True while this PC should hold the channel."""
        self.read, self.on_result, self.wanted = read, on_result, wanted
        self.failures = 0
        self._wake = threading.Event()
        self._stop = threading.Event()

    def step(self):
        """One attempt. Returns the pause before the next one, in seconds."""
        if not self.wanted():
            self.failures = 0
            return IDLE_S
        res = self.read()
        self.on_result(res)
        if "error" in res:
            self.failures += 1
            return BACKOFF_S[min(self.failures, len(BACKOFF_S)) - 1]
        self.failures = 0
        return CHECK_S

    def kick(self):
        """Look again now: the setting changed, or the user took control back."""
        self.failures = 0
        self._wake.set()

    def stop(self):
        self._stop.set()
        self._wake.set()

    def run(self):
        while not self._stop.is_set():
            try:
                delay = self.step()
            except Exception:
                logging.exception("keeper step failed")
                delay = BACKOFF_S[-1]
            self._wake.wait(delay)
            self._wake.clear()


def selftest():
    import time
    busy = {"error": {"kind": "busy", "message": "phone has it"}}
    ok = {"mode": 1, "nc": 50, "amb": 50}

    # backoff grows, caps at 60 s, and resets after a success
    script, seen = [busy, busy, busy, busy, busy, ok, busy], []
    k = Keeper(read=lambda: script.pop(0), on_result=seen.append, wanted=lambda: True)
    assert [k.step() for _ in range(7)] == [5, 10, 30, 60, 60, 60, 5]
    assert seen == [busy, busy, busy, busy, busy, ok, busy], seen

    # not wanted: nothing is read, nothing is reported
    calls = []
    k = Keeper(read=lambda: calls.append(1) or ok, on_result=seen.append, wanted=lambda: False)
    assert k.step() == IDLE_S and calls == []

    # kick resets the backoff
    k = Keeper(read=lambda: busy, on_result=lambda r: None, wanted=lambda: True)
    for _ in range(3):
        k.step()
    k.kick()
    assert k.step() == 5

    # run: kick wakes the loop at once, stop ends it
    count = []
    k = Keeper(read=lambda: count.append(1) or ok, on_result=lambda r: None, wanted=lambda: True)
    t = threading.Thread(target=k.run, daemon=True)
    t.start()
    deadline = time.monotonic() + 2
    while len(count) < 1 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(count) == 1, count
    k.kick()
    deadline = time.monotonic() + 2
    while len(count) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(count) == 2, "kick did not wake the loop"
    k.stop()
    t.join(2)
    assert not t.is_alive(), "stop did not end the loop"
    print("keeper selftest ok")


if __name__ == "__main__":
    selftest()
