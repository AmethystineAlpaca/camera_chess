"""Independent image -> position inference. No move history, reference FEN or tracking.
Temporal votes only gate display; raw per-image outputs remain in the log.
Confidence thresholds are experimental, not a calibrated error guarantee.
"""
import sys,time,json,io,os,signal,logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R/'research/chessqueries'))
import numpy as np,torch,chess,chess.svg
from PIL import Image
from torchvision.io import decode_image,ImageReadMode
from chessqueries.models.checkpoint import load_safetensors_model
from chessqueries.data.transforms import build_transform
from chessqueries.core import Board
from lite_consensus import Consensus
from board_orientation import forward_with_geometry,infer_orientation
D=R/'validation/frames/lite-live';O=R/'validation/results/lite-live';O.mkdir(exist_ok=True)
def write(name,value):
 p=O/name;t=p.with_name(p.name+'.tmp');t.write_text(value);os.replace(t,p)
def svg(fen):return chess.svg.board(chess.Board(fen+' w - - 0 1'),orientation=chess.BLACK,size=560)
def image_write(name,jpeg):
 p=O/name;t=p.with_name(p.name+'.tmp');t.write_bytes(jpeg);os.replace(t,p)
started=time.time();running=True
signal.signal(signal.SIGTERM,lambda *_:globals().update(running=False))
previous_status={}
write('status.json',json.dumps(dict(phase='loading',detail='Loading recognition model',updated=time.time(),confirmed=previous_status.get('confirmed'),confirmed_at=previous_status.get('confirmed_at'))))
torch.set_num_threads(4);model=load_safetensors_model(R/'research/models/chessq-lite.safetensors',device='mps').float();transform=build_transform(644,train=False)
last=-1;confirmed=None;confirmed_at=None;processed=0;times=[]
gate=Consensus(confirmed);last_raw=None
log=logging.getLogger('camera-events');log.setLevel(logging.INFO)
handler=RotatingFileHandler(O/'events.jsonl',maxBytes=5_000_000,backupCount=2);handler.setFormatter(logging.Formatter('%(message)s'));log.addHandler(handler)
with torch.inference_mode():
 while running and not (D/'STOP').exists() and not (D/'RESTART_READER').exists():
  try:
   h,j=(D/'latest.bin').read_bytes().split(b'\n',1);seq,ts=h.decode().split(',');seq=int(seq);ts=float(ts)
  except (FileNotFoundError,ValueError):time.sleep(.1);continue
  if time.time()-ts>3:
   write('status.json',json.dumps(dict(phase='stale',detail='Camera feed interrupted. Retaining the last recognized position.',updated=time.time(),capture_time=ts,confirmed=confirmed,confirmed_at=confirmed_at)));time.sleep(.2);continue
  if seq==last:time.sleep(.025);continue
  last=seq;t=time.perf_counter();rgb=decode_image(torch.frombuffer(bytearray(j),dtype=torch.uint8),mode=ImageReadMode.RGB).float();x=transform(rgb).unsqueeze(0).to('mps')
  try:needs_orientation=json.loads((O/'orientation-control.json').read_text()).get('needs_orientation',True)
  except (OSError,ValueError):needs_orientation=True
  orientation=None
  if needs_orientation:
   logits,centroids=forward_with_geometry(model,x)
   orientation=infer_orientation(centroids,int(rgb.shape[-1]),int(rgb.shape[-2]))
  else:logits=model(x)
  probs=logits.softmax(-1)[0].cpu();top=probs.topk(2,dim=-1);ids=top.indices[:,0];fen=Board.from_tensor(ids).to_fen().split()[0];elapsed=time.perf_counter()-t;b=chess.Board(fen+' w - - 0 1')
  conf=float(top.values[:,0].min());margin=float((top.values[:,0]-top.values[:,1]).min());pieces=len(b.piece_map());valid=len(b.pieces(chess.KING,chess.WHITE))==1 and len(b.pieces(chess.KING,chess.BLACK))==1 and pieces<=32
  # Vote only over complete per-image positions. Never synthesize per-square states.
  eligible=valid and conf>=.55 and margin>=.10
  changed_squares=[]
  if confirmed:
   old_ids=Board.from_fen(confirmed).labels;changed_squares=[i for i,v in enumerate(ids.tolist()) if v!=old_ids[i]]
  score=float(top.values[changed_squares,0].min()) if changed_squares else conf
  decision=gate.step(fen,time.monotonic(),eligible,score);votes=decision['votes'];changed=decision['changed'];confirmed=decision['confirmed'];phase=decision['phase']
  if changed:
   confirmed_at=time.time();write('confirmed.svg',svg(confirmed));image_write('confirmed.jpg',j);
   if os.environ.get('CHESS_SAVE_DEBUG_FRAMES')=='1':image_write(f'accepted-debug-{processed%32}.jpg',j)
   print('CONFIRMED',confirmed,flush=True)
  detail='Detected position is stable' if phase=='stable' else 'Checking changes. Retaining the last stable detection.'
  processed+=1;times.append(elapsed);times=times[-30:];record=dict(phase=phase,detail=detail,updated=time.time(),capture_time=ts,sequence=seq,raw=fen,confirmed=confirmed,confirmed_at=confirmed_at,votes=min(votes,5),min_score=conf,min_margin=margin,piece_count=pieces,seconds=elapsed,capture_to_result=time.time()-ts,processing_fps=1/(sum(times)/len(times)),processed=processed,changed=changed,version=2,change_score=score,square_scores={chess.square_name(chess.square(i%8,7-i//8)):float(v) for i,v in enumerate(top.values[:,0].tolist())})
  if orientation is not None:record.update(orientation=orientation,orientation_at=ts)
  write('status.json',json.dumps(record,ensure_ascii=False));
  if fen!=last_raw:write('raw.svg',svg(fen));last_raw=fen
  image_write('live.jpg',j);log.info(json.dumps(record))
write('status.json',json.dumps(dict(phase='stopped',detail='Camera session stopped',updated=time.time(),confirmed=confirmed,confirmed_at=confirmed_at),ensure_ascii=False));handler.close();print('STOPPED',flush=True)
