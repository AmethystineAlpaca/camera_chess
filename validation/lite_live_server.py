"""Local UI, game controller, and Stockfish lifecycle."""
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
import json
import os
import shutil
import signal
import threading
import time
import chess.engine
from chess_game import Game
from camera_session import CameraSession
from app_lifecycle import LauncherLease, BrowserPresence
from saved_games import SavedGames

R = Path(__file__).resolve().parents[1]
D = R/'validation/frames/lite-live'
O = R/'validation/results/lite-live'
O.mkdir(parents=True, exist_ok=True)
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
game = Game(engine, R/'validation/game-state.json')
game.reset(computer='auto')
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
        game.load_data(json.loads(path.read_text()),automatic=True,strict=True)
    finally:
        path.unlink(missing_ok=True)

def observe():
    previous_control = None
    while True:
        with game.lock:
            control = {'session':game.id,'needs_orientation':True}
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
                time.sleep(.1)
                continue
            record = json.loads((O/'status.json').read_text())
            game.observe(record, time.monotonic(), time.time())
        except (OSError, ValueError, KeyError):
            pass
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
        data = json.dumps(value).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)
    def do_GET(self):
        if self.path.split('?')[0] == '/api/game':
            self.respond(game.snapshot(time.time()))
        elif self.path.split('?')[0] == '/api/app-status':
            self.respond({'running':not quit_requested.is_set()})
        elif self.path.split('?')[0] == '/api/library':
            with game.lock:
                self.respond(dict(games=library.list(),can_resume=game.state not in ('setup','endgame_scan','endgame_ready')))
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
        if self.headers.get('Origin') not in (None, 'http://127.0.0.1:8769', 'http://localhost:8769'):
            self.respond({'error':'Unexpected origin.'},403)
            return
        actions = {'/api/pause':game.pause,'/api/resume':game.resume,'/api/undo':game.undo_round,
                   '/api/claim-draw':game.claim_draw,'/api/clear-corrections':game.clear_corrections}
        if self.path not in (*actions,'/stop','/api/new-game','/api/start-game','/api/exit-game','/api/load-saved','/api/correct-square','/api/save-game','/api/delete-game','/api/load-game','/api/resume-game','/api/start-endgame','/api/confirm-endgame','/api/quit-app','/api/launcher-heartbeat','/api/page-heartbeat','/api/page-leave','/api/browser-entry'):
            self.send_error(404)
            return
        try:
            data = self.read_json(required=self.path in ('/api/new-game','/api/load-saved'))
            if self.path in ('/api/page-heartbeat','/api/page-leave'):
                identity = data.get('page')
                if not isinstance(identity,str) or not 1 <= len(identity) <= 100:
                    raise ValueError('Invalid page identity.')
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
            with game.lock:
                self.validate_current(data)
                if self.path == '/api/quit-app':
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
                    camera.start()
                    self.respond(game.snapshot(time.time()))
                elif self.path == '/api/save-game':
                    saved=library.save(game,data.get('name'))
                    self.respond(dict(saved_id=saved))
                elif self.path == '/api/delete-game':
                    library.delete(data.get('id'))
                    self.respond(dict(games=library.list()))
                elif self.path == '/api/load-game':
                    game.load_data(library.load(data.get('id')),automatic=True,strict=True)
                    camera.start()
                    self.respond(game.snapshot(time.time()))
                elif self.path == '/api/resume-game':
                    game.resume_current()
                    camera.start()
                    self.respond(game.snapshot(time.time()))
                elif self.path == '/api/start-endgame':
                    game.start_endgame(data.get('difficulty','normal'))
                    camera.start()
                    self.respond(game.snapshot(time.time()))
                elif self.path == '/api/confirm-endgame':
                    game.confirm_endgame(data.get('placement'),data.get('turn'),data.get('player'))
                    self.respond(game.snapshot(time.time()))
                elif self.path == '/api/correct-square':
                    game.correct_square(data.get('square'),data.get('piece'),data.get('seen'))
                    self.respond(game.snapshot(time.time()))
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
            self.respond({'error':str(exc)},400)

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
    server.server_close()
    if engine:
        engine.quit()
