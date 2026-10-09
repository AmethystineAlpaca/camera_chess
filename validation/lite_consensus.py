"""Whole-board temporal evidence. Never synthesizes or repairs model predictions."""
from collections import deque

class Consensus:
    def __init__(self, confirmed=None, votes=5, span=1.2, window=7, score_threshold=.85, consecutive=2):
        self.confirmed = confirmed
        self.window = deque(maxlen=window)
        self.last_change = None
        self.required_votes, self.required_span = votes, span
        self.score_threshold, self.consecutive = score_threshold, consecutive

    def step(self, fen, now, eligible, change_score=1.0):
        self.window.append((fen, now, eligible, change_score))
        supporting = [r for r in self.window if r[0] == fen and r[2] and r[3] >= self.score_threshold]
        count = len(supporting)
        latest = list(self.window)[-self.consecutive:]
        recent_agreement = len(latest) == self.consecutive and all(r[0] == fen and r[2] and r[3] >= self.score_threshold for r in latest)
        span = now-supporting[0][1] if supporting else 0
        ready = eligible and recent_agreement and count >= self.required_votes and span >= self.required_span
        changed = False
        if fen != self.confirmed and ready:
            self.confirmed, self.last_change, changed = fen, now, True
        recent = list(self.window)[-3:]
        unsettled = len(recent) >= 3 and all(r[0] != self.confirmed or not r[2] for r in recent)
        phase = 'pending' if self.confirmed is None or unsettled else 'stable'
        return dict(confirmed=self.confirmed, changed=changed, votes=count, phase=phase, ready=ready)
