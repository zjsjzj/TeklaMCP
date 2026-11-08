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
    # 螺旋梁
    # ------------------------------------------------------------------
    def test_spiral_beam_creation(self):
        spec = {"type": "spiral", "profile": "RHS300*100*10", "name": "内环梁",
                "start": "600,0,-2.4", "axis_base": "0,0,-2.4",
                "axis_up": "0,0,1000",
                "rotation_angle": 270, "total_rise": 2952.4}
        preview = ts.tekla_create_parts(json.dumps([spec]))
        self.assertTrue(preview["dry_run"])
        self.assertEqual(preview["valid"], 1)
        self.assertEqual(preview["items"][0]["type"], "spiral")
        self.assertEqual(preview["items"][0]["rotation_angle"], 270)

        result = ts.tekla_create_parts(json.dumps([spec]), confirm=True)
        self.assertEqual(result["created"], 1)
        parts = [o for o in fake_tekla.MODEL.objects.values()
                 if isinstance(o, fake_tekla.SpiralBeam)]
        self.assertEqual(len(parts), 1)
        beam = parts[0]
        self.assertAlmostEqual(beam.StartPoint.X, 600.0)
        self.assertAlmostEqual(beam.RotationAngle, 270.0)
        self.assertAlmostEqual(beam.TotalRise, 2952.4)
        self.assertAlmostEqual(beam.RotationAxisBasePoint.Z, -2.4)
        self.assertAlmostEqual(beam.RotationAxisUpPoint.Z, 1000.0)
        self.assertAlmostEqual(beam.RotationAxisDirection.Z, 1.0)
        self.assertEqual(beam.Name, "内环梁")

    def test_spiral_beam_requires_start(self):
        """螺旋梁必须给 start（半径由它到旋转轴的距离决定），且不写只读的 EndPoint。"""
        spec = {"type": "spiral", "profile": "RHS300*100*10",
                "start": "600,0,-2.4", "end": "0,-600,2950",
                "axis_base": "0,0,-2.4", "axis_up": "0,0,1000",
                "rotation_angle": 270, "total_rise": 2952.4}
        result = ts.tekla_create_parts(json.dumps([spec]), confirm=True)
        self.assertEqual(result["created"], 1)

    def test_spiral_beam_chinese_alias(self):
        spec = {"type": "螺旋梁", "profile": "RHS300*100*10",
                "start": "600,0,0", "axis_base": "0,0,0", "axis_up": "0,0,1000",
                "rotation_angle": 270}
        result = ts.tekla_create_parts(json.dumps([spec]), confirm=True)
        self.assertEqual(result["created"], 1)
        self.assertEqual(result["created_items"][0]["type"], "spiral")

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
        self.assertIsInstance(component, fake_tekla.Connection)
        self.assertEqual(component.Number, 1014)
        self.assertEqual(len(component.secondaries), 1)
        self.assertEqual(component.attributes, {"d": "12"})
        self.assertEqual(result["created_items"][0]["guid"],
                         component.Identifier.GUID)
        self.assertEqual(result["created_items"][0]["component_class"],
                         "connection")

    def test_component_kind_selection(self):
        primary, secondary = self._create_two_beams()
        custom = ts.tekla_create_components(json.dumps([{
            "primary_guid": primary, "secondary_guids": secondary,
            "name": "MyNode", "number": 30000063,
        }]), confirm=True)
        self.assertEqual(custom["created_items"][0]["component_class"], "component")
        self.assertIsInstance(fake_tekla.COMPONENTS[0], fake_tekla.Component)
        detail = ts.tekla_create_components(json.dumps([{
            "primary_guid": primary, "name": "MyDetail", "kind": "detail",
            "point": "0,0,0",
        }]), confirm=True)
        self.assertEqual(detail["created_items"][0]["component_class"], "detail")
        self.assertEqual(fake_tekla.COMPONENTS[1].reference_point.X, 0.0)

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



class RunNumberingTests(unittest.TestCase):
    """编号工具：dry_run 不改模型；confirm 后编号状态闭环。"""

    def _fresh(self, parts=3):
        import tekla_server as ts
        fake_tekla.MODEL.objects.clear()
        fake_tekla.MODEL.numbered = False
        fake_tekla.MODEL.numbering_calls = 0
        rows = [dict(_beam(), name=f"TB-{i:02d}") for i in range(1, parts + 1)]
        created = ts.tekla_create_parts(json.dumps(rows), confirm=True)
        return ts, created

    def test_dry_run_reports_unnumbered_without_running(self):
        ts, _ = self._fresh()
        res = ts.tekla_run_numbering(confirm=False)
        self.assertTrue(res["dry_run"])
        self.assertEqual(res["before"]["part_count"], 3)
        self.assertEqual(res["before"]["unnumbered_count"], 3)
        self.assertFalse(res["before"]["bom_ready"])
        self.assertEqual(fake_tekla.MODEL.numbering_calls, 0)

    def test_confirm_runs_numbering_and_closes_loop(self):
        ts, _ = self._fresh()
        res = ts.tekla_run_numbering(confirm=True, timeout_seconds=5)
        self.assertFalse(res["dry_run"])
        self.assertTrue(res["launched"])
        self.assertTrue(res["after"]["bom_ready"])
        self.assertEqual(res["after"]["unnumbered_count"], 0)
        self.assertEqual(fake_tekla.MODEL.numbering_calls, 1)

    def test_already_numbered_skips_operation(self):
        ts, _ = self._fresh()
        ts.tekla_run_numbering(confirm=True, timeout_seconds=5)
        res = ts.tekla_run_numbering(confirm=True, timeout_seconds=5)
        self.assertTrue(res.get("already_numbered"))
        self.assertEqual(fake_tekla.MODEL.numbering_calls, 1)

    def test_tool_registered(self):
        import tekla_server as ts
        self.assertIn("tekla_run_numbering", ts.server.tools)


if __name__ == "__main__":
    unittest.main()


class CutPlaneHelperTests(unittest.TestCase):
    """平面切割辅助函数：轮廓点必须是 Tekla Point 且落在给定平面上。"""

    def test_cut_plate_points_on_plane(self):
        import tekla_server as ts

        pts = ts._cut_plate_points((0.0, 0.0, 3058.0), (0.0, 0.0, 1.0), 8000.0, 0.0)
        self.assertEqual(len(pts), 4)
        for p in pts:
            self.assertTrue(hasattr(p, "X"), "轮廓点必须是 Tekla Point 对象")
            self.assertAlmostEqual(p.Z, 3058.0, places=6)
        # 方形轮廓：对角线长度 = 2*sqrt(2)*half_size
        import math
        diag = math.dist((pts[0].X, pts[0].Y), (pts[2].X, pts[2].Y))
        self.assertAlmostEqual(diag, 2 * math.sqrt(2) * 8000.0, places=1)

    def test_cut_plate_points_offset_along_normal(self):
        import tekla_server as ts

        pts = ts._cut_plate_points((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), 100.0, -400.0)
        for p in pts:
            self.assertAlmostEqual(p.Z, -400.0, places=6)

    def test_vector_helpers(self):
        import tekla_server as ts

        self.assertAlmostEqual(ts._dot((1, 2, 3), (4, 5, 6)), 32.0)
        n = ts._unit((0.0, 0.0, 5.0))
        self.assertAlmostEqual(n[2], 1.0)
        c = ts._cross((1.0, 0.0, 0.0), (0.0, 1.0, 0.0))
        self.assertAlmostEqual(c[2], 1.0)


class BooleanTypeEnumTests(unittest.TestCase):
    """BooleanTypeEnum 的成员名随版本变化，选择逻辑必须容忍 CUT/DIFFERENCE 等命名。"""

    def test_enum_names_filters_dunders(self):
        import tekla_server as ts

        class FakeEnum:
            CUT_PART = 1
            ADD = 2
            __members__ = {"CUT_PART": 1}

        self.assertEqual(ts._enum_names(FakeEnum), ["ADD", "CUT_PART"])

    def test_cut_type_prefers_cut(self):
        import tekla_server as ts

        class FakeEnum:
            CUT = 1
            ADD = 2

        self.assertEqual(ts._boolean_cut_type(FakeEnum), 1)

    def test_cut_type_falls_back_to_keyword(self):
        import tekla_server as ts

        class FakeEnum:
            JOIN = 1
            DIFFERENCE = 2

        self.assertEqual(ts._boolean_cut_type(FakeEnum), 2)

    def test_cut_type_returns_none_when_missing(self):
        import tekla_server as ts

        class FakeEnum:
            UNION = 1
            INTERSECT = 2

        self.assertIsNone(ts._boolean_cut_type(FakeEnum))
