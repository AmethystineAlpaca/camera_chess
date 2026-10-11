"""Own camera and inference workers; only Start Game acquires the camera."""
import json
import os
from pathlib import Path
import signal
import subprocess
import threading
import time


class CameraSession:
    def __init__(self, root):
        self.root = Path(root)
        self.data = self.root/'validation/frames/lite-live'
        self.output = self.root/'validation/results/lite-live'
        self.data.mkdir(parents=True, exist_ok=True)
        self.output.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.workers = []
        self.logs = []
        self.data.joinpath('STOP').touch()

    @property
    def running(self):
        return len(self.workers) == 2 and all(p.poll() is None for p in self.workers)

    def start(self):
        with self.lock:
            if self.running:
                return
            self.stop()
            for name in ('STOP', 'RESTART_READER', 'latest.bin'):
                self.data.joinpath(name).unlink(missing_ok=True)
            self.output.joinpath('status.json').write_text(json.dumps({
                'phase': 'loading', 'updated': time.time(), 'detail': 'Starting camera'}))
            commands = [
                (['/usr/bin/arch', '-arm64', '/usr/bin/swift', 'validation/lite-live-camera.swift', str(self.data)], 'camera.log'),
                ([str(self.root/'research/edgetam-env/bin/python'), 'validation/lite_live.py'], 'reader.log')]
            env = dict(os.environ, PYTORCH_ENABLE_MPS_FALLBACK='1')
            try:
                for command, name in commands:
                    log = self.output.joinpath(name).open('a')
                    self.logs.append(log)
                    self.workers.append(subprocess.Popen(command, cwd=self.root, env=env,
                        stdout=log, stderr=subprocess.STDOUT, start_new_session=True))
            except Exception:
                self.stop()
                raise

    def stop(self):
        with self.lock:
            self.data.joinpath('STOP').touch()
            # Stop capture immediately, including the Swift compiler's child process.
            for process in self.workers:
                if process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
            for index, process in enumerate(self.workers):
                try:
                    # The inference worker flushes its bounded local-evidence queue.
                    process.wait(timeout=6 if index == 1 else 2)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=2)
            self.workers.clear()
            for log in self.logs:
                log.close()
            self.logs.clear()
