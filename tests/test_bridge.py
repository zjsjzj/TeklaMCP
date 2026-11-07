"""远程桥的工具白名单与调用封装测试（不需要真实 Tekla）。"""

import contextlib
import importlib.util
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fake_tekla

fake_tekla.install()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import tekla_server  # noqa: E402,F401  先让仿真模块生效

_spec = importlib.util.spec_from_file_location(
    "tekla_remote_bridge", os.path.join(ROOT, "tools", "tekla_remote_bridge.py"))
bridge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bridge)


class BridgeTestCase(unittest.TestCase):
    def test_registry_contains_public_tools_only(self):
        registry = bridge.tool_registry()
        self.assertIn("tekla_create_parts", registry)
        self.assertIn("tekla_check_connection", registry)
        self.assertNotIn("_model", registry)
        self.assertTrue(all(name.startswith("tekla_") for name in registry))

    def test_call_tool_returns_result(self):
        result = bridge.call_tool({"tool": "tekla_check_connection", "args": {}})
        self.assertTrue(result["ok"])
        self.assertTrue(result["result"]["connected"])

    def test_call_tool_rejects_unknown_tool_and_bad_args(self):
        self.assertFalse(bridge.call_tool({"tool": "tekla_nope", "args": {}})["ok"])
        self.assertFalse(bridge.call_tool({"tool": "tekla_create_parts",
                                           "args": ["not", "a", "dict"]})["ok"])

    def test_self_test_passes_with_fake_tekla(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(bridge.self_test(), 0)


if __name__ == "__main__":
    unittest.main()
