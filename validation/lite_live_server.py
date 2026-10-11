"""Local UI, game controller, and Stockfish lifecycle."""
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
import json
import os
import shutil
import signal
import threading
import time
import traceback
import subprocess
import chess.engine
from chess_game import Game
from camera_session import CameraSession
from app_lifecycle import LauncherLease, BrowserPresence
from saved_games import SavedGames
from recognition_log import RecognitionLog, read_observation
from improvement_log import DataLogControl, ControllerEvidence, game_context, atomic_json
from crf_observation import CRFObservation, MODELS, DEFAULT_MODEL, model_choices

R = Path(__file__).resolve().parents[1]
D = R/'validation/frames/lite-live'
O = R/'validation/results/lite-live'
O.mkdir(parents=True, exist_ok=True)
audit = RecognitionLog(R/'validation/recognition-logs')
camera = CameraSession(R)
engine = None
candidates = [os.environ.get('STOCKFISH_PATH'), shutil.which('stockfish'), '/opt/homebrew/bin/stockfish', '/usr/local/bin/stockfish']
candidates += [str(p) for p in (R/'vendor/stockfish').rglob('stockfish-macos-*') if p.is_file() and os.access(p, os.X_OK)]
for path in candidates:
    if not path:
        continue
    try:
        engine = chess.engine.SimpleEngine.popen_uci(path, timeout=5)
        engine.configure({'Threads': 2, 'Hash': 64})
        break
    except Exception as exc:
        print('Engine launch failed:', exc, flush=True)
settings_path = R/'validation/recognition-settings.json'
try:
    saved_settings = json.loads(settings_path.read_text())
    selected_model = saved_settings.get('recognition_model', DEFAULT_MODEL)
except (OSError, ValueError, AttributeError):
    saved_settings = {}
    selected_model = DEFAULT_MODEL
if selected_model not in MODELS:
    selected_model = DEFAULT_MODEL
game = Game(engine, R/'validation/game-state.json',
            postprocessor=CRFObservation(model=selected_model) if os.environ.get('CHESS_CRF', '1') != '0' else None)
game.reset(computer='auto')
log_control = DataLogControl(R/'validation/improvement-logs', O/'data-log-control.json',
                             enabled=saved_settings.get('log_data', True) is not False)
improvements = ControllerEvidence(log_control)
improvements.sync(game)
audit.improvements = improvements
audit.data_logging = log_control.enabled
library = SavedGames(R/'validation/saved-games', R/'validation/game-state.json')
launcher_lease = LauncherLease()
if os.environ.get('CHESS_LAUNCHER') == '1':
    launcher_lease.heartbeat()
browser_presence = BrowserPresence()
quit_requested = threading.Event()
# Preserve the current in-memory game during a local application update.
update_state = os.environ.get('CHESS_UPDATE_STATE')
if update_state:
    try:
        game.load_data(json.loads(Path(update_state).read_text()), automatic=True, strict=True)
    except (OSError,ValueError) as exc:
        print('Update recovery failed:',exc,flush=True)

# Preserve RAM state only for an explicitly controlled development restart.
# Normal launches never save or resume automatically.
upgrade_snapshot=os.environ.get('CHESS_RUNTIME_SNAPSHOT')
if upgrade_snapshot:
    path=Path(upgrade_snapshot)
    try:
        runtime_data=json.loads(path.read_text())
        game.load_data(runtime_data,strict=True)
        if runtime_data.get('runtime_paused'):
            game.pause()
    finally:
        path.unlink(missing_ok=True)

def observe():
    previous_control = None
    while True:
        with game.lock:
            game.finish_endgame_scan(time.time())
            control = {'session':game.id,'needs_orientation':game.side_auto,'recognition_paused':game.state == 'endgame_ready'}
        if control != previous_control:
            try:
                temporary = O/'orientation-control.tmp'
                temporary.write_text(json.dumps(control))
                os.replace(temporary,O/'orientation-control.json')
                previous_control = control
            except OSError:
                with game.lock:
                    game.save_error = 'Camera controls could not be saved. Free disk space.'
        try:
            if not camera.running or (D/'STOP').exists():
                with game.lock:
                    game.camera_phase = 'stopped'
                    game.clear_observations()
                    audit.observe(game)
                time.sleep(.1)
                continue
            record, jpeg = read_observation(O)
            with game.lock:
                improvements.observe(game, record, time.monotonic(), time.time())
                audit.observe(game, record, jpeg)
                audit.problem(None)
        except (OSError, ValueError, KeyError) as exc:
            audit.problem(exc)
        time.sleep(.1)
threading.Thread(target=observe, daemon=True).start()

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(O), **kw)
    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()
    def log_message(self, *a):
        pass
    def respond(self, value, status=200):
        if value.get('recognition_frame'):
            audit.remember_view(value)
        if self.command == 'POST' and self.path not in ('/api/diagnostics','/api/page-heartbeat','/api/page-leave','/api/launcher-heartbeat','/api/browser-entry'):
            audit.event('request_completed',path=self.path,status=status,session=game.id,
                        state=game.state,error=value.get('error'),correction_id=value.get('correction_id'))
        collector = getattr(audit, 'improvements', None)
        if collector and 200 <= status < 300 and getattr(self, 'log_before', None) is not None:
            with game.lock:
                collector.event(self.path, self.log_before, game_context(game),
                                getattr(self, 'evidence_request', None), getattr(self, 'log_reviewed', None))
                try:
                    collector.control.publish(game_context(game))
                except OSError as exc:
                    collector.writer.error = str(exc)
        data = json.dumps(value).encode()
        try:
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            audit.event('response_lost',path=self.path,status=status,correction_id=value.get('correction_id'))
    def do_GET(self):
        if self.path.split('?')[0] == '/api/settings':
            with game.lock:
                self.respond(dict(recognition_model=getattr(game.postprocessor, 'model', None), recognition_models=model_choices(),
                                  log_data=log_control.enabled, data_logging=log_control.status(),
                                  rectify_board=saved_settings.get('rectify_board', True) is not False))
        elif self.path.split('?')[0] == '/api/game':
            with game.lock:
                snapshot = audit.remember_view(game.snapshot(time.time()))
                snapshot['data_logging'] = log_control.status()
                snapshot['rectify_board'] = saved_settings.get('rectify_board', True) is not False
                self.respond(snapshot)
        elif self.path.split('?')[0] == '/api/app-status':
            self.respond({'running':not quit_requested.is_set()})
        elif self.path.split('?')[0] == '/api/library':
            with game.lock:
                self.respond(dict(games=library.list(),can_resume=game.state not in ('setup','endgame_scan','endgame_ready')))
        elif self.path.split('?')[0].startswith(('/api/correction-camera/','/api/correction-review/')):
            try:
                directory = audit.directory(self.path.split('?')[0].rsplit('/',1)[1])
                review = self.path.startswith('/api/correction-review/')
                data = (directory/('review.html' if review else 'recognized-camera.jpg')).read_bytes()
                self.send_response(200)
                self.send_header('Content-Type','text/html; charset=utf-8' if review else 'image/jpeg')
                self.send_header('Content-Length',str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except (OSError,ValueError):
                self.send_error(404)
        else:
            super().do_GET()
    def read_json(self, required=False):
        size = int(self.headers.get('Content-Length',0))
        if not 0 <= size < 4096 or (required and size == 0):
            raise ValueError('Invalid request size.')
        data = json.loads(self.rfile.read(size)) if size else {}
        if not isinstance(data,dict):
            raise ValueError('Expected game settings.')
        return data
    def validate_current(self, data):
        if ('session' in data and data['session'] != game.id) or ('event_id' in data and data['event_id'] != game.event_id):
            raise ValueError('The game changed in another tab. Review the current board and try again.')
    def do_POST(self):
        if self.headers.get('Origin') not in (None, f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'):
            audit.event('request_rejected',path=self.path,reason='Unexpected origin')
            self.respond({'error':'Unexpected origin.'},403)
            return
        actions = {'/api/pause':game.pause,'/api/resume':game.resume,'/api/undo':game.undo_round,
                   '/api/claim-draw':game.claim_draw,'/api/clear-corrections':game.clear_corrections}
        if self.path not in (*actions,'/api/open-data-folder','/api/settings','/stop','/api/new-game','/api/start-game','/api/exit-game','/api/load-saved','/api/correction-context','/api/end-correction','/api/diagnostics','/api/correct-square','/api/correct-squares','/api/save-game','/api/delete-game','/api/load-game','/api/resume-game','/api/start-endgame','/api/confirm-endgame','/api/confirm-promotion','/api/quit-app','/api/launcher-heartbeat','/api/page-heartbeat','/api/page-leave','/api/browser-entry'):
            self.send_error(404)
            return
        try:
            data = self.read_json(required=self.path in ('/api/new-game','/api/load-saved'))
            if self.path in ('/api/page-heartbeat','/api/page-leave'):
                identity = data.get('page')
                if not isinstance(identity,str) or not 1 <= len(identity) <= 100:
                    raise ValueError('Invalid page identity.')
                with game.lock:
                    if data.get('session') == game.id and data.get('correction_id'):
                        if self.path == '/api/page-leave':
                            game.end_correction(data['correction_id'])
                        else:
                            game.touch_correction(data['correction_id'])
                if self.path == '/api/page-leave':
                    browser_presence.leave(identity)
                    self.respond({'left':True})
                else:
                    self.respond({'running':not quit_requested.is_set(),'focus_id':browser_presence.heartbeat(identity)})
                return
            if self.path == '/api/browser-entry':
                self.respond({'already_open':browser_presence.open_browser()})
                return
            if self.path == '/api/launcher-heartbeat':
                launcher_lease.heartbeat()
                self.respond({'running':not quit_requested.is_set()})
                return
            if self.path == '/api/diagnostics':
                audit.client_event(data)
                self.respond({'logged':True})
                return
            if self.path == '/api/open-data-folder':
                log_control.root.mkdir(parents=True, exist_ok=True)
                subprocess.Popen(['/usr/bin/open', str(log_control.root)])
                self.respond({'opened':True})
                return
            with game.lock:
                self.validate_current(data)
                if getattr(audit, 'improvements', None):
                    self.log_before = game_context(game)
                    self.evidence_request = data
                if self.path == '/api/settings':
                    if not data or not set(data) <= {'recognition_model', 'log_data', 'rectify_board'}:
                        raise ValueError('Invalid settings.')
                    if 'log_data' in data and type(data['log_data']) is not bool:
                        raise ValueError('Log Data must be true or false.')
                    if 'rectify_board' in data and type(data['rectify_board']) is not bool:
                        raise ValueError('Perspective correction must be true or false.')
                    replacement = None
                    if 'recognition_model' in data:
                        if game.postprocessor is None:
                            raise ValueError('Recognition assist is disabled by CHESS_CRF.')
                        replacement = CRFObservation(model=data['recognition_model'])
                        if replacement.error:
                            raise ValueError('Could not load the selected recognition model.')
                    settings = dict(saved_settings)
                    settings.update(recognition_model=replacement.model if replacement else getattr(game.postprocessor, 'model', selected_model),
                                    log_data=data.get('log_data', log_control.enabled))
                    if 'rectify_board' in data:
                        settings['rectify_board'] = data['rectify_board']
                    atomic_json(settings_path, settings)
                    if 'log_data' in data:
                        log_control.set_enabled(data['log_data'], game_context(game))
                        audit.data_logging = data['log_data']
                    saved_settings.update(settings)
                    if 'rectify_board' in data:
                        game.invalidate_crf()
                    if replacement:
                        game.postprocessor = replacement
                        game.invalidate_crf()
                        game.crf_last_decision = None
                    self.respond(dict(recognition_model=getattr(game.postprocessor, 'model', None),
                                      log_data=log_control.enabled, data_logging=log_control.status(),
                                  rectify_board=saved_settings.get('rectify_board', True) is not False))
                elif self.path == '/api/quit-app':
                    if data.get('save'):
                        library.save(game,data.get('name'))
                    quit_requested.set()
                    # Schedule cleanup even if the requesting page disappears mid-response.
                    threading.Thread(target=server.shutdown,daemon=True).start()
                    self.respond({'quitting':True})
                elif self.path in ('/stop','/api/exit-game'):
                    if game.state not in ('setup','endgame_scan','endgame_ready','paused','finished'):
                        game.pause()
                    game.camera_phase = 'stopped'
                    game.clear_observations()
                    D.mkdir(parents=True,exist_ok=True)
                    camera.stop()
                    self.respond({'stopping':True})
                elif self.path == '/api/start-game':
                    game.reset('auto',data.get('difficulty','normal'),False)
                    collector = getattr(audit, 'improvements', None)
                    if collector:
                        collector.sync(game)
                    camera.start()
                    self.respond(game.snapshot(time.time()))
                elif self.path == '/api/save-game':
                    saved=library.save(game,data.get('name'))
                    self.respond(dict(saved_id=saved))
                elif self.path == '/api/delete-game':
                    library.delete(data.get('id'))
                    self.respond(dict(games=library.list()))
                elif self.path == '/api/load-game':
                    game.load_data(library.load(data.get('id')),strict=True)
                    collector = getattr(audit, 'improvements', None)
                    if collector:
                        collector.sync(game)
                    camera.start()
                    self.respond(game.snapshot(time.time()))
                elif self.path == '/api/resume-game':
                    game.resume_current()
                    collector = getattr(audit, 'improvements', None)
                    if collector:
                        collector.sync(game)
                    camera.start()
                    self.respond(game.snapshot(time.time()))
                elif self.path == '/api/start-endgame':
                    game.start_endgame(data.get('difficulty','normal'),data.get('player'))
                    collector = getattr(audit, 'improvements', None)
                    if collector:
                        collector.sync(game)
                    camera.start()
                    self.respond(game.snapshot(time.time()))
                elif self.path == '/api/confirm-endgame':
                    game.confirm_endgame(data.get('placement'),data.get('turn'),data.get('player'))
                    self.respond(game.snapshot(time.time()))
                elif self.path == '/api/confirm-promotion':
                    game.confirm_promotion(data.get('promotion_id'),data.get('piece'),data.get('representation'),manual=data.get('manual',False))
                    self.respond(game.snapshot(time.time()))
                elif self.path == '/api/end-correction':
                    game.end_correction(data.get('correction_id'))
                    self.respond({'closed':True})
                elif self.path == '/api/correction-context':
                    snapshot = game.snapshot(time.time())
                    if (game.state != 'endgame_ready' and not snapshot['fresh']) or not snapshot['raw_placement']:
                        raise ValueError('Wait for a live camera image before correcting the board.')
                    if data.get('correction_id'):
                        identity = data['correction_id']
                        snapshot = audit.opened(identity)
                        if snapshot['session'] != game.id:
                            raise ValueError('The game changed. Reopen Fix recognition.')
                        missing = json.loads((audit.directory(identity)/'context.json').read_text())['missing_evidence']
                    else:
                        snapshot = audit.reviewed_view(data,snapshot)
                        identity, missing = audit.open(snapshot)
                    game.begin_correction(identity,snapshot['position_token'])
                    self.respond(dict(snapshot, correction_id=identity, evidence_warning=bool(missing)))
                elif self.path == '/api/correct-square':
                    game.correct_square(data.get('square'),data.get('piece'),data.get('seen'))
                    self.respond(game.snapshot(time.time()))
                elif self.path == '/api/correct-squares':
                    identity = data.get('correction_id')
                    before = game.snapshot(time.time())
                    if identity is None:
                        raise ValueError('This page has no original recognition snapshot. Reload the page and review the board before applying corrections.')
                    self.log_reviewed = audit.opened(identity)
                    if audit.prepare(identity,data,before):
                        if game.editing_correction() and game.correction_edit[0] != identity:
                            raise ValueError('Another correction editor is active. Reopen this draft before applying it.')
                        game.correct_squares(data.get('changes'),data.get('position_token'),audit.opened(identity)['observed_placement'])
                        game.end_correction(identity)
                        after = game.snapshot(time.time())
                        try:
                            audit.finish(identity,data['changes'],after)
                        except OSError as exc:
                            audit.event('correction_evidence_save_failed',id=identity,error=str(exc))
                            self.respond(dict(after,correction_id=identity,evidence_report=False,
                                evidence_warning='Corrections applied, but some evidence could not be saved. Check disk space.'))
                            return
                    else:
                        game.end_correction(identity)
                        try:
                            audit.recover(identity)
                        except OSError as exc:
                            audit.event('correction_archive_recovery_failed',id=identity,error=str(exc))
                    self.respond(dict(game.snapshot(time.time()),**audit.receipt(identity)))
                elif self.path == '/api/new-game':
                    game.reset('auto',data.get('difficulty','normal'),False)
                    self.respond(game.snapshot(time.time()))
                elif self.path == '/api/load-saved':
                    game.load_saved(automatic=True)
                    self.respond(game.snapshot(time.time()))
                else:
                    actions[self.path]()
                    self.respond(game.snapshot(time.time()))
        except (ValueError,TypeError,OSError) as exc:
            audit.event('request_failed', path=self.path, error=str(exc),
                        correction_id=locals().get('data',{}).get('correction_id'), session=game.id)
            self.respond({'error':str(exc)},400)
        except Exception as exc:
            audit.event('request_exception',path=self.path,error=str(exc),traceback=traceback.format_exc())
            self.respond({'error':'Something went wrong. Your edits are kept. Check the board before retrying.'},500)

server = ThreadingHTTPServer(('127.0.0.1', 8769), Handler)
def watch_launcher():
    while not quit_requested.wait(1):
        if browser_presence.should_stop_camera() and camera.running:
            with game.lock:
                if game.state not in ('setup','endgame_scan','endgame_ready','paused','finished'):
                    game.pause()
                game.camera_phase = 'stopped'
                game.clear_observations()
                camera.stop()
        if launcher_lease.expired():
            quit_requested.set()
            server.shutdown()
            return
threading.Thread(target=watch_launcher,daemon=True).start()
# shutdown must run outside serve_forever's thread. The finally block releases hardware.
for sig in (signal.SIGTERM, signal.SIGINT):
    signal.signal(sig, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start())
try:
    server.serve_forever()
finally:
    quit_requested.set()
    camera.stop()
    improvements.writer.close()
    server.server_close()
    if engine:
        engine.quit()
