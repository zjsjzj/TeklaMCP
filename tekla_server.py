"""
Tekla Structures MCP Server
===========================
用 Python + pythonnet + MCP 把 Tekla Open API 暴露成 MCP 工具。

已在 Tekla Structures 2026.0 上实测通过
（Python 3.13.14 / pythonnet 3.2.1 / mcp 2.3.0 / openpyxl 3.1.5）

工具清单
--------
建模类（v2.1.0）
  tekla_create_parts           批量创建梁/柱/折梁/多边形板等构件
  tekla_create_components      插入 Tekla 组件（连接/节点）把构件连起来
  tekla_modify_parts           批量改规格/材质/等级/名称/表面处理/位置
  tekla_delete_objects         按 GUID 删除对象（需 confirm=True）

查询类
  tekla_check_connection        检查是否连上 Tekla
  tekla_get_model_info          模型信息 + 对象数量统计
  tekla_list_phases             列出阶段
  tekla_find_parts              按规格/材质/名称筛选构件
  tekla_get_selected_objects    读取当前选中对象属性

清单与统计
  tekla_export_bom              导出构件清单（CSV / Excel）
  tekla_summarize               按 Class / 阶段 / 材质 / 规格 / 名称 汇总工程量

UDA 读写
  tekla_read_udas               批量读取自定义属性
  tekla_write_udas              批量写入自定义属性（需 confirm=True）

选择集
  tekla_select_by_class         按 Class 号批量选中

━━━ 五条实测结论，别改，改了就连不上 ━━━

1. Tekla 2026 主程序是 .NET Framework 4.8 宿主 → 必须用 pythonnet 的 netfx
   运行时。设成 coreclr 会加载失败。

2. 程序集搜索路径必须把 Net48Runtime 排在 bin 前面。bin 里没有
   Tekla.Structures.dll 和 Tekla.Structures.Geometry3d.dll，它们只在
   Net48Runtime 里。顺序错了报 No module named 'Tekla'。

3. 不需要任何「允许外部程序访问」开关 —— Tekla 2025+ 没有这个设置。
   能连上的条件只有：Tekla 在运行 + 已打开模型 + 双方都是 64 位。

4. Tekla 2026 删了 Tekla.Structures.Model.UI.Selector。
   改用 ModelObjectSelector：GetSelectedObjects() / Select(ArrayList)。

5. 2026 的 API 差异（都实测过）：
   - ModelObjectEnum 没有 PART，要遍历 GetAllObjects() 再 isinstance(o, Part)
   - Part 没有 Weight 属性，重量走 GetReportProperty("WEIGHT", System.Double(0.0))
   - Part 没有 Phase 属性，用 GetPhase(out Phase)
   - Phase 没有 GetAllPhases()，用 Model.GetPhases()
   - Profile / Material 是对象，取字符串用 .ProfileString / .MaterialString
   - 所有 out 参数在 pythonnet 里都变成返回值元组 (是否成功, 值)

stdout 是 MCP 协议通道，任何 print() 都会破坏协议，日志一律走 stderr。
"""

import csv
import datetime as _dt
import functools
import json
import os
import sys
import logging
import threading
import time

# ---------------------------------------------------------------------------
# 0. 日志：必须在 import clr 之前重定向到 stderr
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=os.environ.get("TEKLA_MCP_LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("tekla-mcp")

# ---------------------------------------------------------------------------
# 1. Tekla Open API 程序集定位
# ---------------------------------------------------------------------------
def _detect_tekla_bin():
    """定位 Tekla 的 bin 目录。

    优先级：环境变量 TEKLA_BIN > 自动扫描安装目录（取版本号最高的、
    且存在 Net48Runtime 的那个）。这样同一个包在多版本环境下也能用。
    """
    env = os.environ.get("TEKLA_BIN", "").strip()
    if env and os.path.isdir(env):
        return env

    # Tekla 可能装在任意盘（本机就遇到过从 C 盘搬到 D 盘的情况），
    # 所以把「每个盘符下的常见安装位置」都扫一遍。
    roots = []
    import string
    for drive in string.ascii_uppercase:
        for base in (
            rf"{drive}:\Program Files\Tekla Structures",
            rf"{drive}:\Program Files (x86)\Tekla Structures",
        ):
            if os.path.isdir(base):
                roots.append(base)
    candidates = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for name in os.listdir(root):
            bin_dir = os.path.join(root, name, "bin")
            if not os.path.isdir(bin_dir):
                continue
            # 只有带 Net48Runtime 的安装才能被 pythonnet/netfx 加载
            if not os.path.isdir(os.path.join(bin_dir, "Net48Runtime")):
                continue
            try:
                key = tuple(int(p) for p in name.split("."))
            except ValueError:
                key = (0,)
            candidates.append((key, bin_dir))

    if candidates:
        candidates.sort(reverse=True)
        return candidates[0][1]
    return ""


TEKLA_BIN = _detect_tekla_bin()
TEKLA_NET48 = os.path.join(TEKLA_BIN, "Net48Runtime") if TEKLA_BIN else ""

# 顺序敏感：Net48Runtime 必须在 bin 之前
_ASSEMBLY_PATHS = [p for p in (TEKLA_NET48, TEKLA_BIN) if p]
if not TEKLA_BIN:
    log.error(
        "未找到 Tekla 安装目录。请设置环境变量 TEKLA_BIN 指向 "
        r"<Tekla 安装目录>\bin"
    )
else:
    log.info("Tekla bin = %s", TEKLA_BIN)
for _p in reversed(_ASSEMBLY_PATHS):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)
os.environ["PATH"] = os.pathsep.join(
    [p for p in _ASSEMBLY_PATHS if os.path.isdir(p)]
    + [os.environ.get("PATH", "")]
)

os.environ["PYTHONNET_RUNTIME"] = "netfx"

import clr  # noqa: E402

_API_READY = False
_IMPORT_ERROR = None
try:
    clr.AddReference("Tekla.Structures")
    clr.AddReference("Tekla.Structures.Model")

    import System  # noqa: E402
    import System.Collections  # noqa: E402

    from Tekla.Structures.Model import Model, ModelObject, Part  # noqa: E402,F401
    from Tekla.Structures.Model.UI import ModelObjectSelector  # noqa: E402,F401
    from Tekla.Structures.Geometry3d import Point  # noqa: E402,F401

    _API_READY = True
    log.info("Tekla Open API 加载成功 (runtime=netfx)")
except Exception as exc:  # pragma: no cover
    _IMPORT_ERROR = f"{type(exc).__name__}: {exc}"
    log.error("Tekla Open API 加载失败: %s", _IMPORT_ERROR)

# ---------------------------------------------------------------------------
# 2. MCP 服务端（兼容 mcp 1.x 的 FastMCP 与 2.x 的 MCPServer）
# ---------------------------------------------------------------------------
try:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server
except ImportError:  # mcp 2.x
    from mcp.server.mcpserver import MCPServer as _Server

__version__ = "2.2.0"
try:
    server = _Server("TeklaStructures", version=__version__)
except TypeError:  # 老版本 MCP SDK 没有 version 参数
    server = _Server("TeklaStructures")

_MODEL = None
# Tekla Open API 不是并发安全的。MCP 客户端可能并行调用工具，所有入口统一串行化，
# 防止一个工具遍历模型时另一个工具同时提交修改。
_TEKLA_LOCK = threading.RLock()
_EXPORT_DIR = os.environ.get(
    "TEKLA_MCP_EXPORT_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "export"),
)


# ---------------------------------------------------------------------------
# 内部工具函数
# ---------------------------------------------------------------------------
def _synchronized(fn):
    """串行执行 Tekla 调用，并记录慢操作耗时（只写 stderr）。"""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        started = time.perf_counter()
        with _TEKLA_LOCK:
            try:
                return fn(*args, **kwargs)
            finally:
                elapsed = time.perf_counter() - started
                if elapsed >= 1.0:
                    log.info("%s 用时 %.2fs", fn.__name__, elapsed)
                else:
                    log.debug("%s 用时 %.3fs", fn.__name__, elapsed)
    return wrapper


def _require_api():
    if not _API_READY:
        raise RuntimeError(
            f"Tekla Open API 未加载成功（{_IMPORT_ERROR}）。"
            f"请确认 {TEKLA_BIN} 下存在 Net48Runtime 子目录，且 Python 为 64 位。"
        )


def _model():
    """惰性创建 Model，避免 import 阶段就连接 Tekla。"""
    global _MODEL
    _require_api()
    if _MODEL is None:
        _MODEL = Model()
    return _MODEL


def _safe(obj, name, default=None):
    try:
        return getattr(obj, name, default)
    except Exception:
        return default


def _report_str(obj, name):
    """读取字符串型报表属性，如 PROFILE / MATERIAL / ASSEMBLY_POS。"""
    try:
        res = obj.GetReportProperty(name, System.String(""))
        if isinstance(res, tuple) and len(res) >= 2 and res[0]:
            return str(res[1])
    except Exception:
        pass
    return None


def _report_num(obj, name):
    """读取数值型报表属性，如 WEIGHT / AREA / VOLUME / LENGTH。"""
    try:
        res = obj.GetReportProperty(name, System.Double(0.0))
        if isinstance(res, tuple) and len(res) >= 2 and res[0]:
            return float(res[1])
    except Exception:
        pass
    return None


def _phase_of(obj):
    """Part 没有 Phase 属性，只能用 GetPhase(out Phase)。"""
    try:
        res = obj.GetPhase()
        if isinstance(res, tuple) and len(res) >= 2 and res[0]:
            ph = res[1]
            return {
                "number": _safe(ph, "PhaseNumber"),
                "name": _safe(ph, "PhaseName"),
            }
    except Exception:
        pass
    return None


def _identifier_of(obj):
    ident = _safe(obj, "Identifier")
    if ident is None:
        getter = _safe(obj, "GetIdentifier")
        if callable(getter):
            try:
                ident = getter()
            except Exception:
                ident = None
    return ident


def _guid_of(obj):
    """模型对象返回真正的 GUID；2026 公开 Drawing 没有此属性，图纸用 Mark。"""
    guid = _safe(_identifier_of(obj), "GUID")
    return str(guid) if guid is not None else ""


def _object_id(obj):
    value = _safe(_identifier_of(obj), "ID")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _point_row(point):
    if point is None:
        return None
    try:
        return {"x": round(float(point.X), 3),
                "y": round(float(point.Y), 3),
                "z": round(float(point.Z), 3)}
    except Exception:
        return None


def _split_names(value):
    return [x.strip() for x in (value or "").split(",") if x.strip()]


def _objects_from_guids(guids):
    """按 GUID 精确取对象，分别报告格式错误和模型中不存在。"""
    objects, invalid, not_found = [], [], []
    for text in _split_names(guids):
        try:
            guid = System.Guid(text)
        except Exception:
            invalid.append(text)
            continue
        try:
            # Tekla 2026：SelectModelObject 只收 Identifier，不能直接传 Guid。
            identifier = _model().GetIdentifierByGUID(str(guid))
            obj = (_model().SelectModelObject(identifier)
                   if identifier is not None else None)
        except Exception:
            obj = None
        if obj is None:
            not_found.append(text)
        else:
            objects.append(obj)
    return objects, invalid, not_found


def _part_matches(obj, profile="", material="", name="", class_number="",
                  phase="", finish="", part_type=""):
    """真正的组合过滤；参考项目的恒真 _matches_filter 不可照搬。"""
    checks = (
        (profile, _report_str(obj, "PROFILE") or "", "contains"),
        (material, _report_str(obj, "MATERIAL") or "", "contains"),
        (name, _report_str(obj, "NAME") or str(_safe(obj, "Name", "")), "contains"),
        (finish, str(_safe(obj, "Finish", "") or ""), "contains"),
        (class_number, str(_safe(obj, "Class", "") or ""), "exact"),
        (part_type, type(obj).__name__, "exact"),
    )
    for expected, actual, mode in checks:
        if not expected:
            continue
        if mode == "exact":
            if actual.casefold() != str(expected).casefold():
                return False
        elif str(expected).casefold() not in actual.casefold():
            return False
    if phase:
        ph = _phase_of(obj)
        number = str(ph.get("number", "")) if ph else ""
        ph_name = str(ph.get("name", "")) if ph else ""
        expected = str(phase).casefold()
        if expected != number.casefold() and expected not in ph_name.casefold():
            return False
    return True


def _matching_parts(profile="", material="", name="", class_number="",
                    phase="", finish="", part_type=""):
    return [o for o in _iter_parts()
            if _part_matches(o, profile, material, name, class_number,
                             phase, finish, part_type)]


def _get_uda(obj, name):
    """UDA 有三重重载，pythonnet 下按类型逐个试。"""
    fallback = None
    for placeholder in (System.String(""), System.Double(0.0), System.Int32(0)):
        try:
            res = obj.GetUserProperty(name, placeholder)
        except Exception:
            continue
        if not (isinstance(res, tuple) and len(res) >= 2 and res[0]):
            continue
        val = res[1]
        if isinstance(val, str):
            if val != "":
                return val
            fallback = "" if fallback is None else fallback
        else:
            return val
    return fallback


def _set_uda(obj, name, value, value_type="auto"):
    """写入 UDA。

    auto 根据当前值推断类型；当前值为空时保持旧行为，按字符串写入。
    数值型 UDA 必须可显式指定 double / int，避免旧版始终先命中 string 重载。
    """
    kind = (value_type or "auto").lower()
    if kind == "auto":
        current = _get_uda(obj, name)
        if isinstance(current, float):
            kind = "double"
        elif isinstance(current, int):
            kind = "int"
        else:
            kind = "string"
    converters = {"string": str, "double": float, "int": int}
    if kind not in converters:
        raise ValueError("value_type 只能是 auto / string / double / int")
    converted = converters[kind](value)
    return bool(obj.SetUserProperty(name, converted))


def _part_row(obj, uda_names=()):
    """把一个 Part 展开成一行清单数据。"""
    row = {
        "零件编号": _report_str(obj, "PART_POS") or "",
        "构件编号": _report_str(obj, "ASSEMBLY_POS") or "",
        "名称": _report_str(obj, "NAME") or str(_safe(obj, "Name", "")),
        "规格": _report_str(obj, "PROFILE") or "",
        "材质": _report_str(obj, "MATERIAL") or "",
        "等级": str(_safe(obj, "Class", "")),
    }
    ph = _phase_of(obj)
    row["阶段"] = f"{ph['number']}-{ph['name']}" if ph else ""

    length = _report_num(obj, "LENGTH")
    area = _report_num(obj, "AREA")
    weight = _report_num(obj, "WEIGHT")
    row["长度(mm)"] = round(length, 1) if length is not None else ""
    row["面积(m²)"] = round(area / 1e6, 4) if area is not None else ""
    row["重量(kg)"] = round(weight, 3) if weight is not None else ""
    row["类型"] = type(obj).__name__
    row["ID"] = _object_id(obj)
    row["GUID"] = _guid_of(obj)

    for u in uda_names:
        v = _get_uda(obj, u)
        row[f"UDA:{u}"] = "" if v is None else v
    return row


def _iter_parts():
    """遍历模型中的所有 Part（2026 的枚举里没有 PART，只能按类型过滤）。"""
    m = _model()
    en = m.GetModelObjectSelector().GetAllObjects()
    while en.MoveNext():
        obj = en.Current
        if isinstance(obj, Part):
            yield obj


def _to_arraylist(objs):
    al = System.Collections.ArrayList()
    for o in objs:
        al.Add(o)
    return al


def _targets(scope, class_number=""):
    """按 scope 取出要操作的对象列表。"""
    scope = (scope or "selected").lower()
    if scope == "selected":
        en = ModelObjectSelector().GetSelectedObjects()
        out = []
        while en.MoveNext():
            out.append(en.Current)
        return out
    if scope == "class":
        return [p for p in _iter_parts()
                if str(_safe(p, "Class", "")) == str(class_number)]
    if scope == "all":
        return list(_iter_parts())
    raise ValueError(f"未知 scope: {scope}，可选 selected / class / all")


def _ensure_export_dir():
    os.makedirs(_EXPORT_DIR, exist_ok=True)
    return _EXPORT_DIR


def _default_filename(prefix, ext):
    ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(_ensure_export_dir(), f"{prefix}_{ts}.{ext}")


def _safe_filename(value):
    text = "".join(c if c not in '\\/:*?"<>|' else "_" for c in str(value))
    return text.strip(" .") or "drawing"


def _available_path(path, overwrite=False):
    """避免同名图纸或重复导出静默覆盖已有文件。"""
    if overwrite or not os.path.exists(path):
        return path
    root, ext = os.path.splitext(path)
    for i in range(2, 10000):
        candidate = f"{root}_{i}{ext}"
        if not os.path.exists(candidate):
            return candidate
    raise RuntimeError(f"无法为 {path} 生成不冲突的文件名")


def _write_csv(rows, path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    headers = list(rows[0].keys()) if rows else []
    # utf-8-sig 保证 Excel 打开中文不乱码
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        w.writerows(rows)
    return path


def _write_xlsx(rows, path, sheet_name="构件清单"):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name[:31]

    headers = list(rows[0].keys()) if rows else []
    ws.append(headers)

    fill = PatternFill("solid", fgColor="1F4E79")
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF", size=10)
        c.fill = fill
        c.alignment = Alignment(horizontal="center", vertical="center")

    for r in rows:
        ws.append([r.get(h, "") for h in headers])

    ws.freeze_panes = "A2"
    if rows:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{len(rows) + 1}"

    for i, h in enumerate(headers, start=1):
        width = max(10, min(28, len(str(h)) * 2 + 6))
        ws.column_dimensions[get_column_letter(i)].width = width

    wb.save(path)
    return path


# ---------------------------------------------------------------------------
# 图纸辅助
# ---------------------------------------------------------------------------
def _drawing_handler():
    clr.AddReference("Tekla.Structures.Drawing")
    from Tekla.Structures.Drawing import DrawingHandler

    dh = DrawingHandler()
    if not dh.GetConnectionStatus():
        raise RuntimeError("DrawingHandler 连接失败；请确认 Tekla 正在运行且已打开模型")
    return dh


def _iter_drawings():
    dh = _drawing_handler()
    en = dh.GetDrawings()
    while en.MoveNext():
        yield en.Current


def _all_drawings():
    return list(_iter_drawings())


def _drawing_key(d):
    """2026 公开 Drawing API 没有 GUID，使用公开的 Mark 作为首选键。"""
    return str(_safe(d, "Mark") or "")


def _same_drawing(a, b):
    try:
        return bool(a.IsSameDatabaseObject(b))
    except Exception:
        return a is b


def _find_drawing(name="", index=-1, mark=""):
    """按 Mark、序号或名字定位图纸，优先级依次降低。

    Mark 是公开 API 提供的业务键；index 只适合当前列表快照；名称可能大量重复。
    """
    items = _all_drawings()
    if mark:
        expected = str(mark).casefold()
        for d in items:
            if _drawing_key(d).casefold() == expected:
                return d
        return None
    if index is not None and int(index) >= 0:
        i = int(index)
        return items[i] if i < len(items) else None
    if name:
        for d in items:
            if str(_safe(d, "Name") or "") == str(name):
                return d
    return None


def _drawing_row(d, index=None):
    title = _safe(d, "Title1")
    row = {
        "name": str(_safe(d, "Name") or ""),
        "mark": str(_safe(d, "Mark") or ""),
        "title": str(title).strip() if title is not None else "",
        "title2": str(_safe(d, "Title2") or ""),
        "title3": str(_safe(d, "Title3") or ""),
        "type": str(_safe(d, "DrawingTypeStr") or ""),
        "drawing_class": type(d).__name__,
        "created": str(_safe(d, "CreationDate") or ""),
        "modified": str(_safe(d, "ModificationDate") or ""),
        "issued": bool(_safe(d, "IsIssued", False)),
        "locked": bool(_safe(d, "IsLocked", False)),
        "frozen": bool(_safe(d, "IsFrozen", False)),
        "ready_for_issue": bool(_safe(d, "IsReadyForIssue", False)),
        "up_to_date": str(_safe(d, "UpToDateStatus") or ""),
    }
    source = _safe(d, "PartIdentifier")
    if source is None:
        source = _safe(d, "AssemblyIdentifier")
    if source is not None:
        row["source_guid"] = str(_safe(source, "GUID") or "")
        row["source_id"] = _safe(source, "ID")
    if index is not None:
        row["index"] = index
    return row


# ---------------------------------------------------------------------------
# 建模辅助（创建构件 / 组件）
# ---------------------------------------------------------------------------
MAX_CREATE_PARTS = 50
MAX_CREATE_COMPONENTS = 20

# 构件类型：中文别名与 svMCP 的文件后缀习惯都接受，统一映射到公开 API 类。
_PART_KIND_ALIASES = {
    "beam": "beam", "梁": "beam", "钢梁": "beam", "prt": "beam", "crs": "beam",
    "column": "column", "柱": "column", "钢柱": "column", "clm": "column",
    "panel": "panel", "墙板": "panel", "cpn": "panel",
    "pad_footing": "pad_footing", "独立基础": "pad_footing", "cpf": "pad_footing",
    "strip_footing": "strip_footing", "条形基础": "strip_footing", "csf": "strip_footing",
    "polybeam": "polybeam", "折梁": "polybeam", "ply": "polybeam",
    "contour_plate": "contour_plate", "板": "contour_plate", "钢板": "contour_plate",
    "多边形板": "contour_plate", "cpl": "contour_plate",
    "spiral": "spiral", "spiralbeam": "spiral", "螺旋梁": "spiral",
    "螺旋": "spiral", "helix": "spiral", "spr": "spiral",
}
_PART_KINDS = {
    "beam": ("beam", "BEAM"),
    "column": ("beam", "COLUMN"),
    "panel": ("beam", "PANEL"),
    "pad_footing": ("beam", "PAD_FOOTING"),
    "strip_footing": ("beam", "STRIP_FOOTING"),
    "polybeam": ("polybeam", None),
    "contour_plate": ("contour_plate", None),
    "spiral": ("spiral", None),
}
_PLANE_ENUM = {"middle": "MIDDLE", "left": "LEFT", "right": "RIGHT"}
_ROTATION_ENUM = {"front": "FRONT", "top": "TOP", "back": "BACK", "bottom": "BOTTOM"}
_DEPTH_ENUM = {"middle": "MIDDLE", "front": "FRONT", "behind": "BEHIND"}


def _load_json_items(raw, label, limit):
    """接受 JSON 字符串或已解析的列表，并校验批量上限与元素类型。"""
    if isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        text = str(raw or "").strip()
        if not text:
            raise ValueError(f"{label} 不能为空，需要 JSON 数组")
        try:
            items = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{label} 不是合法 JSON: {exc}") from exc
    if not isinstance(items, list) or not items:
        raise ValueError(f"{label} 必须是非空 JSON 数组")
    if len(items) > limit:
        raise ValueError(f"{label} 单次最多 {limit} 条，收到 {len(items)} 条")
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"{label} 第 {i} 条必须是 JSON 对象")
    return items


def _as_float(value, label):
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} 需要数字，收到 {value!r}") from exc


def _parse_point(value, label):
    """解析坐标，支持 "x,y,z"、"(x,y,z)"、[x,y,z]、{"x":..,"y":..,"z":..}。"""
    if isinstance(value, dict):
        coords = [value.get("x"), value.get("y"), value.get("z")]
    elif isinstance(value, str):
        text = value.strip().strip("()[]")
        coords = [p for p in text.replace(";", ",").replace(" ", ",").split(",")
                  if p.strip()]
    elif isinstance(value, (list, tuple)):
        coords = list(value)
    else:
        raise ValueError(f"{label} 坐标格式无法识别: {value!r}")
    if len(coords) != 3 or any(c is None or c == "" for c in coords):
        raise ValueError(f"{label} 需要 3 个坐标 (x,y,z)，收到: {value!r}")
    return Point(_as_float(coords[0], f"{label}.x"),
                 _as_float(coords[1], f"{label}.y"),
                 _as_float(coords[2], f"{label}.z"))


def _parse_points(values, label, minimum):
    if isinstance(values, str):
        try:
            values = json.loads(values)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{label} 需要坐标数组，例如 "
                             f"[[0,0,0],[6000,0,0]]（{exc}）") from exc
    if not isinstance(values, (list, tuple)) or len(values) < minimum:
        raise ValueError(f"{label} 至少需要 {minimum} 个坐标点")
    return [_parse_point(v, f"{label}[{i}]") for i, v in enumerate(values)]


def _enum_member(enum_type, text, label, mapping):
    key = str(text).strip().lower()
    if key not in mapping:
        raise ValueError(f"{label} 只能是 {' / '.join(mapping)}，收到 {text!r}")
    return getattr(enum_type, mapping[key])


# 螺旋梁属性候选名（不同 Tekla 版本命名略有差异，逐个尝试）
_SPIRAL_PROP_CANDIDATES = {
    "radius": ("Radius", "Radius2", "R"),
    "turns": ("Turns", "NumberOfTurns", "TurnCount", "Rotations"),
    "pitch": ("Pitch", "Height", "Rise", "TotalRise", "HeightPerTurn"),
    "axis": ("RotationAxis", "AxisLine", "Axis", "CenterAxis", "RotationAxisLine"),
    "direction": ("RotationDirection", "Direction", "Clockwise", "IsClockwise"),
    "start_angle": ("StartAngle", "Angle", "StartRotation"),
}


def _spiral_beam_class():
    """取 SpiralBeam 类；老版本/未安装时给出可读错误。"""
    try:
        from Tekla.Structures.Model import SpiralBeam
        return SpiralBeam
    except Exception as exc:  # pragma: no cover - 依赖真实 Tekla
        try:
            import Tekla.Structures.Model as _m
            return getattr(_m, "SpiralBeam")
        except Exception:
            raise RuntimeError(
                "当前 Open API 没有 SpiralBeam 类型（需 Tekla 2016+）："
                f"{type(exc).__name__}: {exc}") from exc


def _apply_spiral_parameters(part, spec, label):
    """把 radius / turns / pitch / axis / direction 写入 SpiralBeam，返回实际生效项。"""
    applied, missing = {}, []
    for key, names in _SPIRAL_PROP_CANDIDATES.items():
        if spec.get(key) in (None, ""):
            continue
        value = spec[key]
        done = False
        for name in names:
            if hasattr(part, name):
                try:
                    if key == "axis":
                        value = _parse_point(value, f"{label}.axis")
                    elif key == "direction":
                        value = bool(value) if isinstance(value, bool) else (
                            str(value).lower() in ("1", "true", "cw", "clockwise", "顺", "顺时针"))
                    elif key in ("radius", "turns", "pitch", "start_angle"):
                        value = _as_float(value, f"{label}.{key}")
                    setattr(part, name, value)
                    applied[key] = name
                    done = True
                    break
                except Exception:
                    continue
        if not done:
            missing.append(key)
    return applied, missing


def _new_part(part_type):
    """按类型创建公开 API 的 Part 对象（不插入模型）。"""
    from Tekla.Structures.Model import Beam, ContourPlate, PolyBeam

    raw = str(part_type or "beam").strip().lower()
    kind = _PART_KIND_ALIASES.get(raw, raw)
    if kind not in _PART_KINDS:
        raise ValueError(
            f"不支持的构件类型 {part_type!r}，可用: {', '.join(sorted(_PART_KINDS))}"
            "（也接受中文：梁/柱/板/折梁/独立基础/条形基础）")
    family, beam_type = _PART_KINDS[kind]
    if family == "beam":
        enum = getattr(Beam.BeamTypeEnum, beam_type or "BEAM")
        try:
            return Beam(enum), kind
        except Exception:  # 老版本构造函数可能不接收类型
            part = Beam()
            part.Type = enum
            return part, kind
    if family == "spiral":
        return _spiral_beam_class()(), kind
    return (PolyBeam() if family == "polybeam" else ContourPlate()), kind


def _apply_part_attributes(part, spec):
    """写入规格/材质/等级/名称/表面处理；只覆盖明确给出的字段。"""
    profile = str(spec.get("profile") or "").strip()
    material = str(spec.get("material") or "").strip()
    part_class = str(spec.get("class") or "").strip()
    name = str(spec.get("name") or "").strip()
    finish = str(spec.get("finish") or "").strip()
    if profile:
        part.Profile.ProfileString = profile
    if material:
        part.Material.MaterialString = material
    if part_class:
        part.Class = part_class
    if name:
        part.Name = name
    if finish:
        part.Finish = finish
    return {"profile": profile, "material": material, "class": part_class,
            "name": name, "finish": finish}


def _apply_part_position(part, spec):
    """应用位置属性（平面/旋转/深度 + 偏移，单位 mm）。"""
    from Tekla.Structures.Model import Position

    position = part.Position
    for key, attr, mapping in (("plane", "Plane", _PLANE_ENUM),
                               ("rotation", "Rotation", _ROTATION_ENUM),
                               ("depth", "Depth", _DEPTH_ENUM)):
        if spec.get(key) not in (None, ""):
            setattr(position, attr,
                    _enum_member(getattr(Position, f"{attr}Enum"),
                                 spec[key], key, mapping))
    for key, attr in (("plane_offset", "PlaneOffset"),
                      ("rotation_offset", "RotationOffset"),
                      ("depth_offset", "DepthOffset")):
        if spec.get(key) not in (None, ""):
            setattr(position, attr, _as_float(spec[key], key))


def _contour_point(point):
    """兼容不同版本的 ContourPoint 构造函数。

    2026 文档示例是 ContourPoint(Point, Chamfer)，且 Chamfer 允许为 null；
    个别版本只提供单参数重载，这里逐个尝试，避免版本差异直接报错。
    """
    from Tekla.Structures.Model import Chamfer, ContourPoint

    for args in ((point, None), (point,), (point, Chamfer())):
        try:
            return ContourPoint(*args)
        except Exception:
            continue
    raise RuntimeError("无法创建 ContourPoint：当前 Tekla 版本的构造函数不兼容")


def _add_contour_points(part, points):
    """PolyBeam / ContourPlate 的轮廓点；AddContourPoint 不存在时退回 Contour 属性。"""
    contour_points = [_contour_point(point) for point in points]
    added = 0
    for contour_point in contour_points:
        try:
            part.AddContourPoint(contour_point)
        except Exception:
            contour = part.Contour
            for cp in contour_points:
                contour.AddContourPoint(cp)
            part.Contour = contour
            return len(contour_points)
        added += 1
    return added


def _build_part(spec, label):
    """按一份 spec 组装 Part 对象：几何 + 属性 + 位置。不插入模型。"""
    part, kind = _new_part(spec.get("type"))
    if not str(spec.get("profile") or "").strip():
        raise ValueError(f"{label} 缺少 profile（例如 HEA200 / PL10 / RHS300*200*6）")
    if kind in ("polybeam", "contour_plate"):
        minimum = 2 if kind == "polybeam" else 3
        points = _parse_points(spec.get("points"), f"{label}.points", minimum)
        _add_contour_points(part, points)
    else:
        if spec.get("start") in (None, "") or spec.get("end") in (None, ""):
            raise ValueError(f"{label} 需要 start 与 end 坐标")
        part.StartPoint = _parse_point(spec.get("start"), f"{label}.start")
        part.EndPoint = _parse_point(spec.get("end"), f"{label}.end")
    attributes = _apply_part_attributes(part, spec)
    _apply_part_position(part, spec)
    if kind == "spiral":
        applied, missing = _apply_spiral_parameters(part, spec, label)
        attributes["spiral"] = applied
        if missing:
            attributes["spiral_missing"] = missing
    return part, kind, attributes


def _part_preview(spec, kind, attributes):
    preview = {"type": kind}
    if kind in ("polybeam", "contour_plate"):
        preview["points"] = spec.get("points")
    else:
        preview["start"] = spec.get("start")
        preview["end"] = spec.get("end")
    preview.update({k: v for k, v in attributes.items() if v})
    for key in ("radius", "turns", "pitch", "axis", "direction", "start_angle"):
        if spec.get(key) not in (None, ""):
            preview[key] = spec[key]
    for key in ("plane", "plane_offset", "rotation", "rotation_offset",
                "depth", "depth_offset"):
        if spec.get(key) not in (None, ""):
            preview[key] = spec[key]
    return preview


# ---------------------------------------------------------------------------
# 查询类工具
# ---------------------------------------------------------------------------
@server.tool()
@_synchronized
def tekla_check_connection() -> dict:
    """检查当前是否已连接到运行中的 Tekla Structures 模型。"""
    try:
        m = _model()
        connected = bool(m.GetConnectionStatus())
        result = {"connected": connected, "tekla_bin": TEKLA_BIN}
        if connected:
            info = m.GetInfo()
            result["model_name"] = _safe(info, "ModelName")
            result["model_path"] = _safe(info, "ModelPath")
            result["message"] = "已连接到 Tekla 模型"
        else:
            result["message"] = "Tekla 未打开模型"
        return result
    except Exception as exc:
        return {"connected": False, "error": f"{type(exc).__name__}: {exc}"}


@server.tool()
@_synchronized
def tekla_spiral_api_info(probe: bool = False) -> dict:
    """查看本机 Open API 的 SpiralBeam（螺旋梁）类可用属性。

    用于确认不同 Tekla 版本里螺旋参数的真实属性名（Radius/Turns/Pitch/Axis…）。
    probe=True 时还会尝试创建一个**不插入模型**的临时对象，报告能被赋值的属性。
    不会写模型，可安全反复调用。
    """
    try:
        cls = _spiral_beam_class()
        inst = cls()
        members = sorted(m for m in dir(inst) if not m.startswith("_"))
        has = {key: [n for n in names if hasattr(inst, n)]
               for key, names in _SPIRAL_PROP_CANDIDATES.items()}
        result = {
            "available": True,
            "class": str(cls),
            "has_spiral_properties": has,
            "missing_everywhere": [k for k, v in has.items() if not v],
            "candidate_members": [m for m in members
                                  if any(word in m.lower() for word in
                                         ("spiral", "radius", "turn", "pitch", "axis", "rise", "height", "angle"))],
            "member_count": len(members),
        }
        if probe:
            writes = {}
            for key, names in _SPIRAL_PROP_CANDIDATES.items():
                for name in names:
                    if hasattr(inst, name):
                        try:
                            probe_value = 600.0 if key == "radius" else (
                                0.75 if key == "turns" else (3936.5 if key == "pitch" else
                                (0.0 if key == "start_angle" else
                                 (None if key == "axis" else True))))
                            if probe_value is not None:
                                setattr(inst, name, probe_value)
                            writes[key] = name
                            break
                        except Exception as exc:
                            writes[key] = f"{name} (失败: {type(exc).__name__})"
            result["probe_writes"] = writes
        return result
    except Exception as exc:
        return {"available": False, "error": f"{type(exc).__name__}: {exc}"}


@server.tool()
@_synchronized
def tekla_get_model_info() -> dict:
    """获取当前模型的名称、路径，以及各类对象的数量统计。"""
    try:
        m = _model()
        info = m.GetInfo()
        result = {
            "model_name": _safe(info, "ModelName"),
            "model_path": _safe(info, "ModelPath"),
        }
        counts = {}
        for label, enum_name in (
            ("assembly", "ASSEMBLY"),
            ("bolt", "BOLT_ARRAY"),
            ("weld", "WELD"),
            ("rebar", "REBARGROUP"),
            ("reference_model", "REFERENCE_MODEL"),
        ):
            enum_val = getattr(ModelObject.ModelObjectEnum, enum_name, None)
            if enum_val is None:
                continue
            try:
                en = m.GetModelObjectSelector().GetAllObjectsWithType(enum_val)
                n = 0
                while en.MoveNext():
                    n += 1
                counts[label] = n
            except Exception:
                continue
        try:
            counts["part"] = sum(1 for _ in _iter_parts())
        except Exception:
            pass
        result["object_counts"] = counts
        return {k: v for k, v in result.items() if v is not None}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


@server.tool()
@_synchronized
def tekla_list_phases() -> dict:
    """列出当前模型中的所有阶段(Phase)及其编号。"""
    try:
        phases = _model().GetPhases()
        return {
            "count": len(phases),
            "phases": [
                {"number": _safe(p, "PhaseNumber"), "name": _safe(p, "PhaseName")}
                for p in phases
            ],
        }
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


@server.tool()
@_synchronized
def tekla_find_parts(profile: str = "", material: str = "",
                     name: str = "", class_number: str = "", phase: str = "",
                     finish: str = "", part_type: str = "", offset: int = 0,
                     limit: int = 200) -> dict:
    """组合筛选构件，支持规格/材质/名称/Class/阶段/涂装/类型和分页。

    文本字段为不区分大小写的包含匹配；class_number 和 part_type 为精确匹配。
    返回的 GUID 可传给 tekla_get_object_details 或 tekla_select_by_filter。
    """
    try:
        offset, limit = max(0, int(offset)), max(1, min(int(limit), 2000))
        matches = _matching_parts(profile, material, name, class_number,
                                  phase, finish, part_type)
        page = matches[offset:offset + limit]
        return {
            "count": len(page),
            "total_count": len(matches),
            "offset": offset,
            "limit": limit,
            "parts": [_part_row(o) for o in page],
            "truncated": offset + len(page) < len(matches),
        }
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


@server.tool()
@_synchronized
def tekla_get_selected_objects(offset: int = 0, limit: int = 200) -> dict:
    """获取当前选中对象属性，支持分页；GUID/ID 可用于后续精确定位。"""
    try:
        _require_api()
        offset, limit = max(0, int(offset)), max(1, min(int(limit), 2000))
        selected = _targets("selected")
        page = selected[offset:offset + limit]
        rows = [_part_row(o) for o in page]
        total_weight = sum(r["重量(kg)"] for r in rows
                           if isinstance(r["重量(kg)"], float))
        return {
            "count": len(rows),
            "total_count": len(selected),
            "offset": offset,
            "total_weight_kg": round(total_weight, 2),
            "objects": rows,
            "truncated": offset + len(rows) < len(selected),
            "hint": "选中集为空时，先在 Tekla 里框选，或调用 tekla_select_by_filter",
        }
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------------------
# 选择集
# ---------------------------------------------------------------------------
@server.tool()
@_synchronized
def tekla_select_by_class(class_number: str) -> dict:
    """按 Tekla 的 Class（等级号，如 "1"、"2"、"100"）选中模型中的构件。"""
    try:
        _require_api()
        picked = _targets("class", class_number)
        ok = False
        if picked:
            ok = bool(ModelObjectSelector().Select(_to_arraylist(picked)))
        return {
            "class": str(class_number),
            "selected": len(picked),
            "applied": ok,
            "message": f"已选中 {len(picked)} 个 Class={class_number} 的构件"
                       if picked else f"没有找到 Class={class_number} 的构件",
        }
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


@server.tool()
@_synchronized
def tekla_select_by_filter(guids: str = "", profile: str = "",
                           material: str = "", name: str = "",
                           class_number: str = "", phase: str = "",
                           finish: str = "", part_type: str = "",
                           append: bool = False, limit: int = 1000) -> dict:
    """按 GUID 或组合条件选中对象。写选择集，但不修改构件属性。

    guids 为逗号分隔；提供 guids 时优先使用，其他过滤条件忽略。
    不提供 guids 时至少需要一个过滤条件，防止空调用误选整个模型。
    append=True 保留当前选择并追加；limit 是允许选中的最大对象数。
    """
    try:
        _require_api()
        invalid, not_found = [], []
        if guids:
            objects, invalid, not_found = _objects_from_guids(guids)
        else:
            filters = (profile, material, name, class_number, phase, finish, part_type)
            if not any(str(x).strip() for x in filters):
                return {"error": "请提供 guids 或至少一个过滤条件；拒绝无条件选择全模型"}
            objects = _matching_parts(profile, material, name, class_number,
                                      phase, finish, part_type)

        matched = len(objects)
        if not objects:
            return {"matched": 0, "selected": 0, "applied": False,
                    "invalid_guids": invalid, "not_found_guids": not_found}
        if len(objects) > max(1, int(limit)):
            return {"error": f"匹配 {len(objects)} 个对象，超过 limit={limit}；"
                             "请缩小范围或显式调大 limit",
                    "matched": matched}

        if append:
            existing = _targets("selected")
            seen = {_guid_of(o) for o in existing}
            objects = existing + [o for o in objects if _guid_of(o) not in seen]

        applied = bool(ModelObjectSelector().Select(_to_arraylist(objects)))
        return {
            "matched": matched,
            "selected": len(objects),
            "applied": applied,
            "appended": bool(append),
            "invalid_guids": invalid,
            "not_found_guids": not_found,
            "sample": [{"guid": _guid_of(o), "id": _object_id(o),
                        "name": str(_safe(o, "Name", "")), "type": type(o).__name__}
                       for o in objects[:20]],
        }
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


@server.tool()
@_synchronized
def tekla_get_object_details(guids: str, uda_names: str = "",
                             report_properties: str = "",
                             include_components: bool = True) -> dict:
    """按 GUID 读取任意模型对象的详情、几何、UDA 和报表属性。

    guids 逗号分隔，单次最多 100 个。report_properties 如 "WEIGHT,LENGTH,AREA"，
    返回键以 REPORT: 为前缀；UDA 返回键以 UDA: 为前缀。
    """
    try:
        _require_api()
        if len(_split_names(guids)) > 100:
            return {"error": "单次最多查询 100 个 GUID"}
        objects, invalid, not_found = _objects_from_guids(guids)
        udas = _split_names(uda_names)
        reports = _split_names(report_properties)
        rows = []
        for obj in objects:
            row = (_part_row(obj) if isinstance(obj, Part) else {
                "名称": str(_safe(obj, "Name", "") or ""),
                "类型": type(obj).__name__,
                "ID": _object_id(obj),
                "GUID": _guid_of(obj),
            })
            geometry = {}
            for label, attr in (("start", "StartPoint"), ("end", "EndPoint"),
                                ("position", "Position")):
                point = _point_row(_safe(obj, attr))
                if point is not None:
                    geometry[label] = point
            if geometry:
                row["geometry"] = geometry
            for prop in reports:
                value = _report_str(obj, prop)
                if value is None:
                    value = _report_num(obj, prop)
                row[f"REPORT:{prop}"] = value
            for uda in udas:
                row[f"UDA:{uda}"] = _get_uda(obj, uda)
            if include_components:
                names = []
                try:
                    en = obj.GetComponents()
                    while en.MoveNext() and len(names) < 100:
                        names.append(str(_safe(en.Current, "Name", "") or ""))
                except Exception:
                    pass
                row["components"] = names
            rows.append(row)
        return {"count": len(rows), "objects": rows,
                "invalid_guids": invalid, "not_found_guids": not_found}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


@server.tool()
@_synchronized
def tekla_check_numbering(scope: str = "all", class_number: str = "") -> dict:
    """只读检查编号是否足以支撑 BOM 分组和批量出图，不执行编号。"""
    try:
        _require_api()
        objs = [o for o in _targets(scope, class_number) if isinstance(o, Part)]
        part_pos = [_report_str(o, "PART_POS") or "" for o in objs]
        asm_pos = [_report_str(o, "ASSEMBLY_POS") or "" for o in objs]
        unnumbered = [i for i, (p, a) in enumerate(zip(part_pos, asm_pos))
                      if not p or "?" in p or not a or "?" in a]
        numbered_parts = [p for p in part_pos if p and "?" not in p]
        shared = len(numbered_parts) - len(set(numbered_parts))
        return {
            "part_count": len(objs),
            "unnumbered_count": len(unnumbered),
            "numbered_count": len(objs) - len(unnumbered),
            "bom_ready": bool(objs) and not unnumbered,
            "unique_part_position_count": len(set(numbered_parts)),
            "shared_position_count": shared,
            "sample_unnumbered": [{
                "guid": _guid_of(objs[i]),
                "name": _report_str(objs[i], "NAME") or "",
                "part_pos": part_pos[i], "assembly_pos": asm_pos[i],
            } for i in unnumbered[:20]],
            "recommendation": (
                "当前范围没有构件。" if not objs else
                "编号完整，可按真实零件/构件位置分组；相同零件位置共享一张单件图。"
                if not unnumbered else
                "先在 Tekla 中执行编号(Numbering)，再导出正式 BOM 或批量出图；"
                "未编号时多张单件图会被 Tekla 静默拒绝。"
            ),
        }
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------------------
# 构件清单导出
# ---------------------------------------------------------------------------
@server.tool()
@_synchronized
def tekla_export_bom(format: str = "xlsx", group_by: str = "part",
                     output_path: str = "", uda_names: str = "",
                     scope: str = "all", class_number: str = "") -> dict:
    """导出构件清单(BOM)到 CSV 或 Excel。

    Args:
        format: "xlsx" 或 "csv"
        group_by: "part" 按零件逐行；"assembly" 按构件合并（汇总重量与零件数）
        output_path: 输出路径；留空则写到 tekla-mcp/export/ 下带时间戳
        uda_names: 逗号分隔的 UDA 名，会作为额外列一起导出
        scope: "all" 全模型 / "selected" 当前选中 / "class" 指定 Class
        class_number: scope="class" 时必填
    """
    try:
        _require_api()
        fmt = (format or "xlsx").lower()
        if fmt not in ("xlsx", "csv"):
            return {"error": "format 只能是 xlsx 或 csv"}

        uda_list = [u.strip() for u in (uda_names or "").split(",") if u.strip()]
        objs = _targets(scope, class_number)
        if not objs:
            return {"error": f"scope={scope} 没有取到任何构件"}

        rows = [_part_row(o, uda_list) for o in objs]

        if (group_by or "part").lower() == "assembly":
            groups = {}
            for r in rows:
                key = r["构件编号"] or "(未编号)"
                g = groups.setdefault(key, {
                    "构件编号": key,
                    "名称": r["名称"],
                    "规格": r["规格"],
                    "材质": r["材质"],
                    "等级": r["等级"],
                    "阶段": r["阶段"],
                    "零件数": 0,
                    "总重(kg)": 0.0,
                })
                g["零件数"] += 1
                if isinstance(r["重量(kg)"], float):
                    g["总重(kg)"] += r["重量(kg)"]
                if len(str(r["规格"])) > len(str(g["规格"])):
                    g["规格"] = r["规格"]
            rows = list(groups.values())
            for g in rows:
                g["总重(kg)"] = round(g["总重(kg)"], 3)

        path = output_path or _default_filename("BOM", fmt)
        if fmt == "csv":
            _write_csv(rows, path)
        else:
            _write_xlsx(rows, path)

        total_weight = sum(r.get("重量(kg)", 0) if isinstance(r.get("重量(kg)"), float)
                           else r.get("总重(kg)", 0) for r in rows)
        return {
            "ok": True,
            "path": path,
            "format": fmt,
            "group_by": group_by,
            "row_count": len(rows),
            "total_weight_kg": round(float(total_weight), 2),
            "columns": list(rows[0].keys()) if rows else [],
        }
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------------------
# 工程量统计
# ---------------------------------------------------------------------------
@server.tool()
@_synchronized
def tekla_summarize(group_by: str = "class", sort_by: str = "重量(kg)",
                    top_n: int = 0, scope: str = "all",
                    class_number: str = "") -> dict:
    """按指定字段汇总工程量（数量 / 重量 / 面积 / 长度）。

    Args:
        group_by: class | phase | material | profile | name | assembly
        sort_by: "重量(kg)" | "数量" | "面积(m²)" | "长度(mm)"
        top_n: 只返回前 N 组；0 表示全部
        scope: all / selected / class
    """
    try:
        _require_api()
        key_map = {
            "class": "等级",
            "phase": "阶段",
            "material": "材质",
            "profile": "规格",
            "name": "名称",
            "assembly": "构件编号",
        }
        key = key_map.get((group_by or "class").lower())
        if not key:
            return {"error": f"group_by 只能是 {list(key_map)}"}

        objs = _targets(scope, class_number)
        if not objs:
            return {"error": f"scope={scope} 没有取到任何构件"}

        agg = {}
        for o in objs:
            r = _part_row(o)
            g = agg.setdefault(r[key] or "(空)", {
                key: r[key] or "(空)",
                "数量": 0, "重量(kg)": 0.0, "面积(m²)": 0.0, "长度(mm)": 0.0,
            })
            g["数量"] += 1
            for col in ("重量(kg)", "面积(m²)", "长度(mm)"):
                if isinstance(r[col], float):
                    g[col] += r[col]

        items = list(agg.values())
        for it in items:
            for col in ("重量(kg)", "面积(m²)", "长度(mm)"):
                it[col] = round(it[col], 3)

        # total 必须基于全部组，不能在 top_n 截断后计算。
        total = {
            "数量": len(objs),
            "重量(kg)": round(sum(i["重量(kg)"] for i in items), 2),
            "面积(m²)": round(sum(i["面积(m²)"] for i in items), 2),
            "长度(mm)": round(sum(i["长度(mm)"] for i in items), 2),
        }
        sort_key = sort_by if sort_by in items[0] else "重量(kg)"
        items.sort(key=lambda x: x.get(sort_key, 0), reverse=True)
        returned_groups = len(items) if not top_n or top_n <= 0 else min(len(items), int(top_n))
        if top_n and top_n > 0:
            items = items[:int(top_n)]

        return {
            "group_by": group_by,
            "part_count": len(objs),
            "group_count": len(agg),
            "returned_groups": returned_groups,
            "total": total,
            "items": items,
        }
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------------------
# UDA 读写
# ---------------------------------------------------------------------------
@server.tool()
@_synchronized
def tekla_read_udas(uda_names: str, scope: str = "selected",
                    class_number: str = "", offset: int = 0,
                    limit: int = 200) -> dict:
    """批量读取自定义属性(UDA)。

    Args:
        uda_names: 逗号分隔，如 "USER_FIELD_1,USER_FIELD_2"
        scope: selected / class / all
        class_number: scope="class" 时必填
    """
    try:
        _require_api()
        names = [u.strip() for u in (uda_names or "").split(",") if u.strip()]
        if not names:
            return {"error": "uda_names 不能为空"}

        objs = _targets(scope, class_number)
        offset, limit = max(0, int(offset)), max(1, min(int(limit), 2000))
        page = objs[offset:offset + limit]
        rows = []
        for o in page:
            r = {"零件编号": _report_str(o, "PART_POS") or "",
                 "构件编号": _report_str(o, "ASSEMBLY_POS") or "",
                 "名称": _report_str(o, "NAME") or "",
                 "GUID": _guid_of(o)}
            for n in names:
                v = _get_uda(o, n)
                r[n] = "" if v is None else v
            rows.append(r)

        return {
            "count": len(rows),
            "total_count": len(objs),
            "offset": offset,
            "uda_names": names,
            "rows": rows,
            "truncated": offset + len(rows) < len(objs),
            "note": "空字符串表示该对象上这个 UDA 没有值，或该 UDA 名不存在",
        }
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


@server.tool()
@_synchronized
def tekla_write_udas(uda_name: str, value: str, scope: str = "selected",
                     class_number: str = "", value_type: str = "auto",
                     only_if_empty: bool = False, confirm: bool = False) -> dict:
    """批量写入 UDA，先预览后确认；逐对象 Modify，最后只提交一次。

    value_type: auto（按当前值推断，空值默认 string）/ string / double / int。
    only_if_empty=True 时只写空值，保留已有数据。
    返回逐项失败原因，不再把部分失败压缩成一个总数。
    """
    try:
        _require_api()
        if not (uda_name or "").strip():
            return {"error": "uda_name 不能为空"}
        if (value_type or "auto").lower() not in ("auto", "string", "double", "int"):
            return {"error": "value_type 只能是 auto / string / double / int"}

        objs = _targets(scope, class_number)
        if not objs:
            return {"error": f"scope={scope} 没有取到任何构件"}

        targets, skipped = [], 0
        for o in objs:
            current = _get_uda(o, uda_name)
            if only_if_empty and current not in (None, ""):
                skipped += 1
                continue
            targets.append((o, current))

        sample = [{"guid": _guid_of(o),
                   "零件编号": _report_str(o, "PART_POS") or "",
                   "当前值": current} for o, current in targets[:3]]
        if not confirm:
            return {
                "dry_run": True,
                "would_change": len(targets),
                "skipped_non_empty": skipped,
                "uda_name": uda_name,
                "new_value": value,
                "value_type": value_type,
                "sample_before": sample,
                "message": f"预览：将写入 {len(targets)} 个对象，跳过 {skipped} 个。"
                           "确认后重新调用并传 confirm=True。",
            }

        changed, failures, changed_objects = 0, [], []
        for o, _ in targets:
            guid = _guid_of(o)
            try:
                if not _set_uda(o, uda_name, value, value_type):
                    raise RuntimeError("SetUserProperty 返回 False")
                modifier = _safe(o, "Modify")
                if callable(modifier) and modifier() is False:
                    raise RuntimeError("Modify 返回 False")
                changed += 1
                changed_objects.append(o)
            except Exception as exc:
                if len(failures) < 20:
                    failures.append({"guid": guid,
                                     "error": f"{type(exc).__name__}: {exc}"})

        committed = bool(_model().CommitChanges()) if changed else False
        return {
            "dry_run": False,
            "changed": changed,
            "failed": len(targets) - changed,
            "skipped_non_empty": skipped,
            "committed": committed,
            "uda_name": uda_name,
            "value": value,
            "value_type": value_type,
            "failures": failures,
            "read_back_sample": [{"guid": _guid_of(o),
                                  "value": _get_uda(o, uda_name)}
                                 for o in changed_objects[:3]],
        }
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


@server.tool()
@_synchronized
def tekla_create_parts(parts: str, confirm: bool = False) -> dict:
    """批量创建构件（梁/柱/折梁/多边形板/墙板/基础），一条 JSON 一次建完。

    parts 是 JSON 数组，单次最多 50 条。每条至少包含：
      type       beam(默认) / column / polybeam / contour_plate / panel /
                 pad_footing / strip_footing / spiral（螺旋梁）
                 （也接受 梁/柱/板/折梁/螺旋梁 等中文）
      ★ spiral（螺旋梁）额外参数：radius 半径、turns 圈数 或 pitch 螺距(mm/圈)、
                 axis 旋转轴心点 [x,y,z]（默认原点）、direction 是否顺时针、start_angle 起始角
      profile    规格，如 "HEA200"、"PL10"、"RHS300*200*6"（必填）
      start/end  beam 类构件的起止点："6000,0,0" 或 [6000,0,0]
      points     polybeam / contour_plate 的坐标数组
    可选：material、class、name、finish，
          plane=middle|left|right + plane_offset，
          rotation=front|top|back|bottom + rotation_offset，
          depth=middle|front|behind + depth_offset（偏移单位 mm）。

    默认只做预览；传 confirm=True 才真正插入并 CommitChanges。
    返回每个构件的 GUID，便于后续选图/建模核对。
    """
    try:
        _require_api()
        items = _load_json_items(parts, "parts", MAX_CREATE_PARTS)
        errors, built = [], []
        for i, spec in enumerate(items):
            try:
                part, kind, attributes = _build_part(spec, f"parts[{i}]")
                built.append((i, spec, part, kind, attributes))
            except Exception as exc:
                errors.append({"index": i,
                               "error": f"{type(exc).__name__}: {exc}"})
        result = {
            "dry_run": not confirm,
            "count": len(items),
            "valid": len(built),
            "invalid": len(errors),
            "items": [_part_preview(spec, kind, attrs)
                      for _, spec, _, kind, attrs in built],
            "errors": errors,
        }
        result["would_create"] = len(built)
        if not confirm:
            result["message"] = (
                "预览通过；确认后重新调用并传 confirm=True 才会插入模型。"
                if not errors else
                f"有 {len(errors)} 条参数无效，先修正 errors 后再确认；"
                f"其余 {len(built)} 条可以创建。")
            return result
        if not built:
            result["error"] = "没有可创建的构件，请先修正 errors 中的参数"
            return result

        created = []
        for i, spec, part, kind, attributes in built:
            try:
                if not bool(part.Insert()):
                    raise RuntimeError(
                        "Insert 返回 False：检查 profile/material 是否存在于当前目录、"
                        "坐标是否合法，以及模型是否处于可写状态")
                created.append({"index": i, "type": kind,
                                "guid": _guid_of(part),
                                "id": _object_id(part),
                                "profile": attributes.get("profile", "")})
            except Exception as exc:
                errors.append({"index": i,
                               "error": f"{type(exc).__name__}: {exc}"})
        committed = bool(_model().CommitChanges()) if created else False
        result = {
            "dry_run": False,
            "count": len(items),
            "created": len(created),
            "failed": len(items) - len(created),
            "committed": committed,
            "created_items": created,
            "errors": errors,
        }
        guids = [c["guid"] for c in created if c.get("guid")]
        if guids:
            objects, _, missing = _objects_from_guids(",".join(guids))
            result["verified_after_commit"] = len(objects)
            if missing:
                result["not_found_after_commit"] = missing
        return result
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def _resolve_parts(text, label):
    """把 GUID（逗号分隔或列表）解析成 Part；用于组件主/次构件。"""
    if isinstance(text, (list, tuple)):
        text = ",".join(str(x) for x in text)
    text = str(text or "").strip()
    if not text:
        return []
    objects, invalid, missing = _objects_from_guids(text)
    if invalid or missing:
        raise ValueError(f"{label} 无法解析：invalid={invalid}, not_found={missing}")
    parts = [o for o in objects if isinstance(o, Part)]
    if len(parts) != len(objects):
        raise ValueError(f"{label} 中必须全部是 Part 对象")
    return parts


@server.tool()
@_synchronized
def tekla_create_components(components: str, confirm: bool = False) -> dict:
    """插入 Tekla 组件（连接/节点/细部），把主构件与次构件连成可出图的节点。

    components 是 JSON 数组，单次最多 20 条。每条包含：
      primary_guid      主构件 GUID（必填）
      secondary_guids   次构件 GUID，逗号分隔或数组（可选）
      name              组件名，如 "Base plate (1014)"
      number            组件编号，如 1014（内置节点用编号更稳）
      kind              connection（系统节点，默认）/ detail / component（自定义）
                        / auto（按编号自动判断并逐个尝试）
      point / point2    定位点：Detail 用 point 作参考点；自定义组件用 point/point2；
                        Connection（系统节点）由主/次构件决定位置，忽略 point
      attributes        组件属性键值对，如 {"d": "12"}（字符串）
      attributes_file   组件属性文件，如 "standard"
    默认只预览；confirm=True 才插入并 CommitChanges。
    """
    try:
        _require_api()
        items = _load_json_items(components, "components", MAX_CREATE_COMPONENTS)
        errors, plans = [], []
        for i, spec in enumerate(items):
            label = f"components[{i}]"
            try:
                primary = _resolve_parts(spec.get("primary_guid"), f"{label}.primary_guid")
                if len(primary) != 1:
                    raise ValueError(f"{label}.primary_guid 必须且只能给 1 个 GUID")
                secondary = _resolve_parts(spec.get("secondary_guids"),
                                           f"{label}.secondary_guids")
                name = str(spec.get("name") or "").strip()
                number = spec.get("number")
                if number not in (None, ""):
                    try:
                        number = int(number)
                    except (TypeError, ValueError) as exc:
                        raise ValueError(f"{label}.number 需要整数，收到 {spec['number']!r}") from exc
                if not name and number is None:
                    raise ValueError(f"{label} 需要 name 或 number（内置节点建议用 number）")
                point = None
                point2 = None
                if spec.get("point") not in (None, ""):
                    point = _parse_point(spec["point"], f"{label}.point")
                if spec.get("point2") not in (None, ""):
                    if point is None:
                        raise ValueError(f"{label} 给了 point2 就必须同时给 point")
                    point2 = _parse_point(spec["point2"], f"{label}.point2")
                kind = str(spec.get("kind") or "auto").strip().lower()
                if kind not in _COMPONENT_KINDS:
                    raise ValueError(f"{label}.kind 只能是 {' / '.join(_COMPONENT_KINDS)}")
                attributes = spec.get("attributes") or {}
                if not isinstance(attributes, dict):
                    raise ValueError(f"{label}.attributes 需要 JSON 对象")
                plans.append({"index": i, "spec": spec, "kind": kind,
                              "primary": primary[0],
                              "secondary": secondary, "name": name,
                              "number": number, "point": point, "point2": point2,
                              "attributes": {str(k): str(v) for k, v in attributes.items()},
                              "attributes_file": str(spec.get("attributes_file") or "")})
            except Exception as exc:
                errors.append({"index": i, "error": f"{type(exc).__name__}: {exc}"})

        preview = [{
            "index": plan["index"],
            "primary_guid": _guid_of(plan["primary"]),
            "secondary_guids": [_guid_of(p) for p in plan["secondary"]],
            "kind": plan["kind"],
            "name": plan["name"],
            "number": plan["number"],
            "attributes": plan["attributes"],
        } for plan in plans]
        result = {"dry_run": not confirm, "count": len(items),
                  "valid": len(plans), "invalid": len(errors),
                  "items": preview, "errors": errors}
        if not confirm:
            result["message"] = "预览通过；confirm=True 后才会插入组件。"
            return result
        if not plans:
            result["error"] = "没有可插入的组件，请先修正 errors 中的参数"
            return result

        classes = _component_classes()
        created = []
        for plan in plans:
            attempts = _component_class_order(plan["kind"], plan["number"])
            last_error = ""
            for class_name in attempts:
                try:
                    component = _build_component(classes[class_name], class_name, plan)
                    if not bool(component.Insert()):
                        last_error = (
                            f"{class_name}.Insert 返回 False：检查编号是否存在；"
                            "系统节点（Connection）通常必须同时给主构件和次构件；"
                            "自定义组件再检查定位点/属性是否足够")
                        continue
                    created.append({"index": plan["index"],
                                    "component_class": class_name,
                                    "guid": _guid_of(component),
                                    "id": _object_id(component),
                                    "name": plan["name"],
                                    "number": plan["number"]})
                    last_error = ""
                    break
                except Exception as exc:
                    last_error = f"{class_name}: {type(exc).__name__}: {exc}"
            if last_error:
                errors.append({"index": plan["index"],
                               "error": f"尝试 {attempts} 均失败；最后一次: {last_error}"})
        committed = bool(_model().CommitChanges()) if created else False
        return {"dry_run": False, "count": len(items),
                "created": len(created), "failed": len(items) - len(created),
                "committed": committed, "created_items": created,
                "errors": errors}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


_COMPONENT_KINDS = ("auto", "component", "connection", "detail")


def _component_class_order(kind, number):
    """系统节点用 Connection/Detail，自定义组件用 Component。

    Tekla 公开 API 里三者是不同类：Connection/Detail 对应系统节点，
    Component 对应自定义组件（编号通常 >= 1000000）。不确定时按顺序试，
    Insert 返回 False 不会写模型，多试一类没有副作用。
    """
    if kind and kind != "auto":
        return [kind]
    if number is None or number >= 1000000:
        return ["component", "connection", "detail"]
    return ["connection", "detail", "component"]


def _component_classes():
    from Tekla.Structures.Model import Component, Connection, Detail
    return {"component": Component, "connection": Connection, "detail": Detail}


def _build_component(component_class, class_name, plan):
    """按组件类型走不同 API：

    - Connection：SetPrimaryObject + SetSecondaryObject(s)，定位由构件决定
    - Detail：SetPrimaryObject + SetReferencePoint(point)
    - Component（自定义）：ComponentInput 的输入对象与输入点

    这几个类虽然同属 BaseComponent，但连接/细部并没有 SetComponentInput，
    混用会直接 AttributeError（已在真实 Tekla 上验证）。
    """
    from Tekla.Structures.Model import ComponentInput

    component = component_class()
    if plan["number"] is not None:
        component.Number = plan["number"]
    if plan["name"]:
        component.Name = plan["name"]
    if plan["attributes_file"]:
        component.LoadAttributesFromFile(plan["attributes_file"])
    for key, value in plan["attributes"].items():
        component.SetAttribute(key, value)

    if class_name == "connection":
        component.SetPrimaryObject(plan["primary"])
        if len(plan["secondary"]) == 1:
            component.SetSecondaryObject(plan["secondary"][0])
        elif plan["secondary"]:
            component.SetSecondaryObjects(_to_arraylist(plan["secondary"]))
        try:  # 让方向跟随属性文件，避免节点方向随机
            from Tekla.Structures.Model import AutoDirectionTypeEnum
            component.AutoDirectionType = (
                AutoDirectionTypeEnum.AUTODIR_FROM_ATTRIBUTE_FILE)
        except Exception:
            pass
        return component
    if class_name == "detail":
        component.SetPrimaryObject(plan["primary"])
        if plan["point"] is not None:
            component.SetReferencePoint(plan["point"])
        return component

    component_input = ComponentInput()
    component_input.AddInputObject(plan["primary"])
    for part in plan["secondary"]:
        component_input.AddInputObject(part)
    if plan["point"] is not None and plan["point2"] is not None:
        component_input.AddTwoInputPositions(plan["point"], plan["point2"])
    elif plan["point"] is not None:
        component_input.AddOneInputPosition(plan["point"])
    component.SetComponentInput(component_input)
    return component


def _part_snapshot(part):
    return {
        "profile": _report_str(part, "PROFILE") or "",
        "material": _report_str(part, "MATERIAL") or "",
        "class": str(_safe(part, "Class", "") or ""),
        "name": str(_safe(part, "Name", "") or ""),
        "finish": str(_safe(part, "Finish", "") or ""),
    }


@server.tool()
@_synchronized
def tekla_modify_parts(guids: str = "", changes: str = "",
                       confirm: bool = False) -> dict:
    """批量修改已有构件的规格/材质/等级/名称/表面处理与位置偏移。

    guids 逗号分隔，单次最多 100 个；changes 是 JSON 对象，可用字段：
    profile、material、class、name、finish、
    plane/plane_offset、rotation/rotation_offset、depth/depth_offset。
    默认只预览 before/after；confirm=True 才对每个构件 Modify() 并提交。
    """
    try:
        _require_api()
        objects, invalid, missing = _objects_from_guids(guids)
        if invalid or missing:
            return {"error": "部分 GUID 无效或不存在",
                    "invalid_guids": invalid, "not_found_guids": missing}
        if not objects:
            return {"error": "没有取到任何对象，请检查 guids"}
        if len(objects) > 100:
            return {"error": f"单次最多修改 100 个对象，收到 {len(objects)} 个"}
        parts = [o for o in objects if isinstance(o, Part)]
        if not parts:
            return {"error": "给定 GUID 中没有 Part 构件"}
        skipped = [{"guid": _guid_of(o), "type": type(o).__name__}
                   for o in objects if not isinstance(o, Part)]

        allowed = {"profile", "material", "class", "name", "finish",
                   "plane", "plane_offset", "rotation", "rotation_offset",
                   "depth", "depth_offset"}
        if isinstance(changes, str):
            text = changes.strip()
            if not text:
                return {"error": "changes 不能为空，需要 JSON 对象"}
            try:
                change_spec = json.loads(text)
            except json.JSONDecodeError as exc:
                return {"error": f"changes 不是合法 JSON: {exc}"}
        else:
            change_spec = changes
        if not isinstance(change_spec, dict) or not change_spec:
            return {"error": "changes 必须是非空 JSON 对象"}
        unknown = sorted(k for k in change_spec if k not in allowed)
        if unknown:
            return {"error": f"不支持字段 {unknown}；可用: {sorted(allowed)}"}
        if not any(str(v or "").strip() for v in change_spec.values()):
            return {"error": "changes 中所有字段都为空，没有可执行的修改"}

        plan = []
        for part in parts:
            before = _part_snapshot(part)
            after = dict(before)
            for key in ("profile", "material", "class", "name", "finish"):
                if change_spec.get(key) not in (None, ""):
                    after[key] = str(change_spec[key]).strip()
            item = {"guid": _guid_of(part), "before": before, "after": after}
            for key in ("plane", "plane_offset", "rotation", "rotation_offset",
                        "depth", "depth_offset"):
                if change_spec.get(key) not in (None, ""):
                    item[key] = change_spec[key]
            plan.append(item)
        result = {"dry_run": not confirm, "count": len(parts),
                  "skipped_non_part": skipped, "items": plan}
        if not confirm:
            result["message"] = "预览通过；confirm=True 后才会 Modify 并提交。"
            return result

        changed, failures = 0, []
        for part in parts:
            try:
                _apply_part_attributes(part, change_spec)
                _apply_part_position(part, change_spec)
                if bool(part.Modify()):
                    changed += 1
                else:
                    failures.append({"guid": _guid_of(part),
                                     "error": "Modify 返回 False"})
            except Exception as exc:
                failures.append({"guid": _guid_of(part),
                                 "error": f"{type(exc).__name__}: {exc}"})
        committed = bool(_model().CommitChanges()) if changed else False
        return {"dry_run": False, "count": len(parts), "changed": changed,
                "failed": len(parts) - changed, "committed": committed,
                "skipped_non_part": skipped, "failures": failures[:20]}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


@server.tool()
@_synchronized
def tekla_delete_objects(guids: str = "", confirm: bool = False) -> dict:
    """按 GUID 删除模型对象（构件/组件等），删除前必须先预览。

    单次最多 100 个；confirm=True 才真正删除并提交。删除后复核 GUID，
    返回仍能查到的对象，避免出现“报成功但还在”的假成功。
    """
    try:
        _require_api()
        objects, invalid, missing = _objects_from_guids(guids)
        if invalid or missing:
            return {"error": "部分 GUID 无效或不存在",
                    "invalid_guids": invalid, "not_found_guids": missing}
        if not objects:
            return {"error": "没有取到任何对象，请检查 guids"}
        if len(objects) > 100:
            return {"error": f"单次最多删除 100 个对象，收到 {len(objects)} 个"}
        preview = [{"guid": _guid_of(o), "type": type(o).__name__,
                    "name": str(_safe(o, "Name", "") or "")}
                   for o in objects]
        if not confirm:
            return {"dry_run": True, "count": len(objects), "items": preview,
                    "message": "预览通过；confirm=True 后才会真正删除，无法撤销。"}

        deleted, failures = 0, []
        for obj in objects:
            try:
                if bool(obj.Delete()):
                    deleted += 1
                else:
                    failures.append({"guid": _guid_of(obj),
                                     "error": "Delete 返回 False"})
            except Exception as exc:
                failures.append({"guid": _guid_of(obj),
                                 "error": f"{type(exc).__name__}: {exc}"})
        committed = bool(_model().CommitChanges()) if deleted else False
        _, _, remaining = _objects_from_guids(guids)
        return {"dry_run": False, "count": len(objects), "deleted": deleted,
                "failed": len(objects) - deleted, "committed": committed,
                "failures": failures[:20],
                "remaining_guids": remaining}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


@server.tool()
@_synchronized
def tekla_list_drawings(name: str = "", mark: str = "", status: str = "",
                        drawing_type: str = "", offset: int = 0,
                        limit: int = 500) -> dict:
    """筛选并分页列出图纸，返回 Mark、编号、状态和来源对象 GUID。

    name/mark/status 为不区分大小写的包含匹配；drawing_type 匹配 W/A/G 等类型
    或 SinglePartDrawing/AssemblyDrawing 等类名。后续操作优先传 Mark。
    """
    try:
        _require_api()
        rows = []
        for i, d in enumerate(_all_drawings()):
            row = _drawing_row(d, i)
            if name and name.casefold() not in row["name"].casefold():
                continue
            if mark and mark.casefold() not in row["mark"].casefold():
                continue
            if status and status.casefold() not in row["up_to_date"].casefold():
                continue
            if drawing_type:
                expected = drawing_type.casefold()
                if (expected != row["type"].casefold()
                        and expected not in row["drawing_class"].casefold()):
                    continue
            rows.append(row)
        offset, limit = max(0, int(offset)), max(1, min(int(limit), 2000))
        page = rows[offset:offset + limit]
        return {"count": len(page), "total_count": len(rows),
                "offset": offset, "drawings": page,
                "truncated": offset + len(page) < len(rows),
                "note": "后续操作优先使用 mark；mark 为空时才用当前列表的 index"}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


@server.tool()
@_synchronized
def tekla_get_drawing_detail(drawing_mark: str = "", drawing_name: str = "",
                             index: int = -1) -> dict:
    """查看单张图纸的视图、标注、尺寸和对象统计，优先按 Mark 定位。"""
    try:
        _require_api()
        d = _find_drawing(drawing_name, index, drawing_mark)
        if d is None:
            return {"error": f"找不到图纸：mark={drawing_mark!r} "
                             f"name={drawing_name!r} index={index}"}

        row = _drawing_row(d, index if index >= 0 else None)
        sheet = d.GetSheet()
        views = []
        try:
            ve = sheet.GetViews()
            while ve.MoveNext():
                views.append(type(ve.Current).__name__)
        except Exception:
            pass

        counts = {}
        total = 0
        try:
            oe = sheet.GetAllObjects()
            while oe.MoveNext():
                n = type(oe.Current).__name__
                counts[n] = counts.get(n, 0) + 1
                total += 1
        except Exception:
            pass

        # 只把名字以 Dimension 结尾的算作尺寸，DimensionSet 是容器，单独统计
        row["view_count"] = len(views)
        row["views"] = views
        row["object_total"] = total
        row["object_counts"] = counts
        row["mark_count"] = sum(v for k, v in counts.items()
                                if k.endswith("Mark"))
        row["dimension_count"] = sum(v for k, v in counts.items()
                                     if k.endswith("Dimension"))
        row["dimension_set_count"] = sum(v for k, v in counts.items()
                                         if k.endswith("DimensionSet"))
        return row
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


@server.tool()
@_synchronized
def tekla_manage_drawing(action: str, drawing_mark: str = "",
                         drawing_name: str = "", index: int = -1,
                         save_current: bool = True, show_drawing: bool = True,
                         confirm: bool = False) -> dict:
    """打开、关闭或更新图纸。所有动作都先预览，confirm=True 才执行。

    action=open 时按 Mark/index/name 定位；切换到另一张图前会按 save_current
    保存并关闭当前图。action=close 不需要目标。action=update 重新生成目标图纸。
    """
    try:
        _require_api()
        action = (action or "").lower()
        if action not in ("open", "close", "update"):
            return {"error": "action 只能是 open / close / update"}
        dh = _drawing_handler()
        try:
            active = dh.GetActiveDrawing()
        except Exception:
            active = None

        target = None
        if action in ("open", "update"):
            target = _find_drawing(drawing_name, index, drawing_mark)
            if target is None:
                return {"error": "找不到目标图纸", "action": action}
        else:
            target = active
            if target is None:
                return {"action": action, "has_active_drawing": False,
                        "closed": False, "message": "当前没有打开的图纸"}

        if not confirm:
            return {
                "dry_run": True,
                "action": action,
                "target": _drawing_row(target),
                "active_before": _drawing_row(active) if active is not None else None,
                "save_current": bool(save_current),
                "message": "确认后重新调用并传 confirm=True。",
            }

        if action == "close":
            ok = bool(dh.CloseActiveDrawing(bool(save_current)))
            return {"action": action, "closed": ok, "saved": bool(save_current)}

        if action == "update":
            ok = bool(dh.UpdateDrawing(target))
            return {"action": action, "updated": ok,
                    "drawing": _drawing_row(target)}

        if active is not None and not _same_drawing(active, target):
            if not bool(dh.CloseActiveDrawing(bool(save_current))):
                return {"action": action, "opened": False,
                        "error": "当前图纸关闭失败，未继续打开目标图纸"}
        ok = bool(dh.SetActiveDrawing(target, bool(show_drawing)))
        return {"action": action, "opened": ok,
                "drawing": _drawing_row(target)}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


@server.tool()
@_synchronized
def tekla_create_drawings(kind: str = "single_part", scope: str = "selected",
                          class_number: str = "", name_prefix: str = "",
                          limit: int = 50, skip_existing: bool = True,
                          deduplicate: bool = True, confirm: bool = False) -> dict:
    """批量创建零件图/构件图，先合并重复位置并跳过已有图纸。

    Tekla 对同一零件/构件位置只保留一张对应图纸，因此默认按位置去重，
    不再让每个相同零件都执行一次必然失败的 Insert。limit 限制实际创建数。
    name_prefix 会拼接位置号，避免所有图纸仍叫同一个名字。
    """
    try:
        _require_api()
        kind = (kind or "single_part").lower()
        if kind not in ("single_part", "assembly"):
            return {"error": "kind 只能是 single_part 或 assembly"}

        objs = [o for o in _targets(scope, class_number) if isinstance(o, Part)]
        if not objs:
            return {"error": f"scope={scope} 没有取到任何 Part 构件"}
        position_prop = "PART_POS" if kind == "single_part" else "ASSEMBLY_POS"
        groups = {}
        for i, o in enumerate(objs):
            position = _report_str(o, position_prop) or ""
            key = position if deduplicate else (_guid_of(o) or str(i))
            if not key:
                key = _guid_of(o) or str(i)
            groups.setdefault(key, []).append(o)

        existing_rows = [_drawing_row(d) for d in _all_drawings()]
        existing_sources = {r.get("source_guid") for r in existing_rows
                            if r.get("source_guid")}
        pending, skipped_existing = [], 0
        for position, members in groups.items():
            if skip_existing and any(_guid_of(o) in existing_sources for o in members):
                skipped_existing += 1
            else:
                pending.append((position, members[0]))

        if len(pending) > max(1, int(limit)):
            return {"error": f"去重后仍需创建 {len(pending)} 张，超过 limit={limit}；"
                             "请缩小范围或显式调大 limit"}

        unnumbered = sum(1 for position, _ in pending
                         if not position or "?" in position)
        preview = [{"position": position,
                    "guid": _guid_of(o),
                    "名称": _report_str(o, "NAME") or ""}
                   for position, o in pending[:5]]
        base = {
            "kind": kind,
            "target_part_count": len(objs),
            "unique_position_count": len(groups),
            "would_create": len(pending),
            "skipped_shared_position": len(objs) - len(groups),
            "skipped_existing": skipped_existing,
            "unnumbered_position_count": unnumbered,
            "existing_drawing_count": len(existing_rows),
            "sample_targets": preview,
        }
        if not confirm:
            return dict(base, dry_run=True,
                        message="预览已按位置去重；确认后重新调用并传 confirm=True。")
        if not pending:
            return dict(base, dry_run=False, created=0, failed=0,
                        committed=False, message="没有需要新建的图纸")

        clr.AddReference("Tekla.Structures.Drawing")
        from Tekla.Structures.Drawing import AssemblyDrawing, SinglePartDrawing

        created, errors, created_items = 0, [], []
        for position, o in pending:
            try:
                if kind == "assembly":
                    asm = _safe(o, "GetAssembly")
                    asm = asm() if callable(asm) else asm
                    ident = _safe(asm, "Identifier")
                    if ident is None:
                        ident = _safe(o, "Identifier")
                    d = AssemblyDrawing(ident)
                else:
                    d = SinglePartDrawing(_safe(o, "Identifier"))
                if name_prefix:
                    d.Name = f"{name_prefix}{position or _object_id(o) or ''}"
                if bool(d.Insert()):
                    created += 1
                    created_items.append({"position": position, "drawing_mark": _drawing_key(d),
                                          "source_guid": _guid_of(o)})
                elif len(errors) < 20:
                    errors.append({"position": position, "source_guid": _guid_of(o),
                                   "error": "Insert 返回 False"})
            except Exception as exc:
                if len(errors) < 20:
                    errors.append({"position": position, "source_guid": _guid_of(o),
                                   "error": f"{type(exc).__name__}: {exc}"})

        committed = bool(_model().CommitChanges()) if created else False
        result = dict(base, dry_run=False, created=created,
                      failed=len(pending) - created, committed=committed,
                      errors=errors, created_items=created_items,
                      drawing_count_after=len(_all_drawings()))
        if unnumbered:
            result["hint"] = ("检测到未编号位置。工具已按位置去重，但 Tekla 可能仍只允许"
                              "创建一张；正式批量出图前请先执行编号(Numbering)。")
        return result
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


@server.tool()
@_synchronized
def tekla_place_views(drawing_mark: str = "", drawing_name: str = "",
                      index: int = -1, confirm: bool = False) -> dict:
    """自动排布图纸视图，优先按 Mark 定位；confirm=True 才执行。"""
    try:
        _require_api()
        d = _find_drawing(drawing_name, index, drawing_mark)
        if d is None:
            return {"error": f"找不到图纸：mark={drawing_mark!r} "
                             f"name={drawing_name!r} index={index}"}
        label = drawing_mark or f"{_safe(d, 'Name')}#{index if index >= 0 else '-'}"
        if not confirm:
            return {
                "dry_run": True,
                "drawing": label,
                "message": "预览：将对这张图纸执行视图自动排布。"
                           "确认后重新调用并传 confirm=True。",
            }
        ok = bool(d.PlaceViews())
        committed = False
        try:
            committed = bool(_model().CommitChanges())
        except Exception:
            pass
        return {"drawing": label, "placed": ok, "committed": committed}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


@server.tool()
@_synchronized
def tekla_delete_drawings(drawing_mark: str = "", drawing_name: str = "",
                          index: int = -1, scope: str = "all",
                          confirm: bool = False) -> dict:
    """删除图纸。破坏性操作，优先按 Mark 定位，必须 confirm=True。"""
    try:
        _require_api()
        if drawing_mark or index >= 0 or drawing_name:
            d = _find_drawing(drawing_name, index, drawing_mark)
            targets = [d] if d is not None else []
        elif (scope or "all").lower() == "all":
            targets = _all_drawings()
        else:
            return {"error": "scope 只支持 all"}

        if not targets:
            return {"error": "没有匹配到任何图纸"}

        names = [str(_safe(d, "Name") or "") for d in targets]
        if not confirm:
            return {
                "dry_run": True,
                "would_delete": len(targets),
                "names": names[:20],
                "message": f"预览：将删除 {len(targets)} 张图纸，不可撤销。"
                           f"确认后重新调用并传 confirm=True。",
            }

        deleted, failed = 0, 0
        for d in targets:
            try:
                if bool(d.Delete()):
                    deleted += 1
                else:
                    failed += 1
            except Exception:
                failed += 1

        committed = False
        try:
            committed = bool(_model().CommitChanges())
        except Exception:
            pass
        return {"deleted": deleted, "failed": failed,
                "committed": committed,
                "drawing_count_after": len(_all_drawings())}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


@server.tool()
@_synchronized
def tekla_export_drawings_pdf(drawing_mark: str = "", drawing_name: str = "",
                              index: int = -1, output_dir: str = "",
                              printer: str = "", limit: int = 20,
                              overwrite: bool = False) -> dict:
    """把图纸输出为 PDF，优先按 Mark 定位，逐项校验文件真实有效。

    文件名包含 Mark 或序号，避免多张「零件图」互相覆盖；同一张图重复导出时，
    overwrite=False 会自动追加序号。留空目标则导出前 limit 张。
    """
    try:
        _require_api()
        dh = _drawing_handler()
        if drawing_mark or index >= 0 or drawing_name:
            d = _find_drawing(drawing_name, index, drawing_mark)
            targets = [d] if d is not None else []
        else:
            targets = _all_drawings()[:max(1, int(limit))]
        if not targets:
            return {"error": "没有可导出的图纸"}

        from Tekla.Structures.Drawing import PrintAttributes

        out_dir = output_dir or os.path.join(_EXPORT_DIR, "pdf")
        os.makedirs(out_dir, exist_ok=True)
        pa = PrintAttributes()
        if printer:
            pa.PrinterInstance = printer

        results = []
        for d in targets:
            name = str(_safe(d, "Name") or "drawing")
            mark = _drawing_key(d)
            suffix = _safe_filename(mark)[:40] if mark else str(index if index >= 0 else "drawing")
            base = _safe_filename(f"{name}_{suffix}")
            path = _available_path(os.path.join(out_dir, f"{base}.pdf"), overwrite)
            try:
                api_ok = bool(dh.PrintDrawing(d, pa, path))
                exists = os.path.isfile(path)
                size = os.path.getsize(path) if exists else 0
                header = b""
                if exists and size >= 5:
                    with open(path, "rb") as f:
                        header = f.read(5)
                valid = api_ok and size > 5 and header == b"%PDF-"
                item = {"drawing": name, "drawing_mark": mark, "path": path,
                        "ok": valid, "api_ok": api_ok, "bytes": size}
                if not valid:
                    item["error"] = "PrintDrawing 返回成功但 PDF 文件无效" if api_ok else "PrintDrawing 返回 False"
                results.append(item)
            except Exception as exc:
                results.append({"drawing": name, "drawing_mark": mark, "path": path,
                                "ok": False, "error": f"{type(exc).__name__}: {exc}"})

        succeeded = sum(1 for r in results if r["ok"])
        return {"output_dir": out_dir, "count": len(results),
                "succeeded": succeeded, "failed": len(results) - succeeded,
                "results": results}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


if __name__ == "__main__":
    log.info("启动 Tekla MCP Server (stdio)")
    server.run(transport="stdio")
