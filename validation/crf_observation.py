"""Live adapter for the frozen CRF checkpoint and temporal stability gate."""
from pathlib import Path
import sys

import chess
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.crf_postprocessor import CLASSES, SQUARES, GROUPS, candidates, load_weights, score, validate_probabilities
from lite_consensus import Consensus


class CRFObservation:
    def __init__(self, model_path=None):
        self.error = None
        try:
            self.weights = load_weights(model_path or ROOT / 'research/models/crf-postprocessor-v1.json')
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.weights = None
            self.error = str(exc)
        self.reset()

    def reset(self):
        self.context = None
        self.options = None
        self.gate = Consensus(votes=3, span=.45, window=4, score_threshold=0)

    def step(self, fen, context, record, corrections, now):
        if self.error:
            return dict(status='unavailable', detail='Recognition assist could not load. Restart after checking the model file.')
        if context != self.context:
            self.reset()
            self.context = context
            self.options = candidates(fen)
        try:
            values = record['square_probabilities']
            if not isinstance(values, dict) or set(values) != set(chess.SQUARE_NAMES):
                raise ValueError('Missing square probabilities')
            q = validate_probabilities([[values[chess.square_name(s)][c] for c in CLASSES] for s in SQUARES])
        except (ValueError, KeyError, TypeError, IndexError):
            self.reset()
            return dict(status='uncertain', detail='Waiting for a complete camera reading.')
        options = self.options
        allowed = np.ones(len(options.boards), dtype=bool)
        manual = np.zeros(64, dtype=bool)
        for name, correction in corrections.items():
            index = SQUARES.index(chess.parse_square(name))
            manual[index] = True
            allowed &= options.boards[:, index] == CLASSES.index(correction['piece'])
        if not allowed.any():
            self.reset()
            return dict(status='uncertain', detail='The edited position does not match a supported move. Check the corrected squares.')
        scores = np.where(allowed, score(q, options, self.weights), -np.inf)
        order = np.argsort(-scores, kind='stable')
        winner = int(order[0])
        mass = np.exp(scores - scores[winner]); posterior = float(mass[winner] / mass.sum())
        # A single manually constrained candidate is still subject to visual-fit checks.
        gap = float(scores[winner] - scores[order[1]]) if allowed.sum() > 1 else None
        target = options.boards[winner]
        fit = q[np.arange(64), target]
        groups = np.stack([q[:, 0], q[:, 1:7].sum(-1), q[:, 7:].sum(-1)], axis=-1)
        occupancy = groups[np.arange(64), GROUPS[target]]
        changed = target != options.boards[0]
        visible = ~manual
        mean_log_fit = float(np.log(np.maximum(fit[visible], 1e-9)).mean()) if visible.any() else 0.
        strong_contradiction = bool(((occupancy < .15) & (q.max(-1) > .85) & visible).any())
        changed_support = occupancy[changed & visible]
        # Experimental screening, not a calibrated probability of correctness.
        eligible = (posterior >= .60 and (gap is None or gap >= .7)
                    and mean_log_fit >= np.log(.25) and not strong_contradiction
                    and (not len(changed_support) or float(changed_support.min()) >= .55))
        board = chess.Board(None)
        for square, category in zip(SQUARES, target):
            if category:
                board.set_piece_at(square, chess.Piece.from_symbol(CLASSES[int(category)]))
        placement = board.board_fen()
        stable = self.gate.step(placement, now, eligible)
        ready = bool(stable['ready'])
        operation = ['legal_move', 'unchanged', 'illegal_relocation'][int(options.operations[winner])]
        status = 'stable' if ready else 'settling' if eligible else 'uncertain'
        detail = ('Position recognized.' if ready
                  else 'Waiting for the position to settle.' if status == 'settling'
                  else 'Clear your hands or use Fix recognition.')
        return dict(status=status, detail=detail, placement=placement, operation=operation,
                    move_uci=options.moves[winner], context=context,
                    conditional_probability=posterior, score_gap=gap, mean_log_visual_fit=mean_log_fit,
                    candidate_count=int(allowed.sum()), votes=int(stable['votes']))
