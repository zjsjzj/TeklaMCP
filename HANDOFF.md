# Tekla MCP Server 交接文档（HANDOFF）

> 写给在新电脑上继续开发的人（也可能是未来的自己）。
> 本文档自包含：读完这一份就能接着干，不需要原电脑上的任何上下文。
>
> 版本：服务端 v2.1.0（2026-11-07，新增建模/组件工具）
> 验证环境：Windows 11 / Tekla Structures 2026.0 / Python 3.13 / pythonnet 3.2.1 / mcp 2.3.0

---

## 一、这个项目是什么

一个 **Python + pythonnet 实现的 Tekla Structures MCP Server**（stdio 传输），
让 AI 客户端（WorkBuddy AI / Claude Desktop / Cline）通过 MCP 协议直接操作 Tekla：
查模型、导 BOM、工程量统计、UDA 读写、建图出 PDF 等。

**当前 24 个工具**（全部已注册并通过协议握手验证）：

| 类别 | 工具 |
|---|---|
| 建模 | `tekla_create_parts` `tekla_create_components` `tekla_modify_parts` `tekla_delete_objects` |
| 连接/信息 | `tekla_check_connection` `tekla_get_model_info` `tekla_list_phases` |
| 查询/选择 | `tekla_find_parts` `tekla_get_selected_objects` `tekla_select_by_class` `tekla_select_by_filter` `tekla_get_object_details` |
| 编号/清单 | `tekla_check_numbering` `tekla_export_bom` `tekla_summarize` |
| UDA | `tekla_read_udas` `tekla_write_udas`（写需 `confirm=True`，先预览） |
| 图纸 | `tekla_list_drawings` `tekla_get_drawing_detail` `tekla_manage_drawing`(open/close/update) `tekla_create_drawings` `tekla_place_views` `tekla_delete_drawings` `tekla_export_drawings_pdf` |

文件结构：`tekla_server.py`（主程序，单文件约 1600 行）、`diagnose.py`（环境诊断）、
`install.ps1`（一键安装）、`format_json.py`、`config/`（三客户端配置模板）、
`README.md`（API 原理与坑点）、`INSTALL.md`（快速上手）。

---

## 二、新电脑首次搭建（5 分钟）

1. 前提：装好 **64 位 Python 3.10+** 和 **Tekla Structures**（任意盘符，会自动探测）。
2. 解压本包，在目录里运行：
   ```powershell
   powershell -ExecutionPolicy Bypass -File install.ps1
   ```
   参数：`-Client workbuddy|claude|cline|none`、`-PythonExe <路径>`、
   `-VenvPath <复用已有venv>`、`-SkipDiagnose`。
3. 打开 Tekla 并打开一个模型，然后验证：
   ```powershell
   .venv\Scripts\python.exe diagnose.py
   ```
   全绿（显示模型名、构件数）即成功。
4. WorkBuddy AI 的配置在 `%USERPROFILE%\.workbuddy-ai\mcp.json`
   （注意**没有**点前缀）。`install.ps1` 是**合并写入**，不会冲掉已有 MCP 服务。
   写完不会自动生效——到连接器管理页右上角「自定义连接器」点「信任」。

---

## 三、已完成并验证的内容（不用重做）

- ✅ 协议握手 + 20 工具全部注册（stdio JSON-RPC 实测）。
- ✅ 16 条只读路径在真实模型上回归通过（2026-11-04，模型 `插件开发.db1`）。
- ✅ 纯逻辑断言通过：组合过滤、UDA 类型转换、`summarize` 的 `top_n` 后总计不变、
  PDF 防覆盖文件名、空模型/空选择集保护。
- ✅ Tekla 路径**多盘符自动探测**（2026-11-04 修复：Tekla 从 C 盘搬到 D 盘后探测失效，
  现在扫 `A:\`~`Z:\` 的 Program Files；`tekla_server.py`、`diagnose.py`、`install.ps1` 三处同步）。
- ✅ v1 时期图纸全周期实测通过（在模型 `乡村服务驿站` 上：建图→明细→排视图→PDF→删除），
  PDF 导出确认**不需要** PDF 打印机驱动，`PrintDrawing()` 直接出合法 PDF。
- ✅ BOM 导出 CSV（utf-8-sig）与 Excel（openpyxl，冻结首行+筛选）实测。
- ✅ UDA 写入链路实测（set→读回→还原，未调 CommitChanges 时磁盘不变）。
- ✅ `install.ps1` 合并写配置实测（预置其它 MCP 服务不被冲掉）。

### v2.1.0 新增（2026-11-07）

- ✅ 建模/组件工具已实现并注册：`tekla_create_parts`、`tekla_create_components`、
  `tekla_modify_parts`、`tekla_delete_objects`。全部「先预览、再 `confirm=True`」，
  批量插入后一次 `CommitChanges()` 并回查 GUID。
- ✅ 组件类层次已处理：系统节点走 `Connection`/`Detail`，自定义组件走 `Component`；
  未指定 `kind` 时按编号自动排序逐个尝试（`Insert` 失败不写模型）。
- ✅ 参数层覆盖：坐标四种写法（`"x,y,z"`/`"(x,y,z)"`/数组/对象）、
  中文构件别名、`Position` 平面/旋转/深度三向偏移、组件属性与属性文件。
- ✅ 21 个单元测试通过（`python -m unittest discover -s tests`），
  用 `tests/fake_tekla.py` 模拟 pythonnet 的元组返回、GUID 与 Enumerator，
  无需 Windows/Tekla 即可回归参数校验、插入、提交、修改、删除回查。
- ✅ 建模工具已在真实模型回归（2026-11-08，`插件开发.db1`，Tekla 2026.0）：
  建柱/梁/多边形板/折梁、改等级与名称、1014 底板节点（柱+底板）、按 GUID 删除；
  删除后 `part/assembly` 计数回到 4，测试数据已清理。
- ✅ 过程中修正两处真实 API 差异：系统节点要用 `Connection`/`Detail`
  （没有 `SetComponentInput`），且 `Connection` 通常必须给主+次构件；
  `Component` 只用于自定义组件。
- ✅ 远程调试通道：`tools/tekla_remote_bridge.py`（Windows，需 token）+
  `tools/remote_relay.py`（代理侧中继）。Windows 主动轮询，不用开入站端口；
  远程代理通过中继调用 MCP 工具，方便在没有交互式客户端的机器上做真实模型回归。

---

## 四、未完成的任务（按优先级，接手从这里开始）

### 1. v2.1.0 建模工具未在真实模型回归 ⚠️ 优先做

代码与单测已完成，缺的是真实 Tekla 环境的冒烟：
1. `python demo_modeling.py` 看预览（不写模型）；
2. `python demo_modeling.py --confirm` 实建 1 柱 1 梁 1 板，记下返回 GUID；
3. `tekla_get_object_details(<GUID>)` 核对起止点/规格/材质；
4. `tekla_create_components` 试一个内置节点（如 1014），确认组件挂上主次构件；
5. `tekla_modify_parts` 改等级 → `tekla_delete_objects` 清理。
重点观察：`ContourPoint(point, None)` 的 Chamfer 是否被接受、
`Component.SetAttribute` 对内建节点是否生效、`LoadAttributesFromFile` 的文件名约定。

### 2. v2.0.0 图纸写路径未在真实图纸上回归 ⚠️ 优先做

v2 重构了图纸工具（mark 定位、`tekla_manage_drawing`、PDF 文件校验），
但当前验证模型里 **0 张图纸**，所以 `tekla_manage_drawing(action='open')`、
`place_views`、`create_drawings`（confirm=True 真实建图）、`export_drawings_pdf`
的真实执行路径**在 v2 代码上没跑过**（v1 代码跑过，v2 改动集中在定位与校验逻辑）。

做法：在 Tekla 里建/打开一个有构件的模型，然后：
1. `tekla_create_drawings(scope='all', limit=5, confirm=True)` 建几张图；
2. `tekla_list_drawings` 拿到 mark/index，逐个测 `get_drawing_detail`、
   `manage_drawing(action='open')` → `close`（注意 save_current 参数）、`place_views`、
   `export_drawings_pdf`；
3. `tekla_delete_drawings(confirm=True)` 清理。
注意：模型未编号时（编号含 `?`）只能建出 1 张单件图——这是 Tekla 行为不是 bug，
工具已带 hint。先编号再测多图。

### 3. svMCP 的高价值能力未移植（原任务背景）

参考仓库已附在本包 `reference/` 下（离线可用，原始地址见下）：
- https://github.com/40ushek/svMCP （C# 双进程，50+ 工具，图纸标注排版最强）
- https://github.com/YuriyKirillov/TeklaMCPServer （Python，GUID 定位思路）

调研结论（已验证的事实，别再走弯路）：
- svMCP 的**标注/尺寸防碰撞算法**是 C# + Tekla 内部 API（`DrawingInternal`）实现，
  不能直接搬进 pythonnet。要借鉴得先读
  `reference/svMCP/src/TeklaMcpServer.Api/Drawing/` 下的几何计算思路，用公开 API 重写。
- **Tekla 2026 公开 API 没有图纸 GUID**（`Drawing` 类只有 `Mark` 可当稳定标识）。
  svMCP 用内部接口拿 GUID——我们选择只用公开 `Mark`，不要为对齐功能名引入内部 API。
- TeklaMCPServer 有过滤恒真（空条件=选全模型）、空实现伪成功的问题，已规避；
  它的**组件(Component)操作**思路已在 v2.1.0 用公开 API 实现
  （`tekla_create_components`），并补了主次构件与定位点校验。
- `DrawingCreator.CreateDrawings(AutoRule, Identifier)` 规则自动出图接口存在但未封装成工具。

### 4. 文档更新到 2.1.0（本次已补建模章节，其余仍偏 v1）

包内 `README.md` / `INSTALL.md` 还停留在 16 工具/v1 描述。需要：
- 工具表补 4 个新工具（`select_by_filter`、`get_object_details`、`check_numbering`、`manage_drawing`）；
- 图纸定位说明改为「mark 优先，index 退回」；
- 新增「多盘符自动探测」说明。

### 5. 可选：离线安装包

目标机器没网时 `pip install` 会失败。做法：在本机
`.venv\Scripts\pip download -d wheels/ pythonnet mcp openpyxl`（约 +10MB），
`install.ps1` 里检测到 `wheels/` 就改用 `--no-index --find-links wheels`。

### 6. 可选增强（按详图工作流价值排序）

- 批量改 Class/材质/规格（`Part.Profile`/`Material` 属性可直接 set + Modify）；
- 按阶段筛选导出（`GetPhase` 相关）；参考模型/螺栓/焊缝对象遍历；
- BOM 模板自定义列；图纸批量重命名（`Drawing.Name` 可写，已反射确认）。

---

## 五、硬性技术约束（违反就连不上/就出错，全是实测血泪）

**Tekla 2026 环境事实：**
1. `PYTHONNET_RUNTIME` 必须是 `netfx`（Tekla 2026 主程序是 .NET Framework 4.8 宿主，
   不是 .NET 8——网上 2025 版资料有误）。
2. `sys.path` 里 `bin\Net48Runtime` **必须排在 `bin` 之前**（`bin` 里没有
   `Tekla.Structures.dll`，只在 Net48Runtime 里；顺序反了报
   `ModuleNotFoundError: No module named 'Tekla'`，极具误导性）。
3. Tekla **没有**「允许外部程序访问」开关，也不需要（网传说法是错的）。
4. 64 位 Tekla 必须配 64 位 Python。

**Tekla 2026 API 破坏性变更（网上教程大量过时）：**
5. `Model.UI.Selector` 已删除，替代 `ModelObjectSelector`（`GetSelectedObjects()`/`Select(ArrayList)`）。
6. `ModelObjectEnum` 没有 `PART`，拿全部构件要遍历 `GetAllObjects()` 再 `isinstance(o, Part)`。
7. `Part` 没有 `Weight` 属性，用 `GetReportProperty("WEIGHT")`；`SURFACE_AREA` 不存在，涂装面积用 `AREA`。
8. 报表属性里字符串 `CLASS` 读不出来，等级走 `Part.Class` 属性。
9. `Phase.GetAllPhases` 删了，用 `Model().GetPhases()`。
10. `DrawingTypeEnum` 不存在；`DrawingHandler.CreateDrawings(...)` 也不存在。
    建图走 `SinglePartDrawing(ident)` / `AssemblyDrawing(ident)` + `Insert()`。
11. GUID 定位链路：`Model().GetIdentifierByGUID(guid_string)` →
    `SelectModelObject(identifier)`（2026 的 `SelectModelObject` 收 `Identifier`，不收 `Guid`）。

**pythonnet 3.2 特性：**
12. `clr.Reference[T]` 已移除。out 参数返回元组：`GetReportProperty(name, placeholder)`
    返回 `(ok, value)`；`GetUserProperty` 有 string/double/int 三重重载，
    **必须传对应类型占位值**才能命中重载。
13. 未编号模型所有构件 `PART_POS` 都是 `P0(?)`，Tekla **静默拒绝**为同一零件位置建
    第二张单件图（`Insert()` 返回 False 不抛异常）。批量出图前必须编号。

**MCP / pythonnet 工程约束：**
14. stdio 下 stdout 是 JSON-RPC 通道，**任何 `print()` 都会杀死连接**，日志必须走 stderr
    （`logging.basicConfig(stream=sys.stderr)`，且要在 `import clr` 之前配置）。
15. 所有 Tekla 调用要过同一把锁（`_synchronized` 装饰器）——MCP 并发调用同时读写模型会崩。
16. Python 3.13 下 Windows 商店假 python 别名（`python.exe` 0 字节）会混进探测候选，
    探测要**实测执行**而不是只看路径存在。

**PowerShell 5.1 三个坑（改 `install.ps1` 必看）：**
17. `.ps1` 必须存 **UTF-8 带 BOM**，否则按 GBK 解析、中文乱码并破坏字符串引号。
    （Write/Edit 工具写完要检查/补 BOM。）
18. 变量名**不区分大小写**：`foreach ($root in ...)` 会覆盖 `$Root`。
19. 向原生程序传参**会吞内嵌双引号**：`python -c "...'utf-8'..."` 里的引号会丢，
    复杂代码放独立 `.py` 文件（本包的 `format_json.py` 就是这么来的）。

---

## 六、关键代码导读（`tekla_server.py`）

- `_detect_tekla_bin()`（~77 行）：多盘符 Tekla 探测，环境变量 `TEKLA_BIN` 优先。
- `_require_api()`：懒加载 clr + AddReference，失败抛 RuntimeError（工具层捕获转 JSON error）。
- `_synchronized`：全局 `threading.Lock`，所有工具装饰。
- `_targets(scope, class_number)`：按 `selected`/`class`/`all` 取 Part 列表（统一入口）。
- `_part_row(obj)` / `_report_str()`：报表属性读取与 BOM 行构造。
- `_get_uda` / `_set_uda(obj, name, value, value_type='auto')`：三重重载处理。
- `_find_drawing(mark, name, index)`：图纸定位（mark 优先）。
- `_available_path(path, overwrite)`：PDF 防覆盖。
- `_load_json_items` / `_parse_point` / `_parse_points`：建模入参解析（四种坐标写法）。
- `_new_part` / `_build_part`：按类型建 `Beam`/`PolyBeam`/`ContourPlate`，套属性与位置。
- `_apply_part_attributes` / `_apply_part_position`：规格/材质/等级 + 三向偏移，
  创建与修改共用同一套 setter。
- `_resolve_parts`：组件的主/次构件 GUID 解析（复用 `_objects_from_guids`）。
- 工具函数一律 `@server.tool()` + `@_synchronized`，返回 dict（错误放 `error` 键，不抛协议异常）。

## 七、参考仓库

> 原始离线包里的 `reference/`（约 6.4MB）没有放进 git，避免把第三方仓库塞进本仓库；
> 需要时按下面的地址 clone。

- `reference/svMCP/` — C# 双进程桥接实现，50+ 工具。重点看
  `src/TeklaMcpServer.Api/Drawing/`（图纸查询/排版几何）与 `Tools/`（工具粒度设计）。
- `reference/TeklaMCPServer/` — 简单 Python 实现，重点看 `tekla_utils.py` 的
  GUID 定位与组合筛选（注意其缺陷，别照抄）。
- `tests/` + `tests/fake_tekla.py` — 离线单测，改建模逻辑后先跑它。
- `demo_modeling.py` — 真实 Tekla 上的建模冒烟脚本（默认只预览）。
