"""Numerical and compatibility checks before running the capacity experiment."""
import json
from pathlib import Path
import tempfile
import unittest

import chess
import numpy as np
import torch

import crf_postprocessor as v1
import crf_postprocessor_v2 as v2
from train_crf import batches, batch_scores


FENS = [chess.STARTING_FEN, "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1",
        "4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1", "4k3/P7/8/8/8/8/8/4K3 w - - 0 1"]


class ExpandedConditionalModelTests(unittest.TestCase):
    def test_initialization_and_v1_embedding_preserve_distribution(self):
        rng = np.random.default_rng(23)
        self.assertEqual(len(v2.FEATURES), 250)
        self.assertEqual(len(set(v2.FEATURES)), 250)
        for fen in FENS:
            options = v1.candidates(fen)
            q = rng.dirichlet(np.full(13, .4), size=(3, 64)).astype(np.float32)
            np.testing.assert_allclose(v2.score(q, options, v2.fixed_weights()),
                                       v1.score(q, options, v1.fixed_weights()), atol=1e-5)
            for w in (v1.fixed_weights(), rng.normal(size=25).astype(np.float32)):
                old = v1.score(q, options, w)
                new = v2.score(q, options, v2.from_v1(w))
                np.testing.assert_allclose(new - new[:, :1], old - old[:, :1], atol=3e-5)

    def test_dense_numpy_torch_and_gradients_agree(self):
        rng = np.random.default_rng(15)
        q = rng.dirichlet(np.ones(13), size=(2, 64)).astype(np.float64)
        options = v1.candidates(FENS[1])
        w = rng.normal(0, .1, size=250)
        # Dense scoring is an independent oracle for changed-square subtraction.
        p = v2.potentials(q, w)
        dense = p[:, np.arange(64)[None, :], options.boards].sum(-1)
        dense = dense - dense[:, :1] + options.flags[:, v2.OP_COLUMNS] @ w[244:]
        np.testing.assert_allclose(v2.score(q, options, w), dense, atol=1e-10)
        arrays = [torch.tensor(a[None], dtype=torch.long) for a in (options.squares, options.before, options.after)]
        weights = torch.tensor(w, dtype=torch.float64, requires_grad=True)
        actual = v2.torch_scores(torch.tensor(q[None]), *arrays,
                                 torch.tensor(options.flags[None], dtype=torch.float64), weights)[0]
        np.testing.assert_allclose(actual.detach().numpy(), dense, atol=2e-5)
        # Check every weight, including interactions and operation flags.
        actual.square().mean().backward()
        for i in range(250):
            delta = np.zeros(250); delta[i] = 1e-4
            plus = v2.score(q, options, w + delta)
            minus = v2.score(q, options, w - delta)
            numerical = ((plus ** 2).mean() - (minus ** 2).mean()) / 2e-4
            self.assertAlmostEqual(float(weights.grad[i]), float(numerical), delta=2e-4)

    def test_variable_candidate_padding_is_neutral(self):
        rng = np.random.default_rng(6)
        options = [v1.candidates(fen) for fen in (FENS[0], FENS[-1])]
        q = rng.dirichlet(np.ones(13), size=(2, 10, 64)).astype(np.float32)
        data = {k: np.concatenate([getattr(o, k) for o in options])
                for k in ("squares", "before", "after", "flags")}
        data["offsets"] = np.cumsum([0] + [len(o.boards) for o in options])
        data["targets"] = np.array([0, 0])
        batch = next(batches(q, data, np.array([0, 1]), 2))
        w = rng.normal(0, .1, size=250).astype(np.float32)
        actual = batch_scores(batch, torch.tensor(w), v2).numpy()
        for i, options_i in enumerate(options):
            n = len(options_i.boards)
            np.testing.assert_allclose(actual[i, :, :n], v2.score(q[i], options_i, w), atol=2e-5)
            self.assertTrue(np.all(actual[i, :, n:] == -1e9))

    def test_artifact_is_separate_and_cross_version_loading_fails(self):
        board = chess.Board(); board.push_uci("e2e4")
        q = np.full((64, 13), .01 / 12, dtype=np.float32)
        q[np.arange(64), v1.labels(board)] = .99
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "v2.json"
            record = dict(schema=v2.SCHEMA, model_id=v2.MODEL_ID, classes=v2.CLASSES,
                          features=v2.FEATURES, weights=v2.fixed_weights().tolist())
            path.write_text(json.dumps(record))
            w = v2.load_weights(path)
            self.assertEqual(v2.predict(chess.STARTING_FEN, q, w)["move_uci"], "e2e4")
            with self.assertRaises(ValueError): v1.load_weights(path)
            record["weights"][0] = float("nan")
            path.write_text(json.dumps(record))
            with self.assertRaises(ValueError): v2.load_weights(path)
            path.write_text(json.dumps(dict(schema=v1.SCHEMA, classes=v1.CLASSES,
                                            features=v1.FEATURES, weights=v1.fixed_weights().tolist())))
            with self.assertRaises(ValueError): v2.load_weights(path)
        with self.assertRaises(ValueError): v2.predict(chess.STARTING_FEN, q * 2, v2.fixed_weights())


if __name__ == "__main__":
    unittest.main()
