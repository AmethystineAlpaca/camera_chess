"""Promotion is an explicit choice; physical pawns retain logical piece identities."""
import copy
import time
import unittest
from unittest.mock import patch

import chess
from chess_game import Game, instruction, proxy_badges


class PromotionTests(unittest.TestCase):
    def test_proxy_badge_remains_an_inline_html_svg_on_first_render(self):
        import xml.etree.ElementTree as ET
        # No earlier error-board rendering should be required to register SVG namespaces.
        with patch.dict(ET._namespace_map, {}, clear=True):
            svg=proxy_badges(chess.svg.board(chess.Board()),{chess.A8})
        self.assertTrue(svg.startswith('<svg '))
        self.assertNotIn('<ns0:',svg)
        self.assertIn('data-square="a8"',svg)

    def game(self, fen='4k3/P7/8/8/8/8/7p/4K3 w - - 0 1', computer=False):
        g = Game(object())
        g.board = chess.Board(fen)
        self.assertTrue(g.board.is_valid())
        g.initial_fen = fen
        g.mode = 'endgame'
        g.computer = g.board.turn if computer else not g.board.turn
        g.state = 'computer_move' if computer else 'player_turn'
        return g

    def stable(self, g, board):
        g.last_frame = g.last_supported = time.time()
        g.camera_phase = 'stable'
        with patch('chess_game.threading.Thread'):
            g.accept_observation(board)

    def confirm(self, g, piece='q', representation='pawn'):
        with patch('chess_game.threading.Thread'):
            g.confirm_promotion(g.promotion['id'], piece, representation)

    def promote(self, g, uci='a7a8q', pawn=True):
        move = chess.Move.from_uci(uci)
        self.stable(g, g.physical_after(move, pawn))
        self.assertFalse(g.board.move_stack)
        self.confirm(g, uci[4], 'pawn' if pawn else 'piece')

    def test_all_four_choices_both_colors_and_representations(self):
        for color in ('white', 'black'):
            for piece in 'qrbn':
                for pawn in (True, False):
                    with self.subTest(color=color, piece=piece, pawn=pawn):
                        fen = '4k3/P7/8/8/8/8/7p/4K3 '+('w' if color=='white' else 'b')+' - - 0 1'
                        g = self.game(fen)
                        uci = ('a7a8' if color=='white' else 'h2h1')+piece
                        self.promote(g, uci, pawn)
                        target = chess.parse_square(uci[2:4])
                        self.assertEqual(g.board.piece_at(target).symbol().lower(), piece)
                        self.assertEqual(g.proxies, {target} if pawn else set())
                        self.assertTrue(g.board.is_valid())
                        self.assertIsNone(g.promotion)

    def test_pawn_never_auto_queens_or_times_out(self):
        g = self.game()
        board = g.physical_after(chess.Move.from_uci('a7a8q'), True)
        for now in (1, 10, 100):
            with patch('chess_game.time.monotonic', return_value=now):
                self.stable(g, board)
            self.assertEqual(g.state, 'player_turn')
            self.assertFalse(g.history)
            self.assertFalse(g.errors)
        self.assertEqual(len(g.promotion_options(time.time())['choices']), 4)

    def test_capture_promotion_waits_for_complete_board(self):
        g = self.game('1r2k3/P7/8/8/8/8/7p/4K3 w - - 0 1')
        move = chess.Move.from_uci('a7b8n')
        after = g.physical_after(move, True)
        wrong = after.copy();wrong.set_piece_at(chess.A7,chess.Piece(chess.PAWN,chess.WHITE))
        self.stable(g, wrong)
        self.assertIsNone(g.promotion)
        self.stable(g, after)
        self.confirm(g, 'n')
        self.assertEqual(g.history[-1]['san'], 'axb8=N')
        self.assertEqual(g.proxies, {chess.B8})

    def test_computer_choice_is_fixed_and_requires_confirmation(self):
        for pawn in (True, False):
            g = self.game(computer=True)
            g.pending = chess.Move.from_uci('a7a8n')
            self.stable(g, g.physical())
            options = g.promotion_options(time.time())
            self.assertEqual([c['piece'] for c in options['choices']], ['n'])
            with self.assertRaises(ValueError):self.confirm(g, 'n')
            self.stable(g, g.physical_after(g.pending, pawn))
            self.assertFalse(g.history)
            with self.assertRaises(ValueError):self.confirm(g, 'q')
            self.confirm(g, 'n', 'pawn' if pawn else 'piece')
            self.assertEqual(g.state, 'player_turn')
            self.assertIsNone(g.pending)

    def test_engine_opens_promotion_card_before_physical_move(self):
        g = self.game(computer=True)
        class Engine:
            def play(self, *args, **kwargs):
                return chess.engine.PlayResult(chess.Move.from_uci('a7a8r'), None)
        g.engine = Engine();g.state = 'thinking';g.engine_job = 'job'
        g.think(g.id, g.board.copy(), 'normal', 'job')
        self.assertEqual(g.promotion['piece'], 'r')
        self.assertFalse(g.history)

    def test_stale_wrong_paused_and_duplicate_confirmations_are_rejected(self):
        g = self.game()
        after = g.physical_after(chess.Move.from_uci('a7a8q'), True)
        self.stable(g, after)
        identity = g.promotion['id']
        for phase in ('settling','uncertain','orienting','stale','stopped'):
            g.camera_phase = phase
            with self.assertRaises(ValueError):self.confirm(g)
        self.stable(g, after);g.last_supported -= 4
        with self.assertRaises(ValueError):self.confirm(g)
        self.stable(g, after)
        with self.assertRaises(ValueError):g.confirm_promotion('old', 'q', 'pawn')
        with self.assertRaises(ValueError):self.confirm(g, 'q', 'piece')
        g.pause()
        with self.assertRaises(ValueError):g.confirm_promotion(identity, 'q', 'pawn')
        g.resume();self.stable(g, after);self.confirm(g)
        with self.assertRaises(ValueError):g.confirm_promotion(identity, 'q', 'pawn')
        self.assertEqual(len(g.history), 1)

    def test_restore_cancels_and_restarting_invalidates_old_choice(self):
        g = self.game()
        before = g.physical()
        self.stable(g, g.physical_after(chess.Move.from_uci('a7a8q'), True))
        identity = g.promotion['id']
        self.stable(g, before)
        self.assertIsNone(g.promotion)
        self.assertFalse(g.history)
        g.reset()
        with self.assertRaises(ValueError):g.confirm_promotion(identity,'q','pawn')

    def test_camera_change_disables_previous_stable_confirmation(self):
        g = self.game()
        after = g.physical_after(chess.Move.from_uci('a7a8q'), True)
        now = time.time()
        for i in range(5):
            capture = now+i*.4
            g.observe(dict(raw=after.board_fen(),sequence=i,capture_time=capture,
                           min_score=.99,min_margin=.9,change_score=.99,phase='stable'),i*.4,capture)
        self.assertIsNotNone(g.promotion)
        self.assertTrue(g.promotion_options(capture)['choices'][0]['pawn_ready'])
        changed = after.copy();changed.remove_piece_at(chess.E1)
        capture += .4
        g.observe(dict(raw=changed.board_fen(),sequence=9,capture_time=capture,
                       min_score=.99,min_margin=.9,change_score=.99,phase='stable'),2,capture)
        self.assertFalse(g.promotion_options(capture)['choices'][0]['pawn_ready'])
        with self.assertRaises(ValueError):self.confirm(g)

    def test_proxy_moves_with_queen_rules_and_ordinary_pawn_stays_a_pawn(self):
        g = self.game();self.promote(g)
        g.commit(chess.Move.from_uci('e8f7'));g.state = 'player_turn'
        move = chess.Move.from_uci('a8d5')
        expected = g.board.copy();expected.push(move)
        physical = expected.copy();physical.set_piece_at(chess.D5,chess.Piece(chess.PAWN,chess.WHITE))
        self.stable(g, physical)
        self.assertEqual(g.board.fen(), expected.fen())
        self.assertEqual(g.proxies, {chess.D5})
        self.assertEqual(g.board.piece_type_at(chess.H2), chess.PAWN)
        self.assertIn('pawn representing this queen',instruction(g.board,chess.Move.from_uci('d5d6'),g.proxies))
        self.assertFalse(g.errors)

    def test_proxy_capture_and_undo_restore_identity(self):
        g = self.game('1r2k3/P7/8/8/8/8/7p/4K3 w - - 0 1')
        self.promote(g)
        g.pending = chess.Move.from_uci('b8a8');g.state='computer_move'
        self.stable(g,g.physical_after(g.pending))
        self.assertFalse(g.proxies)
        self.assertEqual(g.board.piece_at(chess.A8),chess.Piece(chess.ROOK,chess.BLACK))
        g.undo_round()
        self.assertFalse(g.proxies)
        self.assertEqual(g.board.piece_type_at(chess.A7),chess.PAWN)
        self.assertFalse(g.history)

    def test_undo_proxy_move_restores_previous_square(self):
        g = self.game();self.promote(g)
        for uci in ('e8f7','a8d5','f7g6'):g.commit(chess.Move.from_uci(uci))
        g.state='player_turn';g.undo_round()
        self.assertEqual(g.proxies,{chess.A8})
        self.stable(g,g.physical())
        self.assertEqual(g.state,'player_turn')

    def test_capture_of_existing_proxy_is_restored_by_undo_after_reload(self):
        g=self.game('1r2k3/P7/8/8/8/8/7p/4K3 w - - 0 1');self.promote(g)
        for uci in ('e8f7','e1d1','b8a8'):
            move=chess.Move.from_uci(uci);self.assertIn(move,g.board.legal_moves);g.commit(move)
        g.state='player_turn'
        self.assertFalse(g.proxies)
        h=Game(object());h.load_data(g.serialize());h.undo_round()
        self.assertEqual(h.proxies,{chess.A8})
        self.assertEqual(h.board.piece_at(chess.A8),chess.Piece(chess.QUEEN,chess.WHITE))

    def test_proxy_can_capture_opposing_proxy(self):
        g=self.game('8/P7/4k3/8/8/8/7p/4K3 w - - 0 1');self.promote(g)
        g.pending=chess.Move.from_uci('h2h1q');g.state='computer_move'
        self.stable(g,g.physical_after(g.pending,True));self.confirm(g)
        self.assertEqual(g.proxies,{chess.A8,chess.H1})
        move=chess.Move.from_uci('a8h1');self.assertIn(move,g.board.legal_moves)
        self.stable(g,g.physical_after(move))
        self.assertEqual(g.proxies,{chess.H1})
        self.assertEqual(g.board.piece_at(chess.H1),chess.Piece(chess.QUEEN,chess.WHITE))

    def test_multiple_promoted_pawns_survive_save_load_and_undo(self):
        g = self.game('4k3/PP6/8/8/8/8/7p/4K3 w - - 0 1')
        for uci,pawn in [('a7a8q',True),('e8f7',False),('b7b8r',True),('f7g6',False),('a8d5',False)]:
            move=chess.Move.from_uci(uci);self.assertIn(move,g.board.legal_moves)
            g.commit(move,pawn_promotion=pawn)
        g.state='thinking'
        saved=g.serialize();h=Game(object());h.load_data(saved,strict=True)
        self.assertEqual(h.proxies,{chess.D5,chess.B8})
        self.assertEqual(h.board.fen(),g.board.fen())
        self.assertEqual(h.history,g.history)
        # Restoring actual queens/rooks is not interchangeable with the saved pawns.
        self.stable(h,h.board.copy());self.assertEqual(h.state,'reconcile')
        self.assertEqual(set(h.errors),{'d5','b8'})
        self.stable(h,h.physical());self.assertEqual(h.state,'thinking')
        h.undo_round();self.assertEqual(h.proxies,{chess.A8,chess.B8})
        snapshot=h.snapshot(time.time())
        self.assertEqual({p['square'] for p in snapshot['proxies']},{'a8','b8'})
        self.assertIn('pawn-proxies',snapshot['board_svg'])

    def test_corrupt_proxy_save_never_changes_live_game(self):
        g = self.game();self.promote(g)
        saved=g.serialize();identity=g.id
        for invalid in ([99],[-1],[0,0],[True],['0'],None):
            data=copy.deepcopy(saved);data['pawn_promotions']=invalid
            with self.assertRaises(ValueError):g.load_data(data)
            self.assertEqual(g.id,identity)
        g.commit(chess.Move.from_uci('e8f7'));g.state='player_turn'
        data=g.serialize();data['pawn_promotions']=[1]
        with self.assertRaises(ValueError):g.load_data(data)

    def test_legacy_real_promotion_save_remains_compatible(self):
        g=self.game();self.promote(g,pawn=False)
        for version in (1,2):
            data=g.serialize();data['version']=version;data.pop('pawn_promotions')
            h=Game(object());h.load_data(data,strict=True)
            self.assertFalse(h.proxies);self.assertEqual(h.board.fen(),g.board.fen())

    def test_saved_pending_computer_promotion_cannot_bypass_confirmation(self):
        g=self.game(computer=True);g.pending=chess.Move.from_uci('a7a8q')
        data=g.serialize();h=Game(object());h.load_data(data,strict=True)
        after=h.physical_after(h.pending,True)
        self.stable(h,after);self.assertTrue(h.strict_recovery)
        self.stable(h,h.physical());self.assertIsNotNone(h.promotion)
        self.stable(h,after);self.assertFalse(h.history)
        self.confirm(h);self.assertEqual(h.proxies,{chess.A8})

    def test_partial_proxy_move_has_no_wrong_piece_error(self):
        g=self.game();self.promote(g);g.commit(chess.Move.from_uci('e8f7'));g.state='player_turn'
        lifted=g.physical();lifted.remove_piece_at(chess.A8)
        self.stable(g,lifted)
        self.assertIn('Move in progress',g.message)
        self.assertEqual(g.proxies,{chess.A8})


if __name__ == '__main__':
    unittest.main()
