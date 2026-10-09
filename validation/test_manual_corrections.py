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

    def feed(self,g,b,i):
        t=time.time()
        g.observe(dict(raw=b.board_fen(),sequence=i,capture_time=t,min_score=.99,min_margin=.9,change_score=.99,phase='stable'),i*.5,t)

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

    def test_changed_reading_clears_override(self):
        g=self.game();raw=g.board.copy();raw.set_piece_at(chess.H1,chess.Piece.from_symbol('N'));self.feed(g,raw,0)
        g.correct_square('h1','R','N');raw.remove_piece_at(chess.H1);self.feed(g,raw,1)
        self.assertFalse(g.manual_corrections)
        self.assertIsNone(g.corrected_observation(raw).piece_at(chess.H1))

    def test_stale_and_changed_requests_are_rejected(self):
        g=self.game();self.feed(g,g.board,0)
        with self.assertRaises(ValueError):g.correct_square('h1','R','N')
        g.last_frame=time.time()-4
        with self.assertRaises(ValueError):g.correct_square('h1','N','R')

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
