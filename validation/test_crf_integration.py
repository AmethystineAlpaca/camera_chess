"""Exercise the actual packaged weights through the live game controller."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import chess
import numpy as np

from chess_game import Game
from crf_observation import CRFObservation, CLASSES, SQUARES, MODELS, model_choices


def reading(board, sequence, confuse=None):
    q = np.full((64, 13), .01 / 12, dtype=np.float32)
    for i, square in enumerate(SQUARES):
        piece = board.piece_at(square)
        q[i, CLASSES.index(piece.symbol()) if piece else 0] = .99
    if confuse:
        name, wrong = confuse
        i = SQUARES.index(chess.parse_square(name))
        truth = int(q[i].argmax())
        q[i] = .02 / 11
        q[i, CLASSES.index(wrong)] = .93
        q[i, truth] = .05
    raw = chess.Board(None)
    for square, category in zip(SQUARES, q.argmax(-1)):
        if category:
            raw.set_piece_at(square, chess.Piece.from_symbol(CLASSES[int(category)]))
    return dict(raw=raw.board_fen(), sequence=sequence, capture_time=100 + sequence * .3,
                phase='stable', min_score=float(q.max(-1).min()), min_margin=.8, change_score=.99,
                square_probabilities={chess.square_name(s): dict(zip(CLASSES, map(float, row))) for s, row in zip(SQUARES, q)})


class CRFIntegrationTests(unittest.TestCase):
    def test_all_packaged_models_use_live_gate_and_manual_constraints(self):
        board = chess.Board()
        board.push_uci('e2e4')
        for model in MODELS:
            with self.subTest(model=model):
                adapter = CRFObservation(model=model)
                self.assertIsNone(adapter.error)
                for i in range(4):
                    result = adapter.step(chess.STARTING_FEN, 'turn', reading(board, i), {}, i * .3)
                self.assertEqual(result['status'], 'stable')
                self.assertEqual(result['move_uci'], 'e2e4')
                result = adapter.step(chess.STARTING_FEN, 'next', reading(board, 5), {}, 2)
                self.assertNotEqual(result['status'], 'stable')
                result = adapter.step(chess.STARTING_FEN, 'next', reading(board, 6), {'e4': {'piece': 'k'}}, 2.3)
                self.assertEqual(result['status'], 'uncertain')

    def test_labels_match_published_evaluation(self):
        import json
        report = json.loads((Path(__file__).resolve().parents[1] / 'docs/evaluations/neural-postprocessors-20261011.json').read_text())
        aliases = {'small': 'crf_v1_25', 'large': 'crf_v2_250'}
        for choice in model_choices():
            summary = report['summary_by_method'][aliases.get(choice['id'], choice['id'])]
            self.assertIn(f"{summary['single_random_previous_exact_board_accuracy']:.2%} accuracy on evaluation set", choice['label'])

    def test_both_models_recognize_move_with_fresh_votes(self):
        board = chess.Board()
        board.push_uci('e2e4')
        for model, count in [('small', 25), ('large', 250)]:
            with self.subTest(model=model):
                adapter = CRFObservation(model=model)
                self.assertIsNone(adapter.error)
                self.assertEqual(len(adapter.weights), count)
                for i in range(4):
                    result = adapter.step(chess.STARTING_FEN, 'turn', reading(board, i), {}, i * .3)
                self.assertEqual(result['status'], 'stable')
                self.assertEqual(result['move_uci'], 'e2e4')
                adapter.reset()
                result = adapter.step(chess.STARTING_FEN, 'turn', reading(board, 5), {}, 2)
                self.assertNotEqual(result['status'], 'stable')

    def test_invalid_model_is_rejected(self):
        with self.assertRaises(ValueError):
            CRFObservation(model='unknown')

    def game(self, fen=chess.STARTING_FEN):
        g = Game(object(), postprocessor=CRFObservation())
        self.assertIsNone(g.postprocessor.error)
        g.reset(aligned=True)
        g.board = chess.Board(fen); g.state = 'player_turn'
        return g

    def feed(self, game, board, start=0, count=4, confuse=None):
        with patch('chess_game.threading.Thread'):
            for i in range(start, start + count):
                record = reading(board, i, confuse)
                original = deepcopy(record)
                game.observe(record, i * .3, record['capture_time'])
                self.assertEqual(record, original, 'Raw probabilities/labels must remain unchanged')
        return game.snapshot(game.last_frame)

    def correct(self, game, changes, reviewed=None):
        with patch('chess_game.time.time', return_value=game.last_frame), patch('chess_game.threading.Thread'):
            game.correct_squares(changes, game.position_token(), reviewed)

    def test_noisy_move_advances_automatically_and_updates_next_reference(self):
        g = self.game(); after = g.board.copy(); after.push_uci('e2e4')
        snapshot = self.feed(g, after, count=8, confuse=('e4', 'B'))
        self.assertEqual(g.board.fen(), after.fen())
        self.assertEqual(snapshot['observed_placement'], after.board_fen())
        self.assertNotEqual(snapshot['raw_placement'], after.board_fen())
        self.assertEqual(len(g.history), 1)
        self.assertFalse(hasattr(g, 'confirm_recognition'))
        g.pending = chess.Move.from_uci('e7e5'); g.state = 'computer_move'
        final = after.copy(); final.push(g.pending)
        self.feed(g, final, start=8)
        self.assertEqual(g.board.fen(), final.fen())
        self.assertEqual(len(g.history), 2)
        self.assertEqual(g.state, 'player_turn')

    def test_fix_false_move_restores_unchanged_board_and_cancels_engine(self):
        for pending in (False, True):
            with self.subTest(pending=pending):
                g = self.game(); wrong = g.board.copy(); wrong.push_uci('e2e4')
                self.feed(g, wrong)
                old_job = g.engine_job
                if pending:
                    g.pending = chess.Move.from_uci('e7e5'); g.state = 'computer_move'
                self.correct(g, {'e4': '.', 'e2': 'P'})
                self.assertEqual(g.board.fen(), chess.STARTING_FEN)
                self.assertEqual(g.observed.board_fen(), chess.Board().board_fen())
                self.assertFalse(g.history)
                self.assertIsNone(g.pending)
                self.assertIsNone(g.engine_job)
                self.assertEqual(g.state, 'player_turn')
                self.assertIsNotNone(old_job)
                # The same wrong classifier output cannot re-commit the move.
                self.feed(g, wrong, start=4)
                self.assertFalse(g.history)

    def test_fix_wrong_move_replaces_history_and_recomputes_reply(self):
        g = self.game(); wrong = g.board.copy(); wrong.push_uci('e2e4')
        frozen = self.feed(g, wrong, confuse=('a2', 'B'))
        old_job = g.engine_job
        self.correct(g, {'e4': '.', 'e2': 'P', 'd2': '.', 'd4': 'P'}, frozen['observed_placement'])
        actual = chess.Board(); actual.push_uci('d2d4')
        self.assertEqual(g.board.fen(), actual.fen())
        self.assertEqual([m['uci'] for m in g.history], ['d2d4'])
        self.assertEqual(g.state, 'thinking')
        self.assertNotEqual(g.engine_job, old_job)
        self.assertEqual(g.board.piece_at(chess.A2), chess.Piece(chess.PAWN, chess.WHITE))

    def test_corrected_reference_allows_a_later_genuine_move(self):
        g = self.game(); moved = g.board.copy(); moved.push_uci('e2e4')
        self.feed(g, moved)
        self.correct(g, {'e4': '.', 'e2': 'P'})
        self.feed(g, chess.Board(), start=4)
        self.feed(g, moved, start=8, count=8)
        self.assertEqual(g.board.fen(), moved.fen())
        self.assertEqual(len(g.history), 1)

    def test_failed_correction_keeps_editor_and_reference(self):
        g = self.game(); self.feed(g, chess.Board())
        g.begin_correction('draft', g.position_token())
        with self.assertRaises(ValueError): self.correct(g, {'invalid': 'P'})
        self.assertTrue(g.editing_correction())
        self.assertFalse(g.manual_corrections)
        self.assertEqual(g.board.fen(), chess.STARTING_FEN)

    def test_old_engine_reply_after_fix_is_discarded(self):
        from types import SimpleNamespace
        g = self.game(); wrong = g.board.copy(); wrong.push_uci('e2e4')
        self.feed(g, wrong); old_job = g.engine_job
        self.correct(g, {'e4': '.', 'e2': 'P', 'd2': '.', 'd4': 'P'})
        g.engine = SimpleNamespace(play=lambda *a, **k: SimpleNamespace(move=chess.Move.from_uci('e7e5')))
        new_job = g.engine_job
        g.think(g.id, wrong, g.difficulty, old_job)
        self.assertIsNone(g.pending)
        self.assertEqual(g.engine_job, new_job)
        self.assertEqual(g.state, 'thinking')

    def test_fix_computer_false_move_restores_pending_instruction(self):
        g = self.game(); after = g.board.copy(); after.push_uci('e2e4')
        self.feed(g, after)
        g.pending = chess.Move.from_uci('e7e5'); g.state = 'computer_move'
        wrong = after.copy(); wrong.push(g.pending)
        self.feed(g, wrong, start=4)
        self.correct(g, {'e5': '.', 'e7': 'p'})
        self.assertEqual(g.board.fen(), after.fen())
        self.assertEqual(g.pending.uci(), 'e7e5')
        self.assertEqual(g.state, 'computer_move')
        self.assertEqual(len(g.history), 1)

    def test_turn_protocol_prevents_inference_running_ahead_of_engine(self):
        g = self.game(); player = g.board.copy(); player.push_uci('e2e4')
        self.feed(g, player)
        imagined_reply = player.copy(); imagined_reply.push_uci('e7e5')
        # Even an apparently perfect legal reply cannot advance while the
        # engine has not issued its instruction.
        self.feed(g, imagined_reply, start=4, count=8)
        self.assertEqual(len(g.history), 1)
        self.assertEqual(g.state, 'thinking')
        g.pending = chess.Move.from_uci('c7c5'); g.state = 'computer_move'
        self.feed(g, imagined_reply, start=12)
        self.assertEqual(len(g.history), 1)
        self.assertTrue(g.errors)
        # Restoring the current board is a correction, not an undo.
        self.correct(g, {'e5': '.', 'e7': 'p'})
        self.assertEqual(g.board.fen(), player.fen())
        self.assertEqual(g.pending.uci(), 'c7c5')
        self.assertFalse(g.errors)

    def test_computer_reply_requires_new_votes_after_instruction(self):
        g = self.game(); player = g.board.copy(); player.push_uci('e2e4')
        self.feed(g, player)
        reply = player.copy(); reply.push_uci('e7e5')
        self.feed(g, reply, start=4)
        g.pending = chess.Move.from_uci('e7e5'); g.state = 'computer_move'
        self.feed(g, reply, start=8, count=2)
        self.assertEqual(len(g.history), 1)
        self.feed(g, reply, start=10, count=1)
        self.assertEqual(len(g.history), 2)

    def test_edit_lease_holds_moves_cancel_and_expiry_resume_with_fresh_votes(self):
        for release in ('cancel', 'expire'):
            with self.subTest(release=release):
                g = self.game(); after = g.board.copy(); after.push_uci('e2e4')
                g.begin_correction('draft', g.position_token())
                self.feed(g, after, count=6)
                self.assertFalse(g.history)
                g.end_correction('old-draft')
                self.assertTrue(g.editing_correction())
                if release == 'cancel': g.end_correction('draft')
                else: g.correction_edit = ('draft', 0)
                self.feed(g, after, start=6, count=1)
                self.assertFalse(g.history)
                self.feed(g, after, start=7)
                self.assertEqual(g.history[0]['uci'], 'e2e4')

    def test_pending_reply_does_not_invalidate_edit_but_move_does(self):
        g = self.game(); after = g.board.copy(); after.push_uci('e2e4')
        self.feed(g, after)
        token = g.position_token()
        g.begin_correction('draft', token)
        g.pending = chess.Move.from_uci('e7e5'); g.state = 'computer_move'
        self.assertEqual(g.position_token(), token)
        self.correct(g, {'e4': '.', 'e2': 'P'})
        with self.assertRaises(ValueError): g.begin_correction('stale', token)

    def test_edit_preserves_correct_move_and_does_not_recompute_engine(self):
        g = self.game(); after = g.board.copy(); after.push_uci('e2e4')
        self.feed(g, after); job = g.engine_job
        self.correct(g, {'a2': 'P'})
        self.assertEqual(g.board.fen(), after.fen())
        self.assertEqual(g.engine_job, job)
        self.assertEqual(len(g.history), 1)

    def test_illegal_observation_is_automatic_but_never_commits_to_pgn(self):
        g = self.game(); wrong = g.board.copy()
        piece = wrong.remove_piece_at(chess.E2); wrong.set_piece_at(chess.E5, piece)
        self.feed(g, wrong, count=8)
        self.assertEqual(g.board.fen(), chess.STARTING_FEN)
        self.assertFalse(g.history)
        self.assertEqual(g.observed.board_fen(), wrong.board_fen())
        self.assertEqual(g.state, 'restore')
        self.assertTrue(g.errors)

    def test_occlusion_malformed_stale_and_duplicate_frames_do_not_commit(self):
        g = self.game(); lifted = g.board.copy(); lifted.remove_piece_at(chess.E2)
        self.assertEqual(self.feed(g, lifted)['recognition_postprocess']['status'], 'uncertain')
        self.assertFalse(g.history)
        after = g.board.copy(); after.push_uci('e2e4')
        record = reading(after, 5)
        for i in range(10): g.observe(record, i * .3, record['capture_time'])
        self.assertFalse(g.history)
        record = reading(after, 6); record['square_probabilities']['e4']['P'] = float('nan')
        g.observe(record, 3.1, record['capture_time'])
        self.assertFalse(g.history)
        self.assertEqual(g.camera_phase, 'uncertain')
        record = reading(after, 7)
        g.observe(record, 4, record['capture_time'] + 4)
        self.assertFalse(g.history)
        self.assertEqual(g.camera_phase, 'stale')

    def test_normal_auto_orientation_uses_crf_and_flip_holds_next_move(self):
        g = Game(object(), postprocessor=CRFObservation()); g.reset(computer='auto')
        def oriented(board, sequence, near='black'):
            record = reading(board, sequence)
            record.update(orientation=dict(status='detected', near=near), orientation_at=record['capture_time'])
            with patch('chess_game.threading.Thread'):
                g.observe(record, sequence * .3, record['capture_time'])
        for i in range(6): oriented(chess.Board(), i)
        self.assertEqual(g.state, 'player_turn')
        self.assertTrue(g.side_auto)
        after = g.board.copy(); after.push_uci('e2e4')
        for i in range(6, 10): oriented(after, i)
        self.assertEqual(len(g.history), 1)
        g.pending = chess.Move.from_uci('e7e5'); g.state = 'computer_move'
        after.push(g.pending)
        for i in range(10, 15): oriented(after, i, near='white')
        self.assertEqual(len(g.history), 1)
        self.assertEqual(g.orientation_status, 'mismatch')

    def test_castling_en_passant_and_promotion_keep_existing_rules(self):
        for fen, move in [('r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1', 'e1g1'),
                          ('4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1', 'e5d6'),
                          ('4k3/P7/8/8/8/8/8/4K3 w - - 0 1', 'a7a8n')]:
            g = self.game(fen); after = g.board.copy(); after.push_uci(move)
            self.feed(g, after, count=8)
            if move.endswith('n'):
                self.assertFalse(g.history)
                self.assertIsNotNone(g.promotion)
                with patch('chess_game.time.time', return_value=g.last_frame), patch('chess_game.threading.Thread'):
                    g.confirm_promotion(g.promotion['id'], 'n', 'piece')
            self.assertEqual(g.board.fen(), after.fen())

    def test_reset_and_undo_drop_old_correction_checkpoints(self):
        for action in ('reset', 'undo_round'):
            g = self.game(); after = g.board.copy(); after.push_uci('e2e4')
            self.feed(g, after)
            self.assertEqual(len(g.recognition_checkpoints), 1)
            g.begin_correction('draft', g.position_token())
            getattr(g, action)()
            self.assertFalse(g.recognition_checkpoints)
            self.assertIsNone(g.correction_edit)

    def test_proxy_uses_physical_scoring_and_saved_recovery_remains_strict(self):
        g = self.game(); g.proxies = {chess.A2}
        result = self.feed(g, g.physical())['recognition_postprocess']
        self.assertEqual(result['status'], 'stable')
        self.assertEqual(result['scorer'], 'physical_visual_likelihood')
        g = self.game(); g.strict_recovery = True; g.state = 'reconcile'; g.resume_state = 'player_turn'
        self.assertEqual(self.feed(g, g.board, count=1)['recognition_postprocess']['status'], 'bypassed')

    def test_missing_checkpoint_holds_instead_of_committing_raw_move(self):
        with tempfile.TemporaryDirectory() as d:
            g = Game(object(), postprocessor=CRFObservation(Path(d) / 'missing.json'))
            g.reset(aligned=True); g.state = 'player_turn'
            after = g.board.copy(); after.push_uci('e2e4')
            self.assertEqual(self.feed(g, after)['recognition_postprocess']['status'], 'unavailable')
            self.assertFalse(g.history)


if __name__ == '__main__':
    unittest.main()
