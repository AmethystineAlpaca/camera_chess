"""Data fidelity, opt-out races, persistence failures and trusted export gates."""
import ast
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import chess
from PIL import Image
from chess_game import Game
from crf_observation import CRFObservation
from improvement_log import (DataLogControl, EvidenceWriter, InferenceEvidence, ControllerEvidence,
                             board_array, game_context, read_json, atomic_json, digest, SQUARES, CLASSES)
from evidence_review import load_packet, paired_decision, label, export, gallery


def reading(board, sequence=1):
    labels = board_array(board.fen())
    values = {s:{c:(.988 if i == y else .001) for i,c in enumerate(CLASSES)} for s,y in zip(SQUARES,labels)}
    return dict(sequence=sequence,capture_time=time.time(),raw=board.board_fen(),phase='stable',
                square_probabilities=values,min_score=.988,min_margin=.987,change_score=.988,input_size=644)


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.game = Game(postprocessor=CRFObservation());self.game.reset(aligned=True);self.game.state='player_turn'
        self.control = DataLogControl(self.root/'logs',self.root/'out/control.json')
        self.control.publish(game_context(self.game))
        self.controller = ControllerEvidence(self.control)
        self.addCleanup(self.controller.writer.close)
        buffer=io.BytesIO();Image.new('RGB',(24,24),(30,80,120)).save(buffer,format='JPEG');self.jpeg=buffer.getvalue()
        model=self.root/'model';model.write_bytes(b'checkpoint')
        self.producer = InferenceEvidence(self.control.path,self.root/'absent-capture',model)
        self.addCleanup(self.producer.close)

    def drain(self):
        self.producer.writer.queue.join();self.controller.writer.queue.join()

    def capture(self, board=None, sequence=1):
        record=reading(board or self.game.board,sequence)
        self.producer.observation(record,self.jpeg,self.producer.writer.control())
        self.controller.observe(self.game,record,sequence*.3,record['capture_time'])
        self.drain()
        return record

    def decisions(self):return sorted(self.control.root.glob('*/decisions/*'))

    def test_exact_image_probabilities_reference_and_colors_are_paired(self):
        original=game_context(self.game)
        record=self.capture()
        decision,observation,image_dir=paired_decision(self.decisions()[0])
        self.assertEqual((image_dir/'image.jpg').read_bytes(),self.jpeg)
        self.assertEqual(observation['data']['record']['square_probabilities'],record['square_probabilities'])
        self.assertEqual(decision['data']['before'],original)
        self.assertEqual(decision['data']['before']['player'],'white')
        self.assertEqual(decision['data']['before']['computer'],'black')
        self.assertEqual(decision['label_status'],'unverified');self.assertIsNone(decision['label'])
        self.assertEqual(decision['label_mask'],[False]*64)
        self.assertRegex(decision['id'],r'^\d{4}-\d\d-\d\d_')
        self.assertEqual(len(decision['data']['proposed_64']),64)

    def test_commit_keeps_pre_move_reference_and_failure_frames(self):
        previous=self.game.board.fen();target=self.game.board.copy();target.push_uci('e2e4')
        with patch('chess_game.threading.Thread'):
            for i in range(1,5):self.capture(target,i)
        decisions=[load_packet(p)['data'] for p in self.decisions()]
        committed=[d for d in decisions if d['application_board_changed']]
        self.assertEqual(len(committed),1)
        self.assertEqual(committed[0]['before']['reference_fen'],previous)
        self.assertEqual(committed[0]['after']['reference_64'],board_array(target.fen()))
        self.assertTrue(any(d['trace']['postprocess']['status']=='settling' for d in decisions))

    def test_disabled_recording_saves_nothing_and_recognition_continues(self):
        self.control.set_enabled(False,game_context(self.game))
        self.capture()
        self.assertFalse(list(self.control.root.glob('*/observations/*')))
        self.assertFalse(self.decisions())
        self.assertIsNotNone(self.game.raw_observed)

    def test_opt_out_discards_in_flight_and_reenable_cannot_revive_it(self):
        old=self.producer.writer.control()
        self.control.set_enabled(False,game_context(self.game))
        self.control.set_enabled(True,game_context(self.game))
        record=reading(self.game.board)
        self.producer.observation(record,self.jpeg,old)
        self.drain()
        self.assertFalse(list(self.control.root.glob('*/observations/*')))
        self.capture(sequence=2)
        self.assertEqual(len(list(self.control.root.glob('*/observations/*'))),1)

    def test_game_rotation_preserves_queued_last_frames(self):
        old=self.producer.writer.control()
        self.game.reset();self.control.publish(game_context(self.game))
        self.producer.observation(reading(chess.Board()),self.jpeg,old)
        self.drain()
        self.assertTrue(list((self.control.root/old['recording']/'observations').glob('*')))

    def test_budget_stops_without_overwriting_and_game_continues(self):
        self.control.budget=1;self.control.publish(game_context(self.game),force=True)
        self.capture()
        self.assertIn('budget',self.control.status()['stopped_reason'])
        self.assertFalse(list(self.control.root.glob('*/observations/*')))
        self.assertIsNotNone(self.game.raw_observed)

    def test_broken_write_cannot_be_a_complete_sample(self):
        with patch('improvement_log.os.rename',side_effect=OSError('Disk disconnected')):
            self.capture()
        self.assertIn('Disk disconnected',self.control.status()['stopped_reason'])
        for path in self.control.root.rglob('record.json'):
            with self.assertRaises(ValueError):load_packet(path.parent)

    def test_orientation_rejection_is_logged_without_fake_crf_result(self):
        self.game.side_auto=True;self.game.side_confirmed=False
        self.capture()
        data=load_packet(self.decisions()[0])['data']
        self.assertEqual(data['trace']['stage'],'orientation_gate')
        self.assertIsNone(data['proposed_64'])

    def test_duplicate_poll_does_not_create_duplicate_decisions(self):
        record=self.capture()
        self.controller.observe(self.game,record,3,time.time());self.drain()
        self.assertEqual(len(self.decisions()),1)

    def test_producer_records_without_controller_consumption(self):
        record=reading(self.game.board)
        self.producer.observation(record,self.jpeg,self.producer.writer.control());self.drain()
        self.assertEqual(len(list(self.control.root.glob('*/observations/*'))),1)
        self.assertFalse(self.decisions())

    def test_each_model_selection_is_in_decision_and_proxies_are_distinct(self):
        self.capture()
        self.game.postprocessor=CRFObservation(model='large');self.game.invalidate_crf()
        self.capture(sequence=2)
        models=[load_packet(p)['data']['before'] for p in self.decisions()]
        self.assertEqual([d['recognition_model'] for d in models],['small','large'])
        self.assertNotEqual(models[0]['checkpoint']['sha256'],models[1]['checkpoint']['sha256'])
        self.game.proxies={chess.A1}
        context=game_context(self.game)
        self.assertNotEqual(context['reference_64'],context['physical_reference_64'])

    def test_export_requires_review_and_late_correction_invalidates_label(self):
        self.capture();decision=self.decisions()[0];run=decision.parent.parent
        self.assertEqual(export(run,self.root/'empty.jsonl')['exported'],0)
        with self.assertRaises(ValueError):label(decision,self.game.board.fen(),'reviewer-id',True,False)
        label(decision,self.game.board.fen(),'reviewer-id',True,True)
        self.assertEqual(export(run,self.root/'verified.jsonl')['exported'],1)
        self.controller.event('/api/correct-squares',game_context(self.game),game_context(self.game),{'changes':{'a1':'Q'}})
        self.drain()
        result=export(run,self.root/'after-correction.jsonl')
        self.assertEqual(result['exported'],0)
        self.assertIn('Corrections changed',result['excluded'][0]['reason'])
        label(decision,self.game.board.fen(),'reviewer-id',True,True)
        self.assertEqual(export(run,self.root/'rereviewed.jsonl')['exported'],1)

    def test_corrupt_image_and_incorrect_array_cannot_be_exported(self):
        self.capture();decision=self.decisions()[0]
        _,_,image_dir=paired_decision(decision)
        (image_dir/'image.jpg').write_bytes(b'wrong')
        with self.assertRaisesRegex(ValueError,'checksum'):paired_decision(decision)

    def test_gallery_has_original_image_and_unverified_warning(self):
        self.capture();run=self.decisions()[0].parent.parent
        page=gallery(run).read_text()
        self.assertIn('UNVERIFIED',page);self.assertIn('image.jpg',page)

    def test_capture_timeline_records_gaps_without_claiming_continuity(self):
        path=self.producer.capture_path
        path.write_bytes(('1,'+str(time.time())+'\n').encode()+self.jpeg)
        time.sleep(.25)
        path.write_bytes(('5,'+str(time.time())+'\n').encode()+self.jpeg)
        time.sleep(.25);self.drain()
        packets=[load_packet(p)['data'] for p in sorted(self.control.root.glob('*/timeline/*'))]
        self.assertEqual([p['sequence'] for p in packets],[1,5])
        self.assertEqual(packets[1]['skipped_capture_sequences'],3)

    def test_queue_pressure_is_visible_and_does_not_interrupt_inference(self):
        self.producer.writer.max_bytes=1
        record=self.capture()
        self.assertIsNone(record['data_log'])
        time.sleep(.4)
        self.assertGreater(self.control.status()['dropped'],0)
        self.assertIn('queue full',self.control.status()['stopped_reason'])
        self.assertIsNotNone(self.game.raw_observed)

    def test_frozen_endgame_does_not_reuse_an_old_postprocessor_result(self):
        self.capture()
        self.game.state='endgame_ready'
        self.capture(sequence=2)
        data=load_packet(self.decisions()[-1])['data']
        self.assertEqual(data['trace']['stage'],'endgame_scan_frozen')
        self.assertIsNone(data['proposed_64'])


    def test_physical_substitute_scorer_is_logged_separately_from_selected_model(self):
        self.game.board=chess.Board('4k3/8/8/8/8/8/8/Q3K3 w - - 0 1')
        self.game.proxies={chess.A1};self.game.invalidate_crf()
        self.capture(self.game.physical())
        data=load_packet(self.decisions()[0])['data']
        self.assertEqual(data['trace']['postprocess']['scorer'],'physical_visual_likelihood')
        self.assertEqual(data['before']['recognition_model'],'small')
        self.assertNotEqual(data['before']['reference_64'],data['before']['physical_reference_64'])
        self.assertIsNone(self.control.status()['stopped_reason'])

    def test_live_recording_cannot_be_labeled_until_collection_is_off(self):
        self.capture();decision=self.decisions()[0]
        control_path=self.root/'results/lite-live/data-log-control.json'
        atomic_json(control_path,dict(root=str(self.control.root),enabled=True))
        with self.assertRaisesRegex(ValueError,'Turn Log Data off'):
            label(decision,self.game.board.fen(),'reviewer-id',True,True)
        atomic_json(control_path,dict(root=str(self.control.root),enabled=False))
        self.assertTrue(label(decision,self.game.board.fen(),'reviewer-id',True,True).exists())



class SettingsHTTPTests(unittest.TestCase):
    def setUp(self):
        from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
        from recognition_log import RecognitionLog
        import traceback
        import subprocess
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.audit=RecognitionLog(self.root/'old-logs')
        self.addCleanup(lambda:[h.close() for h in self.audit.logger.handlers])
        self.game=Game(postprocessor=CRFObservation())
        self.control=DataLogControl(self.root/'logs',self.root/'control.json')
        self.collector=ControllerEvidence(self.control);self.addCleanup(self.collector.writer.close)
        self.audit.improvements=self.collector
        tree=ast.parse(Path(__file__).with_name('lite_live_server.py').read_text())
        definition=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Handler')
        from crf_observation import model_choices
        self.scope=dict(SimpleHTTPRequestHandler=SimpleHTTPRequestHandler,game=self.game,audit=self.audit,
            O=self.root,D=self.root,time=time,json=json,traceback=traceback,subprocess=subprocess,
            log_control=self.control,saved_settings={'recognition_model':'small','other_preference':'keep'},
            settings_path=self.root/'settings.json',selected_model='small',CRFObservation=CRFObservation,
            atomic_json=atomic_json,game_context=game_context,model_choices=model_choices)
        exec(compile(ast.Module(body=[definition],type_ignores=[]),'<handler>','exec'),self.scope)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),self.scope['Handler'])
        self.worker=threading.Thread(target=self.server.serve_forever,daemon=True);self.worker.start()
        self.addCleanup(self.shutdown)
    def shutdown(self):
        self.server.shutdown();self.server.server_close();self.worker.join()
    def request(self,path='/api/settings',data=None):
        from urllib.request import Request,urlopen
        from urllib.error import HTTPError
        req=Request('http://127.0.0.1:'+str(self.server.server_port)+path,
                    data=json.dumps(data).encode() if data is not None else None,
                    headers={'Content-Type':'application/json'})
        try:
            with urlopen(req) as r:return r.status,json.load(r)
        except HTTPError as r:return r.code,json.load(r)
    def test_default_and_persistent_toggle_does_not_replace_model(self):
        self.assertTrue(self.request()[1]['log_data'])
        adapter=self.game.postprocessor
        status,data=self.request(data={'log_data':False})
        self.assertEqual(status,200);self.assertFalse(data['log_data'])
        self.assertIs(self.game.postprocessor,adapter)
        saved=read_json(self.root/'settings.json')
        self.assertEqual(saved,dict(log_data=False,recognition_model='small',other_preference='keep'))
        self.assertFalse(self.audit.data_logging)
        self.assertEqual(self.request(data={'recognition_model':'large'})[0],200)
        self.assertFalse(self.request()[1]['log_data'])
        self.assertFalse(read_json(self.root/'settings.json')['log_data'])
    def test_perspective_setting_defaults_on_and_preserves_game_corrections(self):
        self.assertTrue(self.request()[1]['rectify_board'])
        self.game.manual_corrections['h1'] = {'piece': 'R'}
        status, data = self.request(data={'rectify_board': False})
        self.assertEqual(status, 200)
        self.assertFalse(data['rectify_board'])
        self.assertEqual(self.game.manual_corrections['h1']['piece'], 'R')
        self.assertFalse(read_json(self.root/'settings.json')['rectify_board'])
        self.assertEqual(self.request(data={'rectify_board': 'false'})[0], 400)
        self.assertFalse(self.request()[1]['rectify_board'])
        self.assertEqual(self.request(data={'rectify_board': True})[0], 200)
        self.assertTrue(self.request()[1]['rectify_board'])

    def test_toggle_works_without_postprocessor(self):
        self.game.postprocessor=None
        self.assertEqual(self.request(data={'log_data':False})[0],200)
        self.assertFalse(self.request()[1]['log_data'])
    def test_invalid_toggle_is_rejected_without_changing_preference(self):
        self.assertEqual(self.request(data={'log_data':'false'})[0],400)
        self.assertTrue(self.request()[1]['log_data'])
    def test_open_folder_only_opens_fixed_local_directory(self):
        with patch('subprocess.Popen') as run:
            self.assertEqual(self.request('/api/open-data-folder',{})[0],200)
            run.assert_called_once_with(['/usr/bin/open',str(self.control.root)])
    def test_opt_out_correction_is_memory_only_and_still_applies(self):
        self.request(data={'log_data':False})
        self.game.board=chess.Board('4k3/8/8/8/8/8/8/4K2R w - - 0 1');self.game.state='player_turn'
        record=reading(self.game.board)
        self.game.observe(record,1,record['capture_time'])
        jpeg=io.BytesIO();Image.new('RGB',(24,24)).save(jpeg,format='JPEG')
        self.audit.observe(self.game,record,jpeg.getvalue())
        status,snapshot=self.request('/api/correction-context',{'session':self.game.id})
        self.assertEqual(status,200)
        status,result=self.request('/api/correct-squares',dict(session=self.game.id,
            position_token=snapshot['position_token'],correction_id=snapshot['correction_id'],changes={'h1':'Q'}))
        self.assertEqual(status,200);self.assertFalse(result['evidence_persisted'])
        self.assertEqual(self.game.manual_corrections['h1']['piece'],'Q')
        self.assertFalse(list(self.audit.root.glob('*/recognized-camera.jpg')))
        self.assertFalse(list(self.control.root.glob('*/events/*')))


if __name__=='__main__':unittest.main()
