"""Check camera lifetime without accessing hardware or loading recognition."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from camera_session import CameraSession

class CameraLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.session = CameraSession(Path(self.temp.name))

    @patch('camera_session.os.killpg')
    @patch('camera_session.subprocess.Popen')
    def test_home_start_exit_and_restart(self, launch, kill):
        workers = [MagicMock(pid=i) for i in (10, 11, 12, 13)]
        for worker in workers:
            worker.poll.return_value = None
        launch.side_effect = workers
        self.assertFalse(self.session.running)
        launch.assert_not_called()  # Opening the software must not open the camera.
        self.assertTrue((self.session.data/'STOP').exists())
        self.session.start()
        self.assertTrue(self.session.running)
        self.assertFalse((self.session.data/'STOP').exists())
        self.session.start()
        self.assertEqual(launch.call_count, 2)
        self.session.stop()
        self.assertFalse(self.session.running)
        self.assertEqual(kill.call_count, 2)
        self.assertTrue((self.session.data/'STOP').exists())
        self.session.start()
        self.assertTrue(self.session.running)
        self.assertEqual(launch.call_count, 4)
        self.session.stop()

    @patch('camera_session.os.killpg')
    @patch('camera_session.subprocess.Popen')
    def test_partial_start_failure_releases_camera(self, launch, kill):
        worker = MagicMock(pid=10)
        worker.poll.return_value = None
        launch.side_effect = [worker, OSError('reader launch failed')]
        with self.assertRaises(OSError):
            self.session.start()
        self.assertFalse(self.session.running)
        kill.assert_called_once()
        self.assertFalse(self.session.logs)
        self.assertTrue((self.session.data/'STOP').exists())

    @patch('camera_session.os.killpg')
    @patch('camera_session.subprocess.Popen')
    def test_unresponsive_camera_is_terminated(self, launch, kill):
        workers = [MagicMock(pid=i) for i in (10, 11)]
        for worker in workers:
            worker.poll.return_value = None
        workers[0].wait.side_effect = [subprocess.TimeoutExpired('camera', 2), None]
        launch.side_effect = workers
        self.session.start()
        self.session.stop()
        self.assertEqual(kill.call_count, 3)
        self.assertFalse(self.session.running)

if __name__ == '__main__':
    unittest.main()
