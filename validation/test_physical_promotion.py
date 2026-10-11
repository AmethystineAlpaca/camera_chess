"""Offline sequence regressions for physical appearance and logical identity."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

import chess

from chess_game import Game
from crf_observation import CRFObservation
from test_crf_integration import reading


class PhysicalPromotionTests(unittest.TestCase):
    def game(self, color=chess.WHITE, computer=False, model='small'):
        g = Game(object(), postprocessor=CRFObservation(model=model))
        g.reset(aligned=True)
        g.initial_fen = '4k3/P7/8/8/8/8/7p/4K3 ' + ('w' if color else 'b') + ' - - 0 1'
        g.board = chess.Board(g.initial_fen)
        g.mode = 'endgame'
        g.computer = color if computer else not color
        g.state = 'computer_move' if computer else 'player_turn'
        self.sequence = 0
        return g

    def feed(self, g, board, count=8, confuse=None, edit=None):
        with patch('chess_game.threading.Thread'):
            for _ in range(count):
                self.sequence += 1
                record = reading(board, self.sequence, confuse)
                if edit:
                    edit(record)
                original = deepcopy(record)
                g.observe(record, self.sequence * .3, record['capture_time'])
                self.assertEqual(record, original)
                json.dumps(g.snapshot(record['capture_time']))

    def confirm(self, g, symbol, pawn=True):
        with patch('chess_game.time.time', return_value=g.last_frame), patch('chess_game.threading.Thread'):
            g.confirm_promotion(g.promotion['id'], symbol, 'pawn' if pawn else 'piece')

    def prepare(self, g, symbol='q', pawn=True, computer=False, confuse=None):
        move = chess.Move.from_uci(('a7a8' if g.board.turn else 'h2h1') + symbol)
        if computer:
            g.pending = move
            g.begin_promotion(move)
        self.feed(g, g.physical_after(move, pawn), confuse=confuse)
        return move

    def test_both_sides_all_identities_and_representations_with_unrelated_type_noise(self):
        for color in (chess.WHITE, chess.BLACK):
            for computer in (False, True):
                for symbol in 'qrbn':
                    for pawn in (False, True):
                        with self.subTest(color=color, computer=computer, symbol=symbol, pawn=pawn):
                            g = self.game(color, computer)
                            noise = ('h2', 'q') if color else ('a7', 'Q')
                            move = self.prepare(g, symbol, pawn, computer, noise)
                            self.assertIsNotNone(g.promotion)
                            self.assertFalse(g.history)
                            choice = next(c for c in g.promotion_options(g.last_frame)['choices'] if c['piece'] == symbol)
                            self.assertTrue(choice['pawn_ready' if pawn else 'piece_ready'])
                            self.confirm(g, symbol, pawn)
                            self.assertEqual(g.board.peek(), move)
                            self.assertEqual(g.board.piece_type_at(move.to_square), move.promotion)
                            self.assertEqual(g.proxies, {move.to_square} if pawn else set())

    def test_manual_underpromotion_keeps_pawn_through_reply_move_and_reload(self):
        for symbol, uci in [('q','a8a6'),('r','a8a6'),('b','a8c6'),('n','a8b6')]:
            with self.subTest(symbol=symbol):
                g=self.game(model='move_mlp2')
                # Recognition guessed a real queen, although the user keeps a pawn.
                self.prepare(g,'q',pawn=False)
                self.assertEqual(g.promotion['representation'],'pawn')
                with patch('chess_game.threading.Thread'):
                    g.confirm_promotion(g.promotion['id'],symbol,'pawn',manual=True)
                self.assertEqual(g.history[-1]['uci'],'a7a8'+symbol)
                self.assertTrue(g.history[-1]['pawn_promotion'])
                self.assertEqual(g.proxies,{chess.A8})
                g.pending=chess.Move.from_uci('e8f7');g.state='computer_move'
                self.feed(g,g.physical_after(g.pending))
                self.assertEqual(g.state,'player_turn')
                self.feed(g,g.physical_after(chess.Move.from_uci(uci)))
                self.assertEqual(g.board.peek().uci(),uci)
                target=chess.parse_square(uci[2:4])
                self.assertEqual(g.proxies,{target})
                self.assertEqual(g.board.piece_type_at(target),chess.Piece.from_symbol(symbol).piece_type)
                h=self.game();h.load_data(g.serialize(),strict=True)
                self.assertEqual(h.proxies,{target})
                self.assertEqual(h.board.fen(),g.board.fen())

    def test_confirm_does_not_choose_identity_from_pawn_appearance(self):
        g = self.game(); self.prepare(g)
        choices = g.promotion_options(g.last_frame)['choices']
        self.assertEqual(len(choices), 4)
        self.assertTrue(all(c['pawn_ready'] for c in choices))
        self.assertFalse(g.history)
        self.confirm(g, 'n')
        self.assertEqual(g.board.piece_type_at(chess.A8), chess.KNIGHT)

    def test_computer_knight_confirm_is_disabled_on_partial_or_wrong_occupancy(self):
        for kind in ('source', 'lifted', 'extra', 'wrong-color', 'bad-data'):
            with self.subTest(kind=kind):
                g = self.game(computer=True)
                move = chess.Move.from_uci('a7a8n'); g.pending = move; g.begin_promotion(move)
                target = g.physical_after(move, True)
                if kind == 'source': target.set_piece_at(chess.A7, chess.Piece(chess.PAWN, chess.WHITE))
                if kind == 'lifted': target.remove_piece_at(chess.A8)
                if kind == 'extra': target.set_piece_at(chess.D5, chess.Piece(chess.PAWN, chess.WHITE))
                if kind == 'wrong-color': target.set_piece_at(chess.A8, chess.Piece(chess.PAWN, chess.BLACK))
                edit = (lambda r: r['square_probabilities']['a8'].update(P=-1.)) if kind == 'bad-data' else None
                self.feed(g, target, count=8, edit=edit)
                choice = g.promotion_options(g.last_frame)['choices'][0]
                self.assertFalse(choice['pawn_ready']); self.assertFalse(choice['piece_ready'])
                self.assertTrue(choice['pawn_reason'])
                with self.assertRaises(ValueError): self.confirm(g, 'n')
                self.assertFalse(g.history)

    def test_diagonal_proxy_move_and_capture_update_identity_atomically(self):
        g = self.game()
        g.initial_fen = '4k3/P7/8/7p/8/8/8/4K3 w - - 0 1'
        g.board = chess.Board(g.initial_fen)
        self.prepare(g); self.confirm(g, 'q')
        g.pending = chess.Move.from_uci('e8f7'); g.state = 'computer_move'
        self.feed(g, g.physical_after(g.pending))
        move = chess.Move.from_uci('a8d5')
        lifted = g.physical(); lifted.remove_piece_at(chess.A8)
        self.feed(g, lifted, count=5)
        self.assertEqual(g.proxies, {chess.A8}); self.assertEqual(len(g.history), 2)
        self.feed(g, g.physical_after(move))
        self.assertEqual(g.board.peek(), move); self.assertEqual(g.proxies, {chess.D5})
        self.assertEqual(g.board.piece_type_at(chess.D5), chess.QUEEN)
        g.pending = chess.Move.from_uci('f7g6'); g.state = 'computer_move'
        self.feed(g, g.physical_after(g.pending))
        capture = chess.Move.from_uci('d5h5')
        self.feed(g, g.physical_after(capture))
        self.assertEqual(g.board.peek(), capture); self.assertEqual(g.proxies, {chess.H5})

    def test_wrong_physical_type_cannot_impersonate_moving_proxy(self):
        g = self.game(); self.prepare(g); self.confirm(g, 'q')
        g.pending = chess.Move.from_uci('e8f7'); g.state = 'computer_move'
        self.feed(g, g.physical_after(g.pending))
        target = g.physical_after(chess.Move.from_uci('a8d5'))
        self.feed(g, target, count=8, confuse=('d5', 'R'))
        self.assertEqual(len(g.history), 2); self.assertEqual(g.proxies, {chess.A8})
        self.assertIn('D5', g.crf_result['problem_squares'])

    def test_stable_confirm_is_revoked_by_new_uncertain_or_stale_frame(self):
        g = self.game(computer=True); move = self.prepare(g, 'n', computer=True)
        identity = g.promotion['id']
        lifted = g.physical_after(move, True); lifted.remove_piece_at(chess.A8)
        self.feed(g, lifted, count=1)
        self.assertEqual(g.promotion['id'], identity)
        self.assertFalse(g.promotion_options(g.last_frame)['choices'][0]['pawn_ready'])
        self.feed(g, g.physical_after(move, True))
        self.assertFalse(g.promotion_options(g.last_frame + 4)['choices'][0]['pawn_ready'])

    def test_only_pending_computer_route_can_be_confirmed(self):
        g = self.game(computer=True)
        g.pending = chess.Move.from_uci('a7a8n'); g.begin_promotion(g.pending)
        other = g.physical_after(chess.Move.from_uci('e1d1'))
        self.feed(g, other, count=8)
        self.assertFalse(g.promotion_options(g.last_frame)['choices'][0]['pawn_ready'])
        self.assertFalse(g.history)

    def test_computer_black_knight_proxy_jumps_and_keeps_its_identity(self):
        g = self.game(chess.BLACK, computer=True)
        self.prepare(g, 'n', computer=True); self.confirm(g, 'n')
        self.feed(g, g.physical_after(chess.Move.from_uci('e1d2')))
        g.pending = chess.Move.from_uci('h1f2'); g.state = 'computer_move'
        self.feed(g, g.physical_after(g.pending))
        self.assertEqual(g.proxies, {chess.F2})
        self.assertEqual(g.board.piece_at(chess.F2), chess.Piece(chess.KNIGHT, chess.BLACK))

    def test_illegal_proxy_relocation_is_not_committed(self):
        g = self.game(); self.prepare(g); self.confirm(g, 'q')
        g.pending = chess.Move.from_uci('e8f7'); g.state = 'computer_move'
        self.feed(g, g.physical_after(g.pending))
        wrong = g.physical(); wrong.remove_piece_at(chess.A8)
        wrong.set_piece_at(chess.B6, chess.Piece(chess.PAWN, chess.WHITE))
        self.feed(g, wrong)
        self.assertEqual(len(g.history), 2)
        self.assertEqual(g.proxies, {chess.A8})
        self.assertEqual(g.crf_result['operation'], 'illegal_relocation')

    def test_capture_of_proxy_clears_mapping_and_undo_restores_it(self):
        g = self.game()
        g.initial_fen = '1r2k3/P7/8/8/8/8/7p/4K3 w - - 0 1'
        g.board = chess.Board(g.initial_fen)
        self.prepare(g); self.confirm(g, 'q')
        g.pending = chess.Move.from_uci('e8f7'); g.state = 'computer_move'
        self.feed(g, g.physical_after(g.pending))
        self.feed(g, g.physical_after(chess.Move.from_uci('e1d1')))
        g.pending = chess.Move.from_uci('b8a8'); g.state = 'computer_move'
        self.feed(g, g.physical_after(g.pending))
        self.assertFalse(g.proxies)
        self.assertEqual(g.board.piece_at(chess.A8), chess.Piece(chess.ROOK, chess.BLACK))
        g.undo_round()
        self.assertEqual(g.proxies, {chess.A8})

    def test_new_path_can_be_disabled_without_changing_saved_mapping(self):
        g = self.game(computer=True)
        with patch.dict('os.environ', {'CHESS_PHYSICAL_PROMOTION': '0'}):
            self.prepare(g, 'n', computer=True)
            self.assertEqual(g.crf_result['status'], 'bypassed')
            self.confirm(g, 'n')
        self.assertEqual(g.proxies, {chess.A8})

    def test_temporal_evidence_is_reset_when_proxy_context_changes(self):
        adapter = CRFObservation()
        g = self.game(); move = chess.Move.from_uci('a7a8q')
        target = g.physical_after(move, True)
        for i in range(3):
            result = adapter.step_physical(g.board.fen(), 'old', reading(target, i), {}, i * .3)
        self.assertEqual(result['status'], 'stable')
        result = adapter.step_physical(g.board.fen(), 'new', reading(target, 4), {}, 1.2)
        self.assertEqual(result['status'], 'settling')
        self.assertEqual(result['votes'], 1)

    def test_multiple_proxies_only_move_the_matching_mapping(self):
        g = self.game()
        g.initial_fen = '4k3/PP6/8/8/8/8/7p/4K3 w - - 0 1'
        g.board = chess.Board(g.initial_fen)
        for uci, pawn in [('a7a8q', True), ('e8f7', False), ('b7b8r', True), ('f7g6', False)]:
            g.commit(chess.Move.from_uci(uci), pawn_promotion=pawn)
        g.state = 'player_turn'
        self.feed(g, g.physical_after(chess.Move.from_uci('a8d5')))
        self.assertEqual(g.proxies, {chess.D5, chess.B8})
        self.assertEqual(g.board.piece_type_at(chess.D5), chess.QUEEN)
        self.assertEqual(g.board.piece_type_at(chess.B8), chess.ROOK)

    def test_old_source_correction_does_not_pin_moving_proxy(self):
        g = self.game(); self.prepare(g); self.confirm(g, 'q')
        g.pending = chess.Move.from_uci('e8f7'); g.state = 'computer_move'
        self.feed(g, g.physical_after(g.pending))
        g.manual_corrections['a8'] = dict(seen='.', piece='P', occupancy_confirmed=False)
        self.feed(g, g.physical_after(chess.Move.from_uci('a8d5')))
        self.assertEqual(g.proxies, {chess.D5})
        self.assertNotIn('a8', g.manual_corrections)

    def test_mapping_survives_save_load_and_undo_with_new_inference_context(self):
        g = self.game(); self.prepare(g); self.confirm(g, 'q')
        g.pending = chess.Move.from_uci('e8f7'); g.state = 'computer_move'
        self.feed(g, g.physical_after(g.pending))
        saved = g.serialize()
        h = self.game(); h.load_data(saved, automatic=True, strict=True)
        self.feed(h, g.physical(), count=8)
        self.assertEqual(h.proxies, {chess.A8})
        self.feed(h, h.physical_after(chess.Move.from_uci('a8d5')))
        self.assertEqual(h.proxies, {chess.D5})
        h.undo_round()
        self.assertEqual(h.proxies, {chess.A8})
        self.feed(h, h.physical(), count=8)
        self.assertEqual(h.board.piece_type_at(chess.A8), chess.QUEEN)


if __name__ == '__main__':
    unittest.main()
