"""Numerical/structural checks for neural candidate scoring, without photo data."""
import tempfile
import unittest
from pathlib import Path

import chess
import numpy as np
import torch
from torch.nn import functional as F

import crf_postprocessor as v1
from neural_postprocessor import KINDS, ResidualScorer, delta, load_model, score_one
from evaluate_crf_synthetic_previous import summarize
from evaluate_neural_postprocessors import paired


class NeuralScorerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        torch.set_num_threads(2)
        cls.board = chess.Board()
        cls.options = v1.candidates(cls.board.fen())
        cls.previous = v1.labels(cls.board)
        cls.q = np.random.default_rng(7).dirichlet(np.ones(13), size=64).astype(np.float32)
        cls.weights = v1.load_weights(Path(__file__).resolve().parents[1]/"research/models/crf-postprocessor-v1.json")

    def inputs(self):
        o = self.options
        return [torch.tensor(self.q)[None, None], torch.tensor(self.previous.astype(np.int64))[None],
                torch.tensor(o.squares.astype(np.int64))[None], torch.tensor(o.before.astype(np.int64))[None],
                torch.tensor(o.after.astype(np.int64))[None], torch.tensor(o.flags.astype(np.float32))[None],
                torch.ones((1, len(o.boards)), dtype=torch.bool)]

    def test_zero_residual_matches_v1_all_models(self):
        expected = v1.score(self.q, self.options, self.weights)
        for kind in KINDS:
            with self.subTest(kind=kind):
                model = ResidualScorer(kind, self.weights).eval()
                actual = score_one(model, self.q, self.previous, self.options)
                np.testing.assert_allclose(actual, expected, atol=1e-5, rtol=1e-5)

    def test_dense_board_scores_equal_sparse_delta(self):
        values = np.random.default_rng(10).normal(size=(64, 13)).astype(np.float32)
        expected = values[np.arange(64)[None], self.options.boards].sum(-1)
        expected -= values[np.arange(64), self.previous].sum()
        args = self.inputs()
        actual = delta(torch.tensor(values)[None, None], *args[2:5])[0, 0].numpy()
        np.testing.assert_allclose(actual, expected, atol=4e-6)

    def test_training_padding_candidate_order_and_serialization(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                torch.manual_seed(11)
                model = ResidualScorer(kind, self.weights)
                args = self.inputs()
                optimizer = torch.optim.Adam(model.parameters(), lr=.01)
                before = model(*args).detach().clone()
                for _ in range(3):
                    optimizer.zero_grad()
                    loss = F.cross_entropy(model(*args)[:, 0], torch.tensor([1]))
                    loss.backward()
                    self.assertTrue(all(p.grad is not None and torch.isfinite(p.grad).all()
                                        for p in model.parameters()))
                    optimizer.step()
                model.eval()
                actual = model(*args).detach()
                self.assertFalse(torch.allclose(actual, before))
                self.assertIsNone(model.baseline.grad)
                np.testing.assert_array_equal(model.baseline.numpy(), self.weights)
                perm = torch.randperm(args[2].shape[1])
                rearranged = args[:2]+[x[:, perm] for x in args[2:]]
                torch.testing.assert_close(model(*rearranged), actual[:, :, perm])
                padded = args[:2]+[F.pad(args[2], (0, 0, 0, 3), value=64),
                    F.pad(args[3], (0, 0, 0, 3)), F.pad(args[4], (0, 0, 0, 3)),
                    F.pad(args[5], (0, 0, 0, 3)), F.pad(args[6], (0, 3))]
                pred = model(*padded)
                torch.testing.assert_close(pred[..., :-3], actual)
                self.assertTrue((pred[..., -3:] == -1e9).all())
                # Same observation in a larger event/profile batch must agree.
                repeated = [args[0].repeat(2, 3, 1, 1)] + [x.repeat(2, *([1]*(x.ndim-1))) for x in args[1:]]
                torch.testing.assert_close(model(*repeated), actual.expand(2, 3, -1), atol=2e-5, rtol=1e-5)
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory)/"model.pt"
                    torch.save(dict(schema=1, kind=kind, classes=v1.CLASSES, state_dict=model.state_dict()), path)
                    restored, _ = load_model(path)
                    torch.testing.assert_close(restored(*args), actual)


class PredecessorMetricTests(unittest.TestCase):
    def test_nested_all_correct_is_not_voting_and_excludes_short_pools(self):
        rows = []
        # a fails only its tenth condition; b has five conditions; c only two.
        for photo, count in (("a", 10), ("b", 5), ("c", 2)):
            for i in range(count):
                rows.append(dict(transition_id=photo, setup_id=photo, sample_index=i,
                    methods={"model": dict(exact=not (photo == "a" and i == 9), square_accuracy=1.),
                             "crf_v1_25": dict(exact=True, square_accuracy=1.)}))
        result = summarize(rows, "model", 7, 10)
        self.assertEqual(result["single_random_previous_correct_photos"], 3)
        self.assertEqual(result["strict_five_previous_states_correct_photos"], 2)
        self.assertEqual(result["photos_with_five_distinct_previous_states"], 2)
        self.assertEqual(result["strict_requested_previous_states_correct_photos"], 0)
        self.assertEqual(result["photos_with_requested_distinct_previous_states"], 1)
        comparison = paired(rows, "model", 7)
        self.assertEqual(comparison["1"]["delta"], 0.)
        self.assertEqual(comparison["5"]["delta"], 0.)
        self.assertEqual(comparison["10"]["delta"], -1.)
        self.assertEqual(comparison["10"]["v1_only_correct"], 1)


if __name__ == "__main__":
    unittest.main()
