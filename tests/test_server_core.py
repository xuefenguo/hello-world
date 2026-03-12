import unittest

from apps.orchestrator import server


class ServerCoreTests(unittest.TestCase):
    def test_validate_provider(self):
        self.assertTrue(server.validate_provider("local"))
        self.assertTrue(server.validate_provider("cloud"))
        self.assertFalse(server.validate_provider("x"))

    def test_local_generate(self):
        content = server.local_generate([
            {"role": "system", "content": "s"},
            {"role": "user", "content": "你好"},
        ], "local-echo")
        self.assertIn("local-echo", content)
        self.assertIn("你好", content)

    def test_find_or_create_session(self):
        sessions = []
        session = server.find_or_create_session(None, sessions)
        self.assertEqual(len(sessions), 1)
        found = server.find_or_create_session(session["id"], sessions)
        self.assertEqual(found["id"], session["id"])


if __name__ == "__main__":
    unittest.main()
