"""Authoritative game state with explicit, temporary human observation corrections."""
import threading
from copy import deepcopy
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
PROMOTIONS = {'q': chess.QUEEN, 'r': chess.ROOK, 'b': chess.BISHOP, 'n': chess.KNIGHT}


def physical_board(board, proxies=()):
    """Project logical identities into the pieces the camera should actually see."""
    physical = board.copy(stack=False)
    for square in proxies:
        piece = board.piece_at(square)
        if piece:
            physical.set_piece_at(square, chess.Piece(chess.PAWN, piece.color))
    return physical


def moved_proxies(proxies, move, pawn_promotion=False):
    result = set(proxies) - {move.from_square, move.to_square}
    if move.from_square in proxies or pawn_promotion:
        result.add(move.to_square)
    return result


def matches_tracked_position(expected, observed, proxies):
    return all((observed.piece_at(s) is not None and observed.color_at(s) == expected.color_at(s))
               if s in proxies else observed.piece_at(s) == expected.piece_at(s) for s in chess.SQUARES)

def differences(expected, observed):
    return [chess.square_name(s) for s in chess.SQUARES
            if expected.piece_at(s) != observed.piece_at(s)]

def legal_successor(board, observed, proxies=()):
    placement = observed.board_fen()
    match = None
    for move in board.legal_moves:
        after = board.copy()
        after.push(move)
        if physical_board(after, moved_proxies(proxies, move)).board_fen() == placement:
            if match is not None:
                return None
            match = move
    return match

def placement_known(board, observed, pending, state, resume_state, proxies=()):
    if state == 'setup':
        return False
    if observed.board_fen() == physical_board(board, proxies).board_fen():
        return True
    if pending:
        after = board.copy()
        after.push(pending)
        return observed.board_fen() == physical_board(after, moved_proxies(proxies, pending)).board_fen()
    if state in ('player_turn','restore') or (state == 'reconcile' and resume_state in ('player_turn','restore')):
        return legal_successor(board,observed,proxies) is not None
    return False


def instruction(board, move, proxies=()):
    color = 'White' if board.turn else 'Black'
    piece = chess.piece_name(board.piece_type_at(move.from_square))
    text = f'{color} {piece}, {chess.square_name(move.from_square)} to {chess.square_name(move.to_square)}.'
    if move.from_square in proxies:
        text += f' Move the pawn representing this {piece}.'
    if board.is_castling(move):
        kingside = chess.square_file(move.to_square) > chess.square_file(move.from_square)
        text += f" Also move the rook from {'h' if kingside else 'a'}{chess.square_rank(move.from_square)+1} to {'f' if kingside else 'd'}{chess.square_rank(move.from_square)+1}."
    if board.is_en_passant(move):
        text += f' Remove the pawn on {chess.square_name(move.to_square + (-8 if board.turn else 8))}.'
    elif board.is_capture(move):
        text += ' Remove the captured piece.'
    if move.promotion:
        text += f' Promote to {chess.piece_name(move.promotion)}. Choose Keep pawn or Replace piece, then confirm on screen.'
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


def proxy_badges(svg, proxies):
    if not proxies:
        return svg
    ns = 'http://www.w3.org/2000/svg'
    ET.register_namespace('', ns)
    ET.register_namespace('xlink', 'http://www.w3.org/1999/xlink')
    root = ET.fromstring(svg)
    overlay = ET.SubElement(root, '{'+ns+'}g', {'class':'pawn-proxies', 'pointer-events':'none'})
    for rect in root.findall('{'+ns+'}rect'):
        names = set(rect.get('class','').split())
        square = next((s for s in proxies if chess.square_name(s) in names), None)
        if square is None:
            continue
        x, y = float(rect.get('x'))+36, float(rect.get('y'))+9
        badge = ET.SubElement(overlay, '{'+ns+'}g', {'data-square':chess.square_name(square)})
        ET.SubElement(badge, '{'+ns+'}title').text = 'Physical piece: pawn'
        ET.SubElement(badge, '{'+ns+'}circle', dict(cx=str(x), cy=str(y), r='7', fill='#ffd587', stroke='#473518'))
        ET.SubElement(badge, '{'+ns+'}text', {'x':str(x), 'y':str(y+3), 'text-anchor':'middle',
                      'font-size':'9', 'font-family':'sans-serif', 'font-weight':'bold', 'fill':'#33240d'}).text = 'P'
    return ET.tostring(root, encoding='unicode')

class Game:
    def __init__(self, engine=None, save_path=None, postprocessor=None):
        self.save_path = Path(save_path) if save_path else None
        self.save_error = None
        self.engine = engine
        self.postprocessor = postprocessor
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
            self.proxies = set()
            self.promotion = None
            self.observed = None
            self.raw_observed = None
            self.raw_frame = None
            self.latest_observed = None
            self.manual_corrections = {}
            self.correction_changes = {}
            self.crf_version = 0
            self.crf_result = None
            self.crf_last_decision = None
            self.recognition_checkpoints = []
            self.correction_edit = None
            if self.postprocessor is not None:
                self.postprocessor.reset()
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
            self.move_warning = None
            self.svg_cache_key = None
            self.svg_cache = None
            self.history = []
            self.events = []
            self.event_id = 0
            self.event_kind = 'message'
            self.message = 'Set up the starting position. I will automatically detect my side.' if self.side_auto else 'Set up the starting position. Confirm which color is nearest the camera.'
            self.last_frame = 0
            self.visual_status = None  # Compatibility with correction evidence records; no hand detector.
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
        return dict(version=3, computer='white' if self.computer else 'black',
                    difficulty=self.difficulty, initial_fen=self.initial_fen, mode=self.mode,
                    board_near=self.original_near,
                    moves=[m.uci() for m in self.board.move_stack], state=state,
                    pawn_promotions=[i for i, entry in enumerate(self.history) if entry.get('pawn_promotion')],
                    pending=self.pending.uci() if self.pending else None)




    def resume_current(self):
        with self.lock:
            data=self.serialize()
            self.load_data(data, strict=True)

    def start_endgame(self, difficulty='normal', player='white'):
        if player not in ('white','black'):
            raise ValueError('Choose whether you play White or Black.')
        self.reset('black' if player=='white' else 'white',difficulty,True)
        self.mode='endgame'
        self.state='endgame_scan'
        self.board=chess.Board(None)
        self.emit('Place your position. I will scan the board.')

    def confirm_endgame(self, placement, turn, player):
        with self.lock:
            if self.state!='endgame_ready' or not self.side_confirmed or time.time()-self.last_frame>3 or self.camera_phase!='stable':
                raise ValueError('Wait for a stable camera view before confirming.')
            if placement!=self.physical().board_fen():
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
            self.computer=player!='white';self.side_auto=False;self.side_confirmed=True
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
            if seen != actual and symbol != actual:
                raise ValueError('The camera reading changed. Check this square and try again.')
            self.manual_corrections[name] = dict(seen=seen, piece=symbol,
                                                occupancy_confirmed=(actual == '.') == (symbol == '.'))
            self.correction_changes.pop(name, None)
            # Show the accepted correction immediately. Move commitment still
            # requires fresh independent observations and the legal-move gate.
            self.observed = self.corrected_observation(self.raw_observed)
            self.latest_observed = self.observed.copy()
            expected = self.repair_expected.copy() if self.repair_expected is not None else self.physical()
            if self.pending:
                expected = self.physical_after(self.pending)
            self.errors = [s for s in self.errors if self.observed.piece_at(chess.parse_square(s)) != expected.piece_at(chess.parse_square(s))]
            if not self.errors:
                self.clear_errors()
            self.reset_votes()

    def position_token(self):
        # Camera frames and message updates must not invalidate an editor draft.
        pending = self.pending.uci() if self.pending and self.postprocessor is None else ''
        return self.board.fen() + '|' + pending + '|' + ','.join(map(str,sorted(self.proxies)))

    def begin_correction(self, identity, position_token):
        if position_token != self.position_token():
            raise ValueError('The turn changed. Reopen Fix recognition to review the current board.')
        if self.postprocessor is not None:
            self.correction_edit = (identity, time.monotonic() + 20)
            self.reset_votes()

    def touch_correction(self, identity):
        if self.correction_edit and self.correction_edit[0] == identity:
            self.correction_edit = (identity, time.monotonic() + 20)

    def end_correction(self, identity):
        if self.correction_edit and self.correction_edit[0] == identity:
            self.correction_edit = None
            self.reset_votes()

    def editing_correction(self):
        if self.correction_edit and time.monotonic() >= self.correction_edit[1]:
            self.end_correction(self.correction_edit[0])
        return self.correction_edit is not None

    def restore_recognition_reference(self, observed):
        """Undo recent inferred moves if the edited board belongs to their source."""
        if self.strict_recovery or self.state == 'paused':
            return False
        placement = observed.board_fen()
        if (placement == self.physical().board_fen() or
                legal_successor(self.board, observed, self.proxies) is not None):
            return False
        for i in range(len(self.recognition_checkpoints) - 1, -1, -1):
            checkpoint = self.recognition_checkpoints[i]
            board, proxies = checkpoint['board'], checkpoint['proxies']
            if (placement != physical_board(board, proxies).board_fen() and
                    legal_successor(board, observed, proxies) is None):
                continue
            self.board = board.copy()
            self.proxies = set(proxies)
            self.history = deepcopy(checkpoint['history'])
            self.pending = checkpoint['pending']
            self.state = 'computer_move' if self.pending else 'player_turn'
            self.resume_state = None
            self.engine_job = None  # Discard any reply computed for the mistaken position.
            self.thinking_since = None
            self.promotion = None
            self.partial_since = None
            self.recognition_checkpoints = self.recognition_checkpoints[:i]
            self.clear_errors()
            self.emit(instruction(self.board, self.pending, self.proxies) if self.pending else 'Recognition corrected. Your turn.', force=True)
            return True
        return False

    def correct_squares(self, changes, position_token, reviewed_placement=None):
        """Apply a reviewed draft atomically, even if the classifier jitters."""
        with self.lock:
            if position_token != self.position_token():
                raise ValueError('The turn changed. Close this editor and review the current board.')
            if not isinstance(changes, dict) or not 1 <= len(changes) <= 64:
                raise ValueError('Choose at least one square to correct.')
            if any(s not in chess.SQUARE_NAMES or p not in tuple('.PNBRQKpnbrqk') for s,p in changes.items()):
                raise ValueError('Choose a valid square and piece.')
            if self.raw_observed is None or time.time()-self.last_frame > 3 or self.camera_phase in ('stale','stopped','loading','waiting'):
                raise ValueError('Camera disconnected. Your edits are kept; reconnect and apply again.')
            # Use the exact postprocessed board shown in the editor. Raw labels
            # stay separate, both for evidence and for the legacy path.
            reviewed = chess.Board(reviewed_placement + ' w - - 0 1') if reviewed_placement else (
                self.observed.copy() if self.observed is not None else self.raw_observed.copy())
            for name, symbol in changes.items():
                reviewed.set_piece_at(chess.parse_square(name), None if symbol == '.' else chess.Piece.from_symbol(symbol))
            for name, symbol in changes.items():
                piece = self.raw_observed.piece_at(chess.parse_square(name))
                actual = piece.symbol() if piece else '.'
                self.manual_corrections[name] = dict(seen=actual, piece=symbol,
                                                    occupancy_confirmed=(actual == '.') == (symbol == '.'))
                self.correction_changes.pop(name, None)
            self.latest_observed = reviewed if self.postprocessor is not None else self.corrected_observation(self.raw_observed)
            self.observed = self.latest_observed.copy()
            if self.postprocessor is not None:
                self.restore_recognition_reference(self.observed)
                self.correction_edit = None
                self.reset_votes()
                # Applying the editor is explicit evidence of the physical
                # state, not a request to classify the cause of an error.
                self.accept_observation(self.observed)
                return
            expected = self.physical_after(self.pending) if self.pending else self.physical()
            self.errors = differences(expected, self.observed)
            if not self.errors:
                self.clear_errors()
            self.reset_votes()


    def reset_votes(self):
        self.invalidate_crf()
        self.camera_phase = 'settling'
        self.last_supported = 0
        self.gate = Consensus()
        self.fast_gate = Consensus(votes=3, span=.45, window=4, score_threshold=.90)
        self.return_gate = Consensus(votes=2, span=.25, window=3, score_threshold=.55)

    def clear_corrections(self):
        with self.lock:
            self.manual_corrections.clear()
            self.correction_changes.clear()
            self.reset_votes()

    def corrected_observation(self, raw, record=None, now=None):
        observed = raw.copy()
        for name, correction in list(self.manual_corrections.items()):
            square = chess.parse_square(name)
            piece = raw.piece_at(square)
            actual = piece.symbol() if piece else '.'
            replacement = correction['piece']
            if record is not None:
                all_probabilities = record.get('square_probabilities', {})
                probabilities = all_probabilities.get(name, {}) if isinstance(all_probabilities, dict) else {}
                if not isinstance(probabilities, dict):
                    probabilities = {}
                support = probabilities.get(replacement, 0)
                square_scores = record.get('square_scores', {})
                confidence = square_scores.get(name, record.get('min_score', 0)) if isinstance(square_scores, dict) else 0
                # The original mistaken label and the human label are both
                # expected readings. A different occupied/empty state must be
                # sustained; one occluded frame must never erase a correction.
                # A different occupied label is model disagreement, not evidence
                # that the user moved or replaced the corrected piece.
                changed = (actual == '.') != (replacement == '.') and actual != correction['seen']
                # When the user corrected occupancy, the original reading can
                # also represent a later real placement/removal.
                if actual == replacement:
                    correction['occupancy_confirmed'] = True
                occupancy_change = correction.get('occupancy_confirmed', True) and (actual == '.') != (replacement == '.')
                valid = all(isinstance(v,(int,float)) and math.isfinite(v) and 0 <= v <= 1 for v in (confidence,support))
                contradictory = valid and (changed or occupancy_change) and confidence >= .85 and support < .08
                if contradictory:
                    previous, count, since = self.correction_changes.get(name, (actual, 0, now))
                    count, since = (count+1, since) if previous == actual else (1, now)
                    self.correction_changes[name] = (actual, count, since)
                    if count >= 3 and now-since >= .45:
                        del self.manual_corrections[name]
                        self.correction_changes.pop(name, None)
                        self.reset_votes()
                        continue
                else:
                    self.correction_changes.pop(name, None)
            observed.set_piece_at(square, None if replacement == '.' else chess.Piece.from_symbol(replacement))
        return observed

    def release_moved_corrections(self, raw, observed, record):
        """An occupancy correction must not leave a ghost on a moved-from square.

        Release only in a uniquely matching complete legal successor, with strong
        fresh evidence on each released square. Commit removes the old overrides.
        """
        if not self.proxies or not self.manual_corrections or self.strict_recovery or self.state not in ('player_turn','computer_move','restore'):
            return observed
        candidates = []
        moves = [self.pending] if self.pending else self.board.legal_moves
        scores = record.get('square_scores', {})
        for move in moves:
            if move.promotion:
                continue
            target = self.physical_after(move)
            proposal = observed.copy()
            released = False
            for name in differences(self.physical(),target):
                if name not in self.manual_corrections:
                    continue
                square = chess.parse_square(name)
                score = scores.get(name,record.get('min_score',0)) if isinstance(scores,dict) else 0
                if (raw.piece_at(square) == target.piece_at(square) and proposal.piece_at(square) != target.piece_at(square)
                        and isinstance(score,(int,float)) and math.isfinite(score) and .85 <= score <= 1):
                    proposal.set_piece_at(square,target.piece_at(square))
                    released = True
            if released and matches_tracked_position(target, proposal, moved_proxies(self.proxies, move)):
                candidates.append(proposal)
        return candidates[0] if len(candidates) == 1 else observed

    def clear_observations(self):
        self.invalidate_crf()
        self.manual_corrections.clear()
        self.correction_changes.clear()
        self.raw_observed = None
        self.raw_frame = None
        self.latest_observed = None
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
            self.correction_edit = None
            self.invalidate_crf()
            self.engine_job = None
            self.clear_errors()
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
            self.recognition_checkpoints = []
            self.correction_edit = None
            while self.board.move_stack:
                self.board.pop()
                self.proxies = set(self.history.pop().get('proxies_before', ()))
                if self.board.turn != self.computer:
                    break
            self.pending = None
            self.promotion = None
            self.clear_errors()
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
                if not isinstance(data,dict) or data.get('version') not in (1,2,3):raise ValueError()
                color,level,state=data['computer'],data['difficulty'],data['state']
                mode=data.get('mode','standard')
                saved_near=data.get('board_near')
                if saved_near not in (None,'black','white'):raise ValueError()
                if color not in ('black','white') or level not in LEVELS or mode not in ('standard','endgame'):raise ValueError()
                if state not in ('player_turn','computer_move','thinking','restore','engine_error','finished'):raise ValueError()
                moves=data['moves']
                if not isinstance(moves,list) or len(moves)>2000:raise ValueError()
                pawn_promotions=data.get('pawn_promotions',[]) if data['version']==3 else []
                if (not isinstance(pawn_promotions,list) or
                        any(type(i) is not int or not 0 <= i < len(moves) for i in pawn_promotions) or
                        len(set(pawn_promotions)) != len(pawn_promotions)):raise ValueError()
                initial=data.get('initial_fen',chess.STARTING_FEN)
                board=chess.Board(initial)
                if not board.is_valid() or (mode=='standard' and initial!=chess.STARTING_FEN):raise ValueError()
                history=[];proxies=set()
                for i,value in enumerate(moves):
                    move=chess.Move.from_uci(value)
                    if move not in board.legal_moves:raise ValueError()
                    pawn_promotion=i in pawn_promotions
                    if pawn_promotion and not move.promotion:raise ValueError()
                    history.append(dict(san=board.san(move),uci=move.uci(),
                                        proxies_before=sorted(proxies),pawn_promotion=pawn_promotion))
                    proxies=moved_proxies(proxies,move,pawn_promotion);board.push(move)
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
            self.proxies=proxies
            self.original_near=saved_near or (color if mode=='standard' else None)
            # Saved games already record both players. Camera geometry must not
            # reassign their colors or block loading an endgame.
            self.side_auto=False;self.side_confirmed=True
            self.detected_near=self.original_near
            self.orientation_status='saved';self.orientation_detail=''
            self.resume_state=state;self.state='reconcile';self.strict_recovery=strict
            self.emit('Match the saved position. Ghost pieces show where to place pieces; crosses show what to remove.')

    def claim_draw(self):
        with self.lock:
            if self.state != 'player_turn' or self.pending or self.promotion or not (self.board.is_repetition(3) or self.board.is_fifty_moves()):
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
            self.raw_frame = identity
            observed = self.corrected_observation(observed, record, now)
            observed = self.release_moved_corrections(self.raw_observed, observed, record)
            observed, tracked = self.tracked_observation(observed)
            self.latest_observed = observed.copy()
            if self.observe_crf(record, observed, now):
                return
            raw = observed.board_fen()
            scores = [record.get(k,0) for k in ('min_score','min_margin','change_score')]
            if not all(isinstance(v,(int,float)) and math.isfinite(v) for v in scores):
                self.camera_phase = 'uncertain'
                self.clear_observations()
                return
            conf, margin, score = scores
            probabilities = record.get('square_probabilities', {})
            tracked_scores = {}
            if tracked and isinstance(probabilities, dict) and set(probabilities) == set(chess.SQUARE_NAMES):
                # A confirmed substitute's identity comes from the game. At its
                # uniquely determined square, only occupancy and color need to
                # be recognized. Other squares still require piece evidence.
                evidence = []
                for name in chess.SQUARE_NAMES:
                    if name in self.manual_corrections:
                        continue
                    distribution = probabilities.get(name)
                    if (not isinstance(distribution, dict) or set(distribution) != set('.PNBRQKpnbrqk') or
                            not all(isinstance(v, (int,float)) and math.isfinite(v) and 0 <= v <= 1 for v in distribution.values()) or
                            not .99 <= sum(distribution.values()) <= 1.01):
                        evidence = []
                        tracked_scores = {}
                        break
                    if name in tracked:
                        symbols = 'PNBRQK' if tracked[name] else 'pnbrqk'
                        occupancy = min(1., sum(distribution[s] for s in symbols))
                        evidence.append((occupancy, occupancy-(1.-occupancy)))
                        tracked_scores[name] = occupancy
                    else:
                        values = sorted(distribution.values(), reverse=True)
                        evidence.append((values[0], values[0]-values[1]))
                if evidence:
                    conf = min(v[0] for v in evidence)
                    margin = min(v[1] for v in evidence)
            if not tracked_scores and self.manual_corrections and isinstance(probabilities,dict) and set(probabilities) == set(chess.SQUARE_NAMES):
                # Human-confirmed squares no longer block consensus just
                # because the classifier is torn between king and queen.
                remaining = [p for s,p in probabilities.items() if s not in self.manual_corrections]
                if remaining and all(isinstance(p,dict) and len(p) >= 2 and all(isinstance(v,(int,float)) and math.isfinite(v) and 0 <= v <= 1 for v in p.values()) for p in remaining):
                    remaining = [sorted(p.values(), reverse=True) for p in remaining]
                    conf = min(p[0] for p in remaining)
                    margin = min(p[0]-p[1] for p in remaining)
            # A suspected change on a corrected square must settle before any
            # other move can be accepted with that square temporarily held.
            eligible = conf >= .55 and margin >= .10 and not self.correction_changes
            changed = differences(self.physical(), observed)
            square_scores = record.get('square_scores', {})
            values = [tracked_scores.get(s, square_scores.get(s,score)) for s in changed if s not in self.manual_corrections] if isinstance(square_scores,dict) else []
            if values and all(isinstance(v,(int,float)) and math.isfinite(v) for v in values):
                score = min(values)
            elif not changed or all(s in self.manual_corrections for s in changed):
                score = conf
            strict = self.gate.step(raw, now, eligible, score)
            fast = self.fast_gate.step(raw, now, eligible, score)
            returned = self.return_gate.step(raw, now, eligible, conf)
            known = placement_known(self.board, observed, self.pending, self.state, self.resume_state, self.proxies)
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

    def crf_context(self):
        return '|'.join((self.id, self.position_token(), self.pending.uci() if self.pending else '', self.state, str(self.crf_version)))

    def invalidate_crf(self):
        self.crf_version = getattr(self, 'crf_version', 0) + 1
        self.crf_result = None
        if self.postprocessor is not None:
            self.postprocessor.reset()

    def observe_crf(self, record, observed, now):
        if self.postprocessor is None:
            return False
        if self.editing_correction():
            self.crf_result = dict(status='editing', detail='Editing the recognized position.')
            self.camera_phase = 'settling'
            return True
        reason = None
        if self.state not in ('player_turn', 'computer_move', 'restore', 'reconcile', 'thinking', 'finished', 'engine_error'):
            reason = 'Recognition assist starts after the initial position is confirmed.'
        elif self.proxies or self.promotion:
            reason = 'Using the existing promotion and substitute-piece recognition.'
        elif self.strict_recovery or (not self.side_auto and not self.side_confirmed):
            reason = 'Confirming the saved position and board direction first.'
        elif any(observed.board_fen() == self.physical_after(m, True).board_fen() for m in self.promotion_moves()):
            # A pawn kept on the last rank is a supported UI representation,
            # but is outside the CRF's trained candidate set.
            reason = 'Using the existing promotion confirmation.'
        if reason:
            self.crf_result = dict(status='bypassed', detail=reason)
            self.postprocessor.reset()
            return False
        if self.side_auto and not self.observe_orientation(record, now, True):
            # Auto orientation remains enabled throughout a normal game. Its
            # fresh geometry evidence must gate CRF inference too, independently
            # of the piece-label confidence that the CRF is meant to improve.
            self.invalidate_crf()
            self.crf_result = dict(status='uncertain', detail=self.orientation_detail)
            self.camera_phase = 'orienting'
            return True
        if self.correction_changes:
            self.invalidate_crf()
            self.crf_result = dict(status='uncertain', detail='Waiting for a corrected piece to settle.')
            self.camera_phase = 'uncertain'
            return True
        try:
            result = self.postprocessor.step(self.board.fen(), self.crf_context(), record,
                                             self.manual_corrections, now)
        except (ValueError, TypeError, KeyError):
            self.postprocessor.reset()
            result = dict(status='uncertain', detail='Waiting for a complete view of the board.')
        self.crf_result = result
        status = result['status']
        self.camera_phase = status if status in ('stable', 'settling') else 'uncertain'
        if status != 'stable':
            return True
        physical = chess.Board(result['placement'] + ' w - - 0 1')
        self.latest_observed = physical.copy()
        self.last_supported = self.last_frame
        self.crf_last_decision = dict(result, reference_fen=self.board.fen(), frame=self.raw_frame)
        self.accept_observation(physical)
        return True

    def recognition_status(self, wall):
        if self.postprocessor is None:
            return None
        result = dict(self.crf_result or dict(status='waiting', detail='Waiting for a camera reading.'))
        if result.get('context') and result['context'] != self.crf_context():
            result = dict(status='waiting', detail='Waiting for a reading of the current position.')
        fresh = wall-self.last_frame < 3 and self.camera_phase not in ('stale', 'stopped', 'loading', 'waiting')
        if not fresh:
            result.update(status='waiting', detail='Wait for the camera to reconnect.')
        return result


    def tracked_observation(self, observed):
        """Resolve known pawn substitutes against one unambiguous legal position.

        Never turn every detected pawn into a queen, invent occupancy, move a
        substitute through a blocker, or infer a new promotion. Raw model output
        is retained separately for the physical-piece correction editor.
        """
        if not self.proxies or self.strict_recovery or self.state in ('setup','endgame_scan','endgame_ready'):
            return observed, {}
        candidates = [(self.physical(), self.proxies)]
        moves = ([self.pending] if self.pending else self.board.legal_moves
                 if self.state in ('player_turn','restore') or
                 (self.state == 'reconcile' and self.resume_state in ('player_turn','restore')) else [])
        for move in moves:
            candidates.append((self.physical_after(move), moved_proxies(self.proxies, move)))
        matches = []
        for expected, proxies in candidates:
            if matches_tracked_position(expected, observed, proxies):
                matches.append((expected, proxies))
        if len(matches) != 1:
            # An unrelated bad square must not make a stationary substitute
            # forget its identity. This never infers a new destination.
            held = observed.copy(stack=False)
            tracked = {}
            for square in self.proxies:
                if observed.piece_at(square) is not None and observed.color_at(square) == self.board.color_at(square):
                    held.set_piece_at(square, chess.Piece(chess.PAWN, self.board.color_at(square)))
                    tracked[chess.square_name(square)] = self.board.color_at(square)
            return held, tracked
        expected, proxies = matches[0]
        return expected, {chess.square_name(s): expected.color_at(s) for s in proxies}

    def physical(self):
        return physical_board(self.board, self.proxies)

    def physical_after(self, move, pawn_promotion=False):
        after = self.board.copy()
        after.push(move)
        return physical_board(after, moved_proxies(self.proxies, move, pawn_promotion))

    def promotion_moves(self):
        moves = [self.pending] if self.pending else self.board.legal_moves
        return [move for move in moves if move.promotion]

    def begin_promotion(self, move, pawn=True):
        route = move.uci()[:4]
        if not self.promotion or self.promotion['route'] != route:
            self.promotion = dict(id=str(uuid.uuid4()), route=route,
                                  piece='q' if pawn and not self.pending else move.uci()[4],
                                  representation='pawn' if pawn else 'piece')
        self.partial_since = None
        self.clear_errors()
        self.emit(instruction(self.board, move, self.proxies) if self.pending else
                  f'Pawn reached {chess.square_name(move.to_square)}. Choose a promotion and confirm on screen.')

    def handle_promotion(self, observed):
        """Never accept a promotion from appearance alone, including a real replacement."""
        if self.pending and self.pending.promotion:
            self.begin_promotion(self.pending)
            return True
        placement = observed.board_fen()
        for move in self.promotion_moves():
            for pawn in (True, False):
                if placement == self.physical_after(move, pawn).board_fen():
                    self.begin_promotion(move, pawn)
                    return True
        if self.promotion:
            if placement == self.physical().board_fen():
                self.promotion = None
                self.emit('Promotion cancelled. Your turn.')
            else:
                # A lifted/replaced piece must not dismiss the user's choice.
                self.clear_errors()
                return True
        return False

    def promotion_options(self, wall):
        if not self.promotion or self.state not in ('player_turn','restore','computer_move'):
            return None
        fresh = (self.camera_phase == 'stable' and wall-self.last_frame < 3 and
                 wall-self.last_supported < 3 and (not self.side_auto or self.side_confirmed))
        placement = self.observed.board_fen() if self.observed is not None else None
        choices = []
        for symbol, piece in PROMOTIONS.items():
            move = chess.Move.from_uci(self.promotion['route']+symbol)
            if move not in self.promotion_moves():
                continue
            choices.append(dict(piece=symbol, name=chess.piece_name(piece),
                                pawn_ready=fresh and placement == self.physical_after(move, True).board_fen(),
                                piece_ready=fresh and placement == self.physical_after(move).board_fen()))
        return dict(self.promotion, choices=choices, computer=bool(self.pending),
                    color='white' if self.board.turn else 'black')

    def confirm_promotion(self, identity, piece, representation):
        with self.lock:
            options = self.promotion_options(time.time())
            if not options or identity != options['id']:
                raise ValueError('This promotion changed. Review the board and choose again.')
            if piece not in PROMOTIONS or representation not in ('pawn','piece'):
                raise ValueError('Choose a promotion piece and how to place it on the board.')
            choice = next((c for c in options['choices'] if c['piece'] == piece), None)
            if not choice or not choice[representation+'_ready']:
                raise ValueError('Match the chosen pieces on the board, then clear your hands and wait for a stable view.')
            move = chess.Move.from_uci(options['route']+piece)
            self.commit(move, pawn_promotion=representation=='pawn')
            self.pending = None
            self.promotion = None
            self.resume_state = None
            self.clear_errors()
            self.next_turn(ack=True)

    def clear_errors(self):
        self.errors = []
        self.suggestions = []
        self.repair_expected = None
        self.repair_arrows = []
        self.move_warning = None

    def handle_move_warning(self, observed):
        """Explain an exact observed move that would expose the player's king.

        This runs after observation consensus. Pseudo-legal moves are used only
        for diagnosis; they must never be committed or sent to the engine.
        """
        placement = observed.board_fen()
        if (placement == self.physical().board_fen() or
                legal_successor(self.board, observed, self.proxies) is not None or
                any(placement == self.physical_after(m, True).board_fen()
                    for m in self.promotion_moves())):
            return False
        matches = []
        for move in self.board.generate_pseudo_legal_moves():
            if self.board.is_legal(move) or self.board.piece_type_at(move.to_square) == chess.KING:
                continue
            if self.physical_after(move).board_fen() == placement:
                matches.append(move)
        if len(matches) == 1:
            move = matches[0]
            after = self.board.copy()
            after.push(move)
            king = after.king(self.board.turn)
            attackers = sorted(after.attackers(not self.board.turn, king)) if king is not None else []
            if not attackers:
                return False
            piece = self.board.piece_at(move.from_square)
            captured = self.board.piece_at(move.to_square)
            start, end = chess.square_name(move.from_square), chess.square_name(move.to_square)
            threats = ', '.join(f'{chess.piece_name(after.piece_type_at(s))} on {chess.square_name(s)}' for s in attackers)
            if piece.piece_type == chess.KING:
                reason = (f'The {chess.piece_name(captured.piece_type)} on {end} is protected by the {threats}. '
                          if captured else f'{end} is attacked by the {threats}. ')
                reason += 'Your king cannot move into check.'
            else:
                reason = f'This leaves your king in check from the {threats}.'
            restore = f'Return your {chess.piece_name(piece.piece_type)} to {start}.'
            if captured:
                restore += f' Put back the captured {chess.piece_name(captured.piece_type)} on {end}.'
            if self.board.is_en_passant(move):
                square = chess.square_name(move.to_square + (-8 if piece.color else 8))
                restore += f' Put back the captured pawn on {square}.'
            self.clear_errors()
            self.move_warning = dict(uci=move.uci(), reason=reason, restore=restore,
                                     attackers=[chess.square_name(s) for s in attackers])
            self.emit(f'Move recognized: {chess.piece_name(piece.piece_type)} {start} to {end}. '
                      +reason+' '+restore+' Then choose another move.', 'move_warning')
        if not self.move_warning:
            return False
        # Keep the last accepted board as the recovery target, even if later
        # frames are noisy or the player is still putting the pieces back.
        self.errors = differences(self.physical(), observed)
        self.repair_expected = self.physical()
        self.partial_since = None
        if self.state != 'reconcile':
            self.state = 'restore'
        return True

    def offer_corrections(self, observed):
        if any(len(observed.pieces(chess.KING,color)) != 1 for color in (chess.WHITE,chess.BLACK)) or len(observed.piece_map()) > 32:
            self.clear_errors()
            self.errors = differences(self.physical(),observed)
            self.emit('Some pieces are not clearly detected. Clear hands and check the camera view. If pieces are misplaced, complete your legal move directly. No reset is required.')
            return
        options = []
        for move in self.board.legal_moves:
            after = self.physical_after(move)
            diff = differences(after, observed)
            options.append((len(diff),move.uci(),move,after))
        previous = self.suggestions[0]['uci'] if self.suggestions else None
        options.sort(key=lambda option:(option[0],option[1] != previous,option[1]))
        if not options:
            self.errors = differences(self.physical(),observed)
            self.emit('The detected position is uncertain. Clear your hands and check the board framing.')
            return
        minimum = options[0][0]
        unchanged_errors = differences(self.physical(), observed)
        if len(unchanged_errors) < minimum:
            # A single misread piece on the unchanged board is not evidence
            # that the user tried some arbitrary legal move.
            self.clear_errors()
            self.errors = unchanged_errors
            self.emit('Checking the camera reading. Keep the board as played and clear your hands. If the highlighted piece is already correct, fix its recognition.')
            return
        if minimum > 4:
            self.clear_errors()
            self.errors = differences(self.physical(),observed)
            self.emit('The board is hard to read. Clear your hands and check lighting and framing. You can finish any legal move directly; there is no need to reset the board.')
            return
        closest = [option for option in options if option[0] == minimum][:3]
        self.suggestions = [dict(uci=move.uci(),text=instruction(self.board,move,self.proxies)) for _,_,move,_ in closest]
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
                  +instruction(self.board,move,self.proxies)+' To correct the detected placement: '+' '.join(actions[:4])
                  +' You may complete another legal move directly. No need to reset the board.')

    def partial_move(self, observed, move=None):
        changed = set(differences(self.physical(), observed))
        if not changed:
            return False
        for candidate in ([move] if move else self.board.legal_moves):
            after = self.physical_after(candidate)
            affected = set(differences(self.physical(), after))
            if not changed <= affected:
                continue
            valid = True
            for name in affected:
                sq = chess.parse_square(name)
                allowed = [self.physical().piece_at(sq), after.piece_at(sq), None]
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
        if self.editing_correction():
            return
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
            self.errors = differences(self.physical(), observed)
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
        if (not self.pending and not self.strict_recovery and
                (self.state in ('player_turn', 'restore') or
                 (self.state == 'reconcile' and self.resume_state in ('player_turn', 'restore'))) and
                self.handle_move_warning(observed)):
            return
        if self.state == 'reconcile':
            if self.strict_recovery and placement!=self.physical().board_fen():
                self.errors=differences(self.physical(),observed)
                self.emit('Match the saved position. Ghost = place; cross = remove.')
                return
            if self.strict_recovery:
                self.strict_recovery=False
            expected = self.physical_after(self.pending) if self.pending else self.physical()
            if placement != self.physical().board_fen() and (self.pending or self.resume_state in ('player_turn','restore')):
                if self.handle_promotion(observed):
                    self.state = 'computer_move' if self.pending else 'player_turn'
                    self.resume_state = None
                    return
            if placement == self.physical().board_fen():
                self.clear_errors()
                target = self.resume_state
                self.resume_state = None
                self.promotion = None
                if self.pending:
                    self.state = 'computer_move'
                    self.emit(instruction(self.board,self.pending,self.proxies))
                    if self.pending.promotion:
                        self.begin_promotion(self.pending)
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
            elif not self.pending and self.resume_state in ('player_turn','restore') and legal_successor(self.board,observed,self.proxies) is not None:
                self.commit(legal_successor(self.board,observed,self.proxies))
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
        if self.handle_promotion(observed):
            return
        if self.state == 'computer_move':
            expected = self.physical_after(self.pending)
            if placement == expected.board_fen():
                self.commit(self.pending)
                self.pending = None
                self.clear_errors()
                self.next_turn(ack=True)
            elif placement == self.physical().board_fen():
                self.clear_errors()
                self.emit(instruction(self.board, self.pending, self.proxies))
            else:
                self.errors = differences(expected, observed)
                self.emit('The computer move is not confirmed. Check the highlighted squares. '+instruction(self.board, self.pending, self.proxies))
            return
        if placement == self.physical().board_fen():
            was_correction = self.state == 'restore'
            self.state = 'player_turn'
            self.clear_errors()
            if was_correction or self.partial_since is not None:
                self.emit('Position confirmed. Your turn.')
            self.partial_since = None
            return
        matches = []
        for move in self.board.legal_moves:
            after = self.physical_after(move)
            if after.board_fen() == placement:
                matches.append(move)
        if len(matches) == 1:
            self.partial_since = None
            self.commit(matches[0])
            self.clear_errors()
            self.next_turn(ack=True)
        else:
            self.clear_errors()
            self.errors = differences(self.physical(), observed)
            if self.partial_move(observed):
                if self.partial_since is None:
                    self.partial_since = time.monotonic()
                if time.monotonic()-self.partial_since < 8:
                    self.emit('Move in progress. Finish placing the pieces, including any rook, captured pawn, or promotion piece, then clear the board.')
                    return
            self.partial_since = None
            self.state = 'restore'
            self.offer_corrections(observed)

    def commit(self, move, pawn_promotion=False):
        if self.postprocessor is not None:
            self.recognition_checkpoints.append(dict(board=self.board.copy(), history=deepcopy(self.history),
                                                    proxies=set(self.proxies), pending=self.pending))
            self.recognition_checkpoints = self.recognition_checkpoints[-8:]
        self.invalidate_crf()
        before = self.physical()
        self.history.append({'san': self.board.san(move), 'uci': move.uci(),
                             'proxies_before': sorted(self.proxies), 'pawn_promotion': pawn_promotion})
        self.proxies = moved_proxies(self.proxies, move, pawn_promotion)
        self.board.push(move)
        after = self.physical()
        for name in differences(before, after):
            correction = self.manual_corrections.get(name)
            piece = after.piece_at(chess.parse_square(name))
            if correction and correction['piece'] != (piece.symbol() if piece else '.'):
                del self.manual_corrections[name]
                self.correction_changes.pop(name, None)

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
                self.emit(instruction(self.board, self.pending, self.proxies))
                if self.pending.promotion:
                    self.begin_promotion(self.pending)
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
            expected = self.repair_expected.copy() if self.repair_expected is not None and self.errors else self.physical()
            pending_arrived = False
            if self.proxies and self.pending and self.observed is not None and wall-self.last_frame < 3:
                target = self.physical_after(self.pending)
                move_squares = differences(self.physical(), target)
                pending_arrived = bool(move_squares) and all(self.observed.piece_at(chess.parse_square(s)) == target.piece_at(chess.parse_square(s)) for s in move_squares)
            arrows = list(self.repair_arrows) if self.errors else []
            if self.pending and not (self.state=='reconcile' and self.strict_recovery):
                expected = self.physical_after(self.pending, bool(self.pending.promotion))
                if not pending_arrived:
                    arrows.append(chess.svg.Arrow(self.pending.from_square, self.pending.to_square, color='#53c9a5'))
                if self.board.is_castling(self.pending) and not pending_arrived:
                    rank = chess.square_rank(self.pending.from_square)
                    ks = chess.square_file(self.pending.to_square) > chess.square_file(self.pending.from_square)
                    arrows.append(chess.svg.Arrow(chess.square(7 if ks else 0,rank),chess.square(5 if ks else 3,rank),color='#53c9a5'))
            display = self.observed if (self.errors or self.manual_corrections) and self.observed is not None else self.board
            if pending_arrived:
                display = self.board.copy()
                display.push(self.pending)
            promotion = self.promotion_options(wall)
            if promotion and self.observed is not None:
                display = self.physical()
                route = self.promotion['route']
                for square in (chess.parse_square(route[:2]),chess.parse_square(route[2:])):
                    display.set_piece_at(square,self.observed.piece_at(square))
                for square in self.proxies:
                    if display.piece_at(square) == self.physical().piece_at(square):
                        display.set_piece_at(square, self.board.piece_at(square))
            elif not self.errors and self.observed is not None and self.observed.board_fen() == self.physical().board_fen():
                display = self.board
            # The play board shows chess identities, even while a noisy camera
            # frame is being used for error guidance or a pending-move preview.
            # The correction editor below deliberately keeps physical labels.
            display_proxies = self.proxies
            identity_board = self.board
            if pending_arrived and self.pending and not self.pending.promotion:
                display_proxies = moved_proxies(self.proxies, self.pending)
                identity_board = self.board.copy()
                identity_board.push(self.pending)
            if display_proxies:
                display = display.copy(stack=False)
                for square in display_proxies:
                    if display.piece_at(square) is not None and display.color_at(square) == identity_board.color_at(square):
                        display.set_piece_at(square, identity_board.piece_at(square))
            guide_target = expected.copy(stack=False)
            for square in display_proxies:
                physical_piece = physical_board(identity_board,display_proxies).piece_at(square)
                if guide_target.piece_at(square) == physical_piece:
                    guide_target.set_piece_at(square,identity_board.piece_at(square))
            visible_errors = list(self.errors)
            orientation = (self.detected_near != 'white') if self.detected_near else not self.computer
            filled = {}
            for arrow in arrows:
                filled[arrow.tail] = '#53c9a555'
                filled[arrow.head] = '#53c9a577'
            filled.update({chess.parse_square(s): '#7895ab88' for s in self.manual_corrections})
            filled.update({chess.parse_square(s): '#ef6e6e88' for s in visible_errors})
            render = lambda b: chess.svg.board(b, orientation=orientation, size=680, arrows=arrows, fill=filled)
            cache_key = (display.board_fen(),guide_target.board_fen(),orientation,tuple(visible_errors),tuple(self.manual_corrections),tuple(sorted(display_proxies)),
                         tuple((a.tail,a.head,a.color) for a in arrows))
            if cache_key != self.svg_cache_key:
                self.svg_cache = (proxy_badges(recovery_svg(render(display),render(guide_target),display,guide_target,visible_errors),display_proxies),render(guide_target))
                self.svg_cache_key = cache_key
            board_svg, expected_svg = self.svg_cache
            correction_board = self.corrected_observation(self.raw_observed) if self.raw_observed is not None else None
            if self.postprocessor is not None and self.observed is not None:
                correction_board = self.observed.copy()
            correction_svg = chess.svg.board(correction_board, orientation=orientation, size=680) if correction_board is not None else board_svg
            corrections = []
            for name in visible_errors:
                sq = chess.parse_square(name)
                want, seen = expected.piece_at(sq), self.observed.piece_at(sq)
                describe = lambda p: 'empty' if p is None else ('White ' if p.color else 'Black ')+chess.piece_name(p.piece_type)
                corrections.append(f'{name}: detected {describe(seen)}; expected {describe(want)}')
            return dict(session=self.id, state=self.state, message=self.message, event_id=self.event_id,
                        move_instruction=instruction(self.board,self.pending,self.proxies) if self.pending else None,
                        in_check=self.board.is_check(),
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
                        can_claim_draw=self.state=='player_turn' and not self.promotion and (self.board.is_repetition(3) or self.board.is_fifty_moves()),
                        manual_corrections=dict(self.manual_corrections),
                        recognition_postprocess=self.recognition_status(wall),
                        recognition_decision=deepcopy(self.crf_last_decision),
                        position_token=self.position_token(), pending_arrived=pending_arrived,
                        pending_target=self.physical_after(self.pending).board_fen() if self.pending else None,
                        correction_svg=correction_svg,
                        observed_placement=correction_board.board_fen() if correction_board is not None else None,
                        promotion=promotion,
                        move_warning=dict(self.move_warning) if self.move_warning and self.state in ('restore','reconcile') else None,
                        proxies=[dict(square=chess.square_name(s),piece=identity_board.piece_at(s).symbol(),
                                      name=('White ' if identity_board.color_at(s) else 'Black ')+chess.piece_name(identity_board.piece_type_at(s)))
                                 for s in sorted(display_proxies)],
                        raw_placement=self.raw_observed.board_fen() if self.raw_observed else None,
                        recognition_frame=list(self.raw_frame) if self.raw_frame is not None else None,
                        suggestions=list(self.suggestions) if visible_errors else [], errors=corrections,
                        board_svg=board_svg, expected_svg=expected_svg, fen=self.board.fen(),
                        pending=self.pending.uci() if self.pending else None, history=list(self.history))
