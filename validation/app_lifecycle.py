"""A launcher lease keeps the local service tied to its visible desktop control."""
import threading
import time


class LauncherLease:
    def __init__(self, timeout=15, clock=time.monotonic):
        self.timeout = timeout
        self.clock = clock
        self.last_seen = None
        self.lock = threading.Lock()

    def heartbeat(self):
        with self.lock:
            self.last_seen = self.clock()

    def expired(self):
        with self.lock:
            return self.last_seen is not None and self.clock()-self.last_seen > self.timeout


class BrowserPresence:
    def __init__(self, timeout=10, clock=time.monotonic):
        self.timeout = timeout
        self.clock = clock
        self.pages = {}
        self.open_requested = None
        self.focus_id = 0
        self.was_open = False
        self.last_change = self.clock()
        self.lock = threading.Lock()

    def heartbeat(self, identity):
        with self.lock:
            self.pages[identity] = self.clock()
            self.was_open = True
            self.last_change = self.clock()
            self.open_requested = None
            return self.focus_id

    def leave(self, identity):
        with self.lock:
            self.pages.pop(identity, None)
            self.last_change = self.clock()

    def has_pages(self):
        with self.lock:
            now = self.clock()
            self.pages = {key: seen for key, seen in self.pages.items() if now-seen < self.timeout}
            return bool(self.pages)

    def should_stop_camera(self):
        if self.has_pages():
            return False
        with self.lock:
            return self.was_open and self.clock()-self.last_change >= 4

    def open_browser(self):
        with self.lock:
            now = self.clock()
            active = any(now-seen < self.timeout for seen in self.pages.values())
            waiting = self.open_requested is not None and now-self.open_requested < self.timeout
            self.focus_id += 1
            if not active and not waiting:
                self.open_requested = now
            return active or waiting
