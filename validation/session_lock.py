"""Crash-released OS lock for a single camera session, inherited by its workers."""
import fcntl
import os
import sys
from pathlib import Path
root = Path(__file__).resolve().parents[1]
path = root/'validation/frames/lite-live/session.lockfile'
path.parent.mkdir(parents=True, exist_ok=True)
lock = path.open('a+')
try:
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    print('A camera session is already running. Stop it before launching another.', file=sys.stderr)
    sys.exit(1)
lock.seek(0)
lock.truncate()
lock.write(str(os.getpid()))
lock.flush()
os.set_inheritable(lock.fileno(), True)
os.execv(str(root/'research/edgetam-env/bin/python'), [str(root/'research/edgetam-env/bin/python'), str(root/'validation/lite_live_server.py')])
