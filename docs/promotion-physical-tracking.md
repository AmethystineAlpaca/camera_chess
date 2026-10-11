# Promotion and physical substitutes

Implemented on `Promotion-and-Lock`. Offline sequence tests verify controller
behavior. The [October 11 physical-board demo](media/README.md#october-11-2026-progress-demo)
shows endgame setup, queen promotion with a retained pawn, and subsequent
substitute movement. Continuous-camera reliability across sessions remains
unmeasured; the recording does not cover every promotion or recovery path.

## State and inference

The authoritative `Game.board` retains logical identities. `Game.proxies`
records the squares where a physical pawn represents a promoted piece. Existing
history, save/load, undo and recognition-correction checkpoints preserve both.
Only a confirmed move updates the mapping; speculative observations never do.

When a substitute exists, a promotion is pending, or a legal promotion is
available, the observation adapter generates candidates from the **logical**
position, then projects each into its expected physical appearance. Legal moves,
unchanged positions and the existing supported illegal relocations remain in
the candidate set. A substitute moves according to its logical identity, while
the target image is scored against its pawn appearance. Capturing a substitute
removes its mapping. Raw labels and 64 × 13 probabilities are never rewritten.

The new path uses fixed sum-log visual likelihood rather than reusing learned
CRF/neural coefficients trained without substitutes. It identifies its scorer
as `physical_visual_likelihood` in existing recognition decision metadata.
Ordinary positions without promotion candidates keep the selected postprocessor.
Identical physical pawn-promotion candidates are grouped by route: appearance
does not select queen/rook/bishop/knight. Other identity collisions are rejected.
Promotion still requires an explicit confirmation; computer identity is fixed
by the engine. A wrong computer move cannot advance the game.

## Acceptance guards

- Complete, finite, normalized probabilities and the existing fresh-frame,
  orientation and correction-edit checks are required.
- Every unedited square needs at least 0.55 support for expected occupancy/color.
- Changed squares and every remaining substitute additionally need at least
  0.55 support for the expected physical class, including empty source squares.
- Mean log visual fit must be at least log(0.25), candidate-relative mass at
  least 0.60, and score gap at least 0.7 when multiple candidates remain.
- Three distinct supporting readings span at least 0.45 seconds, with two
  consecutive agreeing readings. Votes bind physical placement and identity,
  and reset when reference, pending move, mapping or promotion context changes.
- Explicit correction edits constrain physical labels and substitute for visual
  evidence only on edited squares. Existing correction release and recovery
  behavior remain in force. Saved-position strict recovery keeps its existing
  conservative exact-placement path before ordinary tracking resumes.

These are conservative engineering thresholds, not calibrated correctness
probabilities. The system assumes a correct reference and at most one completed
move; it does not resolve arbitrary missed moves or persistent occlusion. Stable
misrecognition can still occur. It cannot distinguish an actual same-color piece
substitution from classifier type noise with certainty. No zero-risk or measured
camera-accuracy claim is made.

## UI and rollback

Promotion choices carry separate `pawn_reason` / `piece_reason` messages. The UI
shows the selected representation's reason while confirmation is unavailable.
Automatic readiness requires matching visual evidence and a fresh, stable camera
with valid orientation. The UI also offers explicit manual confirmation when
automatic readiness is absent: it asks the user to check the entire position and
use Fix recognition if needed. Manual confirmation records physical-label
corrections and marks the history entry `manual_promotion_confirmation`; it does
not represent successful automatic camera verification. The October 11 demo uses
this manual path. Duplicate/stale promotion identities are still rejected.

Set `CHESS_PHYSICAL_PROMOTION=0` before launching the server to use the previous
promotion/substitute recognition path. Save format and mappings are compatible.
`CHESS_CRF=0` also retains the existing recognition pipeline.

## Offline validation

`validation/test_physical_promotion.py` replays probabilities through the real
controller, including both colors, player/computer promotion, all four choices,
both representations, unrelated type noise, knight jumps, diagonal moves,
captures, multiple substitutes, illegal moves, partial placement, new uncertain
frames, stale evidence, invalid probabilities, undo, save/load, correction release
and rollback. UI tests verify concrete blocked-confirmation messages. Camera
testing must still assess wrong commits, missed moves and acceptance delay.
