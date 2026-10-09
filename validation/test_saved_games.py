import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import chess
from chess_game import Game
from saved_games import SavedGames

class SavedGameTests(unittest.TestCase):
    def game(self):
        g=Game(object());g.reset(aligned=True);g.accept_observation(chess.Board())
        return g

    def test_moves_pause_and_reset_never_auto_save(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'current.json';g=Game(object(),path);g.reset(aligned=True)
            g.accept_observation(chess.Board());g.commit(chess.Move.from_uci('e2e4'))
            g.emit('Moved.');g.pause();g.resume();g.reset()
            self.assertFalse(path.exists())

    def test_multiple_named_saves_survive_unsaved_new_game(self):
        with tempfile.TemporaryDirectory() as directory:
            library=SavedGames(directory);g=self.game()
            first=library.save(g,'Opening')
            for uci in ('e2e4','a7a6','e4e5','d7d5'):g.commit(chess.Move.from_uci(uci))
            second=library.save(g,'En passant')
            expected=g.board.fen();g.reset()
            self.assertEqual(len(library.list()),2)
            loaded=Game(object());loaded.load_data(library.load(second),strict=True)
            self.assertEqual(loaded.board.fen(),expected)
            self.assertEqual(loaded.board.ep_square,chess.D6)
            self.assertEqual(len(loaded.history),4)
            self.assertEqual(library.load(first)['moves'],[])

    def test_load_requires_exact_saved_board_before_any_move(self):
        g=self.game();data=g.serialize();g.load_data(data,strict=True)
        advanced=chess.Board();advanced.push_uci('e2e4')
        with patch('chess_game.threading.Thread') as engine:
            g.accept_observation(advanced)
            self.assertEqual(g.state,'reconcile');self.assertEqual(len(g.history),0)
            engine.assert_not_called()
        g.accept_observation(chess.Board());self.assertEqual(g.state,'player_turn')

    def test_pending_move_is_not_required_until_saved_board_matches(self):
        g=self.game();g.computer=chess.WHITE;g.state='computer_move';g.pending=chess.Move.from_uci('e2e4')
        g.load_data(g.serialize(),strict=True)
        advanced=chess.Board();advanced.push_uci('e2e4');g.accept_observation(advanced)
        self.assertEqual(g.state,'reconcile');self.assertFalse(g.history)
        self.assertEqual(g.snapshot(100)['errors'][0].split(':')[0],'e2')
        g.accept_observation(chess.Board());self.assertEqual(g.state,'computer_move')
        self.assertEqual(g.pending.uci(),'e2e4');self.assertFalse(g.history)

    def test_resume_keeps_unsaved_position_but_rechecks_it(self):
        g=self.game()
        for move in ('e2e4','e7e5'):g.commit(chess.Move.from_uci(move))
        expected=g.board.fen();g.pause();g.clear_observations();g.resume_current()
        self.assertEqual(g.board.fen(),expected);self.assertTrue(g.strict_recovery)
        self.assertEqual(g.state,'reconcile');self.assertEqual(len(g.history),2)

    def test_ghost_missing_piece_and_cross_wrong_piece(self):
        g=self.game();g.load_data(g.serialize(),strict=True)
        wrong=chess.Board();wrong.remove_piece_at(chess.D1);wrong.set_piece_at(chess.E2,chess.Piece(chess.KNIGHT,chess.BLACK))
        g.accept_observation(wrong)
        svg=ET.fromstring(g.snapshot(100)['board_svg'])
        ghosts=[n for n in svg.iter() if n.get('class')=='ghost-piece']
        crosses=[n for n in svg.iter() if n.get('class')=='wrong-piece']
        self.assertEqual({n.get('data-square') for n in ghosts},{'d1','e2'})
        self.assertEqual({n.get('data-square') for n in crosses},{'e2'})
        self.assertTrue(all(float(n.get('opacity'))<.5 for n in ghosts))
        self.assertTrue(any(n.get('id')=='white-queen' for n in svg.iter()))

    def test_invalid_save_does_not_reset_live_game(self):
        g=self.game();identity=g.id;data=g.serialize();data['moves']=['e2e5']
        with self.assertRaises(ValueError):g.load_data(data,strict=True)
        self.assertEqual(g.id,identity);self.assertEqual(g.state,'player_turn')

    def test_library_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):SavedGames(directory).load('../game-state')

    def test_delete_only_selected_save_and_persists(self):
        with tempfile.TemporaryDirectory() as directory:
            library=SavedGames(directory);g=self.game()
            first=library.save(g,'First');second=library.save(g,'Second')
            library.delete(first)
            fresh=SavedGames(directory)
            self.assertEqual([item['id'] for item in fresh.list()],[second])
            self.assertEqual(fresh.load(second)['moves'],[])
            with self.assertRaises(ValueError):fresh.load(first)
            with self.assertRaises(ValueError):fresh.delete(first)

    def test_delete_validates_id_and_supports_previous_save(self):
        with tempfile.TemporaryDirectory() as directory:
            legacy=Path(directory)/'previous.json';legacy.write_text('{}')
            library=SavedGames(Path(directory)/'saves',legacy)
            for identity in ('../previous',None,'invalid'):
                with self.assertRaises(ValueError):library.delete(identity)
            self.assertTrue(legacy.exists())
            library.delete('previous')
            self.assertFalse(legacy.exists());self.assertEqual(library.list(),[])

    def scan(self):
        g=Game(object());g.start_endgame()
        b=chess.Board('8/8/4k3/8/8/3K4/3P4/8 w - - 0 1')
        for i in range(5):
            t=100+i*.4
            g.observe(dict(raw=b.board_fen(),capture_time=t,sequence=i,min_score=.99,min_margin=.9,change_score=.99,phase='stable',
                orientation={'status':'detected','near':'black'},orientation_at=t),i*.4,t)
        self.assertEqual(g.state,'endgame_ready')
        return g,b

    def test_endgame_scans_nonstarting_position_and_needs_confirmation(self):
        g,b=self.scan();self.assertEqual(g.board.board_fen(),b.board_fen());self.assertFalse(g.history)
        self.assertFalse(g.snapshot(102)['can_save'])
        with patch('chess_game.time.time',return_value=102):g.confirm_endgame(b.board_fen(),'white','white')
        self.assertEqual(g.state,'player_turn');self.assertTrue(g.snapshot(102)['can_save'])
        h=Game(object());h.load_data(g.serialize(),strict=True)
        self.assertEqual(h.board.fen(),g.board.fen());self.assertEqual(h.mode,'endgame')
        self.assertFalse(h.side_auto)

    def test_endgame_chooses_black_to_move(self):
        g,b=self.scan()
        with patch('chess_game.time.time',return_value=102),patch('chess_game.threading.Thread') as engine:
            g.confirm_endgame(b.board_fen(),'black','white')
            self.assertEqual(g.state,'thinking');self.assertFalse(g.board.turn);engine.assert_called_once()

    def test_endgame_rejects_changed_or_illegal_position(self):
        g,b=self.scan()
        with patch('chess_game.time.time',return_value=102):
            with self.assertRaisesRegex(ValueError,'changed'):g.confirm_endgame(chess.STARTING_BOARD_FEN,'white','white')
            g.board=chess.Board('8/8/8/8/8/8/4k3/4K3 w - - 0 1')
            with self.assertRaisesRegex(ValueError,'not legal'):g.confirm_endgame(g.board.board_fen(),'white','white')
        self.assertEqual(g.state,'endgame_ready')

    def test_endgame_cannot_confirm_without_both_kings(self):
        g=Game(object());g.start_endgame();g.accept_observation(chess.Board('8/8/8/8/8/8/8/4K3 w - - 0 1'))
        self.assertEqual(g.state,'endgame_scan');self.assertIn('Both kings',g.message)

if __name__=='__main__':unittest.main()
