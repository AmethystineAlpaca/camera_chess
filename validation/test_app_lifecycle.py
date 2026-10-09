import unittest
from app_lifecycle import LauncherLease, BrowserPresence

class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.clock = lambda: self.now

    def test_launcher_crash_expires_but_unmanaged_server_does_not(self):
        lease = LauncherLease(clock=self.clock)
        self.now = 100
        self.assertFalse(lease.expired())
        lease.heartbeat()
        self.now = 114
        self.assertFalse(lease.expired())
        lease.heartbeat()
        self.now = 130
        self.assertTrue(lease.expired())

    def test_browser_clicks_reuse_live_page_and_deduplicate_launch(self):
        pages = BrowserPresence(clock=self.clock)
        self.assertFalse(pages.open_browser())
        self.assertTrue(pages.open_browser())
        pages.heartbeat('one')
        self.assertTrue(pages.open_browser())
        self.now = 11
        self.assertFalse(pages.open_browser())

    def test_refresh_grace_and_last_tab_crash(self):
        pages = BrowserPresence(clock=self.clock)
        pages.heartbeat('one')
        pages.heartbeat('two')
        pages.leave('one')
        self.now = 5
        self.assertTrue(pages.has_pages())
        pages.leave('two')
        self.now = 8
        self.assertFalse(pages.has_pages())
        self.assertFalse(pages.should_stop_camera())
        self.assertFalse(pages.open_browser())
        self.now = 10
        self.assertFalse(pages.has_pages())
        self.assertTrue(pages.should_stop_camera())
        pages.heartbeat('refresh')
        self.assertTrue(pages.has_pages())
        self.now = 21
        self.assertFalse(pages.has_pages())

if __name__ == '__main__':
    unittest.main()
