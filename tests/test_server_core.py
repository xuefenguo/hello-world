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

    def test_sanitize_name(self):
        self.assertEqual(server.sanitize_name("a b/c"), "a_b_c")
        self.assertTrue(server.sanitize_name("***").startswith("snapshot_"))

    def test_chat_generate_local(self):
        cfg = server.DEFAULT_CONFIG
        content, used = server.chat_generate("local", "local-echo", [{"role": "user", "content": "hi"}], cfg)
        self.assertEqual(used, "local")
        self.assertIn("hi", content)

    def test_detect_action_from_text(self):
        plan = server.detect_action_from_text("请告诉我当前目录")
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan["action_type"], "pwd")

    def test_execute_allowed_action(self):
        result = server.execute_allowed_action("pwd")
        self.assertEqual(result["returncode"], 0)
        self.assertIn("command", result)


if __name__ == "__main__":
    unittest.main()
