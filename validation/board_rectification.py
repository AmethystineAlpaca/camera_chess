"""Removable perspective experiment. No game/model dependencies.

Input/output are camera JPEGs. Detection runs on a bounded-size image; LK flow
updates the homography on every subsequent frame, with fresh checker validation.
Failure returns the original bytes, never a stale transform or invented pixels.
"""
import json
import time
from pathlib import Path

try:
    import cv2
except ImportError:  # Existing installations can still run with the experiment disabled.
    cv2 = None
import numpy as np

SETTING = 'rectify_board'
OUTPUT_SIZE = 768
MARGIN = .14  # Keep a little context around the board (including tall pieces).
UNIT = np.float32([[0, 0], [8, 0], [8, 8], [0, 8]])
SAMPLES = np.float32([np.stack(np.meshgrid(np.arange(8)+dx, np.arange(8)+dy), -1).reshape(64, 2)
                      for dy in (.20, .35, .65, .80) for dx in (.20, .35, .65, .80)])


def ordered_quad(points):
    p = np.asarray(points, np.float32).reshape(4, 2)
    center = p.mean(axis=0)
    p = p[np.argsort(np.arctan2(p[:, 1]-center[1], p[:, 0]-center[0]))]
    return np.roll(p, -int(np.argmin(p.sum(axis=1))), axis=0).copy()


def valid_quad(q, shape):
    h, w = shape[:2]
    if q is None or not np.isfinite(q).all():
        return False
    if not cv2.isContourConvex(np.float32(q)) or cv2.contourArea(np.float32(q)) < h*w*.065:
        return False
    if np.any(q < -2) or np.any(q[:, 0] > w+2) or np.any(q[:, 1] > h+2):
        return False
    lengths = np.linalg.norm(q-np.roll(q, 1, axis=0), axis=1)
    return bool(lengths.min() > min(h, w)*.10 and lengths.max()/lengths.min() < 7)


def checker_score(gray, quad):
    """Require an 8x8 alternating pattern, not simply a rectangular object.

    Sample near square edges so piece tops occlude fewer samples. A local
    checker response suppresses lighting gradients. Boundary samples penalize
    off-by-one grids extrapolated beyond the board.
    """
    if not valid_quad(quad, gray.shape):
        return 0.
    h = cv2.getPerspectiveTransform(UNIT, np.float32(quad))
    pts = cv2.perspectiveTransform(SAMPLES.reshape(1, -1, 2), h).reshape(16, 64, 2)
    values = cv2.remap(gray, pts[:, :, 0], pts[:, :, 1], cv2.INTER_LINEAR)
    cells = np.median(values, axis=0).reshape(8, 8).astype(float)
    parity = 1-2*(np.indices((8, 8)).sum(axis=0)%2)
    response = (cells[:-1, :-1]-cells[:-1, 1:]-cells[1:, :-1]+cells[1:, 1:])*parity[:-1, :-1]/4
    sign = 1 if np.median(response) >= 0 else -1
    response *= sign
    # All four sides must have some alternating evidence; this disambiguates
    # the offset of a partial corner lattice in a sparse endgame.
    edge = np.concatenate((np.diff(cells[0]), np.diff(cells[-1]), np.diff(cells[:, 0]), np.diff(cells[:, -1])))
    expected = np.concatenate((-2*parity[0, :-1], -2*parity[-1, :-1], -2*parity[:-1, 0], -2*parity[:-1, -1]))*sign
    edges = (edge*expected > 16).reshape(4, 7).mean(axis=1)
    coverage = np.mean(response > 10)
    if coverage < .60 or np.min(edges) < .28:
        return 0.
    return float(coverage*.55 + np.mean(edges)*.25 + min(np.median(response)/65, 1)*.20)


def line_candidates(gray, edges):
    """Interrupted borders still supply long line segments, unlike contours."""
    from itertools import combinations
    height, width = gray.shape
    segments = cv2.HoughLinesP(edges, 1, np.pi/720, 35,
                               minLineLength=min(height, width)*.22, maxLineGap=12)
    if segments is None:
        return []
    groups = [[], []]
    for x1, y1, x2, y2 in sorted(segments.reshape(-1, 4), key=lambda p: -np.hypot(p[2]-p[0], p[3]-p[1])):
        dx, dy = float(x2-x1), float(y2-y1)
        group = 0 if abs(dy) < abs(dx)*.45 else 1 if abs(dx) < abs(dy)*.85 else -1
        if group < 0:
            continue
        line = np.cross([x1, y1, 1.], [x2, y2, 1.])
        line /= np.linalg.norm(line[:2])
        location = -(line[0]*width/2+line[2])/line[1] if group == 0 else -(line[1]*height/2+line[2])/line[0]
        if any(abs(location-v[0]) < 5 for v in groups[group]):
            continue
        if len(groups[group]) < 12:
            groups[group].append((location, line))
    hs = sorted(groups[0], key=lambda p: p[0])
    vs = sorted(groups[1], key=lambda p: p[0])
    if len(hs) < 2 or len(vs) < 2:
        return []
    intersections = np.cross(np.array([p[1] for p in hs])[:, None], np.array([p[1] for p in vs])[None])
    denom = intersections[:, :, 2:]
    intersections = np.float32(intersections[:, :, :2]/np.where(abs(denom) < 1e-6, np.nan, denom))
    quads = []
    for a, b in combinations(range(len(hs)), 2):
        if hs[b][0]-hs[a][0] < height*.25:
            continue
        for c, d in combinations(range(len(vs)), 2):
            if vs[d][0]-vs[c][0] < width*.25:
                continue
            q = intersections[[a, a, b, b], [c, d, d, c]]
            if valid_quad(q, gray.shape):
                quads.append(q)
    # Cheap batched ranking before the more expensive occlusion-tolerant score.
    if not quads:
        return []
    xx, yy = np.meshgrid(np.arange(8)+.5, np.arange(8)+.5)
    centers = np.float32(np.stack((xx, yy), -1).reshape(1, -1, 2))
    maps = np.array([cv2.perspectiveTransform(centers, cv2.getPerspectiveTransform(UNIT, q))[0] for q in quads])
    values = cv2.remap(gray, maps[:, :, 0], maps[:, :, 1], cv2.INTER_LINEAR).astype(float).reshape(-1, 8, 8)
    parity = 1-2*(np.indices((7, 7)).sum(axis=0)%2)
    response = (values[:, :-1, :-1]-values[:, :-1, 1:]-values[:, 1:, :-1]+values[:, 1:, 1:])*parity/4
    sign = np.where(np.median(response, axis=(1, 2)) >= 0, 1, -1)
    rank = (response*sign[:, None, None] > 10).mean(axis=(1, 2))
    return [quads[i] for i in np.argsort(rank)[-48:] if rank[i] >= .45]


def detect_board(gray):
    """Propose board boundaries from contours and partial chessboard lattices."""
    candidates = []
    edges = cv2.Canny(gray, 40, 120)
    candidates.extend(line_candidates(gray, edges))
    _, dark = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    for source in (edges, cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8)), dark):
        contours, _ = cv2.findContours(source, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:35]:
            if cv2.contourArea(contour) < gray.size*.065:
                continue
            contour = cv2.convexHull(contour)
            polygon = cv2.approxPolyDP(contour, .025*cv2.arcLength(contour, True), True)
            if len(polygon) != 4:
                continue
            quad = ordered_quad(polygon)
            if not valid_quad(quad, gray.shape):
                continue
            h = cv2.getPerspectiveTransform(UNIT, quad)
            for inset in (0, .12, .24, .36, .48):
                inner = np.float32([[inset, inset], [8-inset, inset], [8-inset, 8-inset], [inset, 8-inset]])
                candidates.append(cv2.perspectiveTransform(inner[None], h)[0])
    # SB's larger-grid mode can initialize from a visible patch while pieces
    # hide other corners. RANSAC rejects spurious corners; final checker scoring
    # rejects skipped rows or an extrapolation that does not cover all 64 cells.
    ok, corners, meta = cv2.findChessboardCornersSBWithMeta(
        gray, (3, 3), cv2.CALIB_CB_NORMALIZE_IMAGE | cv2.CALIB_CB_LARGER)
    if ok and meta is not None:
        rows, cols = meta.shape
        if 3 <= rows <= 7 and 3 <= cols <= 7:
            xx, yy = np.meshgrid(np.arange(cols), np.arange(rows))
            lattice = np.float32(np.stack((xx, yy), -1).reshape(-1, 2))
            h, inliers = cv2.findHomography(lattice, corners.reshape(-1, 2), cv2.RANSAC, 1.8)
            if h is not None and inliers.sum() >= max(8, .70*len(lattice)):
                for y in range(1, 9-rows):
                    for x in range(1, 9-cols):
                        q = cv2.perspectiveTransform((UNIT-[x, y]).astype(np.float32)[None], h)[0]
                        candidates.append(ordered_quad(q))
    best, score = None, .80
    for q in candidates:
        s = checker_score(gray, q)
        if s > score:
            best, score = q, s
    return best, score if best is not None else 0.


class BoardRectifier:
    def __init__(self, settings_path):
        self.settings_path = Path(settings_path)
        self.enabled = True
        self.previous = None
        self.quad = None
        self.tracked_frames = 0
        self.last_time = None

    def reset(self):
        self.previous = self.quad = None
        self.tracked_frames = 0

    def read_enabled(self):
        try:
            value = json.loads(self.settings_path.read_text())
            self.enabled = value.get(SETTING, True) is not False
        except (OSError, ValueError, AttributeError):
            pass  # Atomic settings writes; retain the last successfully read setting.
        return self.enabled

    def track(self, gray):
        if self.previous is None or self.previous.shape != gray.shape or self.quad is None:
            return None
        mask = np.zeros_like(gray)
        cv2.fillConvexPoly(mask, self.quad.astype(np.int32), 255)
        pts = cv2.goodFeaturesToTrack(self.previous, 120, .02, 7, mask=mask)
        if pts is None or len(pts) < 12:
            return None
        current, status, _ = cv2.calcOpticalFlowPyrLK(self.previous, gray, pts, None)
        if current is None:
            return None
        back, reverse, _ = cv2.calcOpticalFlowPyrLK(gray, self.previous, current, None)
        if back is None:
            return None
        good = status.ravel().astype(bool) & reverse.ravel().astype(bool) & (np.linalg.norm(pts-back, axis=2).ravel() < 1.2)
        if good.sum() < 12:
            return None
        old, new = pts[good], current[good]
        h, inliers = cv2.findHomography(old, new, cv2.RANSAC, 1.8)
        if h is None or inliers.sum() < 12 or inliers.mean() < .65:
            return None
        # Do not let a moving hand / single piece define the whole board plane.
        support = old[inliers.ravel().astype(bool)].reshape(-1, 2)
        if cv2.contourArea(cv2.convexHull(support)) < cv2.contourArea(self.quad)*.30:
            return None
        return cv2.perspectiveTransform(self.quad[None], h)[0]

    def process(self, jpeg, capture_time=None):
        start = time.perf_counter()
        enabled = self.read_enabled()
        info = dict(enabled=enabled, applied=False, state='off' if not enabled else 'searching',
                    detail='Original camera input' if not enabled else 'Board not located — using original view')
        if not enabled:
            self.reset()
            return jpeg, info
        if cv2 is None:
            info.update(state='unavailable', detail='Correction unavailable — run setup.sh to install OpenCV; using original view')
            return jpeg, info
        try:
            frame = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                raise ValueError('Invalid camera image')
            scale = min(1., 640/max(frame.shape[:2]))
            small = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_LINEAR)
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            if capture_time is not None and self.last_time is not None and not 0 < capture_time-self.last_time < 2:
                self.reset()
            self.last_time = capture_time
            q = self.track(gray)
            score = checker_score(gray, q) if q is not None else 0.
            if score < .76 or self.tracked_frames >= 12:
                detected, detected_score = detect_board(gray)
                if detected is not None:
                    q, score = detected, detected_score
                self.tracked_frames = 0
            if q is None or score < .76:
                self.reset()
                return jpeg, info
            self.previous, self.quad = gray, q
            self.tracked_frames += 1
            # Preserve camera-facing orientation; no semantic 90/180 degree turn.
            dst = UNIT/8*(OUTPUT_SIZE-1)*(1-2*MARGIN)+(OUTPUT_SIZE-1)*MARGIN
            h = cv2.getPerspectiveTransform((q/scale).astype(np.float32), dst.astype(np.float32))
            warped = cv2.warpPerspective(frame, h, (OUTPUT_SIZE, OUTPUT_SIZE), borderMode=cv2.BORDER_CONSTANT)
            ok, encoded = cv2.imencode('.jpg', warped, [cv2.IMWRITE_JPEG_QUALITY, 94])
            if not ok:
                raise ValueError('Could not encode corrected image')
            info.update(applied=True, state='tracking', detail='Perspective corrected · model input',
                        corners=(q/scale).tolist(), homography=h.tolist(), size=OUTPUT_SIZE,
                        margin=MARGIN, jpeg_quality=94, score=round(score, 3))
            return encoded.tobytes(), info
        except (cv2.error, ValueError, np.linalg.LinAlgError):
            self.reset()
            info.update(state='unavailable', detail='Correction unavailable — using original view')
            return jpeg, info
        finally:
            info['milliseconds'] = round((time.perf_counter()-start)*1000, 2)


def preview_packet(original, model_input, info, sequence, capture_time):
    """One atomically published packet prevents the two views drifting apart."""
    import base64
    return json.dumps(dict(sequence=sequence, capture_time=capture_time, rectification=info,
        original=base64.b64encode(original).decode('ascii'),
        model_input=base64.b64encode(model_input).decode('ascii')))
