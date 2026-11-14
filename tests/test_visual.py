import json
import unittest
import urllib.error
import urllib.request

from core import visual_state
from visual.server import start


class VisualStateTests(unittest.TestCase):
    def test_state_is_clamped_and_validated(self):
        visual_state.set_level(5)
        visual_state.set_face(3, -3, True)
        snap = visual_state.snapshot()
        self.assertEqual(snap["level"], 1.0)
        self.assertEqual((snap["face_x"], snap["face_y"]), (1.0, -1.0))
        with self.assertRaises(ValueError):
            visual_state.set_mode("volar")
        visual_state.set_mode("idle")


class VisualServerTests(unittest.TestCase):
    def test_serves_page_and_state_on_localhost_only(self):
        server, url = start(port=18765, open_browser=False)
        try:
            self.assertEqual(server.server_address[0], "127.0.0.1")
            visual_state.set_mode("thinking")
            with urllib.request.urlopen(url + "state") as response:
                self.assertEqual(json.load(response)["mode"], "thinking")
            with urllib.request.urlopen(url) as response:
                self.assertIn(b"JARVIS", response.read())
            request = urllib.request.Request(url + "state", headers={"Host": "evil.example"})
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(request)
            self.assertEqual(caught.exception.code, 403)
        finally:
            visual_state.set_mode("idle")
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
