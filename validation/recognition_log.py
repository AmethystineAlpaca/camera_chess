"""Local correction evidence and bounded operational logs (never uploaded)."""
import base64
from copy import deepcopy
from collections import OrderedDict
from datetime import datetime, timezone
import html
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re
import time
import uuid

import chess
import chess.svg


def read_frame(path):
    header, jpeg = Path(path).read_bytes().split(b'\n', 1)
    return json.loads(header), jpeg


def read_observation(directory):
    """Read a matched classifier result/image, honoring stopped/loading status."""
    directory = Path(directory)
    record = json.loads((directory/'status.json').read_text())
    try:
        paired, jpeg = read_frame(directory/'observation.bin')
        if record.get('raw') and paired.get('capture_time',0) >= record.get('capture_time',0):
            return paired, jpeg
    except (OSError, ValueError):
        pass
    return record, None


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        import os
        os.fsync(stream.fileno())
    temporary.replace(path)


def write_text(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(value)
    temporary.replace(path)


class RecognitionLog:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger('recognition-audit.' + str(self.root))
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        handler = RotatingFileHandler(self.root/'events.jsonl', maxBytes=5_000_000, backupCount=3)
        handler.setFormatter(logging.Formatter('%(message)s'))
        self.logger.addHandler(handler)
        self.last_state = None
        self.last_problem = None
        self.frames = OrderedDict()
        self.views = OrderedDict()
        self.frame_bytes = 0
        self.completed = {}

    def event(self, kind, **fields):
        self.logger.info(json.dumps(dict(time=time.time(), type=kind, **fields), ensure_ascii=False))

    def problem(self, error):
        message = str(error) if error else None
        if message != self.last_problem:
            self.event('observation_error' if message else 'observation_recovered', error=message)
            self.last_problem = message

    def observe(self, game, record=None, jpeg=None):
        if record and jpeg and game.raw_observed is not None:
            if (record.get('raw') == game.raw_observed.board_fen()
                    and game.raw_frame == (record.get('sequence'), record.get('capture_time'))):
                key = (game.id, *game.raw_frame)
                if key not in self.frames:
                    self.frames[key] = (deepcopy(record), jpeg)
                    self.frame_bytes += len(jpeg)
                    while len(self.frames) > 128 or self.frame_bytes > 64_000_000:
                        _, (_, removed) = self.frames.popitem(last=False)
                        self.frame_bytes -= len(removed)
        state = dict(session=game.id, state=game.state, fen=game.board.fen(),
                     pending=game.pending.uci() if game.pending else None,
                     camera_phase=game.camera_phase, visual_status=game.visual_status,
                     orientation=game.orientation_status, errors=list(game.errors),
                     corrections=dict(game.manual_corrections), message=game.message)
        if state != self.last_state:
            self.event('state_changed', **state,
                       sequence=record.get('sequence') if record else None,
                       capture_time=record.get('capture_time') if record else None)
            self.last_state = deepcopy(state)

    @staticmethod
    def view_key(snapshot):
        frame = snapshot.get('recognition_frame')
        if not isinstance(frame, (tuple, list)) or len(frame) != 2:
            return None
        return (snapshot['session'], *frame, snapshot.get('position_token'), snapshot.get('observed_placement'))

    def remember_view(self, snapshot):
        """Remember the board actually sent to the browser, before later frames arrive."""
        key = self.view_key(snapshot)
        if key:
            self.views[key] = deepcopy(snapshot)
            self.views.move_to_end(key)
            while len(self.views) > 128:
                self.views.popitem(last=False)
        return snapshot

    def reviewed_view(self, request, current):
        if not request.get('recognition_frame'):
            return current  # Compatibility with pages loaded before frame IDs existed.
        if request.get('position_token') != current['position_token']:
            raise ValueError('The turn changed. Review the current board before correcting it.')
        key = self.view_key(request)
        snapshot = self.views.get(key)
        if snapshot is None:
            raise ValueError('That camera reading is no longer available. Review the current board and try again.')
        return deepcopy(snapshot)

    def directory(self, identity):
        if not isinstance(identity, str) or not re.fullmatch(r'[0-9a-f]{32}', identity):
            raise ValueError('Reopen Fix recognition to capture a new snapshot.')
        directory = self.root/identity
        if not directory.is_dir():
            raise ValueError('Correction snapshot is missing. Reopen Fix recognition.')
        return directory

    def open(self, snapshot):
        identity = uuid.uuid4().hex
        directory = self.root/identity
        directory.mkdir()
        write_json(directory/'opened.json', snapshot)
        missing = []
        frame = snapshot.get('recognition_frame')
        evidence = self.frames.get((snapshot['session'], *frame)) if frame else None
        source = None
        if evidence and evidence[0].get('raw') == snapshot.get('raw_placement'):
            record, jpeg = evidence
            (directory/'recognized-camera.jpg').write_bytes(jpeg)
            write_json(directory/'recognition.json', record)
            source = dict(sequence=record['sequence'], capture_time=record['capture_time'],
                          image='recognized-camera.jpg', sha256=hashlib.sha256(jpeg).hexdigest())
        else:
            missing.append('Matching recognition frame unavailable')
            self.event('evidence_missing', id=identity, frame=frame, reason=missing[0])
        write_json(directory/'context.json', dict(schema_version=2, id=identity, source=source,
                   opened_at=datetime.now(timezone.utc).isoformat(), missing_evidence=missing))
        self.event('correction_opened', id=identity, session=snapshot['session'], missing_evidence=missing)
        return identity, missing

    def prepare(self, identity, data, before):
        directory = self.directory(identity)
        opened = json.loads((directory/'opened.json').read_text())
        if opened['session'] != before['session'] or data.get('position_token') != opened['position_token']:
            raise ValueError('The game changed. Reopen Fix recognition and review the board.')
        result_path = directory/'result.json'
        if identity in self.completed or result_path.exists():
            result = self.completed.get(identity) or json.loads(result_path.read_text())
            if result['changes'] != data.get('changes'):
                raise ValueError('These corrections were already applied. Reopen Fix recognition to edit again.')
            self.event('correction_retry_already_applied', id=identity)
            return False
        changes = data.get('changes')
        if isinstance(changes, dict):
            for square, piece in changes.items():
                old = opened.get('manual_corrections', {}).get(square, {}).get('piece')
                now = before.get('manual_corrections', {}).get(square, {}).get('piece')
                if now != old and now != piece:
                    raise ValueError(f'Corrections on {square.upper()} changed in another window. Discard edits and reload the reading.')
        attempt = uuid.uuid4().hex
        write_json(directory/('attempt-'+attempt+'.json'), dict(time=time.time(), request=data, before=before))
        self.event('correction_apply_requested', id=identity, attempt=attempt, changes=data.get('changes'))
        return True

    def finish(self, identity, changes, after):
        directory = self.directory(identity)
        result = self.completed.get(identity) or dict(time=time.time(), changes=changes, after=after)
        self.completed[identity] = result
        if len(self.completed) > 128:
            del self.completed[next(iter(self.completed))]
        # Save the receipt first: a lost HTTP response must not apply the batch twice.
        write_json(directory/'result.json', result)
        opened = json.loads((directory/'opened.json').read_text())
        reviewed = chess.Board(opened['observed_placement']+' w - - 0 1')
        original = chess.Board(opened['raw_placement']+' w - - 0 1')
        corrected = original.copy()
        for square, symbol in changes.items():
            piece = None if symbol == '.' else chess.Piece.from_symbol(symbol)
            reviewed.set_piece_at(chess.parse_square(square), piece)
            corrected.set_piece_at(chess.parse_square(square), piece)
        orientation = (opened.get('detected_near') != 'white') if opened.get('detected_near') else opened.get('computer') != 'white'
        write_text(directory/'recognition-before.svg', chess.svg.board(original, orientation=orientation))
        write_text(directory/'recognition-after.svg', chess.svg.board(corrected, orientation=orientation,
            fill={chess.parse_square(s): '#53c9a577' for s in changes}))
        context = json.loads((directory/'context.json').read_text())
        source = context.get('source')
        source_ok = False
        try:
            record = json.loads((directory/'recognition.json').read_text())
            source_ok = bool(source and hashlib.sha256((directory/'recognized-camera.jpg').read_bytes()).hexdigest() == source['sha256']
                             and record['raw'] == opened['raw_placement']
                             and [record['sequence'], record['capture_time']] == opened['recognition_frame']
                             and [source['sequence'], source['capture_time']] == opened['recognition_frame'])
        except (OSError, ValueError, KeyError):
            pass
        if not source_ok:
            self.event('evidence_missing_or_corrupt', id=identity)
        symbol_at = lambda board, name: board.piece_at(chess.parse_square(name)).symbol() if board.piece_at(chess.parse_square(name)) else '.'
        # This pair is the annotation of ONE model input, never a later live board.
        write_json(directory/'pair.json', dict(schema_version=2, source=context.get('source'),
            complete=source_ok, before=original.board_fen(), after=corrected.board_fen(),
            edits={s: dict(before=symbol_at(original,s), after=p) for s,p in changes.items()},
            annotation_scope='edited_squares_only', unchanged_squares='copied_from_original_model_output'))
        write_json(directory/'changes.json', dict(changes=changes, reviewed_before=opened['observed_placement'],
            reviewed_after=reviewed.board_fen(), raw_before=opened['raw_placement'], actual_after=after['observed_placement']))
        self.report(directory, changes)
        files = ['pair.json','changes.json','recognition-before.svg','recognition-after.svg','review.html']
        if source_ok:
            files += ['recognized-camera.jpg','recognition.json']
        write_json(directory/'archive.json', dict(files={name:hashlib.sha256((directory/name).read_bytes()).hexdigest() for name in files}))
        self.event('correction_applied', id=identity, session=after['session'], changes=changes)

    def archive_ready(self, identity):
        directory = self.directory(identity)
        try:
            files = json.loads((directory/'archive.json').read_text())['files']
            return bool(files) and all(hashlib.sha256((directory/name).read_bytes()).hexdigest() == checksum for name,checksum in files.items())
        except (OSError,ValueError,KeyError):
            return False

    def recover(self, identity):
        if not self.archive_ready(identity):
            result = self.completed.get(identity) or json.loads((self.directory(identity)/'result.json').read_text())
            self.completed[identity] = result
            self.finish(identity,result['changes'],result['after'])
            self.event('correction_archive_recovered',id=identity)

    def receipt(self, identity):
        directory = self.directory(identity)
        report = self.archive_ready(identity)
        try:
            missing = not json.loads((directory/'pair.json').read_text()).get('complete')
        except (OSError, ValueError):
            missing = ['Evidence manifest unavailable']
        warning = ('Corrections applied, but some evidence could not be saved. Check disk space.' if not report else
                   'Corrections applied and recorded. Some camera snapshots were unavailable.' if missing else None)
        return dict(correction_id=identity, evidence_warning=warning, evidence_report=report)

    def report(self, directory, changes):
        cards = []
        complete = json.loads((directory/'pair.json').read_text())['complete']
        missing_note = '' if complete else '<p>Original frame unavailable or inconsistent. This is an incomplete annotation; no replacement image has been used.</p>'
        for label, filename in (
            ('Original frame used for recognition', 'recognized-camera.jpg'),
            ('Model recognition before correction', 'recognition-before.svg'),
            ('Same recognition with your corrections', 'recognition-after.svg'),
        ):
            path = directory/filename
            if path.exists() and (filename != 'recognized-camera.jpg' or complete):
                mime = 'image/svg+xml' if path.suffix == '.svg' else 'image/jpeg'
                encoded = base64.b64encode(path.read_bytes()).decode()
                cards.append(f'<section><h2>{label}</h2><img alt="{label}" src="data:{mime};base64,{encoded}"></section>')
        write_text(directory/'review.html',
            '<!doctype html><meta charset="utf-8"><title>Recognition correction</title>'
            '<style>body{font:16px system-ui;margin:24px;background:#14232b;color:#fff}'
            'main{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:24px}'
            'img{width:100%;max-height:480px;object-fit:contain}h2{font-size:18px}</style>'
            '<h1>Recognition correction</h1>'+missing_note+'<p>'+html.escape(directory.name)+'</p><p>'+
            html.escape(', '.join(f'{sq.upper()} → {piece}' for sq,piece in changes.items()))+
            '</p><p>All three views refer to the same original video frame. Only the squares you edited change; '
            'other squares keep the original model output. Live frames received during editing are not used in this pair. '
            'Frame identity, image checksum and per-square annotations are in pair.json.</p><main>'+
            ''.join(cards)+'</main>')

    def client_event(self, data):
        if data.get('event') not in ('cancel', 'edit', 'undo', 'reset', 'apply_error', 'page_error', 'camera_image_error',
                                     'speech_started','speech_finished','speech_error'):
            raise ValueError('Unknown diagnostic event.')
        # Keep the payload bounded and omit browser storage, cookies and unrelated page data.
        self.event('ui_'+data['event'], id=data.get('correction_id'), session=data.get('session'),
                   detail=str(data.get('detail', ''))[:2000], changes=data.get('changes'))
