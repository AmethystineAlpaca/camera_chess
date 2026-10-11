#!/usr/bin/env python3
"""Export the October 11 demo. Requires ffmpeg; source MOV stays outside Git."""
import argparse
from pathlib import Path
import subprocess

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('source', type=Path)
a = p.parse_args()
out = Path(__file__).resolve().parents[1] / 'docs' / 'media'
out.mkdir(parents=True, exist_ok=True)
base = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(a.source)]
# Full recording, unchanged timing, no audio or recording metadata.
subprocess.run(base + ['-map', '0:v:0', '-vf', 'fps=30,scale=1470:718',
    '-c:v', 'libx264', '-preset', 'medium', '-crf', '24', '-pix_fmt', 'yuv420p',
    '-an', '-map_metadata', '-1', '-movflags', '+faststart',
    str(out / 'progress-2026-10-11.mp4')], check=True)
clips = {
    'endgame': [(0.8, 1.8), (7.8, 21.8)],
    'promotion': [(21.8, 37.0)],
    'after-promotion': [(37.0, 47.5)],
}
for name, ranges in clips.items():
    parts = [f'[0:v]trim=start={start}:end={end},setpts=PTS-STARTPTS[v{i}]'
             for i, (start, end) in enumerate(ranges)]
    inputs = ''.join(f'[v{i}]' for i in range(len(ranges)))
    parts.append(f'{inputs}concat=n={len(ranges)}:v=1:a=0,fps=10,scale=1100:-1:flags=lanczos,split[a][b]')
    parts.extend(['[a]palettegen=stats_mode=diff[p]',
                  '[b][p]paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle[g]'])
    subprocess.run(base + ['-filter_complex', ';'.join(parts), '-map', '[g]',
        '-an', '-map_metadata', '-1', '-loop', '0',
        str(out / f'progress-2026-10-11-{name}.gif')], check=True)
for path in sorted(out.glob('progress-2026-10-11*')):
    size = path.stat().st_size
    if size >= 100_000_000:
        raise SystemExit(f'Export exceeds 100 MB: {path}')
    print(f'{path.name}: {size / 1_000_000:.2f} MB')
