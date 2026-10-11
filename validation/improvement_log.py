"""Opt-in/out local evidence collection. No labels are inferred as ground truth.

Two producers (inference and controller) use bounded queues. Only their writer
threads perform image writes, under one interprocess budget/control lock.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
import fcntl
import hashlib
import json
import math
import importlib.metadata
import os
from pathlib import Path
import queue
import threading
import time
import uuid

CLASSES = '.PNBRQKpnbrqk'
SQUARES = [f + r for r in '87654321' for f in 'abcdefgh']
DEFAULT_BUDGET = 20 * 1024**3


def dated_id(kind):
    return datetime.now().astimezone().strftime('%Y-%m-%d_%H-%M-%S.%f%z') + '_' + kind + '_' + uuid.uuid4().hex[:12]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_json(path, default=None):
    try:
        value = json.loads(Path(path).read_text())
        return value if isinstance(value, dict) else default
    except (OSError, ValueError):
        return default


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x') as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def control_lock(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix('.lock').open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def board_array(fen):
    if not fen:
        return None
    import chess
    board = chess.Board(fen if ' ' in fen else fen + ' w - - 0 1')
    return [CLASSES.index(piece.symbol()) if (piece := board.piece_at(chess.parse_square(s))) else 0 for s in SQUARES]


def probabilities(record):
    values = record.get('square_probabilities', {})
    if set(values) != set(SQUARES):
        raise ValueError('Incomplete probability squares')
    rows = []
    for square in SQUARES:
        row = values[square]
        if set(row) != set(CLASSES):
            raise ValueError('Incorrect probability classes')
        numbers = [row[c] for c in CLASSES]
        if any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in numbers):
            raise ValueError('Invalid probabilities')
        if abs(sum(numbers) - 1) > .01:
            raise ValueError('Unnormalized probabilities')
        rows.append(numbers)
    return rows


def game_context(game):
    """Called under the game lock; before/after copies never alias live state."""
    model = game.postprocessor
    return deepcopy(dict(game_id=game.id, position_token=game.position_token(),
        reference_fen=game.board.fen(), reference_64=board_array(game.board.fen()),
        physical_reference_fen=game.physical().fen(), physical_reference_64=board_array(game.physical().fen()),
        reference_truth='application_state_unverified', state=game.state, mode=game.mode,
        turn='white' if game.board.turn else 'black', player='black' if game.computer else 'white',
        computer='white' if game.computer else 'black', colors_confirmed=game.side_confirmed,
        side_auto=game.side_auto, detected_near=game.detected_near, original_near=game.original_near,
        pending=game.pending.uci() if game.pending else None, difficulty=game.difficulty,
        proxies=sorted(game.proxies), promotion=game.promotion, corrections=game.manual_corrections,
        correction_changes=game.correction_changes, history=game.history,
        recognition_model=getattr(model, 'model', None),
        checkpoint=getattr(model, 'checkpoint_identity', None), crf_context=game.crf_context(),
        camera_phase=game.camera_phase, orientation_status=game.orientation_status,
        event_id=game.event_id))


class DataLogControl:
    def __init__(self, root, control_path, enabled=True, budget=DEFAULT_BUDGET):
        self.root, self.path = Path(root), Path(control_path)
        self.enabled, self.budget = enabled, budget
        self.game_id = None
        self.consent_epoch = dated_id("consent")
        source = Path(__file__).parent
        self.sources = {name: digest((source/name).read_bytes()) for name in
            ("improvement_log.py", "lite_live.py", "chess_game.py", "crf_observation.py", "board_orientation.py", "lite-live-camera.swift", "../scripts/crf_postprocessor.py")}
        self.publish(None, force=True)

    def publish(self, context, force=False):
        game_id = context.get('game_id') if context else None
        if not force and game_id == self.game_id:
            return
        self.game_id = game_id
        self.recording = dated_id('game-' + (game_id or 'idle')[:8])
        value = dict(schema_version=1, enabled=self.enabled, recording=self.recording,
                     root=str(self.root), budget_bytes=self.budget, context_hint=context,
                     consent_epoch=self.consent_epoch, source_sha256=self.sources,
                     created_at=datetime.now().astimezone().isoformat(),
                     label_policy='unverified_until_per_image_human_review', upload=False)
        with control_lock(self.path):
            previous = read_json(self.path, {})
            if not force and previous.get('halted'):
                value['halted'] = previous['halted']
            atomic_json(self.path, value)

    def set_enabled(self, enabled, context):
        if type(enabled) is not bool:
            raise ValueError('Log Data must be true or false.')
        self.enabled = enabled
        self.consent_epoch = dated_id("consent")
        self.publish(context, force=True)

    def status(self):
        control = read_json(self.path, {})
        statuses = [read_json(self.path.with_name('data-log-' + role + '-status.json'), {})
                    for role in ('reader', 'controller')]
        statuses = [s for s in statuses if s.get('recording') == control.get('recording')]
        problems = [s['error'] for s in statuses if s.get('error')]
        quota = read_json(self.root/'.budget.json', {})
        return dict(enabled=self.enabled, directory=str(self.root), budget_bytes=self.budget,
                    reserved_bytes=quota.get('reserved_bytes', 0),
                    stopped_reason=control.get('halted') or ('; '.join(problems) if problems else None),
                    dropped=sum(s.get('dropped', 0) for s in statuses), local_only=True)


class EvidenceWriter:
    def __init__(self, control_path, role, max_items=48, max_bytes=32 * 1024**2):
        self.control_path, self.role = Path(control_path), role
        self.queue = queue.Queue(maxsize=max_items)
        self.max_bytes, self.pending_bytes = max_bytes, 0
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.dropped = 0
        self.error = None
        self.recording = None
        self.written = 0
        self.status_dirty = False
        self.worker = threading.Thread(target=self._run, name='evidence-' + role, daemon=True)
        self.worker.start()

    def control(self):
        control = read_json(self.control_path, {})
        return control if control.get('enabled') and not control.get('halted') else None

    def submit(self, kind, payload, jpeg=None, identity=None, control=None):
        control = control or self.control()
        if not control or self.stop.is_set():
            return None
        identity = identity or dated_id(kind)
        reference = dict(recording=control['recording'], kind=kind, id=identity)
        cost = len(jpeg or b'') + 32000
        with self.lock:
            if self.recording != control['recording']:
                self.recording, self.dropped, self.error = control['recording'], 0, None
            if self.pending_bytes + cost > self.max_bytes or self.queue.full():
                self.dropped += 1
                self.error = 'Recording queue full; some evidence was not saved.'
                self.status_dirty = True
                return None
            self.pending_bytes += cost
            self.queue.put_nowait((deepcopy(control), reference, deepcopy(payload), jpeg, cost))
        return reference

    def _status(self):
        self.status_dirty = False
        try:
            atomic_json(self.control_path.with_name('data-log-' + self.role + '-status.json'),
                        dict(recording=self.recording, written=self.written, dropped=self.dropped,
                             error=self.error, updated_at=time.time(), queued=self.queue.qsize()))
        except OSError:
            pass

    def _write(self, control, ref, payload, jpeg):
        # Disable is serialized with writes: after the setting response, no old
        # queued item can write. Re-enabling creates a fresh recording epoch.
        with control_lock(self.control_path):
            current = read_json(self.control_path, {})
            if not current.get('enabled') or current.get('halted') or current.get('consent_epoch') != control['consent_epoch']:
                return
            root = Path(current['root'])
            root.mkdir(parents=True, exist_ok=True)
            run = root/ref['recording']
            packet = dict(schema_version=1, **ref, saved_at=datetime.now().astimezone().isoformat(),
                          class_order=list(CLASSES), square_order=SQUARES, data=payload,
                          label=None, label_mask=[False]*64, label_status='unverified', visibility='unreviewed',
                          collection_health=dict(writer=self.role, dropped_before=self.dropped, error=self.error))
            if jpeg is not None:
                packet['image'] = dict(file='image.jpg', sha256=digest(jpeg), bytes=len(jpeg))
            raw = json.dumps(packet, ensure_ascii=False, allow_nan=False).encode()
            reserve = len(raw) + len(jpeg or b'') + 16384
            quota_path = root/'.budget.json'
            quota = read_json(quota_path)
            if quota is None:
                quota = dict(reserved_bytes=sum(p.stat().st_size for p in root.rglob('*') if p.is_file()))
            if quota['reserved_bytes'] + reserve > current['budget_bytes']:
                current['halted'] = 'Local data budget reached. Existing recordings are preserved.'
                atomic_json(self.control_path, current)
                self.error = current['halted']
                return
            # Reserve before writing; a crash may overcount but can never reuse
            # unaccounted image storage. The budget is shared by both processes.
            quota['reserved_bytes'] += reserve
            atomic_json(quota_path, quota)
            run.mkdir(exist_ok=True)
            if not (run/'session.json').exists():
                atomic_json(run/'session.json', control)
            parent = run/ref['kind']
            parent.mkdir(exist_ok=True)
            incomplete = parent/('.incomplete-' + ref['id'])
            incomplete.mkdir()
            for name, contents in [('record.json', raw)] + ([('image.jpg', jpeg)] if jpeg is not None else []):
                with (incomplete/name).open('xb') as stream:
                    stream.write(contents)
                    stream.flush()
                    os.fsync(stream.fileno())
            atomic_json(incomplete/'complete.json', dict(record_sha256=digest(raw), image_sha256=digest(jpeg) if jpeg is not None else None))
            directory_fd = os.open(incomplete, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            os.rename(incomplete, parent/ref['id'])
            directory_fd = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            self.written += 1

    def _run(self):
        while not self.stop.is_set() or not self.queue.empty():
            try:
                item = self.queue.get(timeout=.25)
            except queue.Empty:
                if self.status_dirty:
                    self._status()
                continue
            control, ref, payload, jpeg, cost = item
            try:
                self._write(control, ref, payload, jpeg)
            except (OSError, ValueError, TypeError, KeyError) as exc:
                self.dropped += 1
                self.error = 'Data recording failed: ' + str(exc)
                try:
                    with control_lock(self.control_path):
                        current = read_json(self.control_path, {})
                        if current.get('recording') == control['recording']:
                            current['halted'] = self.error
                            atomic_json(self.control_path, current)
                except OSError:
                    pass
            finally:
                with self.lock:
                    self.pending_bytes -= cost
                self.queue.task_done()
                self._status()
        self._status()

    def close(self, timeout=5):
        self.stop.set()
        self.worker.join(timeout=timeout)


class InferenceEvidence:
    def __init__(self, control_path, capture_path, model_path):
        self.writer = EvidenceWriter(control_path, 'reader')
        self.stream = dated_id('camera')
        self.model = dict(name=Path(model_path).name, sha256=digest(Path(model_path).read_bytes()))
        transform_path = Path(__file__).resolve().parents[1]/'research/chessqueries/chessqueries/data/transforms.py'
        self.preprocessing = dict(input_size=644, transform='build_transform(size=644, train=False)',
            image_is_original_jpeg=True, crop=None, resize=[644,644], normalization='ImageNet',
            transform_sha256=digest(transform_path.read_bytes()) if transform_path.exists() else None)
        self.runtime = {}
        for package in ('torch','torchvision','numpy','python-chess'):
            try:
                self.runtime[package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                pass
        self.capture_path = Path(capture_path)
        self.done = threading.Event()
        self.thread = threading.Thread(target=self._timeline, name='capture-evidence', daemon=True)
        self.thread.start()

    def _timeline(self):
        previous = None
        while not self.done.wait(.10):
            control = self.writer.control()
            if not control:
                continue
            try:
                header, jpeg = self.capture_path.read_bytes().split(b'\n', 1)
                seq, stamp = header.decode().split(',')
                seq, stamp = int(seq), float(stamp)
                key = (control['recording'], seq, stamp)
                if key == previous or not -1 <= time.time()-stamp <= 3:
                    continue
                gap = seq - previous[1] - 1 if previous and previous[0] == key[0] else None
                previous = key
                self.writer.submit('timeline', dict(stream=self.stream, sequence=seq, capture_time=stamp,
                    sampled_at_monotonic=time.monotonic(), skipped_capture_sequences=gap,
                    purpose='sampled_context_only_not_proof_of_continuity'), jpeg, control=control)
            except (OSError, ValueError):
                continue

    def observation(self, record, jpeg, control):
        record['camera_stream'] = self.stream
        if not control:
            return
        try:
            q, issue = probabilities(record), None
        except (ValueError, TypeError, KeyError) as exc:
            q, issue = None, str(exc)
        ref = self.writer.submit('observations', dict(record=record, probabilities_64x13=q,
            raw_64=board_array(record.get('raw')), validation_error=issue,
            classifier=self.model, preprocessing=dict(self.preprocessing, rectification=record.get('rectification')), runtime=self.runtime,
            context_hint_only=control.get('context_hint'), inference_completed_monotonic=time.monotonic()),
            jpeg, control=control)
        record['data_log'] = ref  # A reference is queued, not a claim of durable completion.

    def close(self):
        self.done.set()
        self.thread.join(timeout=1)
        self.writer.close(timeout=4)


class ControllerEvidence:
    def __init__(self, control):
        self.control = control
        self.writer = EvidenceWriter(control.path, 'controller')
        self.last_seen = None

    def sync(self, game):
        try:
            if self.control.game_id != game.id:
                self.control.publish(game_context(game))
        except OSError as exc:
            self.writer.error = 'Data recording control failed: ' + str(exc)
            self.writer.status_dirty = True

    def observe(self, game, record, now, wall):
        # Controller semantics stay identical; all pre/post state is copied while
        # its caller holds game.lock. Image I/O happens in the producer worker.
        self.sync(game)
        identity = (record.get('camera_stream'), record.get('sequence'), record.get('capture_time'), game.id)
        enabled = self.writer.control()
        before = game_context(game) if enabled and identity != self.last_seen else None
        self.last_seen = identity
        game.observe(record, now, wall)
        if before is not None:
            after = game_context(game)
            result = deepcopy(getattr(game, 'observation_trace', {}))
            proposal = result.get('postprocess') or {}
            self.writer.submit('decisions', dict(observation=record.get('data_log'), frame=dict(
                stream=record.get('camera_stream'), sequence=record.get('sequence'), capture_time=record.get('capture_time')),
                before=before, after=after, trace=result,
                proposed_64=board_array(proposal.get('placement')),
                application_board_changed=before['reference_fen'] != after['reference_fen'],
                application_target_64=after['physical_reference_64'], target_truth='unverified',
                evaluated_monotonic=now, evaluated_wall_time=wall), control=enabled)

    def event(self, kind, before, after, request=None, reviewed=None):
        edits = (request or {}).get('changes', {}) if kind == '/api/correct-squares' else {}
        mask = [s in edits for s in SQUARES]
        partial = [CLASSES.index(edits[s]) if s in edits else None for s in SQUARES]
        self.writer.submit('events', dict(event=kind, before=before, after=after,
            manual_edit_occurred=bool(edits), edited_square_mask=mask, edited_labels_64=partial,
            request=request, reviewed=reviewed,
            annotation_scope='edited_squares_only' if kind == '/api/correct-squares' else 'none',
            invalidates_prior_labels=kind in ('/api/correct-squares','/api/correct-square','/api/undo','/api/clear-corrections',
                                             '/api/load-game','/api/load-saved','/api/confirm-promotion')))
