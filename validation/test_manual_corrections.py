"""Human corrections stay scoped to live observations and legal game transitions."""
import time
import unittest
from unittest.mock import patch
import chess
from chess_game import Game

class ManualCorrectionsTests(unittest.TestCase):
    def game(self):
        g=Game(object());g.reset(aligned=True)
        g.board=chess.Board('4k3/8/8/8/8/8/8/4K2R w - - 0 1')
        g.state='player_turn'
        return g

    def feed(self,g,b,i,**evidence):
        t=time.time()
        g.observe(dict(raw=b.board_fen(),sequence=i,capture_time=t,min_score=.99,min_margin=.9,change_score=.99,phase='stable',**evidence),i*.5,t)

    def test_rook_misread_can_confirm_legal_move_after_new_votes(self):
        g=self.game();raw=g.board.copy();raw.remove_piece_at(chess.H1);raw.set_piece_at(chess.G1,chess.Piece.from_symbol('N'))
        self.feed(g,raw,0)
        g.correct_square('g1','R','N')
        self.assertEqual(len(g.history),0)
        with patch('chess_game.threading.Thread'):
            for i in range(1,4):self.feed(g,raw,i)
        self.assertEqual(g.history[0]['uci'],'h1g1')
        self.assertEqual(g.raw_observed.piece_at(chess.G1).symbol(),'N')
        self.assertEqual(raw.piece_at(chess.G1).symbol(),'N')

    def test_sustained_removal_clears_override_but_single_frame_does_not(self):
        g=self.game();raw=g.board.copy();raw.set_piece_at(chess.H1,chess.Piece.from_symbol('N'));self.feed(g,raw,0)
        g.correct_square('h1','R','N');raw.remove_piece_at(chess.H1);self.feed(g,raw,1)
        self.assertIn('h1',g.manual_corrections)
        self.feed(g,raw,2);self.feed(g,raw,3)
        self.assertFalse(g.manual_corrections)
        self.assertIsNone(g.corrected_observation(raw).piece_at(chess.H1))

    def test_stale_and_changed_requests_are_rejected(self):
        g=self.game();self.feed(g,g.board,0)
        with self.assertRaises(ValueError):g.correct_square('h1','B','N')
        g.last_frame=time.time()-4
        with self.assertRaises(ValueError):g.correct_square('h1','N','R')

    def test_corrected_label_flicker_and_other_piece_move_keep_correction(self):
        g=self.game();raw=g.board.copy();raw.set_piece_at(chess.H1,chess.Piece.from_symbol('N'))
        self.feed(g,raw,0);g.correct_square('h1','R','N')
        raw.set_piece_at(chess.H1,chess.Piece.from_symbol('R'));self.feed(g,raw,1)
        raw.set_piece_at(chess.H1,chess.Piece.from_symbol('N'))
        raw.remove_piece_at(chess.E1);raw.set_piece_at(chess.D1,chess.Piece.from_symbol('K'))
        with patch('chess_game.threading.Thread'):
            for i in range(2,6):self.feed(g,raw,i)
        self.assertEqual(g.manual_corrections['h1']['piece'],'R')
        self.assertEqual(g.history[0]['uci'],'e1d1')

    def test_low_confidence_alternative_keeps_human_choice(self):
        g=self.game();raw=g.board.copy();raw.set_piece_at(chess.H1,chess.Piece.from_symbol('N'))
        self.feed(g,raw,0);g.correct_square('h1','R','N')
        raw.set_piece_at(chess.H1,chess.Piece.from_symbol('B'))
        for i in range(1,7):self.feed(g,raw,i,square_probabilities={'h1':{'R':.10,'B':.88}})
        self.assertIn('h1',g.manual_corrections)
        for i in range(7,10):self.feed(g,raw,i,square_probabilities={'h1':{'R':.01,'B':.98}})
        self.assertIn('h1',g.manual_corrections)

    def test_piece_corrected_from_empty_survives_other_occupied_labels(self):
        g=self.game();raw=g.board.copy();raw.remove_piece_at(chess.H1)
        self.feed(g,raw,0);g.correct_square('h1','R','.')
        # The reported regression: an empty reading is corrected to a rook,
        # then several confident bishop readings used to erase that correction.
        raw.set_piece_at(chess.H1,chess.Piece.from_symbol('B'))
        for i in range(1,7):
            self.feed(g,raw,i,square_probabilities={'h1':{'R':.01,'B':.98}})
        self.assertEqual(g.manual_corrections['h1']['piece'],'R')
        self.assertEqual(g.corrected_observation(raw).piece_at(chess.H1).symbol(),'R')
        self.assertFalse(g.history)

    def test_immediate_preview_does_not_commit_a_move(self):
        g=self.game();raw=g.board.copy();raw.set_piece_at(chess.H1,chess.Piece.from_symbol('N'))
        self.feed(g,raw,0);g.errors=['h1'];g.observed=raw
        g.correct_square('h1','R','N')
        self.assertEqual(g.observed.piece_at(chess.H1).symbol(),'R')
        self.assertFalse(g.errors);self.assertFalse(g.history)

    def test_confirmation_while_camera_agrees_still_keeps_correction(self):
        g=self.game();self.feed(g,g.board,0);g.correct_square('h1','R','N')
        raw=g.board.copy();raw.set_piece_at(chess.H1,chess.Piece.from_symbol('N'))
        for i in range(1,5):self.feed(g,raw,i)
        self.assertIn('h1',g.manual_corrections)

    def test_corrected_square_uncertainty_does_not_block_new_votes(self):
        g=self.game();raw=g.board.copy();raw.remove_piece_at(chess.H1);raw.set_piece_at(chess.G1,chess.Piece.from_symbol('N'))
        self.feed(g,raw,0);g.correct_square('g1','R','N')
        probabilities={s:{'.':.99,'R':.01} for s in chess.SQUARE_NAMES}
        probabilities['g1']={'N':.51,'R':.49}
        with patch('chess_game.threading.Thread'):
            for i in range(1,5):
                t=time.time()
                g.observe(dict(raw=raw.board_fen(),sequence=i,capture_time=t,min_score=.51,min_margin=.02,
                               change_score=.51,square_scores={'h1':.99,'g1':.51},square_probabilities=probabilities),i*.5,t)
        self.assertEqual(g.history[0]['uci'],'h1g1')

    def test_occupancy_correction_does_not_expire_on_original_mistake(self):
        g=self.game();raw=g.board.copy();raw.remove_piece_at(chess.H1)
        self.feed(g,raw,0);g.correct_square('h1','R','.')
        for i in range(1,5):self.feed(g,raw,i)
        self.assertIn('h1',g.manual_corrections)

    def test_unrelated_low_confidence_still_blocks_move(self):
        g=self.game();raw=g.board.copy();raw.remove_piece_at(chess.H1);raw.set_piece_at(chess.G1,chess.Piece.from_symbol('N'))
        self.feed(g,raw,0);g.correct_square('g1','R','N')
        probabilities={s:{'.':.99,'R':.01} for s in chess.SQUARE_NAMES}
        probabilities['e1']={'K':.51,'Q':.49}
        for i in range(1,8):self.feed(g,raw,i,square_probabilities=probabilities)
        self.assertFalse(g.history)

    def test_suspected_removal_does_not_allow_other_move_to_commit_early(self):
        g=self.game();raw=g.board.copy();raw.set_piece_at(chess.H1,chess.Piece.from_symbol('N'))
        self.feed(g,raw,0);g.correct_square('h1','R','N')
        raw.remove_piece_at(chess.E1);raw.set_piece_at(chess.D1,chess.Piece.from_symbol('K'))
        self.feed(g,raw,1)
        raw.remove_piece_at(chess.H1)
        for i in range(2,6):self.feed(g,raw,i)
        self.assertFalse(g.history)

    def test_illegal_corrected_move_does_not_commit(self):
        g=self.game();raw=g.board.copy();raw.remove_piece_at(chess.H1);raw.set_piece_at(chess.G2,chess.Piece.from_symbol('N'));self.feed(g,raw,0)
        g.correct_square('g2','R','N')
        for i in range(1,7):self.feed(g,raw,i)
        self.assertFalse(g.history)
        self.assertEqual(g.board.piece_at(chess.H1).symbol(),'R')

    def test_disconnect_and_reset_clear_corrections(self):
        g=self.game();self.feed(g,g.board,0);g.correct_square('h1','N','R')
        g.clear_observations();self.assertFalse(g.manual_corrections)
        self.feed(g,g.board,1);g.correct_square('h1','N','R');g.reset();self.assertFalse(g.manual_corrections)

if __name__=='__main__':unittest.main()
