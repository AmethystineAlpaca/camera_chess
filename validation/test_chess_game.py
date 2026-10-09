import sys
import time
import unittest
import tempfile
import json
from pathlib import Path
from unittest.mock import patch
import chess
import chess.engine
sys.path.insert(0, str(Path(__file__).parent))
from chess_game import Game, instruction

class GameTests(unittest.TestCase):
    def player_game(self):
        g = Game(object())
        g.reset(aligned=True)
        g.accept_observation(chess.Board())
        self.assertEqual(g.state, 'player_turn')
        return g

    def test_setup_requires_exact_start_and_alignment(self):
        g = Game(object())
        b = chess.Board(); b.remove_piece_at(chess.E2)
        g.accept_observation(b)
        self.assertEqual(g.state, 'setup'); self.assertIn('e2', g.errors)
        g.accept_observation(chess.Board())
        self.assertEqual(g.state, 'setup')
        self.assertIn('Confirm', g.message)

    def test_white_computer_moves_first(self):
        g = Game(object()); g.reset(computer='white', aligned=True)
        with patch('chess_game.threading.Thread') as thread:
            g.accept_observation(chess.Board())
            self.assertEqual(g.state, 'thinking'); thread.assert_called_once()

    def test_player_move_updates_history_only_when_legal(self):
        g = self.player_game(); b = chess.Board(); b.push_uci('e2e4')
        with patch('chess_game.threading.Thread'):
            g.accept_observation(b)
        self.assertEqual(g.board.fen(), b.fen()); self.assertEqual(g.history[0]['san'], 'e4')
        self.assertEqual(g.state, 'thinking')

    def test_illegal_move_can_be_corrected_directly(self):
        g = self.player_game(); wrong = chess.Board()
        wrong.remove_piece_at(chess.E2); wrong.set_piece_at(chess.E5,chess.Piece(chess.PAWN,chess.WHITE))
        g.accept_observation(wrong)
        self.assertEqual(g.state, 'restore'); self.assertEqual(g.board.board_fen(),chess.STARTING_BOARD_FEN)
        legal = chess.Board(); legal.push_uci('e2e4')
        with patch('chess_game.threading.Thread'):g.accept_observation(legal)
        self.assertEqual(g.state, 'thinking');self.assertEqual(g.board.fen(),legal.fen())
        self.assertEqual(len(g.history),1)

    def test_computer_move_requires_exact_expected_result(self):
        g = Game(object()); g.reset(computer='white',aligned=True)
        g.state='computer_move'; g.pending=chess.Move.from_uci('e2e4')
        wrong=chess.Board(); wrong.push_uci('d2d4'); g.accept_observation(wrong)
        self.assertEqual(len(g.history),0); self.assertEqual(g.state,'computer_move')
        correct=chess.Board(); correct.push_uci('e2e4'); g.accept_observation(correct)
        self.assertEqual(g.state,'player_turn'); self.assertEqual(g.board.fen(),correct.fen())
        g.accept_observation(correct); self.assertEqual(len(g.history),1)

    def test_stale_and_repeated_frames_cannot_start_game(self):
        g=Game(object()); g.reset(aligned=True)
        record=dict(raw=chess.STARTING_BOARD_FEN,sequence=1,capture_time=100,min_score=.99,min_margin=.9,change_score=.99,phase='stable')
        for t in range(10): g.observe(record,t,200)
        self.assertEqual(g.state,'setup'); self.assertIsNone(g.observed)
        for t in range(10): g.observe(record,t,100)
        self.assertEqual(g.state,'setup'); self.assertIsNone(g.observed)

    def test_setup_requires_five_frames_without_extra_delay(self):
        g=Game(object()); g.reset(aligned=True)
        for i in range(4):
            g.observe(dict(raw=chess.STARTING_BOARD_FEN,sequence=i,capture_time=100+i*.4,min_score=.99,min_margin=.9,change_score=.99,phase='stable'),i*.4,100+i*.4)
        self.assertEqual(g.state,'setup')
        for i in range(4,5):
            g.observe(dict(raw=chess.STARTING_BOARD_FEN,sequence=i,capture_time=100+i*.4,min_score=.99,min_margin=.9,change_score=.99,phase='stable'),i*.4,100+i*.4)
        self.assertEqual(g.state,'player_turn')

    def test_special_moves_match_full_result(self):
        cases=[('r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1','e1g1'),
               ('4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1','e5d6'),
               ('4k3/P7/8/8/8/8/8/4K3 w - - 0 1','a7a8n')]
        for fen,uci in cases:
            with self.subTest(uci=uci):
                g=Game(object()); g.board=chess.Board(fen); g.state='player_turn'
                after=g.board.copy(); after.push_uci(uci)
                with patch('chess_game.threading.Thread'): g.accept_observation(after)
                self.assertEqual(g.board.fen(),after.fen())

    def test_special_instructions(self):
        b=chess.Board('r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1')
        self.assertIn('rook from h1 to f1',instruction(b,chess.Move.from_uci('e1g1')))
        b=chess.Board('4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1')
        self.assertIn('pawn on d5',instruction(b,chess.Move.from_uci('e5d6')))

    def test_illegal_engine_move_never_commits(self):
        class BadEngine:
            def play(self,*a,**kw): return chess.engine.PlayResult(chess.Move.from_uci('e2e5'),None)
        g=Game(BadEngine()); g.think(g.id,g.board.copy(),'normal')
        self.assertEqual(g.state,'engine_error'); self.assertEqual(len(g.history),0)

    def test_terminal_and_check(self):
        g=Game(object()); g.board=chess.Board()
        for m in ['f2f3','e7e5','g2g4']: g.board.push_uci(m)
        g.state='player_turn';g.computer=chess.WHITE
        b=g.board.copy();b.push_uci('d8h4');g.accept_observation(b)
        self.assertEqual(g.state,'finished');self.assertIn('Black wins',g.message)

    def test_camera_gap_requires_new_stability(self):
        g=Game(object());g.reset(aligned=True)
        for i in range(4):
            g.observe(dict(raw=chess.STARTING_BOARD_FEN,sequence=i,capture_time=100+i*.4,min_score=.99,min_margin=.9,change_score=.99,phase='stable'),i*.4,100+i*.4)
        g.observe(dict(raw=chess.STARTING_BOARD_FEN,sequence=6,capture_time=110,min_score=.99,min_margin=.9,change_score=.99,phase='stable'),10,110)
        self.assertEqual(g.state,'setup');self.assertEqual(len(g.gate.window),1)

    def test_confident_malformed_setup_reports_squares(self):
        g=Game(object());g.reset(aligned=True)
        b=chess.Board();b.remove_piece_at(chess.E8)
        for i in range(10):
            g.observe(dict(raw=b.board_fen(),sequence=i,capture_time=100+i*.4,min_score=.99,min_margin=.9,change_score=.99,phase='stable'),i*.4,100+i*.4)
        self.assertIn('e8',g.errors);self.assertEqual(g.state,'setup')

    def test_late_engine_result_cannot_change_new_game(self):
        g=Game(object());old=g.id;g.reset()
        class Engine:
            def play(self,*a,**kw): return chess.engine.PlayResult(chess.Move.from_uci('e2e4'),None)
        g.engine=Engine();g.think(old,chess.Board(),'normal')
        self.assertEqual(g.state,'setup');self.assertIsNone(g.pending)

    def test_lifted_piece_gets_extra_time(self):
        g=self.player_game();b=g.board.copy();b.remove_piece_at(chess.E2)
        with patch('chess_game.time.monotonic',return_value=10): g.accept_observation(b)
        self.assertEqual(g.state,'player_turn');self.assertIn('Move in progress',g.message)
        with patch('chess_game.time.monotonic',return_value=19): g.accept_observation(b)
        self.assertEqual(g.state,'restore');self.assertEqual(len(g.history),0)

    def test_partial_castling_can_finish_without_restore(self):
        g=Game(object());g.state='player_turn';g.board=chess.Board('r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1')
        b=g.board.copy();b.remove_piece_at(chess.E1);b.set_piece_at(chess.G1,chess.Piece(chess.KING,chess.WHITE))
        g.accept_observation(b);self.assertEqual(g.state,'player_turn')
        after=g.board.copy();after.push_uci('e1g1')
        with patch('chess_game.threading.Thread'): g.accept_observation(after)
        self.assertEqual(g.board.fen(),after.fen())

    def test_promotion_pawn_waits_for_replacement(self):
        g=Game(object());g.state='player_turn';g.board=chess.Board('4k3/P7/8/8/8/8/8/4K3 w - - 0 1')
        b=g.board.copy();b.remove_piece_at(chess.A7);b.set_piece_at(chess.A8,chess.Piece(chess.PAWN,chess.WHITE))
        g.accept_observation(b);self.assertEqual(g.state,'player_turn');self.assertEqual(len(g.history),0)

    def test_pause_blocks_moves_and_resume_accepts_legal_successor(self):
        g=self.player_game();g.pause();b=g.board.copy();b.push_uci('e2e4');g.accept_observation(b)
        self.assertEqual(len(g.history),0);self.assertEqual(g.state,'paused')
        g.resume()
        with patch('chess_game.threading.Thread'):g.accept_observation(b)
        self.assertEqual(g.state,'thinking');self.assertEqual(len(g.history),1)

    def test_pause_invalidates_inflight_engine_job(self):
        g=Game(object());g.state='thinking';g.engine_job='old'
        class Engine:
            def play(self,*a,**kw): return chess.engine.PlayResult(chess.Move.from_uci('e2e4'),None)
        g.engine=Engine();g.pause();g.think(g.id,g.board.copy(),'normal','old')
        self.assertEqual(g.state,'paused');self.assertIsNone(g.pending)

    def test_undo_last_round_restores_player_turn(self):
        g=self.player_game()
        for uci in ['e2e4','e7e5','g1f3','b8c6']:g.commit(chess.Move.from_uci(uci))
        g.undo_round();self.assertEqual(len(g.history),2);self.assertEqual(g.state,'reconcile')
        g.accept_observation(g.board.copy());self.assertEqual(g.state,'player_turn')
        self.assertEqual([m['uci'] for m in g.history],['e2e4','e7e5'])

    def test_saved_game_preserves_rights_and_history(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'game.json';g=Game(object(),p);g.state='player_turn'
            for uci in ['e2e4','a7a6','e1e2','b7b5']:g.commit(chess.Move.from_uci(uci))
            g.save();h=Game(object(),p);h.load_saved('black')
            self.assertEqual(h.board.fen(),g.board.fen());self.assertEqual(h.board.ep_square,g.board.ep_square)
            self.assertFalse(h.board.has_kingside_castling_rights(chess.WHITE))
            self.assertEqual(h.history,g.history);self.assertEqual(h.state,'reconcile')
            h.accept_observation(h.board.copy());self.assertEqual(h.state,'player_turn')

    def test_saved_pending_move_can_already_be_physically_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'game.json';g=Game(object(),p);g.reset(computer='white',aligned=True)
            g.state='computer_move';g.pending=chess.Move.from_uci('e2e4');g.save()
            h=Game(object(),p);h.load_saved('white');b=h.board.copy();b.push_uci('e2e4');h.accept_observation(b)
            self.assertEqual(h.state,'player_turn');self.assertEqual(len(h.history),1)

    def test_corrupt_save_does_not_change_live_game(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'game.json';g=Game(object(),p);old=g.id
            p.write_text(json.dumps(dict(version=1,computer='black',difficulty='normal',state='player_turn',moves=['e2e5'],pending=None)))
            with self.assertRaises(ValueError):g.load_saved()
            self.assertEqual(g.id,old);self.assertEqual(g.board.fen(),chess.STARTING_FEN)

    def test_saved_orientation_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'game.json';g=Game(object(),p);g.state='player_turn';g.save()
            old=g.id
            with self.assertRaisesRegex(ValueError,'Black nearest'):g.load_saved('white')
            self.assertEqual(g.id,old)

    def test_disk_failure_does_not_corrupt_game(self):
        with tempfile.TemporaryDirectory() as directory:
            g=Game(object(),Path(directory)/'game.json');g.state='player_turn'
            with patch('pathlib.Path.write_text',side_effect=OSError('disk full')):g.emit('Ready.');g.save()
            self.assertIn('could not be saved',g.save_error);self.assertEqual(g.state,'player_turn')

    def test_draw_claim_and_completed_save(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'game.json';g=Game(object(),p);g.state='player_turn'
            for uci in ['g1f3','g8f6','f3g1','f6g8']*2:g.commit(chess.Move.from_uci(uci))
            g.claim_draw();g.save();self.assertEqual(g.state,'finished')
            h=Game(object(),p);h.load_saved();h.accept_observation(h.board.copy())
            self.assertEqual(h.state,'finished')

    def test_invalid_draw_claim_does_not_end_game(self):
        g=self.player_game()
        with self.assertRaises(ValueError):g.claim_draw()
        self.assertEqual(g.state,'player_turn')

    def test_future_timestamp_and_nonfinite_score_are_ignored(self):
        g=Game(object());r=dict(raw=chess.STARTING_BOARD_FEN,sequence=1,capture_time=200,min_score=.99,min_margin=.9,change_score=.99,phase='stable')
        g.observe(r,1,100);self.assertEqual(g.camera_phase,'stale');self.assertEqual(len(g.gate.window),0)
        r.update(capture_time=100,min_score=float('nan'));g.observe(r,1,100)
        self.assertEqual(g.camera_phase,'uncertain');self.assertEqual(len(g.gate.window),0)

    def test_engine_timeout_preserves_position(self):
        g=Game(object());g.state='thinking';g.thinking_since=10;g.engine_job='old'
        with patch('chess_game.time.monotonic',return_value=21):g.snapshot(100)
        self.assertEqual(g.state,'engine_error');self.assertIsNone(g.engine_job)
        self.assertEqual(g.board.fen(),chess.STARTING_FEN)

    def record(self,board,sequence,when,confidence=.99,change=.99):
        return dict(raw=board.board_fen(),sequence=sequence,capture_time=100+when,
                    min_score=confidence,min_margin=.2,change_score=change,phase='stable',
                    square_scores={chess.square_name(s):.99 for s in chess.SQUARES})

    def test_legal_move_uses_three_frames_and_changed_square_scores(self):
        g=self.player_game();b=g.board.copy();b.push_uci('e2e4')
        with patch('chess_game.threading.Thread'):
            for i in range(2):g.observe(self.record(b,i,i*.48,confidence=.65,change=.65),i*.48,100+i*.48)
            self.assertEqual(g.state,'player_turn')
            g.observe(self.record(b,2,.96,confidence=.65,change=.65),.96,100.96)
        self.assertEqual(g.state,'thinking');self.assertEqual(len(g.history),1)

    def test_one_wrong_frame_recovers_without_resetting_evidence(self):
        g=self.player_game();good=g.board.copy();good.push_uci('e2e4');glitch=g.board.copy();glitch.push_uci('d2d4')
        with patch('chess_game.threading.Thread'):
            for i,b in enumerate([good,glitch,good,good]):
                g.observe(self.record(b,i,i*.48),i*.48,100+i*.48)
                if i<3:self.assertEqual(len(g.history),0)
        self.assertEqual(g.history[0]['uci'],'e2e4')

    def test_two_frames_cannot_commit_a_legal_glitch(self):
        g=self.player_game();b=g.board.copy();b.push_uci('d2d4')
        for i in range(2):g.observe(self.record(b,i,i*.5),i*.5,100+i*.5)
        self.assertEqual(len(g.history),0)

    def test_illegal_observation_is_not_committed_and_direct_correction_is_fast(self):
        g=self.player_game();bad=g.board.copy();bad.remove_piece_at(chess.E2);bad.set_piece_at(chess.E5,chess.Piece(chess.PAWN,chess.WHITE))
        for i in range(5):g.observe(self.record(bad,i,i*.48),i*.48,100+i*.48)
        self.assertEqual(g.state,'restore');self.assertEqual(len(g.history),0)
        good=g.board.copy();good.push_uci('e2e4')
        with patch('chess_game.threading.Thread'):
            for i in range(5,8):g.observe(self.record(good,i,i*.48),i*.48,100+i*.48)
        self.assertEqual(g.history[0]['uci'],'e2e4');self.assertFalse(g.errors);self.assertFalse(g.suggestions)

    def test_error_disappears_when_camera_recovers_same_board(self):
        g=self.player_game();b=g.board.copy();b.remove_piece_at(chess.E8);g.accept_observation(b)
        self.assertEqual(g.state,'restore')
        correct=g.board.copy()
        for i in range(2):g.observe(self.record(correct,i,i*.4,confidence=.65,change=.65),i*.4,100+i*.4)
        self.assertEqual(g.state,'player_turn');self.assertFalse(g.errors);self.assertEqual(len(g.history),0)

    def test_suggestion_is_legal_but_cannot_auto_repair_observation(self):
        g=self.player_game();b=g.board.copy();b.remove_piece_at(chess.E2);b.set_piece_at(chess.E5,chess.Piece(chess.PAWN,chess.WHITE));g.accept_observation(b)
        self.assertTrue(g.suggestions);self.assertIn('No need to reset',g.message)
        for option in g.suggestions:self.assertIn(chess.Move.from_uci(option['uci']),g.board.legal_moves)
        self.assertEqual(len(g.history),0)
        data=g.snapshot(100);self.assertTrue(data['errors']);self.assertTrue(data['suggestions'])
        self.assertNotIn('e2: detected empty; expected White pawn',data['errors'])

    def test_player_can_choose_a_different_legal_move_after_error(self):
        g=self.player_game();b=g.board.copy();b.remove_piece_at(chess.E2);b.set_piece_at(chess.E5,chess.Piece(chess.PAWN,chess.WHITE));g.accept_observation(b)
        different=g.board.copy();different.push_uci('g1f3')
        with patch('chess_game.threading.Thread'):g.accept_observation(different)
        self.assertEqual(g.history[0]['uci'],'g1f3');self.assertFalse(g.errors)

    def test_only_changed_squares_need_fast_confidence(self):
        g=self.player_game();b=g.board.copy();b.push_uci('e2e4')
        with patch('chess_game.threading.Thread'):
            for i in range(3):
                r=self.record(b,i,i*.5,confidence=.65,change=.65);r['square_scores']['e4']=.89
                g.observe(r,i*.5,100+i*.5)
            self.assertEqual(len(g.history),0)
            for i in range(3,6):g.observe(self.record(b,i,i*.5,confidence=.65,change=.65),i*.5,100+i*.5)
        self.assertEqual(len(g.history),1)

if __name__=='__main__': unittest.main()
