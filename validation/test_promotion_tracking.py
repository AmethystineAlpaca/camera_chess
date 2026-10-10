"""Replay camera frames through consensus, move validation, engine turns and UI data."""
import itertools
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch

import chess
import chess.engine
from chess_game import Game


class PromotionTrackingTests(unittest.TestCase):
    def setUp(self):
        self.capture = 100.
        self.sequence = 0

    def game(self, fen='4k3/P7/8/8/8/8/7p/4K3 w - - 0 1', computer=chess.BLACK):
        g=Game(object());g.board=chess.Board(fen);g.initial_fen=fen;g.mode='endgame'
        g.state='player_turn';g.computer=computer
        g.side_auto=True;g.side_confirmed=True;g.original_near=g.detected_near='black'
        self.assertTrue(g.board.is_valid())
        return g

    def record(self, board, distributions=None):
        self.capture += .3;self.sequence += 1
        probabilities={}
        for s in chess.SQUARES:
            symbol=board.piece_at(s).symbol() if board.piece_at(s) else '.'
            probabilities[chess.square_name(s)]={p:(.99 if p==symbol else .01/12) for p in '.PNBRQKpnbrqk'}
        probabilities.update(distributions or {})
        tops={s:sorted(p.values(),reverse=True) for s,p in probabilities.items()}
        return dict(sequence=self.sequence,capture_time=self.capture,raw=board.board_fen(),phase='stable',
                    min_score=min(p[0] for p in tops.values()),min_margin=min(p[0]-p[1] for p in tops.values()),
                    change_score=min(p[0] for p in tops.values()),square_scores={s:p[0] for s,p in tops.items()},
                    square_probabilities=probabilities,orientation={'status':'detected','near':'black'},orientation_at=self.capture)

    def frames(self, g, board, count=6, jitter_square=None, symbols='PRQ', low_confidence=False):
        for symbol in itertools.islice(itertools.cycle(symbols),count):
            raw=board.copy();distributions={}
            if jitter_square:
                sq=chess.parse_square(jitter_square)
                color=board.color_at(sq)
                symbol=symbol.upper() if color else symbol.lower()
                raw.set_piece_at(sq,chess.Piece.from_symbol(symbol))
                if low_confidence:
                    # Model is sure a white/black piece is present, but not which type.
                    distribution={p:0. for p in '.PNBRQKpnbrqk'}
                    group='PNBRQK' if color else 'pnbrqk'
                    distribution.update({p:.12 for p in group});distribution[symbol]=.38;distribution['.']=.02
                    distributions[jitter_square]=distribution
            record=self.record(raw,distributions)
            with patch('chess_game.threading.Thread'),patch('chess_game.time.monotonic',return_value=self.capture):
                g.observe(record,self.capture,self.capture)

    def promote(self,g,uci='a7a8q'):
        move=chess.Move.from_uci(uci)
        self.frames(g,g.physical_after(move,True))
        self.assertIsNotNone(g.promotion)
        with patch('chess_game.time.time',return_value=self.capture),patch('chess_game.threading.Thread'):
            g.confirm_promotion(g.promotion['id'],uci[4],'pawn')

    def engine_move(self,g,uci):
        self.assertEqual(g.state,'thinking')
        class Engine:
            def play(self,*args,**kwargs):
                return chess.engine.PlayResult(chess.Move.from_uci(uci),None)
        g.engine=Engine();g.think(g.id,g.board.copy(),'normal',g.engine_job)
        self.assertEqual(g.pending.uci(),uci)

    def test_multiple_turns_with_low_confidence_type_jitter_need_no_manual_edits(self):
        g=self.game();self.promote(g)
        proxy=chess.A8
        for engine,player in [('e8f7','a8d5'),('f7g6','d5c5'),('g6f6','c5c2')]:
            self.engine_move(g,engine)
            self.frames(g,g.physical_after(g.pending),jitter_square=chess.square_name(proxy),low_confidence=True)
            self.assertEqual(g.state,'player_turn')
            move=chess.Move.from_uci(player);self.assertIn(move,g.board.legal_moves)
            target=g.physical_after(move);proxy=move.to_square
            # Lifting the pawn temporarily does not erase its identity.
            lifted=g.physical();lifted.remove_piece_at(move.from_square)
            self.frames(g,lifted,count=2)
            self.frames(g,target,jitter_square=chess.square_name(proxy),low_confidence=True)
            self.assertEqual(g.board.peek(),move)
            self.assertEqual(g.board.piece_at(proxy),chess.Piece(chess.QUEEN,chess.WHITE))
            self.assertEqual(g.proxies,{proxy})
            self.assertFalse(g.manual_corrections)
            self.assertFalse(g.errors)
            self.assertIsNone(g.promotion)
        self.frames(g,g.physical(),count=8,jitter_square='c2',low_confidence=True)
        self.assertEqual(len(g.history),7)
        self.assertFalse(any('not confirmed' in e['text'] for e in g.events))
        self.assertEqual(g.snapshot(self.capture)['proxies'][0]['square'],'c2')

    def test_black_computer_proxy_is_tracked_across_frames(self):
        g=self.game('4k3/P7/8/8/8/8/7p/4K3 b - - 0 1',computer=chess.BLACK)
        g.state='thinking';g.engine_job='first'
        self.engine_move(g,'h2h1q');self.promote(g,'h2h1q')
        self.frames(g,g.physical_after(chess.Move.from_uci('e1d2')),jitter_square='h1',low_confidence=True)
        self.engine_move(g,'h1d5')
        self.frames(g,g.physical_after(g.pending),jitter_square='d5',low_confidence=True)
        self.assertEqual(g.board.piece_at(chess.D5),chess.Piece(chess.QUEEN,chess.BLACK))
        self.assertEqual(g.proxies,{chess.D5});self.assertFalse(g.errors)

    def test_illegal_destination_wrong_color_or_occupied_source_never_commits(self):
        for scenario in ('illegal','wrong-color','source-occupied'):
            with self.subTest(scenario=scenario):
                g=self.game();self.promote(g);self.engine_move(g,'e8f7')
                self.frames(g,g.physical_after(g.pending));count=len(g.history)
                if scenario=='illegal':
                    observed=g.physical();observed.remove_piece_at(chess.A8)
                    observed.set_piece_at(chess.B6,chess.Piece(chess.PAWN,chess.WHITE))
                else:
                    observed=g.physical_after(chess.Move.from_uci('a8d5'))
                    if scenario=='wrong-color':observed.set_piece_at(chess.D5,chess.Piece(chess.PAWN,chess.BLACK))
                    else:observed.set_piece_at(chess.A8,chess.Piece(chess.PAWN,chess.WHITE))
                self.frames(g,observed,count=8)
                self.assertEqual(len(g.history),count);self.assertEqual(g.proxies,{chess.A8})

    def test_uncertain_occupancy_or_bad_probability_data_cannot_confirm_move(self):
        for kind in ('empty','bad-data','unrelated-uncertainty'):
            with self.subTest(kind=kind):
                g=self.game();self.promote(g);self.engine_move(g,'e8f7')
                self.frames(g,g.physical_after(g.pending));count=len(g.history)
                observed=g.physical_after(chess.Move.from_uci('a8d5'))
                observed.set_piece_at(chess.D5,chess.Piece(chess.ROOK,chess.WHITE))
                for _ in range(8):
                    record=self.record(observed)
                    p=record['square_probabilities']['d5']
                    p.update({s:0. for s in p});p.update(R=.45,P=.1,**{'.':.45})
                    record['min_score']=.45;record['min_margin']=0;record['square_scores']['d5']=.45
                    if kind=='bad-data':p['R']=float('nan')
                    if kind=='unrelated-uncertainty':
                        p.update(R=.6,P=.38,**{'.':.02})
                        q=record['square_probabilities']['h2'];q.update({s:0. for s in q});q.update(p=.51,q=.49)
                    with patch('chess_game.threading.Thread'):g.observe(record,self.capture,self.capture)
                self.assertEqual(len(g.history),count);self.assertEqual(g.proxies,{chess.A8})

    def test_an_unrelated_recognition_error_never_demotes_the_play_board_icon(self):
        g=self.game();self.promote(g);self.engine_move(g,'e8f7')
        self.frames(g,g.physical_after(g.pending))
        wrong=g.physical();wrong.set_piece_at(chess.H2,chess.Piece(chess.QUEEN,chess.BLACK))
        self.frames(g,wrong,count=8)
        self.assertTrue(g.errors)
        snapshot=g.snapshot(self.capture)
        def uses(svg):
            return [n.get('{http://www.w3.org/1999/xlink}href') for n in ET.fromstring(svg).iter() if n.tag.endswith('}use')]
        self.assertIn('#white-queen',uses(snapshot['board_svg']))
        # The physical-piece editor still truthfully shows a pawn.
        self.assertIn('#white-pawn',uses(snapshot['correction_svg']))
        self.assertEqual(g.board.piece_type_at(chess.A8),chess.QUEEN)

    def test_stationary_proxy_jitter_is_not_reported_as_a_second_board_error(self):
        g=self.game();self.promote(g);self.engine_move(g,'e8f7')
        self.frames(g,g.physical_after(g.pending))
        wrong=g.physical();wrong.set_piece_at(chess.H2,chess.Piece(chess.QUEEN,chess.BLACK))
        self.frames(g,wrong,count=8,jitter_square='a8',symbols='RQB',low_confidence=True)
        self.assertNotIn('a8',g.errors)
        self.assertEqual(g.proxies,{chess.A8})
        self.assertEqual(g.raw_observed.piece_type_at(chess.A8),chess.QUEEN)
        self.assertEqual(len(g.history),2)

    def test_proxy_capture_and_saved_resume_through_camera_frames(self):
        g=self.game('1r2k3/P7/8/8/8/8/7p/4K3 w - - 0 1');self.promote(g)
        self.engine_move(g,'e8f7');self.frames(g,g.physical_after(g.pending))
        saved=g.serialize();h=self.game();h.load_data(saved,automatic=True,strict=True)
        self.frames(h,g.physical())
        self.assertEqual(h.state,'player_turn');self.assertEqual(h.proxies,{chess.A8})
        self.frames(h,h.physical_after(chess.Move.from_uci('e1d1')),jitter_square='a8',low_confidence=True)
        self.engine_move(h,'b8a8');self.frames(h,h.physical_after(h.pending))
        self.assertFalse(h.proxies)
        self.assertEqual(h.board.piece_at(chess.A8),chess.Piece(chess.ROOK,chess.BLACK))
        h.undo_round();self.frames(h,h.physical())
        self.assertEqual(h.proxies,{chess.A8});self.assertEqual(h.state,'player_turn')

    def test_old_manual_correction_does_not_pin_the_promoted_piece_to_its_square(self):
        g=self.game();self.promote(g);self.engine_move(g,'e8f7')
        self.frames(g,g.physical_after(g.pending))
        # Earlier the model missed this pawn; a human corrected its occupancy.
        g.manual_corrections['a8']=dict(seen='.',piece='P',occupancy_confirmed=False)
        target=g.physical_after(chess.Move.from_uci('a8d5'))
        self.frames(g,target,jitter_square='d5',symbols='RBQ',low_confidence=True)
        self.assertEqual(g.proxies,{chess.D5})
        self.assertNotIn('a8',g.manual_corrections)
        self.assertEqual(g.board.piece_type_at(chess.D5),chess.QUEEN)

    def test_pending_preview_places_identity_and_badge_at_the_new_square(self):
        g=self.game();self.promote(g);self.engine_move(g,'e8f7')
        self.frames(g,g.physical_after(g.pending));g.computer=chess.WHITE
        g.state='computer_move';g.pending=chess.Move.from_uci('a8d5')
        target=g.physical_after(g.pending)
        self.frames(g,target,count=1)
        self.assertFalse(g.snapshot(self.capture)['pending_arrived'])
        # A stable observation can preview arrival even with an unrelated error.
        target.set_piece_at(chess.A7,chess.Piece.from_symbol('p'))
        self.frames(g,target,count=6)
        snap=g.snapshot(self.capture)
        self.assertTrue(snap['pending_arrived']);self.assertEqual(g.proxies,{chess.A8})
        self.assertEqual(snap['proxies'][0]['square'],'d5')
        self.assertIn('data-square="d5"',snap['board_svg'])
        self.assertIn('#white-queen',snap['board_svg'])


if __name__=='__main__':unittest.main()
