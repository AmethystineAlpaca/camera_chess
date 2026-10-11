"""Real HTTP correction flow, evidence fidelity, stale drafts and safe retries."""
import ast
import json
import io
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock
import hashlib
import traceback
from pathlib import Path
import tempfile
import threading
import time
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import chess
from PIL import Image
from chess_game import Game
from recognition_log import RecognitionLog, read_frame, read_observation


class CorrectionLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.audit = RecognitionLog(self.root/'logs')
        self.addCleanup(self.close_log)
        self.game = Game()
        self.game.board = chess.Board('4k3/8/8/8/8/8/8/4K2R w - - 0 1')
        self.game.state = 'player_turn'
        self.feed('4k3/8/8/8/8/8/1p6/4K2N', 1)
        # Load the actual HTTP handler without starting camera/engine/module threads.
        tree = ast.parse(Path(__file__).with_name('lite_live_server.py').read_text())
        definition = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Handler')
        scope = dict(SimpleHTTPRequestHandler=SimpleHTTPRequestHandler, O=self.root, D=self.root,
                     game=self.game, audit=self.audit, saved_settings={}, recognition_mode='original', time=time, json=json, traceback=traceback,
                     log_control=Mock(enabled=False, status=Mock(return_value=dict(enabled=False))))
        exec(compile(ast.Module(body=[definition], type_ignores=[]), '<server-handler>', 'exec'), scope)
        self.handler_class=scope['Handler']
        self.server = ThreadingHTTPServer(('127.0.0.1',0),self.handler_class)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join()

    def close_log(self):
        for handler in self.audit.logger.handlers[:]:
            handler.close()
            self.audit.logger.removeHandler(handler)

    def feed(self, placement, sequence):
        stamp = time.time()
        record = dict(raw=placement, capture_time=stamp, sequence=sequence,
                      phase='stable', min_score=.99, min_margin=.9, change_score=.99,
                      square_probabilities={'h1':{'N':.7,'R':.3}}, enhancement='original')
        buffer=io.BytesIO();Image.new('RGB',(32,32),(sequence%256,80,120)).save(buffer,format='JPEG');jpeg=buffer.getvalue()
        (self.root/'latest.bin').write_bytes(json.dumps(record).encode()+b'\n'+jpeg)
        self.game.observe(record,sequence*.5,stamp)
        self.audit.observe(self.game,record,jpeg)
        return jpeg

    def post(self, path, data):
        request = Request('http://127.0.0.1:'+str(self.server.server_port)+path,
                          data=json.dumps(data).encode(), headers={'Content-Type':'application/json'})
        try:
            with urlopen(request) as response:
                return response.status, json.load(response)
        except HTTPError as response:
            return response.code, json.load(response)

    def opened(self):
        status, snapshot = self.post('/api/correction-context',{'session':self.game.id})
        self.assertEqual(status,200)
        return snapshot

    def request_data(self, snapshot):
        return dict(session=snapshot['session'], position_token=snapshot['position_token'],
                    correction_id=snapshot['correction_id'],changes={'h1':'R','b2':'.'})

    def test_crf_correction_rolls_back_automatically_and_keeps_raw_evidence(self):
        from crf_observation import CRFObservation
        from test_crf_integration import reading
        g = self.game
        g.postprocessor = CRFObservation(); g.reset(aligned=True); g.state = 'player_turn'
        moved = g.board.copy(); moved.push_uci('e2e4')
        jpeg = (self.root/'latest.bin').read_bytes().split(b'\n',1)[1]
        with patch('chess_game.threading.Thread'):
            for i in range(4):
                record = reading(moved, i, ('a2', 'B')); record['capture_time'] = time.time()
                g.observe(record, i*.3, record['capture_time'])
                self.audit.observe(g, record, jpeg)
        self.assertEqual(len(g.history), 1)
        snapshot = self.opened()
        self.assertTrue(g.editing_correction())
        self.assertEqual(snapshot['observed_placement'], moved.board_fen())
        self.assertNotEqual(snapshot['raw_placement'], moved.board_fen())
        # The asynchronous engine result must not invalidate the user's draft.
        g.pending = chess.Move.from_uci('e7e5'); g.state = 'computer_move'
        data = dict(session=g.id, position_token=snapshot['position_token'],
                    correction_id=snapshot['correction_id'], changes={'e4': '.', 'e2': 'P'})
        status, after = self.post('/api/correct-squares', data)
        self.assertEqual(status, 200, after)
        self.assertEqual(after['fen'], chess.STARTING_FEN)
        self.assertIsNone(after['pending'])
        self.assertFalse(g.editing_correction())
        directory = self.audit.directory(snapshot['correction_id'])
        pair = json.loads((directory/'pair.json').read_text())
        self.assertTrue(pair['complete'])
        self.assertEqual(set(pair['edits']), {'e4', 'e2'})
        self.assertEqual(pair['annotation_scope'], 'edited_squares_only')
        raw_after = chess.Board(pair['after'] + ' w - - 0 1')
        self.assertEqual(raw_after.piece_at(chess.A2).symbol(), 'B')
        self.assertEqual(g.board.piece_at(chess.A2).symbol(), 'P')
        self.assertEqual(json.loads((directory/'recognition.json').read_text()), record)
        with patch.object(g, 'correct_squares', side_effect=AssertionError('duplicate apply')):
            self.assertEqual(self.post('/api/correct-squares', data)[0], 200)

    def test_crf_editor_cancel_and_reopen_preserve_frozen_snapshot(self):
        from crf_observation import CRFObservation
        self.game.postprocessor = CRFObservation()
        opened = self.opened()
        data = dict(session=self.game.id, correction_id=opened['correction_id'])
        self.assertTrue(self.game.editing_correction())
        self.assertEqual(self.post('/api/end-correction', data)[0], 200)
        self.assertFalse(self.game.editing_correction())
        status, reopened = self.post('/api/correction-context', data)
        self.assertEqual(status, 200)
        self.assertEqual(reopened['recognition_frame'], opened['recognition_frame'])
        self.assertEqual(reopened['correction_id'], opened['correction_id'])
        self.assertTrue(self.game.editing_correction())

    def test_batch_keeps_original_frame_and_retry_is_idempotent(self):
        snapshot = self.opened()
        directory = self.audit.directory(snapshot['correction_id'])
        frozen = (directory/'recognized-camera.jpg').read_bytes()
        apply_frame = self.feed('4k3/8/8/8/8/8/1p6/4K2B', 2)
        status, after = self.post('/api/correct-squares', self.request_data(snapshot))
        self.assertEqual(status,200)
        self.assertEqual(after['observed_placement'],self.game.board.board_fen())
        self.assertEqual((directory/'recognized-camera.jpg').read_bytes(),frozen)
        self.assertNotEqual(frozen,apply_frame)
        self.assertFalse((directory/'apply-camera.jpg').exists())
        changes = json.loads((directory/'changes.json').read_text())
        self.assertEqual(changes['reviewed_before'],snapshot['observed_placement'])
        self.assertEqual(changes['changes'],{'h1':'R','b2':'.'})
        self.assertIn('Same recognition with your corrections',(directory/'review.html').read_text())
        self.assertEqual(json.loads((directory/'recognition.json').read_text())['sequence'],1)
        # Even if a move has since advanced, the same retry is an acknowledgement.
        self.game.board.push_uci('h1h2')
        with patch.object(self.game,'correct_squares',side_effect=AssertionError('duplicate apply')):
            status, after = self.post('/api/correct-squares',self.request_data(snapshot))
        self.assertEqual(status,200)

    def test_stale_and_invalid_batches_are_logged_without_partial_application(self):
        snapshot = self.opened();data=self.request_data(snapshot)
        data['changes']['z9']='K'
        self.assertEqual(self.post('/api/correct-squares',data)[0],400)
        self.assertFalse(self.game.manual_corrections)
        self.game.board.push_uci('h1h2')
        self.assertEqual(self.post('/api/correct-squares',self.request_data(snapshot))[0],400)
        self.assertFalse(self.game.manual_corrections)
        self.assertIn('request_failed',(self.audit.root/'events.jsonl').read_text())
        self.assertFalse((self.audit.directory(snapshot['correction_id'])/'result.json').exists())

    def test_disk_failure_before_apply_preserves_game(self):
        snapshot=self.opened()
        with patch('recognition_log.write_json',side_effect=OSError('Disk full')):
            status, result=self.post('/api/correct-squares',self.request_data(snapshot))
        self.assertEqual(status,400)
        self.assertFalse(self.game.manual_corrections)
        self.assertIn('Disk full',result['error'])

    def test_disk_failure_after_apply_returns_warning_and_retry_does_not_reapply(self):
        snapshot=self.opened()
        from recognition_log import write_json
        def fail_result(path, value):
            if path.name=='result.json':raise OSError('Disk full')
            write_json(path,value)
        with patch('recognition_log.write_json',side_effect=fail_result):
            status,result=self.post('/api/correct-squares',self.request_data(snapshot))
        self.assertEqual(status,200)
        self.assertIn('could not be saved',result['evidence_warning'])
        self.assertFalse(result['evidence_report'])
        with patch.object(self.game,'correct_squares',side_effect=AssertionError('duplicate apply')):
            self.assertEqual(self.post('/api/correct-squares',self.request_data(snapshot))[0],200)

    def test_missing_camera_and_cancel_are_recorded(self):
        self.audit.frames.clear()
        (self.root/'latest.bin').unlink()
        snapshot=self.opened()
        self.assertTrue(snapshot['evidence_warning'])
        self.assertEqual(self.post('/api/diagnostics',dict(event='cancel',correction_id=snapshot['correction_id'],changes={'h1':'R'}))[0],200)
        events=(self.audit.root/'events.jsonl').read_text()
        self.assertIn('ui_cancel',events)
        self.assertIn('evidence_missing',events)
        self.assertFalse(self.game.manual_corrections)


    def frozen_view(self):
        with urlopen('http://127.0.0.1:'+str(self.server.server_port)+'/api/game') as response:
            view=json.load(response)
        return {k:view[k] for k in ('session','position_token','recognition_frame','observed_placement')}

    def test_open_uses_frame_seen_in_browser_even_when_new_frame_is_occluded(self):
        viewed=self.frozen_view()
        original_frame=(self.root/'latest.bin').read_bytes().split(b'\n',1)[1]
        self.feed('4k3/8/8/8/8/8/qp6/4K2B',2)
        status,snapshot=self.post('/api/correction-context',viewed)
        self.assertEqual(status,200)
        self.assertEqual(snapshot['recognition_frame'],viewed['recognition_frame'])
        self.assertEqual(snapshot['raw_placement'],'4k3/8/8/8/8/8/1p6/4K2N')
        # Multiple new frames during editing must not leak into the annotation.
        for seq in range(3,7):self.feed('4k3/8/8/8/8/8/qp6/4K2B',seq)
        status,result=self.post('/api/correct-squares',self.request_data(snapshot))
        self.assertEqual(status,200)
        directory=self.audit.directory(snapshot['correction_id'])
        pair=json.loads((directory/'pair.json').read_text())
        self.assertTrue(pair['complete'])
        self.assertEqual(pair['source']['sequence'],1)
        self.assertEqual(pair['source']['sha256'],hashlib.sha256(original_frame).hexdigest())
        self.assertEqual((directory/'recognized-camera.jpg').read_bytes(),original_frame)
        self.assertEqual({p.name for p in directory.glob('*.jpg')},{'recognized-camera.jpg'})
        self.assertEqual({p.name for p in directory.glob('*.svg')},{'recognition-before.svg','recognition-after.svg'})
        before=chess.Board(pair['before']+' w - - 0 1')
        after=chess.Board(pair['after']+' w - - 0 1')
        self.assertIsNone(after.piece_at(chess.A2)) # Later phantom queen is excluded.
        self.assertIsNone(after.piece_at(chess.B2))
        self.assertEqual(after.piece_at(chess.H1).symbol(),'R')
        for sq in chess.SQUARES:
            if sq not in (chess.B2,chess.H1):self.assertEqual(before.piece_at(sq),after.piece_at(sq))
        self.assertNotEqual(pair['after'],result['observed_placement'])

    def test_identical_positions_do_not_substitute_another_frame(self):
        viewed=self.frozen_view()
        self.feed('4k3/8/8/8/8/8/1p6/4K2N',2)
        del self.audit.frames[(viewed['session'],*viewed['recognition_frame'])]
        status,snapshot=self.post('/api/correction-context',viewed)
        self.assertEqual(status,200)
        self.assertTrue(snapshot['evidence_warning'])
        status,_=self.post('/api/correct-squares',self.request_data(snapshot))
        self.assertEqual(status,200)
        directory=self.audit.directory(snapshot['correction_id'])
        self.assertFalse((directory/'recognized-camera.jpg').exists())
        pair=json.loads((directory/'pair.json').read_text())
        self.assertFalse(pair['complete'])
        self.assertIsNone(pair['source'])

    def test_existing_overrides_are_not_silently_mixed_into_model_labels(self):
        self.game.correct_squares({'a2':'P'},self.game.position_token())
        snapshot=self.opened()
        self.assertNotEqual(snapshot['raw_placement'],snapshot['observed_placement'])
        status,_=self.post('/api/correct-squares',self.request_data(snapshot))
        self.assertEqual(status,200)
        pair=json.loads((self.audit.directory(snapshot['correction_id'])/'pair.json').read_text())
        self.assertEqual(pair['annotation_scope'],'edited_squares_only')
        self.assertIsNone(chess.Board(pair['after']+' w - - 0 1').piece_at(chess.A2))

    def test_pinned_frame_survives_cache_eviction_and_elapsed_time(self):
        snapshot=self.opened()
        directory=self.audit.directory(snapshot['correction_id'])
        original=(directory/'recognized-camera.jpg').read_bytes()
        self.audit.frames.clear()
        self.feed('4k3/8/8/8/8/8/qp6/4K2N',2)
        status,_=self.post('/api/correct-squares',self.request_data(snapshot))
        self.assertEqual(status,200)
        self.assertEqual((directory/'recognized-camera.jpg').read_bytes(),original)

    def test_retry_repairs_archive_after_disk_recovers_without_reapplying(self):
        snapshot=self.opened();data=self.request_data(snapshot)
        from recognition_log import write_json
        def fail_pair(path,value):
            if path.name=='pair.json':raise OSError('Disk full')
            write_json(path,value)
        with patch('recognition_log.write_json',side_effect=fail_pair):
            status,result=self.post('/api/correct-squares',data)
        self.assertEqual(status,200)
        self.assertTrue(result['evidence_warning'])
        with patch.object(self.game,'correct_squares',side_effect=AssertionError('applied twice')):
            status,result=self.post('/api/correct-squares',data)
        self.assertEqual(status,200)
        self.assertIsNone(result['evidence_warning'])
        directory=self.audit.directory(snapshot['correction_id'])
        self.assertTrue(json.loads((directory/'pair.json').read_text())['complete'])

    def test_two_tabs_cannot_silently_overwrite_same_square(self):
        first=self.opened();second=self.opened()
        request=self.request_data(first);request['changes']={'h1':'R'}
        self.assertEqual(self.post('/api/correct-squares',request)[0],200)
        request=self.request_data(second);request['changes']={'h1':'Q'}
        status,result=self.post('/api/correct-squares',request)
        self.assertEqual(status,400)
        self.assertEqual(self.game.manual_corrections['h1']['piece'],'R')

    def test_legacy_batch_without_context_cannot_fabricate_original_frame(self):
        request=self.request_data(self.opened());del request['correction_id']
        status,_=self.post('/api/correct-squares',request)
        self.assertEqual(status,400)
        self.assertFalse(self.game.manual_corrections)

    def test_missing_original_file_is_not_marked_complete(self):
        snapshot=self.opened();directory=self.audit.directory(snapshot['correction_id'])
        (directory/'recognized-camera.jpg').unlink()
        status,result=self.post('/api/correct-squares',self.request_data(snapshot))
        self.assertEqual(status,200)
        self.assertTrue(result['evidence_warning'])
        self.assertFalse(json.loads((directory/'pair.json').read_text())['complete'])

    def test_observation_publication_race_never_pairs_different_results(self):
        old={'raw':'old','capture_time':10,'sequence':1}
        new={'raw':'new','capture_time':11,'sequence':2}
        status=self.root/'status.json';packet=self.root/'observation.bin'
        status.write_text(json.dumps(old));packet.write_bytes(json.dumps(new).encode()+b'\nnew-image')
        self.assertEqual(read_observation(self.root),(new,b'new-image'))
        status.write_text(json.dumps(new));packet.write_bytes(json.dumps(old).encode()+b'\nold-image')
        self.assertEqual(read_observation(self.root),(new,None))
        status.write_text(json.dumps({'phase':'loading'}))
        self.assertEqual(read_observation(self.root),({'phase':'loading'},None))

    def test_corrupted_image_is_not_embedded_or_marked_complete(self):
        snapshot=self.opened();directory=self.audit.directory(snapshot['correction_id'])
        (directory/'recognized-camera.jpg').write_bytes(b'corrupted')
        status,result=self.post('/api/correct-squares',self.request_data(snapshot))
        self.assertEqual(status,200)
        self.assertTrue(result['evidence_warning'])
        self.assertFalse(json.loads((directory/'pair.json').read_text())['complete'])
        self.assertNotIn('data:image/jpeg',(directory/'review.html').read_text())

    def test_disjoint_tabs_preserve_each_others_edits(self):
        first=self.opened();second=self.opened()
        request=self.request_data(first);request['changes']={'h1':'R'}
        self.assertEqual(self.post('/api/correct-squares',request)[0],200)
        request=self.request_data(second);request['changes']={'b2':'.'}
        self.assertEqual(self.post('/api/correct-squares',request)[0],200)
        self.assertEqual(self.game.manual_corrections['h1']['piece'],'R')
        self.assertEqual(self.game.manual_corrections['b2']['piece'],'.')

    def test_saved_image_decodes_and_report_contains_exactly_three_images(self):
        snapshot=self.opened();directory=self.audit.directory(snapshot['correction_id'])
        self.assertEqual(self.post('/api/correct-squares',self.request_data(snapshot))[0],200)
        with Image.open(directory/'recognized-camera.jpg') as image:image.verify()
        self.assertEqual((directory/'review.html').read_text().count('<img '),3)

    def test_concurrent_duplicate_requests_apply_once(self):
        snapshot=self.opened();data=self.request_data(snapshot)
        with patch.object(self.game,'correct_squares',wraps=self.game.correct_squares) as apply:
            with ThreadPoolExecutor(max_workers=2) as workers:
                results=list(workers.map(lambda _:self.post('/api/correct-squares',data),range(2)))
            self.assertEqual([status for status,_ in results],[200,200])
            self.assertEqual(apply.call_count,1)
        self.assertTrue(self.audit.archive_ready(snapshot['correction_id']))

    def test_lost_response_is_logged_without_turning_success_into_error(self):
        handler=Mock(command='POST',path='/api/correct-squares')
        handler.wfile.write.side_effect=BrokenPipeError()
        self.handler_class.respond(handler,{'correction_id':'reply-lost'})
        handler.send_response.assert_called_once_with(200)
        self.assertIn('response_lost',(self.audit.root/'events.jsonl').read_text())

    def test_state_logging_deduplicates_and_reports_recovery(self):
        before=(self.audit.root/'events.jsonl').read_text()
        self.audit.observe(self.game)
        self.assertEqual((self.audit.root/'events.jsonl').read_text(),before)
        self.audit.problem('camera offline');self.audit.problem('camera offline');self.audit.problem(None)
        events=[json.loads(line) for line in (self.audit.root/'events.jsonl').read_text().splitlines()]
        self.assertEqual(sum(e['type']=='observation_error' for e in events),1)
        self.assertEqual(sum(e['type']=='observation_recovered' for e in events),1)
        with self.assertRaises(ValueError):self.audit.directory('../escape')


if __name__=='__main__':unittest.main()
