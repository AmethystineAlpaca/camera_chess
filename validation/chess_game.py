"""Authoritative game state with explicit, temporary human observation corrections."""
import threading
from collections import deque
import shutil
import json
import math
import os
import time
from pathlib import Path
import uuid
import xml.etree.ElementTree as ET
import chess
import chess.engine
import chess.svg
from lite_consensus import Consensus

LEVELS = {'easy': 0, 'normal': 3, 'advanced': 8, 'hard': 14}

def differences(expected, observed):
    return [chess.square_name(s) for s in chess.SQUARES
            if expected.piece_at(s) != observed.piece_at(s)]

def legal_successor(board, observed):
    placement = observed.board_fen()
    match = None
    for move in board.legal_moves:
        after = board.copy()
        after.push(move)
        if after.board_fen() == placement:
            if match is not None:
                return None
            match = move
    return match

def placement_known(board, observed, pending, state, resume_state):
    if state == 'setup':
        return False
    if observed.board_fen() == board.board_fen():
        return True
    if pending:
        after = board.copy()
        after.push(pending)
        return observed.board_fen() == after.board_fen()
    if state in ('player_turn','restore') or (state == 'reconcile' and resume_state in ('player_turn','restore')):
        return legal_successor(board,observed) is not None
    return False


def instruction(board, move):
    color = 'White' if board.turn else 'Black'
    piece = chess.piece_name(board.piece_type_at(move.from_square))
    text = f'{color} {piece}, {chess.square_name(move.from_square)} to {chess.square_name(move.to_square)}.'
    if board.is_castling(move):
        kingside = chess.square_file(move.to_square) > chess.square_file(move.from_square)
        text += f" Also move the rook from {'h' if kingside else 'a'}{chess.square_rank(move.from_square)+1} to {'f' if kingside else 'd'}{chess.square_rank(move.from_square)+1}."
    if board.is_en_passant(move):
        text += f' Remove the pawn on {chess.square_name(move.to_square + (-8 if board.turn else 8))}.'
    elif board.is_capture(move):
        text += ' Remove the captured piece.'
    if move.promotion:
        text += f' Replace the pawn with a {chess.piece_name(move.promotion)}.'
    return text

def recovery_svg(svg, target_svg, observed, expected, errors):
    if not errors:return svg
    ns='http://www.w3.org/2000/svg';link='http://www.w3.org/1999/xlink'
    ET.register_namespace('',ns);ET.register_namespace('xlink',link)
    root=ET.fromstring(svg);target=ET.fromstring(target_svg)
    defs=root.find('{'+ns+'}defs')
    if defs is None:defs=ET.SubElement(root,'{'+ns+'}defs')
    known={node.get('id') for node in defs}
    for node in list(target.find('{'+ns+'}defs')) if target.find('{'+ns+'}defs') is not None else []:
        if node.get('id') not in known:defs.append(node)
    squares={name:node for node in root.findall('{'+ns+'}rect')
             for name in node.get('class','').split() if name in errors}
    overlay=ET.SubElement(root,'{'+ns+'}g',{'class':'recovery-guides','pointer-events':'none'})
    for name in errors:
        rect=squares.get(name)
        if rect is None:continue
        x,y=float(rect.get('x')),float(rect.get('y'));sq=chess.parse_square(name)
        want,seen=expected.piece_at(sq),observed.piece_at(sq)
        if want:
            color='white' if want.color else 'black'
            transform=f'translate({x},{y})' if seen is None else f'translate({x+20},{y+20}) scale(.55)'
            ET.SubElement(overlay,'{'+ns+'}use',{'{'+link+'}href':f'#{color}-{chess.piece_name(want.piece_type)}',
                'transform':transform,'opacity':'.42','class':'ghost-piece','data-square':name})
        if seen:
            cross=ET.SubElement(overlay,'{'+ns+'}g',{'class':'wrong-piece','data-square':name,'stroke':'#ff5555','stroke-width':'3.5','stroke-linecap':'round'})
            for x1,y1,x2,y2 in ((x+30,y+5,x+40,y+15),(x+40,y+5,x+30,y+15)):
                ET.SubElement(cross,'{'+ns+'}line',dict(x1=str(x1),y1=str(y1),x2=str(x2),y2=str(y2)))
    return ET.tostring(root,encoding='unicode')

class Game:
    def __init__(self, engine=None, save_path=None):
        self.save_path = Path(save_path) if save_path else None
        self.save_error = None
        self.engine = engine
        self.lock = threading.RLock()
        self.reset()

    def reset(self, computer='black', difficulty='normal', aligned=False, discard_saved=False):
        if computer not in ('auto','black', 'white') or difficulty not in LEVELS:
            raise ValueError('Invalid side or difficulty.')
        with self.lock:
            if discard_saved and self.save_path:
                try:
                    if self.save_path.exists():
                        os.replace(self.save_path,self.save_path.with_name('game-state.previous.json'))
                    self.save_error = None
                except OSError:
                    self.save_error = 'Could not clear the previous saved game.'
            self.mode = 'standard'
            self.initial_fen = chess.STARTING_FEN
            self.strict_recovery = False
            self.id = str(uuid.uuid4())
            self.board = chess.Board()
            self.computer = computer == 'white'
            self.side_auto = computer == 'auto'
            self.side_confirmed = not self.side_auto
            self.orientation_status = 'checking' if self.side_auto else 'manual'
            self.orientation_detail = 'Detecting which color is nearest the camera.'
            self.orientation_window = deque(maxlen=5)
            self.detected_near = None
            self.original_near = None
            self.save_started = False
            self.difficulty = difficulty
            self.aligned = aligned
            self.state = 'setup'
            self.pending = None
            self.observed = None
            self.raw_observed = None
            self.manual_corrections = {}
            self.gate = Consensus()
            self.fast_gate = Consensus(votes=3, span=.45, window=4, score_threshold=.90)
            self.return_gate = Consensus(votes=2, span=.25, window=3, score_threshold=.55)
            self.sequence = None
            self.candidate = None
            self.candidate_since = None
            self.errors = []
            self.suggestions = []
            self.repair_expected = None
            self.repair_arrows = []
            self.svg_cache_key = None
            self.svg_cache = None
            self.history = []
            self.events = []
            self.event_id = 0
            self.event_kind = 'message'
            self.message = 'Set up the starting position. I will automatically detect my side.' if self.side_auto else 'Set up the starting position. Confirm which color is nearest the camera.'
            self.last_frame = 0
            self.last_supported = 0
            self.camera_phase = 'waiting'
            self.resume_state = None
            self.engine_job = None
            self.thinking_since = None
            self.partial_since = None

    def emit(self, text, kind='message', force=False):
        if force or self.message != text or self.event_kind != kind:
            self.event_id += 1
            self.message, self.event_kind = text, kind
            self.events.append(dict(id=self.event_id, text=text, kind=kind))
            self.events = self.events[-30:]

    def save(self):
        if not self.save_path or self.state == 'setup':
            return
        data = self.serialize()
        try:
            self.save_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.save_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(data))
            if not self.save_started and self.save_path.exists():
                shutil.copy2(self.save_path,self.save_path.with_name('game-state.previous.json'))
            os.replace(temporary, self.save_path)
            self.save_error = None
            self.save_started = True
        except OSError:
            self.save_error = 'Game could not be saved. Free disk space before restarting.'

    def serialize(self):
        state = self.resume_state if self.state in ('paused','reconcile') else self.state
        if state not in ('player_turn','computer_move','thinking','restore','engine_error','finished'):
            raise ValueError('Start a game and confirm the board before saving.')
        return dict(version=2, computer='white' if self.computer else 'black',
                    difficulty=self.difficulty, initial_fen=self.initial_fen, mode=self.mode,
                    board_near=self.original_near,
                    moves=[m.uci() for m in self.board.move_stack], state=state,
                    pending=self.pending.uci() if self.pending else None)

    def resume_current(self):
        with self.lock:
            data=self.serialize()
            self.load_data(data, automatic=True, strict=True)

    def start_endgame(self, difficulty='normal'):
        self.reset('auto',difficulty)
        self.mode='endgame'
        self.state='endgame_scan'
        self.board=chess.Board(None)
        self.emit('Place your position and clear your hands. I will scan the board.')

    def confirm_endgame(self, placement, turn, player):
        with self.lock:
            if self.state!='endgame_ready' or not self.side_confirmed or time.time()-self.last_frame>3 or self.camera_phase!='stable':
                raise ValueError('Wait for a stable camera view before confirming.')
            if placement!=self.board.board_fen():
                raise ValueError('The board changed. Check the new position and confirm again.')
            if turn not in ('white','black') or player not in ('white','black'):
                raise ValueError('Choose who moves next and which color you play.')
            board=self.board.copy();board.turn=turn=='white'
            # A camera cannot know whether kings/rooks moved or the previous move.
            board.castling_rights=0;board.ep_square=None
            if not board.is_valid():
                raise ValueError('This position is not legal for that side to move. Check the kings and pawns.')
            if self.engine is None:
                raise ValueError('The chess engine is unavailable.')
            self.board=board;self.initial_fen=board.fen()
            self.computer=player!='white';self.side_auto=True;self.side_confirmed=True
            self.errors=[];self.next_turn(ready=True)

    def saved_available(self):
        return bool(self.save_path and self.save_path.exists())

    def correct_square(self, name, symbol, seen):
        with self.lock:
            if (self.raw_observed is None or time.time()-self.last_frame > 3
                    or self.camera_phase in ('stale','stopped','loading','waiting')):
                raise ValueError('Wait for a live camera image before correcting a square.')
            if not isinstance(name, str) or name not in chess.SQUARE_NAMES:
                raise ValueError('Choose a board square.')
            if symbol not in ('P','N','B','R','Q','K','p','n','b','r','q','k','.'):
                raise ValueError('Choose a piece or Empty.')
            square = chess.parse_square(name)
            piece = self.raw_observed.piece_at(square)
            actual = piece.symbol() if piece else '.'
            if seen != actual:
                raise ValueError('The camera reading changed. Check this square and try again.')
            if symbol == actual:
                self.manual_corrections.pop(name, None)
            else:
                self.manual_corrections[name] = dict(seen=actual, piece=symbol)
            self.reset_votes()

    def reset_votes(self):
        self.gate = Consensus()
        self.fast_gate = Consensus(votes=3, span=.45, window=4, score_threshold=.90)
        self.return_gate = Consensus(votes=2, span=.25, window=3, score_threshold=.55)

    def clear_corrections(self):
        with self.lock:
            self.manual_corrections.clear()
            self.reset_votes()

    def corrected_observation(self, raw):
        observed = raw.copy()
        for name, correction in list(self.manual_corrections.items()):
            square = chess.parse_square(name)
            piece = raw.piece_at(square)
            actual = piece.symbol() if piece else '.'
            if actual != correction['seen']:
                del self.manual_corrections[name]
                self.reset_votes()
                continue
            replacement = correction['piece']
            observed.set_piece_at(square, None if replacement == '.' else chess.Piece.from_symbol(replacement))
        return observed

    def clear_observations(self):
        self.manual_corrections.clear()
        self.raw_observed = None
        self.gate = Consensus()
        self.fast_gate = Consensus(votes=3, span=.45, window=4, score_threshold=.90)
        self.return_gate = Consensus(votes=2, span=.25, window=3, score_threshold=.55)
        self.candidate = None
        self.candidate_since = None
        self.partial_since = None
        self.sequence = None
        self.last_supported = 0
        if self.side_auto:
            self.side_confirmed = False
            self.orientation_window.clear()
            self.orientation_status = 'checking'

    def pause(self):
        with self.lock:
            if self.state in ('setup', 'paused', 'finished'):
                raise ValueError('There is no active turn to pause.')
            if self.state != 'reconcile':
                self.resume_state = self.state
            self.state = 'paused'
            self.engine_job = None
            self.errors = []
            self.emit('Game paused. Keep the last accepted position, then click Resume.')

    def resume(self):
        with self.lock:
            if self.state != 'paused':
                raise ValueError('The game is not paused.')
            self.state = 'reconcile'
            self.clear_observations()
            self.emit('Resume requested. Restore the reference position and keep the board clear for verification.')

    def undo_round(self):
        with self.lock:
            if not self.board.move_stack:
                raise ValueError('There is no accepted move to undo.')
            # Return to the position immediately before the most recent player move.
            self.engine_job = None
            while self.board.move_stack:
                self.board.pop()
                self.history.pop()
                if self.board.turn != self.computer:
                    break
            self.pending = None
            self.errors = []
            self.resume_state = 'player_turn' if self.board.turn != self.computer else 'thinking'
            self.state = 'reconcile'
            self.clear_observations()
            self.emit('Last round undone. Restore the reference position before continuing.',force=True)

    def load_saved(self, computer=None, automatic=False):
        if not self.saved_available():
            raise ValueError('No saved game is available.')
        try:
            data=json.loads(self.save_path.read_text())
        except (OSError,ValueError):
            raise ValueError('Saved game is unreadable or inconsistent. Start a new game.')
        self.load_data(data,computer,automatic)

    def load_data(self, data, computer=None, automatic=False, strict=False):
        with self.lock:
            try:
                if not isinstance(data,dict) or data.get('version') not in (1,2):raise ValueError()
                color,level,state=data['computer'],data['difficulty'],data['state']
                mode=data.get('mode','standard')
                saved_near=data.get('board_near')
                if saved_near not in (None,'black','white'):raise ValueError()
                if color not in ('black','white') or level not in LEVELS or mode not in ('standard','endgame'):raise ValueError()
                if state not in ('player_turn','computer_move','thinking','restore','engine_error','finished'):raise ValueError()
                moves=data['moves']
                if not isinstance(moves,list) or len(moves)>2000:raise ValueError()
                initial=data.get('initial_fen',chess.STARTING_FEN)
                board=chess.Board(initial)
                if not board.is_valid() or (mode=='standard' and initial!=chess.STARTING_FEN):raise ValueError()
                history=[]
                for value in moves:
                    move=chess.Move.from_uci(value)
                    if move not in board.legal_moves:raise ValueError()
                    history.append(dict(san=board.san(move),uci=move.uci()));board.push(move)
                pending=chess.Move.from_uci(data['pending']) if data.get('pending') else None
                if pending and (pending not in board.legal_moves or board.turn!=(color=='white')):raise ValueError()
                if (state=='computer_move')!=(pending is not None):raise ValueError()
                if state in ('player_turn','restore') and board.turn==(color=='white'):raise ValueError()
            except (ValueError,KeyError,TypeError,AttributeError):
                raise ValueError('Saved game is unreadable or inconsistent. Start a new game.')
            if computer is not None and color!=computer:
                raise ValueError(f'Saved game requires {color.title()} nearest the camera. Check the board and side selection.')
            self.reset(color,level,True)
            self.mode=mode;self.initial_fen=initial
            self.board,self.history,self.pending=board,history,pending
            self.original_near=saved_near or (color if mode=='standard' else None)
            self.side_auto=automatic
            if self.side_auto:self.side_confirmed=False;self.orientation_status='checking'
            self.resume_state=state;self.state='reconcile';self.strict_recovery=strict
            self.emit('Match the saved position. Ghost pieces show where to place pieces; crosses show what to remove.')

    def claim_draw(self):
        with self.lock:
            if self.state != 'player_turn' or self.pending or not (self.board.is_repetition(3) or self.board.is_fifty_moves()):
                raise ValueError('A draw cannot be claimed in this position.')
            self.state = 'finished'
            self.emit('Game over. Draw claimed by repetition or the fifty-move rule.')

    def observe_orientation(self, record, now, eligible):
        result = record.get('orientation')
        if not eligible or not isinstance(result,dict) or record.get('orientation_at') != record.get('capture_time'):
            self.orientation_window.clear()
            return False
        near = result.get('near')
        if result.get('status') != 'detected' or near not in ('black','white'):
            self.orientation_window.clear()
            if self.orientation_status != 'mismatch':
                self.orientation_status = result.get('status') if result.get('status') in ('sideways','uncertain') else 'checking'
                self.orientation_detail = 'Checking board direction. Clear hands and keep the whole board visible.'
            return False
        self.orientation_window.append((near,now))
        supporting = [r for r in self.orientation_window if r[0]==near]
        latest = list(self.orientation_window)[-2:]
        ready = len(supporting)>=3 and len(latest)==2 and all(r[0]==near for r in latest) and now-supporting[0][1]>=.6
        if not ready:
            return False
        self.detected_near = near
        if self.original_near is None:
            self.original_near = ('white' if self.computer else 'black') if self.mode=='standard' and self.state not in ('setup','endgame_scan','endgame_ready') else near
            if self.state in ('setup','endgame_scan','endgame_ready'):
                self.computer = near=='white'
                self.aligned = True
        if near != self.original_near:
            self.side_confirmed = False
            self.orientation_status = 'mismatch'
            self.orientation_detail = 'Board flipped. Turn it back to its original direction, or click Restart Game.'
            self.clear_errors()
            self.reset_votes()
            self.emit(self.orientation_detail)
            return False
        recovered = self.orientation_status == 'mismatch'
        self.side_confirmed = True
        self.orientation_status = 'detected'
        self.orientation_detail = f'{near.title()} is nearest the camera.'
        if recovered:
            self.reset_votes()
            self.emit('Board direction restored. You can continue.')
        return True

    def observe(self, record, now, wall):
        with self.lock:
            capture = record.get('capture_time', 0)
            if not isinstance(capture,(int,float)) or not math.isfinite(capture):
                self.camera_phase = 'unreadable'
                return
            if self.last_frame and wall-self.last_frame > 3:
                self.clear_observations()
            phase = record.get('phase')
            if not -1 <= wall-capture <= 3 or phase in ('loading','stale','stopped'):
                self.camera_phase = phase if phase in ('loading','stale','stopped') else 'stale'
                self.clear_observations()
                return
            identity = (record.get('sequence'), capture)
            if identity == self.sequence:
                return
            self.sequence = identity
            self.last_frame = capture
            raw = record.get('raw')
            try:
                observed = chess.Board(raw+' w - - 0 1')
            except (ValueError,TypeError):
                self.camera_phase = 'unreadable'
                self.clear_observations()
                return
            self.raw_observed = observed.copy()
            observed = self.corrected_observation(observed)
            raw = observed.board_fen()
            scores = [record.get(k,0) for k in ('min_score','min_margin','change_score')]
            if not all(isinstance(v,(int,float)) and math.isfinite(v) for v in scores):
                self.camera_phase = 'uncertain'
                self.clear_observations()
                return
            conf, margin, score = scores
            eligible = conf >= .55 and margin >= .10
            changed = differences(self.board, observed)
            square_scores = record.get('square_scores', {})
            values = [square_scores.get(s,score) for s in changed] if isinstance(square_scores,dict) else []
            if values and all(isinstance(v,(int,float)) and math.isfinite(v) for v in values):
                score = min(values)
            elif not changed:
                score = conf
            strict = self.gate.step(raw, now, eligible, score)
            fast = self.fast_gate.step(raw, now, eligible, score)
            returned = self.return_gate.step(raw, now, eligible, conf)
            known = placement_known(self.board, observed, self.pending, self.state, self.resume_state)
            ready = fast['ready'] if known and not self.strict_recovery and self.state not in ('setup','endgame_scan','endgame_ready') else strict['ready']
            if not changed and not self.strict_recovery and self.state not in ('setup','endgame_scan','endgame_ready'):
                ready = returned['ready']
            self.camera_phase = 'settling' if eligible else 'uncertain'
            if self.side_auto:
                orientation_ready = self.observe_orientation(record,now,eligible)
                if not orientation_ready:
                    self.camera_phase = 'orienting'
                    return
            if not ready:
                return
            self.camera_phase = 'stable'
            self.last_supported = capture
            self.observed = observed
            if self.state != 'paused':
                self.accept_observation(observed)

    def clear_errors(self):
        self.errors = []
        self.suggestions = []
        self.repair_expected = None
        self.repair_arrows = []

    def offer_corrections(self, observed):
        if any(len(observed.pieces(chess.KING,color)) != 1 for color in (chess.WHITE,chess.BLACK)) or len(observed.piece_map()) > 32:
            self.clear_errors()
            self.errors = differences(self.board,observed)
            self.emit('Some pieces are not clearly detected. Clear hands and check the camera view. If pieces are misplaced, complete your legal move directly. No reset is required.')
            return
        options = []
        for move in self.board.legal_moves:
            after = self.board.copy()
            after.push(move)
            diff = differences(after, observed)
            options.append((len(diff),move.uci(),move,after))
        previous = self.suggestions[0]['uci'] if self.suggestions else None
        options.sort(key=lambda option:(option[0],option[1] != previous,option[1]))
        if not options:
            self.errors = differences(self.board,observed)
            self.emit('The detected position is uncertain. Clear your hands and check the board framing.')
            return
        minimum = options[0][0]
        if minimum > 4:
            self.clear_errors()
            self.errors = differences(self.board,observed)
            self.emit('The board is hard to read. Clear your hands and check lighting and framing. You can finish any legal move directly; there is no need to reset the board.')
            return
        closest = [option for option in options if option[0] == minimum][:3]
        self.suggestions = [dict(uci=move.uci(),text=instruction(self.board,move)) for _,_,move,_ in closest]
        _,_,move,expected = closest[0]
        self.repair_expected = expected
        self.errors = differences(expected,observed)
        self.repair_arrows = []
        actions = []
        used = set()
        for name in self.errors:
            sq = chess.parse_square(name)
            want = expected.piece_at(sq)
            if want is None:
                continue
            label = ('White ' if want.color else 'Black ')+chess.piece_name(want.piece_type)
            sources = [chess.parse_square(n) for n in self.errors if n != name and observed.piece_at(chess.parse_square(n)) == want and expected.piece_at(chess.parse_square(n)) != want]
            if len(sources) == 1 and sources[0] not in used:
                source = sources[0]
                used.add(source)
                actions.append(f'Move the {label} from {chess.square_name(source)} to {name}.')
                self.repair_arrows.append(chess.svg.Arrow(source,sq,color='#53c9a5'))
            else:
                actions.append(f'Place the {label} on {name}.')
        for name in self.errors:
            sq = chess.parse_square(name)
            if expected.piece_at(sq) is None and observed.piece_at(sq) is not None and sq not in used:
                actions.append(f'Clear {name}.')
        self.emit('Move not confirmed. If the physical board is already correct, clear your hands and improve the camera view. One legal option: '
                  +instruction(self.board,move)+' To correct the detected placement: '+' '.join(actions[:4])
                  +' You may complete another legal move directly. No need to reset the board.')

    def partial_move(self, observed, move=None):
        changed = set(differences(self.board, observed))
        if not changed:
            return False
        for candidate in ([move] if move else self.board.legal_moves):
            after = self.board.copy()
            after.push(candidate)
            affected = set(differences(self.board, after))
            if not changed <= affected:
                continue
            valid = True
            for name in affected:
                sq = chess.parse_square(name)
                allowed = [self.board.piece_at(sq), after.piece_at(sq), None]
                if candidate.promotion and sq == candidate.to_square:
                    allowed.append(chess.Piece(chess.PAWN,self.board.turn))
                if observed.piece_at(sq) not in allowed:
                    valid = False
                    break
            if valid:
                return True
        return False

    def accept_observation(self, observed):
        """Called only after stable, fresh temporal evidence."""
        self.observed = observed
        placement = observed.board_fen()
        if self.state in ('endgame_scan','endgame_ready'):
            self.board=observed.copy()
            self.errors=[]
            if (len(observed.pieces(chess.KING,chess.WHITE))!=1 or
                    len(observed.pieces(chess.KING,chess.BLACK))!=1):
                self.state='endgame_scan'
                self.emit('Both kings must be visible. Check the camera view.')
            else:
                self.state='endgame_ready'
                self.emit('Check the scanned position, choose who moves next, then confirm.')
            return
        if self.state == 'setup':
            self.errors = differences(self.board, observed)
            if self.errors:
                self.emit('Starting position not confirmed. Check the highlighted squares and keep the board clear.')
                return
            if self.side_auto and not self.side_confirmed:
                self.emit(self.orientation_detail)
                return
            if not self.aligned:
                self.emit('Starting position is correct. Confirm the near-camera color and board alignment to begin.')
                return
            if self.engine is None:
                self.emit('Stockfish is unavailable. Restart the program after installing the engine.')
                return
            self.errors = []
            self.next_turn(ready=True)
            return
        if self.state == 'reconcile':
            if self.strict_recovery and placement!=self.board.board_fen():
                self.errors=differences(self.board,observed)
                self.emit('Match the saved position. Ghost = place; cross = remove.')
                return
            if self.strict_recovery:
                self.strict_recovery=False
            expected = self.board.copy()
            if self.pending and not (self.state=='reconcile' and self.strict_recovery):
                expected.push(self.pending)
            if placement == self.board.board_fen():
                self.clear_errors()
                target = self.resume_state
                self.resume_state = None
                if self.pending:
                    self.state = 'computer_move'
                    self.emit(instruction(self.board,self.pending))
                elif target == 'finished':
                    self.state = 'finished'
                    self.emit('Game over. The saved game is complete.')
                elif target == 'restore':
                    self.state = 'player_turn'
                    self.emit('Board restored. Your turn.', 'correct')
                else:
                    self.next_turn(ack=True)
            elif self.pending and placement == expected.board_fen():
                self.commit(self.pending)
                self.pending = None
                self.clear_errors()
                self.resume_state = None
                self.next_turn(ack=True)
            elif not self.pending and self.resume_state in ('player_turn','restore') and legal_successor(self.board,observed) is not None:
                self.commit(legal_successor(self.board,observed))
                self.resume_state = None
                self.clear_errors()
                self.next_turn(ack=True)
            else:
                self.errors = differences(expected,observed)
                if not self.pending and self.resume_state in ('player_turn','restore'):
                    self.offer_corrections(observed)
            return
        if self.state in ('paused', 'thinking', 'finished', 'engine_error'):
            return
        if self.state == 'computer_move':
            expected = self.board.copy()
            expected.push(self.pending)
            if placement == expected.board_fen():
                self.commit(self.pending)
                self.pending = None
                self.clear_errors()
                self.next_turn(ack=True)
            elif placement == self.board.board_fen():
                self.clear_errors()
                self.emit(instruction(self.board, self.pending))
            else:
                self.errors = differences(expected, observed)
                self.emit('The computer move is not confirmed. Check the highlighted squares. '+instruction(self.board, self.pending))
            return
        if placement == self.board.board_fen():
            was_correction = self.state == 'restore'
            self.state = 'player_turn'
            self.clear_errors()
            if was_correction or self.partial_since is not None:
                self.emit('Position confirmed. Your turn.')
            self.partial_since = None
            return
        matches = []
        for move in self.board.legal_moves:
            after = self.board.copy()
            after.push(move)
            if after.board_fen() == placement:
                matches.append(move)
        if len(matches) == 1:
            self.partial_since = None
            self.commit(matches[0])
            self.clear_errors()
            self.next_turn(ack=True)
        else:
            self.clear_errors()
            self.errors = differences(self.board, observed)
            if self.partial_move(observed):
                if self.partial_since is None:
                    self.partial_since = time.monotonic()
                if time.monotonic()-self.partial_since < 8:
                    self.emit('Move in progress. Finish placing the pieces, including any rook, captured pawn, or promotion piece, then clear the board.')
                    return
            self.partial_since = None
            self.state = 'restore'
            self.offer_corrections(observed)

    def commit(self, move):
        self.history.append({'san': self.board.san(move), 'uci': move.uci()})
        self.board.push(move)

    def next_turn(self, ready=False, ack=False):
        outcome = self.board.outcome(claim_draw=False)
        if outcome:
            self.state = 'finished'
            self.emit('Game over. '+('Draw.' if outcome.winner is None else ('White' if outcome.winner else 'Black')+' wins.')+' '+outcome.termination.name.replace('_',' ').lower()+'.', 'correct' if ack else 'message')
        elif self.board.turn == self.computer:
            self.state = 'thinking'
            self.thinking_since = time.monotonic()
            self.engine_job = str(uuid.uuid4())
            self.emit('Move confirmed. I am thinking.' if ack else 'I am thinking.', 'correct' if ack else 'message')
            token, board, level = self.id, self.board.copy(), self.difficulty
            threading.Thread(target=self.think, args=(token, board, level, self.engine_job), daemon=True).start()
        else:
            self.state = 'player_turn'
            check = ' Check.' if self.board.is_check() else ''
            self.emit(('Move confirmed. Your turn.' if ack else 'Your turn.')+check, 'correct' if ack else 'message')

    def think(self, token, board, level, job=None):
        try:
            result = self.engine.play(board, chess.engine.Limit(time=.35), game=token,
                                      options={'Skill Level': LEVELS[level], 'UCI_LimitStrength': False})
            with self.lock:
                if self.id != token or (job is not None and (self.engine_job != job or self.state != 'thinking')):
                    return
                if result.move not in self.board.legal_moves:
                    raise ValueError('Engine returned an illegal move.')
                self.pending = result.move
                self.state = 'computer_move'
                self.emit(instruction(self.board, self.pending))
        except Exception as exc:
            with self.lock:
                if self.id == token and (job is None or (self.engine_job == job and self.state == 'thinking')):
                    self.state = 'engine_error'
                    self.emit('The engine could not choose a legal move. Restart the game.')
                    print('ENGINE ERROR:', exc, flush=True)

    def snapshot(self, wall):
        with self.lock:
            if self.state == 'thinking' and self.thinking_since is not None and time.monotonic()-self.thinking_since > 10:
                self.engine_job = None
                self.state = 'engine_error'
                self.emit('The engine timed out. Save your position or exit and resume.')
            expected = self.repair_expected.copy() if self.repair_expected is not None and self.errors else self.board.copy()
            arrows = list(self.repair_arrows) if self.errors else []
            if self.pending and not (self.state=='reconcile' and self.strict_recovery):
                expected.push(self.pending)
                arrows.append(chess.svg.Arrow(self.pending.from_square, self.pending.to_square, color='#53c9a5'))
                if self.board.is_castling(self.pending):
                    rank = chess.square_rank(self.pending.from_square)
                    ks = chess.square_file(self.pending.to_square) > chess.square_file(self.pending.from_square)
                    arrows.append(chess.svg.Arrow(chess.square(7 if ks else 0,rank),chess.square(5 if ks else 3,rank),color='#53c9a5'))
            display = self.observed if self.errors and self.observed is not None else self.board
            orientation = (self.detected_near != 'white') if self.detected_near else not self.computer
            filled = {}
            for arrow in arrows:
                filled[arrow.tail] = '#53c9a555'
                filled[arrow.head] = '#53c9a577'
            filled.update({chess.parse_square(s): '#7895ab88' for s in self.manual_corrections})
            filled.update({chess.parse_square(s): '#ef6e6e88' for s in self.errors})
            render = lambda b: chess.svg.board(b, orientation=orientation, size=680, arrows=arrows, fill=filled)
            cache_key = (display.board_fen(),expected.board_fen(),orientation,tuple(self.errors),tuple(self.manual_corrections),
                         tuple((a.tail,a.head,a.color) for a in arrows))
            if cache_key != self.svg_cache_key:
                self.svg_cache = (recovery_svg(render(display),render(expected),display,expected,self.errors),render(expected))
                self.svg_cache_key = cache_key
            board_svg, expected_svg = self.svg_cache
            corrections = []
            for name in self.errors:
                sq = chess.parse_square(name)
                want, seen = expected.piece_at(sq), self.observed.piece_at(sq)
                describe = lambda p: 'empty' if p is None else ('White ' if p.color else 'Black ')+chess.piece_name(p.piece_type)
                corrections.append(f'{name}: detected {describe(seen)}; expected {describe(want)}')
            return dict(session=self.id, state=self.state, message=self.message, event_id=self.event_id,
                        events=list(self.events), event_kind=self.event_kind, computer='white' if self.computer else 'black',
                        player='black' if self.computer else 'white', difficulty=self.difficulty,
                        side_auto=self.side_auto, side_known=self.side_confirmed, orientation_status=self.orientation_status,
                        orientation_detail=self.orientation_detail, detected_near=self.detected_near, original_near=self.original_near, needs_orientation=self.side_auto and (self.state in ('setup','endgame_scan','endgame_ready') or not self.side_confirmed),
                        aligned=self.aligned, fresh=wall-self.last_frame<3 and self.camera_phase not in ('stale','stopped','loading','waiting'),
                        camera_phase=self.camera_phase, save_error=self.save_error,
                        saved_available=self.saved_available(), mode=self.mode, initial_fen=self.initial_fen,
                        can_save=self.state not in ('setup','endgame_scan','endgame_ready'),
                        can_resume=self.state not in ('setup','endgame_scan','endgame_ready'),
                        strict_recovery=self.strict_recovery, can_undo=bool(self.board.move_stack),
                        can_claim_draw=self.state=='player_turn' and (self.board.is_repetition(3) or self.board.is_fifty_moves()),
                        manual_corrections=dict(self.manual_corrections),
                        raw_placement=self.raw_observed.board_fen() if self.raw_observed else None,
                        suggestions=list(self.suggestions), errors=corrections,
                        board_svg=board_svg, expected_svg=expected_svg, fen=self.board.fen(),
                        pending=self.pending.uci() if self.pending else None, history=list(self.history))
