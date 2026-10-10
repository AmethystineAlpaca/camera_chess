"""Checks for data semantics that can silently corrupt CRF training."""
import unittest

import chess
import numpy as np

import build_crf_dataset as data


class DatasetTests(unittest.TestCase):
    def test_order_and_classes_match_model(self):
        board = chess.Board()
        y = data.labels(board)
        self.assertEqual("".join(data.CLASSES[i] for i in y[:8]), "rnbqkbnr")
        self.assertEqual("".join(data.CLASSES[i] for i in y[-8:]), "RNBQKBNR")
        self.assertEqual(data.CLASSES, ".PNBRQKpnbrqk")

    def test_canonical_state_preserves_rights_not_counters(self):
        a = chess.Board()
        b = a.copy()
        b.halfmove_clock, b.fullmove_number = 12, 30
        self.assertEqual(data.state(a), data.state(b))
        b.castling_rights = 0
        self.assertNotEqual(data.state(a), data.state(b))
        ep = chess.Board("4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1")
        self.assertTrue(ep.has_legal_en_passant())
        self.assertIn("d6", data.state(ep))

    def test_special_move_targets(self):
        cases = [("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1", "e1g1", 4),
                 ("4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1", "e5d6", 3)]
        cases += [("4k3/P7/8/8/8/8/8/4K3 w - - 0 1", "a7a8" + p, 2) for p in "qrbn"]
        for fen, uci, changed in cases:
            before = chess.Board(fen)
            after = before.copy()
            after.push_uci(uci)
            row = data.make_row(before, after, "legal_move", "00000000", "test", 1, uci)
            data.validate_rows([row])
            self.assertEqual(int((data.labels(before) != data.labels(after)).sum()), changed)

    def test_physical_mistake_preserves_material_and_game(self):
        before = chess.Board()
        after = before.copy()
        after.push_uci("e2e4")
        row = data.make_row(before, after, "legal_move", "00000000", "test", 1, "e2e4", archive_game_ordinal=0)
        item = data.illegal_row(row, 123)
        data.validate_rows([item])
        physical = chess.Board(item["current_board_fen"] + " w - - 0 1")
        self.assertEqual(sorted(data.labels(before)), sorted(data.labels(physical)))
        self.assertEqual(item["next_game_fen"], before.fen())

    def test_probability_profiles_are_valid_and_reproducible(self):
        y = data.labels(chess.Board())
        for profile in data.PROFILES:
            q, count = data.probabilities(y, profile, np.random.default_rng(321))
            repeat, _ = data.probabilities(y, profile, np.random.default_rng(321))
            np.testing.assert_array_equal(q, repeat)
            self.assertTrue(np.isfinite(q).all())
            self.assertTrue((q > 0).all())
            np.testing.assert_allclose(q.sum(-1), 1, atol=1e-6)
            if profile == "clean":
                np.testing.assert_array_equal(q.argmax(-1), y)
                self.assertEqual(count, 0)
            else:
                self.assertGreater(count, 0)

    def test_leakage_checks_both_endpoints_and_ignores_rights(self):
        a = chess.Board()
        b = a.copy(); b.push_uci("e2e4")
        c = b.copy(); c.push_uci("e7e5")
        rows = [data.make_row(a, b, "legal_move", "00000000", "test", 1, "e2e4"),
                data.make_row(b, c, "legal_move", "00000055", "test", 2, "e7e5")]
        self.assertEqual([r["split"] for r in rows], ["train", "validation"])
        with self.assertRaises(ValueError):
            data.check_disjoint(rows)
        safe, removed, shared = data.remove_leakage(rows)
        self.assertEqual(safe, [])
        self.assertEqual(removed, 2)
        self.assertEqual(shared, 1)

    def test_wrong_legal_target_is_rejected(self):
        a = chess.Board()
        b = a.copy(); b.push_uci("d2d4")
        row = data.make_row(a, b, "legal_move", "00000000", "test", 1, "e2e4")
        with self.assertRaises(ValueError):
            data.validate_rows([row])


if __name__ == "__main__":
    unittest.main()
