"""Image-level tests of the optional preprocessor, without a model or camera."""
import base64
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np
from board_rectification import BoardRectifier, OUTPUT_SIZE, preview_packet


def scene(quad=None, cells=8, pieces=False):
    board = np.zeros((400, 400, 3), np.uint8)
    for y in range(cells):
        for x in range(cells):
            a, b = round(x*400/cells), round(y*400/cells)
            c, d = round((x+1)*400/cells), round((y+1)*400/cells)
            board[b:d, a:c] = 220 if (x+y)%2 else 35
    if pieces:
        for x, y in [(1, 1), (6, 1), (2, 5), (5, 6), (4, 3)]:
            cv2.circle(board, (x*50+25, y*50+25), 13, (95, 90, 85), -1)
    if quad is None:
        quad = np.float32([[190, 100], [580, 125], [660, 510], [100, 490]])
    h = cv2.getPerspectiveTransform(np.float32([[0, 0], [400, 0], [400, 400], [0, 400]]), quad)
    image = cv2.warpPerspective(board, h, (800, 600), borderValue=(135, 135, 135))
    return cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 95])[1].tobytes(), quad


class RectificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'settings.json'
        self.rectifier = BoardRectifier(self.path)

    def test_default_detects_board_and_outputs_square_with_correct_geometry(self):
        jpeg, quad = scene(pieces=True)
        result, info = self.rectifier.process(jpeg, 1.)
        self.assertTrue(info['enabled'])
        self.assertTrue(info['applied'], info)
        self.assertEqual(cv2.imdecode(np.frombuffer(result, np.uint8), 1).shape[:2], (OUTPUT_SIZE, OUTPUT_SIZE))
        self.assertLess(np.max(np.linalg.norm(np.array(info['corners'])-quad, axis=1)), 15)

    def test_tracks_new_frame_and_reacquires_after_scene_cut(self):
        jpeg, quad = scene(pieces=True)
        self.assertTrue(self.rectifier.process(jpeg, 1.)[1]['applied'])
        moved = quad + np.float32([10, -8])
        result, info = self.rectifier.process(scene(moved, pieces=True)[0], 1.5)
        self.assertTrue(info['applied'], info)
        self.assertLess(np.max(np.linalg.norm(np.array(info['corners'])-moved, axis=1)), 15)
        # A missing board must not reuse the previous successful homography.
        blank = cv2.imencode('.jpg', np.full((600, 800, 3), 135, np.uint8))[1].tobytes()
        result, info = self.rectifier.process(blank, 2.)
        self.assertFalse(info['applied']); self.assertEqual(result, blank)
        self.assertIsNone(self.rectifier.quad)
        self.assertTrue(self.rectifier.process(jpeg, 2.5)[1]['applied'])

    def test_disabled_is_byte_identical_and_skips_detector(self):
        self.path.write_text(json.dumps({'rectify_board': False}))
        with patch('board_rectification.cv2.imdecode', side_effect=AssertionError('Should not decode')):
            result, info = self.rectifier.process(b'exact original bytes')
        self.assertEqual(result, b'exact original bytes'); self.assertEqual(info['state'], 'off')
        self.path.write_text(json.dumps({'rectify_board': True}))
        self.assertTrue(self.rectifier.process(scene()[0])[1]['applied'])

    def test_missing_optional_dependency_keeps_original_input(self):
        with patch('board_rectification.cv2', None):
            result, info = self.rectifier.process(b'original')
        self.assertEqual(result, b'original')
        self.assertFalse(info['applied'])
        self.assertEqual(info['state'], 'unavailable')

    def test_invalid_image_and_non_chessboard_fall_back(self):
        result, info = self.rectifier.process(b'not a jpeg')
        self.assertEqual(result, b'not a jpeg'); self.assertFalse(info['applied'])
        for cells in (1, 6):
            jpeg, _ = scene(cells=cells)
            result, info = self.rectifier.process(jpeg)
            self.assertFalse(info['applied'], info); self.assertEqual(result, jpeg)

    def test_atomic_preview_contains_exact_source_and_model_input(self):
        jpeg, _ = scene(); result, info = self.rectifier.process(jpeg)
        packet = json.loads(preview_packet(jpeg, result, info, 14, 1234.5))
        self.assertEqual(packet['sequence'], 14)
        self.assertEqual(base64.b64decode(packet['original']), jpeg)
        self.assertEqual(base64.b64decode(packet['model_input']), result)
        self.assertEqual(packet['rectification'], info)


if __name__ == '__main__':
    unittest.main()
