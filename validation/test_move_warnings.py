"""Stable recognition of a move is distinct from permission to play it."""
import unittest
from unittest.mock import patch

import chess
from chess_game import Game


class MoveWarningTests(unittest.TestCase):
    def game(self, fen='7k/8/8/8/8/2b5/3r4/4K3 w - - 0 1'):
        game = Game(object())
        game.board = chess.Board(fen)
        self.assertTrue(game.board.is_valid())
        game.state = 'player_turn'
        return game

    def attempted(self, game, uci='e1d2'):
        move = chess.Move.from_uci(uci)
        self.assertTrue(game.board.is_pseudo_legal(move))
        self.assertFalse(game.board.is_legal(move))
        return game.physical_after(move)

    def feed(self, game, board, start=0, count=5, confidence=.99):
        for i in range(start, start+count):
            wall = 100+i*.4
            game.observe(dict(raw=board.board_fen(), sequence=i, capture_time=wall,
                              min_score=confidence, min_margin=.9,
                              change_score=confidence, phase='stable'), i*.4, wall)

    def test_protected_capture_explains_attacker_without_changing_game(self):
        game = self.game()
        before = game.board.fen()
        observed = self.attempted(game)
        with patch('chess_game.threading.Thread') as engine:
            self.feed(game, observed, count=4)
            self.assertIsNone(game.move_warning)
            self.feed(game, observed, start=4, count=1)
            engine.assert_not_called()
        self.assertEqual(game.board.fen(), before)
        self.assertFalse(game.history)
        self.assertIsNone(game.pending)
        self.assertEqual(game.state, 'restore')
        self.assertEqual(game.event_kind, 'move_warning')
        self.assertIn('Move recognized: king e1 to d2', game.message)
        self.assertIn('rook on d2 is protected by the bishop on c3', game.message)
        self.assertIn('Return your king to e1', game.message)
        self.assertIn('captured rook on d2', game.message)
        self.assertEqual(game.repair_expected.board_fen(), game.physical().board_fen())
        self.assertFalse(game.suggestions)
        self.assertFalse(game.repair_arrows)
        snapshot = game.snapshot(101.6)
        self.assertEqual(snapshot['move_warning']['attackers'], ['c3'])
        self.assertEqual(snapshot['fen'], before)
        self.assertEqual(game.serialize()['moves'], [])

    def test_warning_and_recovery_target_survive_repetition_and_noise(self):
        game = self.game()
        observed = self.attempted(game)
        game.accept_observation(observed)
        event = game.event_id
        game.accept_observation(observed)
        noise = observed.copy()
        noise.remove_piece_at(chess.C3)
        game.accept_observation(noise)
        self.assertEqual(game.event_id, event)
        self.assertFalse(game.suggestions)
        self.assertEqual(game.repair_expected.board_fen(), game.physical().board_fen())
        self.assertFalse(game.history)

    def test_restore_or_direct_legal_move_clears_warning(self):
        for restore in (True, False):
            with self.subTest(restore=restore):
                game = self.game()
                game.accept_observation(self.attempted(game))
                corrected = game.physical() if restore else game.physical_after(chess.Move.from_uci('e1f1'))
                with patch('chess_game.threading.Thread'):
                    self.feed(game, corrected)
                self.assertIsNone(game.move_warning)
                self.assertFalse(game.errors)
                self.assertFalse(game.suggestions)
                self.assertEqual(len(game.history), 0 if restore else 1)
                self.assertEqual(game.state, 'player_turn' if restore else 'thinking')

    def test_low_confidence_and_incomplete_captures_are_not_move_warnings(self):
        game = self.game()
        self.feed(game, self.attempted(game), confidence=.4)
        self.assertIsNone(game.move_warning)
        observed = game.physical()
        observed.remove_piece_at(chess.D2)
        self.feed(game, observed, start=5)
        self.assertIsNone(game.move_warning)
        self.assertFalse(game.history)

    def test_other_king_safety_cases(self):
        cases = [
            ('7k/8/8/8/8/2b5/8/4K3 w - - 0 1', 'e1d2', 'd2 is attacked by the bishop on c3'),
            ('k3r3/8/8/8/8/8/4R3/4K3 w - - 0 1', 'e2f2', 'king in check from the rook on e8'),
            ('7k/8/8/8/8/8/3rK3/8 w - - 0 1', 'e2f2', 'f2 is attacked by the rook on d2'),
            ('8/8/8/8/2B5/3R4/4k3/7K b - - 0 1', 'e2d3', 'rook on d3 is protected by the bishop on c4'),
        ]
        for fen, uci, reason in cases:
            with self.subTest(uci=uci):
                game = self.game(fen)
                game.accept_observation(self.attempted(game, uci))
                self.assertIn(reason, game.message)
                self.assertFalse(game.history)

    def test_en_passant_exposing_king_restores_captured_pawn(self):
        game = self.game('7k/8/8/r4pPK/8/8/8/8 w - f6 0 1')
        game.accept_observation(self.attempted(game, 'g5f6'))
        self.assertIn('king in check from the rook on a5', game.message)
        self.assertIn('captured pawn on f5', game.message)
        self.assertFalse(game.history)

    def test_legal_protected_capture_by_nonking_is_accepted(self):
        game = self.game('7k/8/8/8/8/2b5/3r4/3RK3 w - - 0 1')
        move = chess.Move.from_uci('d1d2')
        self.assertTrue(game.board.is_legal(move))
        with patch('chess_game.threading.Thread'):
            game.accept_observation(game.physical_after(move))
        self.assertIsNone(game.move_warning)
        self.assertEqual(game.history[0]['uci'], move.uci())

    def test_reconcile_explains_but_strict_saved_position_still_requires_restore(self):
        for strict in (True, False):
            with self.subTest(strict=strict):
                game = self.game()
                game.state = 'reconcile'
                game.resume_state = 'player_turn'
                game.strict_recovery = strict
                game.accept_observation(self.attempted(game))
                self.assertEqual(game.move_warning is None, strict)
                self.assertFalse(game.history)
                game.accept_observation(game.physical())
                self.assertEqual(game.state, 'player_turn')
                self.assertIsNone(game.move_warning)


if __name__ == '__main__':
    unittest.main()
