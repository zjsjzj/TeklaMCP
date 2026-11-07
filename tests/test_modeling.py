"""建模工具单元测试：在伪 Tekla 运行时上验证参数解析、预览、插入与提交逻辑。

运行：
    python -m unittest discover -s tests -v
"""

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


def _model_parts():
    return [o for o in fake_tekla.MODEL.objects.values()
            if isinstance(o, ts.Part)]


class ModelingTestCase(unittest.TestCase):
    def setUp(self):
        fake_tekla.reset()

    # ------------------------------------------------------------------
    # 创建构件
    # ------------------------------------------------------------------
    def test_dry_run_does_not_insert(self):
        result = ts.tekla_create_parts(json.dumps([_beam()]))
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["valid"], 1)
        self.assertEqual(result["invalid"], 0)
        self.assertTrue(result["items"][0]["profile"] == "HEA200")
        self.assertEqual(fake_tekla.MODEL.objects, {})
        self.assertEqual(fake_tekla.MODEL.commit_count, 0)

    def test_create_beam_applies_attributes_and_position(self):
        spec = _beam(plane="left", plane_offset=25, rotation="top",
                     depth="behind", depth_offset=-10, finish="PAINT")
        result = ts.tekla_create_parts(json.dumps([spec]), confirm=True)
        self.assertFalse(result["dry_run"])
        self.assertEqual(result["created"], 1)
        self.assertEqual(result["failed"], 0)
        self.assertTrue(result["committed"])
        self.assertEqual(fake_tekla.MODEL.commit_count, 1)
        self.assertEqual(result["verified_after_commit"], 1)

        part = _model_parts()[0]
        self.assertEqual(part.Profile.ProfileString, "HEA200")
        self.assertEqual(part.Material.MaterialString, "S355")
        self.assertEqual(part.Class, "3")
        self.assertEqual(part.Name, "梁")
        self.assertEqual(part.Finish, "PAINT")
        self.assertEqual(part.StartPoint.X, 0.0)
        self.assertEqual(part.EndPoint.X, 6000.0)
        self.assertEqual(part.Position.Plane, "LEFT")
        self.assertEqual(part.Position.PlaneOffset, 25.0)
        self.assertEqual(part.Position.Rotation, "TOP")
        self.assertEqual(part.Position.Depth, "BEHIND")
        self.assertEqual(part.Position.DepthOffset, -10.0)
        self.assertEqual(result["created_items"][0]["guid"],
                         part.Identifier.GUID)

    def test_column_type_and_chinese_alias(self):
        result = ts.tekla_create_parts(json.dumps([
            _beam(type="column", start="0,0,0", end="0,0,4000"),
            _beam(type="梁", profile="IPE300"),
        ]), confirm=True)
        self.assertEqual(result["created"], 2)
        parts = _model_parts()
        self.assertEqual(parts[0].Type, fake_tekla.Beam.BeamTypeEnum.COLUMN)
        self.assertEqual(parts[1].Type, fake_tekla.Beam.BeamTypeEnum.BEAM)
        self.assertEqual(parts[1].Profile.ProfileString, "IPE300")

    def test_polybeam_and_contour_plate_points(self):
        poly = {"type": "polybeam", "profile": "HEA300",
                "points": [[0, 0, 0], [3000, 0, 0], [3000, 0, 3000]]}
        plate = {"type": "contour_plate", "profile": "PL10",
                 "points": [[0, 0, 0], [500, 0, 0], [500, 500, 0]]}
        result = ts.tekla_create_parts(json.dumps([poly, plate]), confirm=True)
        self.assertEqual(result["created"], 2)
        parts = _model_parts()
        self.assertEqual(len(parts[0].Contour.Points), 3)
        self.assertEqual(parts[0].Contour.Points[1].Point.X, 3000.0)
        self.assertEqual(len(parts[1].Contour.Points), 3)

    def test_points_accept_json_string(self):
        poly = {"type": "polybeam", "profile": "HEA300",
                "points": "[[0,0,0],[3000,0,0]]"}
        result = ts.tekla_create_parts(json.dumps([poly]), confirm=True)
        self.assertEqual(result["created"], 1)
        self.assertEqual(len(_model_parts()[0].Contour.Points), 2)

    def test_point_formats(self):
        result = ts.tekla_create_parts(json.dumps([
            _beam(start={"x": 1, "y": 2, "z": 3}, end="(7, 8, 9)"),
            _beam(start=[0, 0, 0], end="[6000, 0, 0]"),
        ]), confirm=True)
        self.assertEqual(result["created"], 2)
        first = _model_parts()[0]
        self.assertEqual((first.StartPoint.X, first.StartPoint.Y,
                          first.StartPoint.Z), (1.0, 2.0, 3.0))
        self.assertEqual(first.EndPoint.Z, 9.0)

    def test_invalid_specs_reported_without_insert(self):
        specs = [
            {"type": "beam", "start": "0,0,0", "end": "1,0,0"},       # 缺 profile
            {"type": "unicorn", "profile": "PL10", "start": "0,0,0",
             "end": "1,0,0"},                                          # 类型错
            _beam(start="1,2"),                                        # 坐标错
            _beam(plane="sideways"),                                   # 枚举错
            _beam(start="1,0,0", end="1,0,0"),                         # 起止点相同
        ]
        result = ts.tekla_create_parts(json.dumps(specs))
        self.assertEqual(result["valid"], 1)  # 只有最后相同点通过语法校验
        self.assertEqual(result["invalid"], 4)
        self.assertEqual(fake_tekla.MODEL.objects, {})
        errors = " ".join(e["error"] for e in result["errors"])
        self.assertIn("profile", errors)
        self.assertIn("unicorn", errors)

    def test_batch_limit_and_invalid_json(self):
        too_many = json.dumps([_beam() for _ in range(51)])
        self.assertIn("最多 50", ts.tekla_create_parts(too_many)["error"])
        self.assertIn("JSON", ts.tekla_create_parts("{oops")["error"])
        self.assertIn("不能为空", ts.tekla_create_parts("")["error"])
        self.assertIn("JSON 对象", ts.tekla_create_parts(json.dumps(["x"]))["error"])

    # ------------------------------------------------------------------
    # 创建组件
    # ------------------------------------------------------------------
    def _create_two_beams(self):
        result = ts.tekla_create_parts(json.dumps([
            _beam(end="6000,0,0"), _beam(start="6000,0,0", end="6000,0,6000"),
        ]), confirm=True)
        guids = [item["guid"] for item in result["created_items"]]
        self.assertEqual(len(guids), 2)
        return guids

    def test_create_component_between_parts(self):
        primary, secondary = self._create_two_beams()
        result = ts.tekla_create_components(json.dumps([{
            "primary_guid": primary,
            "secondary_guids": secondary,
            "number": 1014,
            "point": "0,0,0",
            "attributes": {"d": "12"},
        }]), confirm=True)
        self.assertEqual(result["created"], 1)
        self.assertTrue(result["committed"])
        component = fake_tekla.COMPONENTS[0]
        self.assertEqual(component.Number, 1014)
        self.assertEqual(len(component.input.objects), 2)
        self.assertEqual(component.attributes, {"d": "12"})
        self.assertEqual(result["created_items"][0]["guid"],
                         component.Identifier.GUID)

    def test_component_requires_name_or_number_and_valid_guids(self):
        primary, _ = self._create_two_beams()
        missing = ts.tekla_create_components(json.dumps([{
            "primary_guid": primary,
        }]))
        self.assertEqual(missing["invalid"], 1)
        self.assertIn("name", missing["errors"][0]["error"])

        bad_guid = ts.tekla_create_components(json.dumps([{
            "primary_guid": "00000000-0000-0000-0000-999999999999",
            "number": 1,
        }]))
        self.assertEqual(bad_guid["valid"], 0)
        self.assertIn("not_found", bad_guid["errors"][0]["error"])

    def test_component_two_points_required_together(self):
        primary, _ = self._create_two_beams()
        result = ts.tekla_create_components(json.dumps([{
            "primary_guid": primary, "number": 1, "point2": "1,0,0",
        }]))
        self.assertEqual(result["invalid"], 1)
        self.assertIn("point2", result["errors"][0]["error"])

    def test_component_batch_limit(self):
        items = [{"primary_guid": "x", "number": 1} for _ in range(21)]
        self.assertIn("最多 20",
                      ts.tekla_create_components(json.dumps(items))["error"])

    # ------------------------------------------------------------------
    # 修改构件
    # ------------------------------------------------------------------
    def test_modify_parts_preview_then_confirm(self):
        created = ts.tekla_create_parts(json.dumps([_beam()]), confirm=True)
        guid = created["created_items"][0]["guid"]

        preview = ts.tekla_modify_parts(guid, json.dumps(
            {"class": "5", "profile": "HEA240", "plane_offset": 15}))
        self.assertTrue(preview["dry_run"])
        item = preview["items"][0]
        self.assertEqual(item["after"]["class"], "5")
        self.assertEqual(item["after"]["profile"], "HEA240")
        self.assertEqual(item["plane_offset"], 15)
        self.assertEqual(fake_tekla.MODEL.commit_count, 1)

        result = ts.tekla_modify_parts(guid, json.dumps(
            {"class": "5", "profile": "HEA240", "plane_offset": 15}),
            confirm=True)
        self.assertTrue(result["changed"] == 1)
        self.assertTrue(result["committed"])
        part = _model_parts()[0]
        self.assertEqual(part.Class, "5")
        self.assertEqual(part.Profile.ProfileString, "HEA240")
        self.assertEqual(part.Position.PlaneOffset, 15.0)
        self.assertEqual(fake_tekla.MODEL.commit_count, 2)

    def test_modify_rejects_unknown_field_and_empty_change(self):
        created = ts.tekla_create_parts(json.dumps([_beam()]), confirm=True)
        guid = created["created_items"][0]["guid"]
        self.assertIn("不支持字段",
                      ts.tekla_modify_parts(guid, json.dumps({"colour": "red"}))["error"])
        self.assertIn("非空", ts.tekla_modify_parts(guid, json.dumps({}))["error"])
        self.assertIn("不存在", ts.tekla_modify_parts(
            "00000000-0000-0000-0000-999999999999", json.dumps({"class": "5"}))["error"])

    # ------------------------------------------------------------------
    # 删除对象
    # ------------------------------------------------------------------
    def test_delete_objects_preview_then_confirm(self):
        created = ts.tekla_create_parts(json.dumps([_beam()]), confirm=True)
        guid = created["created_items"][0]["guid"]

        preview = ts.tekla_delete_objects(guid)
        self.assertTrue(preview["dry_run"])
        self.assertEqual(len(preview["items"]), 1)
        self.assertEqual(len(_model_parts()), 1)

        result = ts.tekla_delete_objects(guid, confirm=True)
        self.assertEqual(result["deleted"], 1)
        self.assertTrue(result["committed"])
        self.assertEqual(_model_parts(), [])
        self.assertIn(guid, result["remaining_guids"])

    def test_delete_unknown_guid_reports_not_found(self):
        result = ts.tekla_delete_objects("00000000-0000-0000-0000-123456789012")
        self.assertIn("不存在", result["error"])
        self.assertTrue(result["not_found_guids"])

    # ------------------------------------------------------------------
    # 工具注册
    # ------------------------------------------------------------------
    def test_new_tools_registered(self):
        for name in ("tekla_create_parts", "tekla_create_components",
                     "tekla_modify_parts", "tekla_delete_objects"):
            self.assertIn(name, ts.server.tools)


if __name__ == "__main__":
    unittest.main()
