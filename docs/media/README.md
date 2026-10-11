# Demo media

Public demo exports for [Camera Chess](https://github.com/AmethystineAlpaca/camera_chess).

| Asset | Format | Use |
| --- | --- | --- |
| [Demo shot by phone](demo-v1-portrait.mp4) | 720 × 1280, about 29 seconds | Casual posts and vertical-video platforms |
| [Demo shot by phone GIF](demo-v1-portrait.gif) | 360 × 640, about 29 seconds, 8 fps | Standalone preview of the phone recording (Earlier UI); not embedded in the project READMEs |
| [Screen recording](demo-v1.mp4) | 1470 × 718, about 55 seconds | Technical walkthroughs and project showcases |
| [Preview](demo-v1.gif) | Animated GIF, about 18 seconds | README and communities that support images but not video |

Video exports are silent and omit source recording metadata; the application itself provides spoken moves. Original recordings stay outside the public repository. When sharing a demo, link to the project above so viewers can find setup instructions, source, limitations, and upstream credits.

## October 11, 2026 progress demo

Source: `录屏2026-10-11 下午3.48.49.mov` (73.378 seconds, 2940 × 1436,
39.92 MB). Timecodes below refer to the original recording and the full MP4,
which keeps the same timeline. The original MOV remains outside the repository.

| Asset | Source timecode | Playback duration | Export | Size |
| --- | --- | --- | --- | --- |
| [Endgame setup](progress-2026-10-11-endgame.gif) | 00:00.8–00:01.8 + 00:07.8–00:21.8 | 15.0 s | 1100 × 537, 10 fps GIF | 1.89 MB |
| [Moves and promotion](progress-2026-10-11-promotion.gif) | 00:21.8–00:37.0 | 15.2 s | 1100 × 537, 10 fps GIF | 1.73 MB |
| [Movement after promotion](progress-2026-10-11-after-promotion.gif) | 00:37.0–00:47.5 | 10.4 s | 1100 × 537, 10 fps GIF | 1.30 MB |
| [Full recording](progress-2026-10-11.mp4) | 00:00–01:13.378 | about 73.4 s | 1470 × 718, 30 fps H.264 MP4 | 1.23 MB |

The first GIF keeps one second of the home screen and cuts the color-selection
pause, then shows the complete scan countdown and confirmation at original
speed. The other two clips are continuous and play at original speed. The third
ends after the promoted queen's move is accepted and the next engine reply is
shown, before the later wait around 00:48. No GIF includes that wait or anything
after it. The full MP4 retains the complete recording, including the wait.

The promotion clip shows a bishop preview followed by selection and confirmation
of **Queen / Keep pawn**. It demonstrates promotion and subsequent movement of a
physical pawn as a logical queen, not completion of a game or exhaustive testing
of all promotion choices. Both original-camera and perspective-corrected model
views are visible. GIFs have no audio; the MP4 export also omits audio and source
recording metadata. All four exports are well below 100 MB each. Frame-rate conversion can round
clip duration by one frame. The promotion is manually confirmed in the recording;
the UI explicitly says the camera has not confirmed the position.

Reproduce with FFmpeg installed, from the repository root:

```bash
python3 scripts/export-progress-demo.py '/path/to/录屏2026-10-11 下午3.48.49.mov'
```

## Earlier UI previews

The earlier gameplay GIF is embedded with an **Earlier UI** label in the
READMEs. The phone demo is linked as a video; its GIF is not embedded because
the video preserves better image quality. The phone GIF retains the recording at original
speed, scaled to 360 × 640 at 8 fps, without audio or source metadata.

Reproduce the phone GIF from the repository root:

```bash
ffmpeg -i docs/media/demo-v1-portrait.mp4 \
  -filter_complex '[0:v]fps=8,scale=360:-1:flags=lanczos,split[a][b];[a]palettegen=stats_mode=diff:max_colors=64[p];[b][p]paletteuse=dither=none:diff_mode=rectangle[g]' \
  -map '[g]' -an -map_metadata -1 -loop 0 docs/media/demo-v1-portrait.gif
```
