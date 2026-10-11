"""Offline integrity checks and explicit per-image labels. Never auto-trains.

Examples:
  python validation/evidence_review.py audit /path/to/improvement-logs
  python validation/evidence_review.py gallery /path/to/recording
  python validation/evidence_review.py label /path/to/decisions/ID --target 'FEN' \
      --reviewer reviewer-id --confirm-all-squares --confirm-reference
  python validation/evidence_review.py export /path/to/recording --output reviewed.jsonl
"""
import argparse
from datetime import datetime
import html
import json
from pathlib import Path

from improvement_log import CLASSES, SQUARES, atomic_json, board_array, dated_id, digest, probabilities, control_lock, read_json


def load_packet(directory):
    directory = Path(directory)
    if directory.name.startswith('.'):
        raise ValueError('Incomplete evidence directory')
    raw = (directory/'record.json').read_bytes()
    manifest = json.loads((directory/'complete.json').read_text())
    if digest(raw) != manifest['record_sha256']:
        raise ValueError('Record checksum mismatch')
    packet = json.loads(raw)
    if packet['id'] != directory.name or packet['kind'] != directory.parent.name or packet['recording'] != directory.parent.parent.name:
        raise ValueError('Record identity/path mismatch')
    if packet['class_order'] != list(CLASSES) or packet['square_order'] != SQUARES:
        raise ValueError('Unexpected class or square ordering')
    if packet.get('image'):
        jpeg = (directory/'image.jpg').read_bytes()
        if digest(jpeg) != packet['image']['sha256'] or digest(jpeg) != manifest['image_sha256']:
            raise ValueError('Image checksum mismatch')
        from PIL import Image
        with Image.open(directory/'image.jpg') as image:
            image.verify()
    if packet['kind'] == 'observations':
        data = packet['data']
        q = probabilities(data['record'])
        if q != data['probabilities_64x13'] or board_array(data['record']['raw']) != data['raw_64']:
            raise ValueError('Probability/raw board mismatch')
        if any(row[label] != max(row) for row, label in zip(q, data['raw_64'])):
            raise ValueError('Raw board is not the saved probability argmax')
    return packet


def paired_decision(directory):
    directory = Path(directory)
    decision = load_packet(directory)
    if decision['kind'] != 'decisions':
        raise ValueError('Select a controller decision directory')
    data = decision['data']
    ref = data['observation']
    if not ref or ref['kind'] != 'observations' or ref['recording'] != decision['recording']:
        raise ValueError('Missing or cross-session observation')
    if any(Path(ref[k]).name != ref[k] or ref[k].startswith('.') for k in ('recording','kind','id')):
        raise ValueError('Invalid observation reference')
    image_dir = directory.parent.parent/ref['kind']/ref['id']
    observation = load_packet(image_dir)
    record = observation['data']['record']
    for key in ('sequence', 'capture_time'):
        if record[key] != data['frame'][key]:
            raise ValueError('Frame mismatch')
    if record.get('camera_stream') != data['frame']['stream']:
        raise ValueError('Camera stream mismatch')
    before = data['before']
    if board_array(before['reference_fen']) != before['reference_64']:
        raise ValueError('Reference mismatch')
    if board_array(before['physical_reference_fen']) != before['physical_reference_64']:
        raise ValueError('Physical reference mismatch')
    hint = observation['data'].get('context_hint_only') or {}
    if hint.get('game_id') != before['game_id']:
        raise ValueError('Capture crossed a game boundary; review separately')
    return decision, observation, image_dir


def event_frontier(run):
    events = []
    for path in sorted((run/'events').glob('*/record.json')):
        packet = load_packet(path.parent)
        if packet['data'].get('invalidates_prior_labels'):
            events.append([packet['id'], digest(path.read_bytes())])
    return digest(json.dumps(events).encode())


def require_offline(run):
    """Do not certify a dataset while its correction frontier can still change."""
    run = Path(run).resolve()
    control = read_json(run.parent.parent/'results/lite-live/data-log-control.json', {})
    if control.get('root') and Path(control['root']).resolve() == run.parent and control.get('enabled'):
        raise ValueError('Turn Log Data off before labeling or exporting this live recording.')


def label(directory, target, reviewer, confirm_all, confirm_reference):
    if not confirm_all or not confirm_reference or not reviewer.strip():
        raise ValueError('Explicit full-image and reference review and reviewer name are required')
    directory = Path(directory)
    require_offline(directory.parent.parent)
    decision, observation, _ = paired_decision(directory)
    # Labels are physical pieces visible in THIS image, never logical proxies.
    target_64 = board_array(target)
    if target_64 is None:
        raise ValueError('A complete target board is required')
    value = dict(schema_version=1, id=dated_id('label'), decision_id=decision['id'],
        observation_id=observation['id'], reviewer=reviewer, reviewed_at=datetime.now().astimezone().isoformat(),
        status='human_verified_per_image', target_64=target_64, mask=[True]*64,
        previous_fen=decision['data']['before']['reference_fen'], previous_reviewed=True,
        target_kind='physical_pieces', decision_sha256=digest((directory/'record.json').read_bytes()),
        image_sha256=observation['image']['sha256'], event_frontier=event_frontier(directory.parent.parent))
    destination = directory.parent.parent/'labels'/decision['id']/(value['id']+'.json')
    atomic_json(destination, value)
    return destination


def export(run, output):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.crf_postprocessor import candidates
    import numpy as np
    run, output = Path(run), Path(output)
    require_offline(run)
    frontier = event_frontier(run)
    session = read_json(run/'session.json', {})
    generator_hash = session.get('source_sha256', {}).get('../scripts/crf_postprocessor.py')
    current_generator = Path(__file__).resolve().parents[1]/'scripts/crf_postprocessor.py'
    if generator_hash != digest(current_generator.read_bytes()):
        raise ValueError('Candidate generator differs from the recording. Use the recorded source version.')
    accepted, rejected = [], []
    for group in sorted((run/'labels').glob('*')):
        try:
            latest = sorted(group.glob('*.json'))[-1]
            label_data = json.loads(latest.read_text())
            directory = run/'decisions'/group.name
            decision, observation, _ = paired_decision(directory)
            before = decision['data']['before']
            if (label_data['status'] != 'human_verified_per_image' or label_data['mask'] != [True]*64
                or label_data.get('previous_reviewed') is not True or not label_data.get('reviewer')):
                raise ValueError('Label is not fully reviewed')
            if label_data['event_frontier'] != frontier:
                raise ValueError('Corrections changed since review; review this image again')
            if label_data['decision_sha256'] != digest((directory/'record.json').read_bytes()) or label_data['image_sha256'] != observation['image']['sha256']:
                raise ValueError('Label evidence changed')
            if label_data['previous_fen'] != before['reference_fen']:
                raise ValueError('Reviewed reference differs from runtime reference')
            if before['proxies'] or before['promotion']:
                raise ValueError('Physical substitutes/promotion require a separate training protocol')
            target = label_data['target_64']
            if len(target) != 64 or any(type(x) is not int or x not in range(13) for x in target):
                raise ValueError('Invalid target array')
            candidates(before['reference_fen']).target_index(np.asarray(target, dtype=np.uint8))
            accepted.append(dict(game_id=before['game_id'], recording=run.name, decision_id=decision['id'],
                observation_id=observation['id'], image_sha256=observation['image']['sha256'],
                probabilities_64x13=observation['data']['probabilities_64x13'], previous_fen=before['reference_fen'],
                previous_64=before['reference_64'], target_64=target, label=label_data,
                split_group=before['game_id']))
        except (ValueError, OSError, KeyError, IndexError, TypeError) as exc:
            rejected.append(dict(decision=group.name, reason=str(exc)))
    # Exclusive create: never replace a previous reviewed export.
    with output.open('x') as stream:
        for row in accepted:
            stream.write(json.dumps(row, allow_nan=False)+'\n')
    atomic_json(output.with_suffix(output.suffix+'.report.json'), dict(exported=len(accepted), excluded=rejected,
        note='Human labels are assertions, not a mathematical guarantee. Split whole games/sessions together.'))
    return dict(exported=len(accepted), excluded=rejected)


def gallery(run):
    run = Path(run)
    cards = []
    for path in sorted((run/'decisions').glob('*/record.json')):
        try:
            decision, observation, image_dir = paired_decision(path.parent)
            data = decision['data']
            text = dict(id=decision['id'], model=data['before']['recognition_model'], player=data['before']['player'],
                        previous_fen=data['before']['reference_fen'], stage=data['trace'].get('stage'),
                        proposed_board=(data['trace'].get('postprocess') or {}).get('placement'), label='UNVERIFIED')
            cards.append('<article><a href="'+html.escape(str(image_dir.relative_to(run)/'image.jpg'))+'"><img loading="lazy" src="'+html.escape(str(image_dir.relative_to(run)/'image.jpg'))+'"></a><pre>'+html.escape(json.dumps(text,indent=2))+'</pre></article>')
        except (ValueError, OSError, KeyError) as exc:
            cards.append('<p>Incomplete: '+html.escape(path.parent.name+': '+str(exc))+'</p>')
    page = run/'review.html'
    page.write_text('<!doctype html><meta charset="utf-8"><title>Local recognition evidence</title><style>body{font:16px system-ui;margin:24px}article{border-bottom:1px solid #aaa;padding:20px 0}img{max-width:700px;width:100%}pre{white-space:pre-wrap}</style><h1>Local recognition evidence</h1><p>All predictions are unverified. Review every visible square and its reference before adding a label. Click images for full resolution. Timeline images are in the timeline folder.</p>'+''.join(cards))
    return page


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=['audit','gallery','label','export','recount'])
    parser.add_argument('path', type=Path)
    parser.add_argument('--target');parser.add_argument('--reviewer',default='')
    parser.add_argument('--confirm-all-squares', action='store_true')
    parser.add_argument('--confirm-reference', action='store_true');parser.add_argument('--output',type=Path)
    args = parser.parse_args()
    if args.command == 'recount':
        control_path = args.path.parent/'results/lite-live/data-log-control.json'
        with control_lock(control_path):
            control = read_json(control_path, {})
            if control.get('enabled'):
                raise ValueError('Turn Log Data off before recounting disk usage.')
            used = sum(p.stat().st_size for p in args.path.rglob('*') if p.is_file())
            atomic_json(args.path/'.budget.json', dict(reserved_bytes=used))
        print(json.dumps(dict(reserved_bytes=used)))
        return 0
    if args.command == 'audit':
        errors, count = [], 0
        for path in args.path.rglob('record.json'):
            try:
                load_packet(path.parent)
                if path.parent.parent.name == 'decisions':paired_decision(path.parent)
                count += 1
            except (ValueError, OSError, KeyError, TypeError) as exc:
                errors.append(dict(path=str(path), error=str(exc)))
        print(json.dumps(dict(valid=count, incomplete_or_invalid=errors), indent=2))
        return 1 if errors else 0
    if args.command == 'gallery':print(gallery(args.path))
    if args.command == 'label':print(label(args.path,args.target,args.reviewer,args.confirm_all_squares,args.confirm_reference))
    if args.command == 'export':
        if not args.output:parser.error('--output is required')
        print(json.dumps(export(args.path,args.output),indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
