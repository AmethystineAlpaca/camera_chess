import json
from pathlib import Path
import tempfile
import unittest

import chess
import numpy as np
import torch

from crf_postprocessor import (FEATURES, SCHEMA, CLASSES, candidates, fixed_weights,
                               labels, load_weights, predict, score, torch_scores)
from train_crf import batches, batch_scores, validation_gate


class ConditionalModelTests(unittest.TestCase):
    def test_special_move_coverage_and_identity(self):
        for fen, uci in [
            (chess.STARTING_FEN, "e2e4"),
            ("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1", "e1g1"),
            ("4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1", "e5d6"),
            *[("4k3/P7/8/8/8/8/8/4K3 w - - 0 1", "a7a8" + p) for p in "qrbn"],
        ]:
            board = chess.Board(fen)
            options = candidates(fen)
            self.assertEqual(len({v.tobytes() for v in options.boards}), len(options.boards))
            self.assertEqual(options.target_index(labels(board)), 0)
            board.push_uci(uci)
            idx = options.target_index(labels(board))
            self.assertEqual(options.operations[idx], 0)
            self.assertEqual(options.moves[idx], uci)
        board = chess.Board()
        piece = board.remove_piece_at(chess.E7)  # Wrong-color, illegal relocation.
        board.set_piece_at(chess.A3, piece)
        options = candidates(chess.STARTING_FEN)
        idx = options.target_index(labels(board))
        self.assertEqual(options.operations[idx], 2)
        self.assertEqual(sorted(options.boards[idx]), sorted(options.boards[0]))

    def test_sparse_score_matches_dense_and_torch_gradient(self):
        rng = np.random.default_rng(4)
        q = rng.dirichlet(np.ones(13), size=(2, 64)).astype(np.float32)
        options = candidates(chess.STARTING_FEN)
        weights = fixed_weights()
        raw = np.log(q[:, np.arange(64)[None, :], options.boards]).sum(-1)
        actual = score(q, options, weights)
        np.testing.assert_allclose(actual, raw - raw[:, :1], atol=5e-5)
        weights = rng.normal(size=len(FEATURES)).astype(np.float32)
        w = torch.tensor(weights, requires_grad=True)
        arrays = [torch.tensor(a[None], dtype=torch.long) for a in (options.squares, options.before, options.after)]
        value = torch_scores(torch.tensor(q[None]), *arrays,
                             torch.tensor(options.flags[None], dtype=torch.float32), w)[0]
        np.testing.assert_allclose(value.detach().numpy(), score(q, options, weights), atol=1e-5)
        value[0, 10].backward()
        for i in [0, 4, 13, 16, 17, 18, 20]:
            delta = np.zeros_like(weights); delta[i] = .01
            numerical = (score(q, options, weights + delta)[0, 10] - score(q, options, weights - delta)[0, 10]) / .02
            self.assertAlmostEqual(float(w.grad[i]), float(numerical), delta=.002)

    def test_clean_prediction_export_and_invalid_input(self):
        board = chess.Board(); board.push_uci("e2e4")
        q = np.full((64, 13), .01 / 12, dtype=np.float32)
        q[np.arange(64), labels(board)] = .99
        result = predict(chess.STARTING_FEN, q, fixed_weights())
        self.assertEqual(result["labels"], labels(board).tolist())
        self.assertEqual(result["move_uci"], "e2e4")
        self.assertEqual(result["decision"], "review_required")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "weights.json"
            p.write_text(json.dumps(dict(schema=SCHEMA, features=FEATURES, classes=CLASSES, weights=fixed_weights().tolist())))
            np.testing.assert_array_equal(load_weights(p), fixed_weights())
        with self.assertRaises(ValueError): predict(chess.STARTING_FEN, q * 2, fixed_weights())
        with self.assertRaises(ValueError): candidates("8/8/8/8/8/8/8/8 w - - 0 1")

    def test_variable_candidate_padding_does_not_change_scores(self):
        options = [candidates(chess.STARTING_FEN), candidates("4k3/P7/8/8/8/8/8/4K3 w - - 0 1")]
        q = np.random.default_rng(2).dirichlet(np.ones(13), size=(2, 10, 64)).astype(np.float32)
        data = {k: np.concatenate([getattr(o, k) for o in options]) for k in ("squares", "before", "after", "flags")}
        data["offsets"] = np.cumsum([0] + [len(o.boards) for o in options])
        data["targets"] = np.array([0, 0])
        batch = next(batches(q, data, np.array([0, 1]), 2))
        w = np.random.default_rng(3).normal(size=len(FEATURES)).astype(np.float32)
        actual = batch_scores(batch, torch.tensor(w)).numpy()
        for i, o in enumerate(options):
            np.testing.assert_allclose(actual[i, :, :len(o.boards)], score(q[i], o, w), atol=1e-5)
            self.assertTrue(np.all(actual[i, :, len(o.boards):] == -1e9))

    def test_failed_real_validation_blocks_integration_despite_synthetic_gain(self):
        fixed = dict(exact_accuracy=.96, by_operation={"illegal_relocation": dict(exact_accuracy=.96)})
        learned = dict(exact_accuracy=.99, by_operation={"illegal_relocation": dict(exact_accuracy=.99)},
                       by_profile={"clean": dict(exact_accuracy=1.)})
        real_fixed = dict(exact_accuracy=.70)
        real_learned = dict(exact_accuracy=.80, cpu_prediction_p95_seconds=.01)
        gate = validation_gate(fixed, learned, real_fixed, real_learned)
        self.assertFalse(gate["passed"])
        self.assertEqual(gate["decision"], "stop_before_integration")
        self.assertFalse(gate["automatic_commits_authorized"])


if __name__ == "__main__":
    unittest.main()
