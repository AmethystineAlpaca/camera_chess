"""Live adapter for the frozen CRF checkpoint and temporal stability gate."""
from pathlib import Path
import sys

import chess
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.crf_postprocessor import CLASSES, SQUARES, GROUPS, candidates, load_weights, score, validate_probabilities
from scripts import crf_postprocessor_v2
from lite_consensus import Consensus

# Exact-board accuracy, one synthetic predecessor per photo, 176-photo run.
# Source: docs/evaluations/neural-postprocessors-20261011.json and its report.
MODELS = {
    'small': ('Small CRF · 25 parameters', 125),
    'large': ('Large CRF · 250 parameters', 122),
    'feature_mlp': ('Feature MLP · 1 hidden layer', 123),
    'square_mlp': ('Shared square MLP', 126),
    'move_mlp1': ('Changed-square MLP · 1 hidden layer', 127),
    'move_mlp2': ('Changed-square MLP · 2 hidden layers', 128),
    'cnn': ('Spatial CNN', 122),
    'attention': ('Spatial Attention', 126),
}
DEFAULT_MODEL = max(MODELS, key=lambda model: MODELS[model][1])


def model_choices():
    return [dict(id=key, label=f'{name} ({correct / 176:.2%} accuracy on evaluation set)')
            for key, (name, correct) in MODELS.items()]


class CRFObservation:
    def __init__(self, model_path=None, model='small'):
        if model not in MODELS:
            raise ValueError('Invalid recognition model.')
        self.model = model
        loader = load_weights if model == 'small' else crf_postprocessor_v2.load_weights
        self.score = score if model == 'small' else crf_postprocessor_v2.score
        self.error = None
        self.neural = None
        try:
            if model in ('small', 'large'):
                filename = 'crf-postprocessor-v1.json' if model == 'small' else 'crf-postprocessor-v2-250.json'
                self.weights = loader(model_path or ROOT / 'research/models' / filename)
            else:
                from scripts.neural_postprocessor import load_model, score_one
                self.neural, checkpoint = load_model(model_path or ROOT / 'research/models/neural-postprocessors-v1' / f'{model}.pt')
                if checkpoint['kind'] != model:
                    raise ValueError('Recognition checkpoint does not match the selected model.')
                self.neural_score = score_one
                self.weights = None
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, ImportError) as exc:
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
        raw_scores = (self.neural_score(self.neural, q, options.boards[0], options)
                      if self.neural is not None else self.score(q, options, self.weights))
        scores = np.where(allowed, raw_scores, -np.inf)
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
