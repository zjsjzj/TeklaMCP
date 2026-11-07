# Tekla MCP Server — 安装说明

把 Tekla Structures 的 Open API 包成 MCP 工具，让 AI 直接读模型、导清单、出图纸。

v2.0.0 的 20 个查询/清单/图纸工具已在 **Tekla Structures 2026.0 + Windows + Python 3.13** 上完整实测通过；
v2.1.0 新增 4 个建模/组件工具（单测通过，首次使用请按 `HANDOFF.md` 做真实模型回归）。

---

## 前置条件

| 项 | 要求 |
|---|---|
| 操作系统 | Windows（Tekla Open API 只有 Windows 版） |
| Tekla Structures | 2019 及以上，安装目录下必须有 `bin\Net48Runtime` |
| Python | **64 位** 3.10+（位数必须和 Tekla 一致） |
| 运行状态 | 装好配置后，Tekla 必须**正在运行**且**已打开模型** |

> ⚠️ 不需要任何「允许外部程序访问」开关 —— Tekla 2025 之后没有这个设置，别去找。
> 能连上的条件只有三条：Tekla 在运行 + 模型已打开 + 双方都是 64 位。

---

## 安装

```powershell
# 在解压出来的目录里执行
powershell -ExecutionPolicy Bypass -File install.ps1
```

脚本会依次完成：检查 Python 位数 → 探测 Tekla 安装路径 → 建 `.venv` 装依赖 → 写 MCP 配置 → 跑连接诊断。

指定客户端：

```powershell
install.ps1 -Client workbuddy   # 默认，写 ~/.workbuddy-ai/mcp.json
install.ps1 -Client claude      # 写 %APPDATA%\Claude\claude_desktop_config.json
install.ps1 -Client cline       # 写 VS Code 的 cline_mcp_settings.json
install.ps1 -Client none        # 只生成 config/generated.json，自己复制
```

其它参数：`-PythonExe "D:\Python313\python.exe"` 指定解释器，`-SkipDiagnose` 跳过诊断。

### 激活

写进配置**不会自动生效**，各客户端还要手动激活一次：

- **WorkBuddy AI**：连接器管理页 → 右上角「自定义连接器」→ 点「信任」
- **Claude Desktop**：完全退出（含托盘图标）再启动
- **Cline**：设置面板里把 tekla 开关打开

---

## 手动安装（不想用脚本）

```powershell
# 1. 建环境
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt

# 2. 验证能连上 Tekla（这一步必须先过）
.venv\Scripts\python.exe diagnose.py

# 3. 复制 config\ 下对应客户端的 JSON，把 <安装目录> 换成当前目录的绝对路径
```

`diagnose.py` 全绿（或至少打印出模型名称和构件数）就说明环境没问题。

---

## 配置示例

```json
{
  "mcpServers": {
    "tekla": {
      "type": "stdio",
      "command": "D:\\tools\\tekla-mcp-server\\.venv\\Scripts\\python.exe",
      "args": ["D:\\tools\\tekla-mcp-server\\tekla_server.py"],
      "env": {
        "PYTHONNET_RUNTIME": "netfx",
        "TEKLA_BIN": "C:\\Program Files\\Tekla Structures\\2026.0\\bin"
      }
    }
  }
}
```

三个字段都别改错：

- `command` 必须是 **venv 里**的 python.exe，不是系统 Python
- `PYTHONNET_RUNTIME` 必须是 `netfx`。Tekla 2026 主程序是 .NET Framework 4.8 宿主，设成 `coreclr` 会加载失败
- `TEKLA_BIN` 可以省略 —— 服务端会自动扫描安装目录取版本最高的那个；多版本共存或装在非默认盘时才需要显式指定

---

## 验证装好了

装完在客户端里说一句：

> 检查一下 Tekla 连接状态

正常返回形如：

```json
{
  "connected": true,
  "tekla_bin": "C:\\Program Files\\Tekla Structures\\2026.0\\bin",
  "model_name": "你的模型.db1",
  "model_path": "F:\\...\\模型目录"
}
```

连不上就看 `diagnose.py` 的输出，它会明确指出卡在哪一步。

---

## 常见问题

**`ModuleNotFoundError: No module named 'Tekla'`**
程序集路径顺序错了。`bin\Net48Runtime` 必须排在 `bin` 前面 —— 因为 `bin` 里根本没有 `Tekla.Structures.dll`，它只在 `Net48Runtime` 里。这个报错极具误导性，会让人以为没装 Tekla。包里的代码已经处理好了，如果你自己改过路径，检查这一点。

**`connected: false`**
按顺序查：Tekla 是否在运行 → 是否打开了模型（不是停在启动页）→ Python 是否 64 位。

**`pythonnet` 加载报 BadImageFormatException**
位数不一致。32 位 Python 加载不了 64 位 Tekla 的程序集。

**MCP 客户端里看不到工具**
配置没激活，或者 JSON 路径写错。用客户端自带的 MCP 日志排查；WorkBuddy 是「自定义连接器 → 信任」，Claude 需要完全重启。

**连续建图纸只有第一张成功**
模型没编号。未编号时所有构件的 `PART_POS` 都是 `P0(?)`，Tekla 拒绝为同一零件位置建第二张单件图，而且 `Insert()` 静默返回 `False` 不报错。先在 Tekla 里执行编号（Numbering）再批量出图。

---

## 工具清单

共 24 个，分五类：

**建模（v2.1.0）** `tekla_create_parts`（梁/柱/折梁/板/基础，JSON 批量）/ `tekla_create_components`（节点/连接）/ `tekla_modify_parts` / `tekla_delete_objects`

**查询** `tekla_check_connection` / `tekla_get_model_info` / `tekla_list_phases` / `tekla_find_parts` / `tekla_get_selected_objects` / `tekla_get_object_details` / `tekla_select_by_filter` / `tekla_check_numbering`

**清单统计** `tekla_export_bom`（CSV/Excel）/ `tekla_summarize`（按 Class/阶段/材质/规格/名称汇总）

**UDA** `tekla_read_udas` / `tekla_write_udas`（需 `confirm=True`）

**选择集与图纸** `tekla_select_by_class` / `tekla_list_drawings` / `tekla_get_drawing_detail` / `tekla_manage_drawing` / `tekla_create_drawings` / `tekla_place_views` / `tekla_delete_drawings` / `tekla_export_drawings_pdf`

写操作（建模、改 UDA、改选择集、建图、删图、排视图）第一次调用都只返回预览，必须再传 `confirm=True` 才真正执行。

详细说明和实测记录见 `README.md`。
