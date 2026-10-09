import json
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import chess
from board_orientation import infer_orientation
from chess_game import Game

class OrientationTests(unittest.TestCase):
    def points(self,near='black'):
        files=np.tile(np.arange(8),8);rows=np.repeat(np.arange(8),8)
        if near=='black':return np.column_stack((.75-files*.06,.75-rows*.07))
        return np.column_stack((.25+files*.06,.25+rows*.07))
    def record(self,near,seq,status='detected'):
        return dict(raw=chess.STARTING_BOARD_FEN,sequence=seq,capture_time=100+seq*.4,
                    min_score=.99,min_margin=.9,change_score=.99,phase='stable',
                    orientation={'status':status,'near':near,'detail':'Turn the board to face the camera.'},
                    orientation_at=100+seq*.4)
    def test_every_new_game_rechecks_full_start_and_orientation(self):
        # Both HTTP entry points reset this controller. Returning home also clears
        # camera observations before the next Start Game resets the session.
        for entry in ('new_game', 'exit_then_start'):
            for near in ('black', 'white'):
                with self.subTest(entry=entry, near=near), patch('chess_game.threading.Thread') as worker:
                    g=Game(object());g.reset(computer='auto')
                    for i in range(5):g.observe(self.record('black',i),i*.4,100+i*.4)
                    self.assertEqual(g.state,'player_turn')
                    old_session=g.id
                    old_position=chess.Board();old_position.push_uci('e2e4')
                    g.commit(chess.Move.from_uci('e2e4'))
                    g.manual_corrections={'e2':dict(seen='.',piece='P')}
                    if entry=='exit_then_start':
                        g.pause();g.clear_observations()
                    g.reset(computer='auto')
                    self.assertNotEqual(g.id,old_session)
                    self.assertFalse(g.side_confirmed)
                    self.assertFalse(g.aligned)
                    self.assertFalse(g.manual_corrections)
                    self.assertEqual(len(g.gate.window),0)
                    worker.reset_mock()
                    # A legal position from the old game is still wrong at setup.
                    for i in range(5,10):
                        r=self.record(near,i);r['raw']=old_position.board_fen()
                        g.observe(r,i*.4,100+i*.4)
                    self.assertEqual(g.state,'setup')
                    self.assertEqual(set(g.errors),{'e2','e4'})
                    self.assertEqual(g.board.board_fen(),chess.STARTING_BOARD_FEN)
                    self.assertFalse(g.history)
                    self.assertIsNone(g.pending)
                    worker.assert_not_called()
                    # The new game needs its own five confirming start frames.
                    for i in range(10,14):g.observe(self.record(near,i),i*.4,100+i*.4)
                    self.assertEqual(g.state,'setup')
                    g.observe(self.record(near,14),5.6,105.6)
                    self.assertEqual(g.computer,near=='white')
                    self.assertEqual(g.state,'thinking' if near=='white' else 'player_turn')

    def test_both_sides(self):
        for side in ('black','white'):
            self.assertEqual(infer_orientation(self.points(side),1920,1080)['near'],side)
    def test_sideways_and_flat_maps_are_rejected(self):
        p=self.points();p=p[:,::-1]
        self.assertEqual(infer_orientation(p,1080,1920)['status'],'sideways')
        self.assertEqual(infer_orientation(np.ones((64,2))*.5,1920,1080)['status'],'uncertain')
    def test_noisy_geometry_is_rejected(self):
        p=np.random.default_rng(12).uniform(0,1,(64,2))
        self.assertEqual(infer_orientation(p,1920,1080)['status'],'uncertain')
    def test_invalid_geometry(self):
        self.assertEqual(infer_orientation([[float('nan'),0]]*64,1920,1080)['status'],'uncertain')
    def test_automatic_black_moves_after_player(self):
        g=Game(object());g.reset(computer='auto')
        for i in range(5):g.observe(self.record('black',i),i*.4,100+i*.4)
        self.assertTrue(g.side_confirmed);self.assertFalse(g.computer);self.assertEqual(g.state,'player_turn')
        self.assertTrue(g.aligned)
    def test_automatic_white_moves_first(self):
        g=Game(object());g.reset(computer='auto')
        with patch('chess_game.threading.Thread') as worker:
            for i in range(5):g.observe(self.record('white',i),i*.4,100+i*.4)
        self.assertTrue(g.computer);self.assertEqual(g.state,'thinking');worker.assert_called_once()
    def test_sideways_does_not_start_even_when_start_fen_is_correct(self):
        g=Game(object());g.reset(computer='auto')
        for i in range(8):g.observe(self.record('black',i,'sideways'),i*.4,100+i*.4)
        self.assertEqual(g.state,'setup');self.assertFalse(g.side_confirmed)
        self.assertEqual(g.orientation_status,'sideways')
    def test_single_wrong_side_does_not_choose_it(self):
        g=Game(object());g.reset(computer='auto')
        for i,side in enumerate(['white','black','black','black','black']):g.observe(self.record(side,i),i*.4,100+i*.4)
        self.assertEqual(g.state,'player_turn');self.assertFalse(g.computer)
    def test_stale_orientation_metadata_does_not_count(self):
        g=Game(object());g.reset(computer='auto')
        for i in range(8):
            r=self.record('white',i);r['orientation_at']=99
            g.observe(r,i*.4,100+i*.4)
        self.assertEqual(g.state,'setup');self.assertFalse(g.side_confirmed)
    def test_resume_cannot_swap_sides_of_existing_game(self):
        g=Game(object());g.reset(computer='auto');g.state='reconcile';g.resume_state='player_turn';g.computer=False
        for i in range(8):g.observe(self.record('white',i),i*.4,100+i*.4)
        self.assertEqual(g.orientation_status,'mismatch');self.assertEqual(g.state,'reconcile');self.assertFalse(g.computer)
    def test_board_can_be_turned_before_start(self):
        g=Game(object());g.reset(computer='auto')
        with patch('chess_game.threading.Thread'):
            for i,side in enumerate(['black','black','white','white','white','white']):g.observe(self.record(side,i),i*.4,100+i*.4)
        self.assertEqual(g.state,'thinking');self.assertTrue(g.computer)

    def test_flip_during_game_blocks_moves_until_restored(self):
        g=Game(object());g.reset(computer='auto')
        for i in range(5):g.observe(self.record('black',i),i*.4,100+i*.4)
        for i in range(5,9):g.observe(self.record('white',i),i*.4,100+i*.4)
        self.assertEqual(g.orientation_status,'mismatch')
        self.assertFalse(g.computer);self.assertFalse(g.history)
        for i in range(9,14):g.observe(self.record('black',i),i*.4,100+i*.4)
        self.assertEqual(g.orientation_status,'detected');self.assertTrue(g.side_confirmed)
        self.assertEqual(g.state,'player_turn')

    def test_transient_wrong_direction_is_not_flip_error(self):
        g=Game(object());g.reset(computer='auto')
        for i in range(5):g.observe(self.record('black',i),i*.4,100+i*.4)
        g.observe(self.record('white',5),2,102)
        self.assertNotEqual(g.orientation_status,'mismatch')
        for i in range(6,10):g.observe(self.record('black',i),i*.4,100+i*.4)
        self.assertEqual(g.state,'player_turn');self.assertFalse(g.history)

    def test_setup_flip_after_direction_locked_requires_restore_or_restart(self):
        g=Game(object());g.reset(computer='auto')
        for i in range(4):g.observe(self.record('black',i),i*.4,100+i*.4)
        self.assertEqual(g.state,'setup')
        for i in range(4,9):g.observe(self.record('white',i),i*.4,100+i*.4)
        self.assertEqual(g.orientation_status,'mismatch');self.assertEqual(g.state,'setup')
        self.assertEqual(g.original_near,'black');self.assertEqual(g.detected_near,'white')
        flipped_svg=g.snapshot(103.2)['board_svg']
        g.reset(computer='auto')
        with patch('chess_game.threading.Thread'):
            for i in range(9,14):g.observe(self.record('white',i),i*.4,100+i*.4)
        self.assertEqual(g.original_near,'white');self.assertTrue(g.computer)
        self.assertEqual(g.snapshot(105.2)['board_svg'],flipped_svg)

    def test_endgame_flip_and_resume_keep_orientation_independent_of_player(self):
        import time
        g=Game(object());g.start_endgame()
        placement='7k/8/8/8/8/8/4P3/K7'
        def feed(near,start):
            for i in range(start,start+9):
                r=self.record(near,i);r['raw']=placement
                g.observe(r,i*.4,100+i*.4)
        feed('black',0)
        self.assertEqual(g.state,'endgame_ready');self.assertEqual(g.original_near,'black')
        feed('white',10)
        self.assertEqual(g.orientation_status,'mismatch')
        self.assertIn('Restart Game',g.snapshot(103.6)['orientation_detail'])
        feed('black',20)
        g.last_frame=time.time()
        # Computer color is independent of physical direction in custom positions.
        g.confirm_endgame(placement,'black','black')
        self.assertTrue(g.computer);self.assertTrue(g.side_auto)
        g.resume_current();self.assertTrue(g.side_auto)
        self.assertEqual(g.original_near,'black');self.assertEqual(g.state,'reconcile')
        feed('white',30)
        self.assertEqual(g.orientation_status,'mismatch');self.assertEqual(g.state,'reconcile')
        feed('black',40)
        self.assertEqual(g.state,'player_turn');self.assertTrue(g.computer)
        self.assertEqual(g.detected_near,'black')

    def test_loaded_standard_game_checks_direction_before_your_turn(self):
        g=Game(object());g.reset(computer='auto')
        for i in range(5):g.observe(self.record('black',i),i*.4,100+i*.4)
        data=g.serialize();g.load_data(data,automatic=True,strict=True)
        for i in range(5,10):g.observe(self.record('white',i),i*.4,100+i*.4)
        self.assertEqual(g.state,'reconcile');self.assertEqual(g.orientation_status,'mismatch')
        for i in range(10,19):g.observe(self.record('black',i),i*.4,100+i*.4)
        self.assertEqual(g.state,'player_turn')

if __name__=='__main__':unittest.main()
