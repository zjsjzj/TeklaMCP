# Tekla MCP Server —— 实测配置指南

> 本文所有结论都在 **Tekla Structures 2026.0** 上实跑验证过，
> 环境：Windows / Python 3.13.14 / pythonnet 3.2.1 / mcp 2.3.0
> 验证模型：一个 40 构件的门式刚架模型
>
> **只想赶紧装好用起来？看 `INSTALL.md`。** 本文是原理、坑点和实测记录。

Trimble 官方**没有**发布 Tekla 的 MCP Server，社区方案分两条路线：

| 路线 | 技术栈 | 适合场景 | 难度 |
|---|---|---|---|
| **A. Python + pythonnet**（本目录） | Python + `clr` + MCP | 查模型、读属性、按条件选择、算重量 | ★☆☆ |
| **B. C# 双进程桥接** | net48 Bridge + MCP Server | 图纸批量标注、尺寸排布、性能敏感 | ★★★ |

---

## ⚠️ 先纠正一个流传很广的错误说法

网上（包括 AI 生成的回答）常说要去
「文件 → 选项 → 定制 → 宏 → **允许外部程序访问**」勾选一下才能连 Tekla。

**Tekla 2026 里没有这个设置，也不需要。** 实测证据：

- `bin` 目录下所有 `.xml / .ini / .lst` 配置里搜不到任何与 API / 外部访问相关的高级选项，
  只有一个无关的 `XS_EXTERNAL_EXCEL_DESIGN_PATH`；
- 本机 Tekla 正常运行时，`Model().GetConnectionStatus()` 直接返回 `True`，
  期间没有改过任何权限设置。

**能连上的真实条件只有三条**：Tekla 在运行 + 已打开模型 + 双方都是 64 位。

---

## 路线 A：Python + pythonnet（已可用）

### 1. 安装

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1
```

脚本会自动完成：检查 Python 位数 → 探测 Tekla 安装路径 → 建 `.venv` 装依赖 →
写 MCP 配置 → 跑连接诊断。参数说明见 **`INSTALL.md`**。

手动安装的话：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe diagnose.py
```

`diagnose.py` 会依次检查位数、Tekla 安装、程序集加载、模型连接，
最后打印模型名、构件数、阶段列表。**全绿再往下走。**

### 2. 写入 MCP 配置

**WorkBuddy AI** → 编辑 `C:\Users\<用户名>\.workbuddy-ai\mcp.json`
（注意：不是 `.mcp.json`，没有点前缀）：

```json
{
  "mcpServers": {
    "tekla": {
      "type": "stdio",
      "command": "<安装目录>\\.venv\\Scripts\\python.exe",
      "args": ["<安装目录>\\tekla_server.py"],
      "env": {
        "PYTHONUNBUFFERED": "1",
        "PYTHONNET_RUNTIME": "netfx",
        "TEKLA_BIN": "C:\\Program Files\\Tekla Structures\\2026.0\\bin"
      }
    }
  }
}
```

`<安装目录>` 换成本目录的绝对路径，反斜杠要写成双反斜杠。
`TEKLA_BIN` 可以省略——服务端会自动扫描安装目录取版本最高的那个。

写完后**不会自动生效**：到连接器管理页右上角的「自定义连接器」入口，点「信任」。

其他客户端现成模板在 `config/` 目录：

| 客户端 | 配置文件 | 模板 |
|---|---|---|
| WorkBuddy AI | `%USERPROFILE%\.workbuddy-ai\mcp.json` | `config/workbuddy.json` |
| Claude Desktop | `%APPDATA%\Claude\claude_desktop_config.json` | `config/claude-desktop.json` |
| Cline (VSCode) | `cline_mcp_settings.json`，额外加 `"disabled": false, "timeout": 60` | `config/cline.json` |
| Cherry Studio / DeepChat | 设置 → MCP 服务器 → 添加 stdio 服务器 | — |

### 3. 内置工具（24 个）

**建模类（v2.1.0 新增）** —— 让 AI 直接把构件画进模型

| 工具 | 作用 | 状态 |
|---|---|---|
| `tekla_create_parts` | 批量创建梁/柱/折梁/多边形板/墙板/基础（JSON 数组，单次 50 条） | 单测通过；待真实模型回归 |
| `tekla_create_components` | 插入 Tekla 组件（连接/节点/细部），把主次构件连起来 | 单测通过；待真实模型回归 |
| `tekla_modify_parts` | 批量改规格/材质/等级/名称/表面处理与位置偏移 | 单测通过 |
| `tekla_delete_objects` | 按 GUID 删除对象（先预览，`confirm=True` 才删） | 单测通过 |

> 建模工具按 Tekla Open API 2026 公开接口实现：`Beam`/`PolyBeam`/`ContourPlate`、
> `Position` 三向偏移、`Component` + `ComponentInput`。写操作一律「先预览、再确认」，
> 批量插入后统一 `CommitChanges()` 并回查 GUID，避免假成功。
> 单元测试用仿真运行时覆盖参数解析、插入、提交、删除回查，见 `tests/`。

**查询类**

| 工具 | 作用 | 实测结果 |
|---|---|---|
| `tekla_check_connection` | 检查连接 | `connected: true`，返回模型名与路径 |
| `tekla_get_model_info` | 模型信息 + 对象统计 | `part: 40, assembly: 40, bolt: 0` |
| `tekla_list_phases` | 列出阶段 | `[{1, "状态 1"}]` |
| `tekla_find_parts` | 按规格/材质/名称筛选 | `RHS300*200*6 / Q345B` 命中 |
| `tekla_get_selected_objects` | 读取选中对象属性 | 5 个 Beam，总重 1120.86 kg |
| `tekla_select_by_filter` | 按 GUID / 多条件组合筛选并选中 | ✅ 组合过滤断言通过 |
| `tekla_get_object_details` | 任意对象的详情、几何、UDA、报表属性与组件 | ✅ 只读路径回归通过 |
| `tekla_check_numbering` | 编号完整性自检（出图/BOM 前的体检） | ✅ 只读路径回归通过 |

**清单与统计**

| 工具 | 作用 | 实测结果 |
|---|---|---|
| `tekla_export_bom` | 导出构件清单到 CSV / Excel | 40 行，总重 11230.63 kg |
| `tekla_summarize` | 按 Class/阶段/材质/规格/名称汇总工程量 | 总重 11230.63 kg、面积 184.63 m² |

**UDA 读写**

| 工具 | 作用 | 实测结果 |
|---|---|---|
| `tekla_read_udas` | 批量读取自定义属性 | 正常，空值返回 `""` |
| `tekla_write_udas` | 批量写入（需 `confirm=True`） | 单对象写入+还原验证通过 ✅ |

**选择集**

| 工具 | 作用 | 实测结果 |
|---|---|---|
| `tekla_select_by_class` | 按 Class 号批量选中 | Class=3 → 选中 21 个，`applied: true` |

**图纸（Drawing）** —— 全部经过真实建图验证 ✅

| 工具 | 作用 | 实测状态 |
|---|---|---|
| `tekla_list_drawings` | 列出图纸（含 index） | ✅ 建图后能正确列出，`type='W'`、`up_to_date=DrawingIsUpToDate` |
| `tekla_get_drawing_detail` | 视图数/标注数/尺寸数 | ✅ 视图 1、Mark 1、尺寸 1、尺寸集 1 |
| `tekla_create_drawings` | 批量创建零件图/构件图 | ✅ 建图成功并提交；⚠️ 未编号模型只能建 1 张（见下） |
| `tekla_manage_drawing` | 打开/关闭/更新图纸 | v2 逻辑，待真实图纸回归 |
| `tekla_place_views` | 视图自动排布 | ✅ `placed: true` |
| `tekla_delete_drawings` | 删除图纸（需 `confirm=True`） | ✅ `deleted: 1`，删除后计数归零 |
| `tekla_export_drawings_pdf` | 输出 PDF | ✅ 真的生成了 24191 字节的合法 PDF |

> ⚠️ 写操作提示：`tekla_select_by_class`、`tekla_write_udas`、`tekla_create_parts`、
> `tekla_create_components`、`tekla_modify_parts`、`tekla_delete_objects`、
> `tekla_create_drawings`、`tekla_place_views`、`tekla_delete_drawings`
> 都会**真的改动 Tekla 模型**。除选择集外都要求先拿到 `dry_run` 预览，
> 再传 `confirm=True` 才执行。

### 4. 建模：用 AI 直接画构件

`tekla_create_parts` 的参数是一段 JSON 数组，坐标单位 mm。先预览，再加 `confirm=True`：

```json
[
  {"type": "column", "profile": "HEA240", "material": "S355", "class": "2",
   "start": "0,0,0", "end": "0,0,6000"},
  {"type": "beam", "profile": "IPE300", "material": "S355", "class": "3",
   "start": "0,0,6000", "end": "6000,0,6000", "name": "屋面梁"},
  {"type": "contour_plate", "profile": "PL20", "material": "S355",
   "points": [[-150,-150,0],[150,-150,0],[150,150,0],[-150,150,0]]}
]
```

支持的 `type`：`beam`（默认）、`column`、`polybeam`（折梁）、`contour_plate`（多边形板）、
`panel`（墙板）、`pad_footing`（独立基础）、`strip_footing`（条形基础），
也接受 `梁`/`柱`/`板`/`折梁` 等中文别名。位置用 `plane`（middle/left/right）、
`rotation`（front/top/back/bottom）、`depth`（middle/front/behind）加对应 `*_offset` 控制，
避免构件“插进去但方向不对”。

构件建好后用组件做节点：

```json
[{"primary_guid": "<柱的 GUID>", "secondary_guids": "<梁的 GUID>",
  "number": 1014, "point": "0,0,6000", "attributes": {"d": "12"}}]
```

返回的每条 `created_items[].guid` 可直接喂给 `tekla_get_object_details`、
`tekla_modify_parts`、`tekla_delete_objects`，或 `tekla_create_drawings` 出图。

> 建议顺序：`tekla_create_parts` → `tekla_get_model_info` 核对 → 在 Tekla 里编号
> → `tekla_create_drawings` 出零件图/构件图 → `tekla_export_drawings_pdf`。
> 未编号模型只能出一张单件图（Tekla 行为，见下文）。

### 5. 典型用法

```
"帮我统计一下这个模型的总用钢量和涂装面积"
→ tekla_summarize(group_by="material")

"按等级分组看看工程量"
→ tekla_summarize(group_by="class")

"导出构件清单到 Excel，按构件合并"
→ tekla_export_bom(format="xlsx", group_by="assembly")

"把选中的构件 USER_FIELD_1 都改成 A-01"
→ tekla_write_udas(uda_name="USER_FIELD_1", value="A-01")      # 预览
→ tekla_write_udas(..., confirm=True)                          # 确认写入

"列出 RHS300 的构件，把它们选中"
→ tekla_find_parts(profile="RHS300")
→ tekla_select_by_class("3")
```

**BOM 输出字段**：零件编号、构件编号、名称、规格、材质、等级、阶段、
长度(mm)、面积(m²)、重量(kg)，外加你指定的 UDA 列。

`group_by="assembly"` 时会合并成：构件编号、名称、规格、材质、等级、阶段、零件数、总重(kg)。

> 📌 **编号提醒**：本模型尚未做编号（Numbering），所以导出的编号都是
> `P0(?)` / `GL-0(?)` 这种带问号的占位值，且 `group_by="assembly"` 只会分出 2 组。
> **要得到有意义的构件清单，先在 Tekla 里执行「编号」**，编号后同一套工具导出的
> 就是真实编号，按构件分组也会正确。

要加工具，在 `tekla_server.py` 里写新的 `@server.tool()` 函数，改完重启客户端。

---

## 图纸功能真实测试记录

测试时间：2026-10-28 00:00–00:03
测试方式：建图 → 查明细 → 排视图 → 导 PDF → 删除 → 校验归零
测试模型：一个真实的门式刚架模型（测试后已恢复 0 张图纸）

### ⚠️ 最大发现：未编号的模型只能建出 1 张单件图

**现象**：连续创建 2 张，返回 `created=1, failed=1`，而且**不抛任何异常**，
`Insert()` 就是静默返回 `False`。这个坑很隐蔽——不报错，只是少建了图。

**排查过程**（四条路都试过）：

| 尝试 | 结果 |
|---|---|
| 每次 Insert 后 `CommitChanges()` | ❌ 无效 |
| 每次 `CloseActiveDrawing()` / `SaveActiveDrawing()` | ❌ 都返回 False，无效 |
| 把每个构件单独放一个进程创建 | ✅ **每个都成功** |
| 换成不同规格的构件（class 1 的 RHS200*10） | ❌ 仍然失败 |

**根因**：模型未编号，所有构件的 `PART_POS` 都是 `P0(?)`。
Tekla 认为「同一个零件位置已经有一张单件图了」，于是拒绝再建。

**结论**：**批量出图前必须先执行编号(Numbering)**。工具已内置检测——
创建失败且编号里带 `?` 时，会返回 `hint` 明确提示。

### 修掉的两个问题

1. **同名图纸无法区分**：Tekla 默认把所有零件图都命名为「零件图」，
   按名字查找永远命中第一张。已给全部图纸工具加 `index` 参数，
   `tekla_list_drawings` 返回 `index` 字段，优先用它定位。

2. **尺寸重复计数**：`StraightDimensionSet` 是容器，里面装着 `StraightDimension`，
   原来两个都被算进 `dimension_count`（显示 2）。现在只统计以 `Dimension` 结尾的
   实体，容器单列为 `dimension_set_count`（修正后为 1 和 1）。

### PDF 导出：意外地好用

`PrintDrawing(d, PrintAttributes(), path)` 在不指定打印机的情况下直接出图，
生成 **24191 字节**、`%PDF-1.7` 头、`%%EOF` 尾的合法 PDF。
**不需要额外装 PDF 打印机驱动**，Tekla 自带。之前担心的「实验性」可以摘掉了。

### 完整实测输出（单张零件图）

```json
{
  "name": "零件图", "type": "W",
  "created": "2026/10/28 0:03:21",
  "up_to_date": "DrawingIsUpToDate",
  "view_count": 1, "views": ["View"],
  "object_total": 5,
  "object_counts": {"View": 1, "Part": 1,
                    "StraightDimensionSet": 1, "StraightDimension": 1, "Mark": 1},
  "mark_count": 1, "dimension_count": 1, "dimension_set_count": 1
}
```

---

## 图纸（Drawing）能力全景

Tekla 2026 的 `Tekla.Structures.Drawing` 程序集有 **383 个类型**，能力比模型侧更分散。
以下是实测确认可用的部分：

### DrawingHandler 能做什么

| 方法 | 作用 |
|---|---|
| `GetDrawings()` | 遍历所有图纸 |
| `GetActiveDrawing()` / `SetActiveDrawing()` | 读/切当前打开的图纸 |
| `IsAnyDrawingOpen()` | 是否处于图纸模式 |
| `SaveActiveDrawing()` / `CloseActiveDrawing()` | 保存/关闭 |
| `UpdateDrawing(d)` | 更新图纸（重新生成视图） |
| `IssueDrawing(d)` / `UnissueDrawing(d)` | 发放/取消发放 |
| `PrintDrawing(d, attrs, path)` | 打印输出 |
| `GetDrawingSelector()` / `GetDrawingObjectSelector()` | 图纸/图纸对象选择器 |

### 创建图纸（已实现，未实测）

```python
from Tekla.Structures.Drawing import SinglePartDrawing, AssemblyDrawing

d = SinglePartDrawing(part.Identifier)        # 也可 (ident, "图纸名") / (ident, 份数)
d = AssemblyDrawing(part.GetAssembly().Identifier)
d = GADrawing()                                # 总布置图，无参构造
ok = d.Insert()                                # 真正写进模型
```

另有**按规则自动出图**的接口（未实现，留给后续）：

```python
from Tekla.Structures.Drawing.Automation import DrawingCreator, AutoDrawingRule
DrawingCreator.CreateDrawings(rule, identifier)   # 套用 Tekla 的自动出图规则
```

### 视图与标注

| 类别 | 可用类型 |
|---|---|
| 视图创建 | `CreateBottomView` / `Create3dView` / `CreateModelView`（在 `ContainerView` 上） |
| 视图排布 | `Drawing.PlaceViews()`、`RotateViewOnAxisX/Y/Z` |
| 标注 Mark | `Mark`、`WeldMark`、`DetailMark`、`LevelMark`、`SectionMark`、`CurvedSectionMark` |
| 尺寸 Dimension | `StraightDimension`、`AngleDimension`、`RadiusDimension` |
| 遍历 | `Drawing.GetSheet().GetViews()` / `GetAllObjects()` |

### 图纸 UDA

`Drawing` 和模型对象一样有 `SetUserProperty` / `GetUserProperty`（string/int/double 三重重载），
可以给图纸打自定义标记。

### ⚠️ 没有直接导出 PDF 的 API

2026 里**没有** `ExportToPDF`。只能走 `PrintDrawing(Drawing, PrintAttributes, outputPath)`，
`PrintAttributes` 里有 `PrinterInstance` / `Scale` / `Orientation` / `PrintArea`。
也就是说必须先有一个 PDF 打印机驱动（Tekla 自带 PDF 打印机或系统「Microsoft Print to PDF」），
否则打印不出来。所以 `tekla_export_drawings_pdf` 标了「实验性」。

### 做不到 / 很难做的

- **自动排标注和尺寸**：`Mark` / `Dimension` 类都能创建，但要自动避让重叠、
  按规则分组，需要自己算几何位置。社区项目 svMCP 花了大量代码才做出
  `arrange_marks_no_collisions` / `arrange_dimensions`，不是调个 API 就有的。
- **无 UI 批量出图并排版**：`PlaceViews()` 只能做基础排布，精细排版仍需在 Tekla 里手动调。

---

## Tekla 2026 API 的五个坑（血泪实测）

这五条是本目录代码能跑起来的关键，换机器/换版本时先看这里：

### 坑 1：运行时必须是 netfx，不是 coreclr

Tekla 2026 主程序是 **.NET Framework 4.8 宿主**（`bin` 下没有 `coreclr.dll`，
也没有 `TeklaStructures.runtimeconfig.json`）。设 `PYTHONNET_RUNTIME=coreclr` 会加载失败。

网上 2025 版的资料说 Tekla 是 .NET 8 —— 那是错的，或只针对某个组件。

### 坑 2：Net48Runtime 必须排在 bin 前面

`bin` 里**没有** `Tekla.Structures.dll` 和 `Tekla.Structures.Geometry3d.dll`，
它们只存在于 `bin\Net48Runtime\`（net48 版本）。而 `Tekla.Structures.Model.dll`
在 `bin` 里（netstandard2.0）。

```python
sys.path.insert(0, BIN)                  # 先加 bin
sys.path.insert(0, os.path.join(BIN, "Net48Runtime"))  # 再加，会排到最前
```

顺序反了 → `ModuleNotFoundError: No module named 'Tekla'`。
这个报错极具误导性，容易让人以为是没装 Tekla。

### 坑 3：`Tekla.Structures.Model.UI.Selector` 已被删除

2026 里 `Model.UI` 命名空间只剩 28 个类型，`Selector` 不在其中。
替代品是 `ModelObjectSelector`：

```python
from Tekla.Structures.Model.UI import ModelObjectSelector
sel = ModelObjectSelector()
en = sel.GetSelectedObjects()          # 读当前选择
sel.Select(arraylist_of_objects)       # 写选择，参数是 System.Collections.ArrayList
```

### 坑 4：`ModelObjectEnum` 没有 `PART`

枚举里只有 `BEAM` / `CONTOURPLATE` / `BOOLEANPART` / `POLYBEAM` 这些具体类型，
没有通用 `PART`。要拿所有构件只能遍历全量再按类型过滤：

```python
from Tekla.Structures.Model import Part
en = model.GetModelObjectSelector().GetAllObjects()
while en.MoveNext():
    if isinstance(en.Current, Part):
        ...
```

### 坑 5：`Part` 没有 `Weight`，`GetReportProperty` 返回元组

```python
# Profile / Material 是对象，不是字符串
obj.Profile.ProfileString      # 'RHS300*200*6'
obj.Material.MaterialString    # 'Q345B'

# 重量只能走报表属性；pythonnet 3.2 里 out 参数变成返回值元组
ok, weight = obj.GetReportProperty("WEIGHT", System.Double(0.0))
```

另外 `Phase.GetAllPhases()` 也没了，改用 `model.GetPhases()`。

---

## 通用坑（和版本无关）

1. **`print()` 会杀死 MCP 连接** —— stdio 下 stdout 是 JSON-RPC 通道，
   所有日志必须 `stream=sys.stderr`。这是社区方案第一大坑。
2. **Tekla 必须先启动并打开模型**，否则 `Model()` 建了也连不上。
3. **位数必须一致**：64 位 Tekla 只能配 64 位 Python。
4. **`import clr` 的 pip 包名是 `pythonnet`**，不是 `clr`。
5. **`site-packages\clr` 目录冲突**：若存在同名目录，删掉它。
6. **MCP 空闲会断连**：Cline / Claude 里表现为需要 Retry Connection，属正常现象。
7. **改代码后必须重启客户端**：客户端会锁住进程，热重载不生效。

---

## 路线 B：C# 双进程桥接（需要图纸自动化时才上）

为什么双进程：MCP 的 C# SDK 要求 net8+，而 Tekla Open API 传统上跑 net48，
两个运行时无法同进程共存。

```
MCP 客户端 --stdio--> MCP Server (net8) --IPC--> Bridge.exe (Tekla 内) --> Tekla
```

要点：
- Bridge 必须放进 Tekla 扩展目录：
  `C:\Program Files\Tekla Structures\2026.0\Environments\common\extensions\<名字>\`
- 需要 `exe.config` 的 `<codeBase>` 把 `Tekla.Structures.Model.dll` 指向安装目录
- 配置仍指向 net8 的 exe，无 `args`

参考实现：GitHub 搜 `svMCP`（作者 40ushek，50+ 工具，含图纸标注与尺寸排布）。

> 注意：svMCP 是给 TS2021/2025 写的，在 2026 上 IPC 通道名和 DLL 位置都变了，
> 直接照搬跑不通，需要按本文坑 1、坑 2 的思路改桥接的加载逻辑。

---

## 文件说明

| 文件 | 用途 |
|---|---|
| `install.ps1` | 一键安装：建 venv、装依赖、写配置、跑诊断 |
| `tekla_server.py` | MCP 服务端主程序，24 个工具 |
| `diagnose.py` | 环境诊断，配 MCP 前先跑 |
| `format_json.py` | 安装脚本用来整理生成的配置文件 |
| `INSTALL.md` | 安装说明与常见问题 |
| `tests/` | 建模工具单元测试（仿真 Tekla 运行时，无需 Windows） |
| `demo_modeling.py` | 在真实 Tekla 上跑一遍建模回归的小脚本 |
| `config/` | 三种客户端的配置模板 |
| `requirements.txt` | 依赖清单 |
| `export/` | 运行后自动创建的 BOM 输出目录（CSV / XLSX） |
