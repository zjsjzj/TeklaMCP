"""回归测试：确认新增建模代码没有破坏原有查询/选择/详情工具。"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fake_tekla

fake_tekla.install()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tekla_server as ts  # noqa: E402


def _beam(**overrides):
    spec = {"type": "beam", "profile": "HEA200", "material": "S355",
            "class": "3", "name": "梁", "start": "0,0,0",
            "end": "6000,0,0"}
    spec.update(overrides)
    return spec


class ExistingToolsRegressionTestCase(unittest.TestCase):
    def setUp(self):
        fake_tekla.reset()

    def _create(self, specs):
        result = ts.tekla_create_parts(json.dumps(specs), confirm=True)
        self.assertEqual(result["created"], len(specs))
        return [item["guid"] for item in result["created_items"]]

    def test_connection_and_model_info(self):
        self.assertTrue(ts.tekla_check_connection()["connected"])
        self._create([_beam(), _beam(profile="PL10", name="板")])
        info = ts.tekla_get_model_info()
        self.assertEqual(info["object_counts"]["part"], 2)

    def test_find_parts_after_creation(self):
        guid = self._create([_beam()])[0]
        found = ts.tekla_find_parts(profile="HEA")
        self.assertEqual(found["total_count"], 1)
        self.assertEqual(found["parts"][0]["GUID"], guid)
        self.assertEqual(found["parts"][0]["规格"], "HEA200")
        self.assertEqual(ts.tekla_find_parts(profile="RHS")["total_count"], 0)

    def test_select_by_class(self):
        self._create([_beam()])
        result = ts.tekla_select_by_class("3")
        self.assertEqual(result["selected"], 1)
        self.assertTrue(result["applied"])
        self.assertEqual(ts.tekla_select_by_class("9")["selected"], 0)

    def test_object_details_geometry(self):
        guid = self._create([_beam()])[0]
        details = ts.tekla_get_object_details(guid)
        self.assertEqual(details["count"], 1)
        item = details["objects"][0]
        self.assertEqual(item["geometry"]["start"]["x"], 0.0)
        self.assertEqual(item["geometry"]["end"]["x"], 6000.0)
        self.assertEqual(item["GUID"], guid)


if __name__ == "__main__":
    unittest.main()
