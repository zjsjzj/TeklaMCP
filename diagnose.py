"""
Tekla MCP 环境诊断脚本
=====================
配 MCP 之前先跑这个，确认 Python 能否真正连上运行中的 Tekla。

用法：
    .venv\\Scripts\\python.exe diagnose.py
"""

import os
import platform
import sys
import traceback

def _detect_tekla_bin():
    """环境变量 TEKLA_BIN 优先，否则扫描安装目录取版本最高的。"""
    env = os.environ.get("TEKLA_BIN", "").strip()
    if env and os.path.isdir(env):
        return env
    # 扫描每个盘符下的常见安装位置（Tekla 可能装在任意盘）
    import string
    found = []
    for drive in string.ascii_uppercase:
        for base in (rf"{drive}:\Program Files\Tekla Structures",
                     rf"{drive}:\Program Files (x86)\Tekla Structures"):
            if not os.path.isdir(base):
                continue
            for name in os.listdir(base):
                bin_dir = os.path.join(base, name, "bin")
                if os.path.isdir(os.path.join(bin_dir, "Net48Runtime")):
                    try:
                        key = tuple(int(p) for p in name.split("."))
                    except ValueError:
                        key = (0,)
                    found.append((key, bin_dir))
    return sorted(found, reverse=True)[0][1] if found else ""


TEKLA_BIN = _detect_tekla_bin()
TEKLA_NET48 = os.path.join(TEKLA_BIN, "Net48Runtime") if TEKLA_BIN else ""


def hr(title):
    print(f"\n{'=' * 62}\n{title}\n{'=' * 62}")


def main():
    hr("1. 基础环境")
    print(f"Python      : {sys.version.split()[0]}")
    print(f"位数        : {platform.architecture()[0]}  (必须 64bit)")

    from importlib.metadata import version as _ver

    for pkg in ("pythonnet", "clr-loader", "mcp"):
        try:
            print(f"{pkg:<12}: {_ver(pkg)}")
        except Exception:
            print(f"{pkg:<12}: (未安装)")

    hr("2. Tekla 安装")
    print(f"TEKLA_BIN   : {TEKLA_BIN}")
    print(f"  目录存在  : {os.path.isdir(TEKLA_BIN)}")
    print(f"Net48Runtime: {os.path.isdir(TEKLA_NET48)}  <- 必须有，否则加载不了")
    for f in ("Tekla.Structures.Model.dll",):
        print(f"  {f:<28}: {'OK' if os.path.isfile(os.path.join(TEKLA_BIN, f)) else '缺失'}")
    for f in ("Tekla.Structures.dll", "Net48RunTime.dll"):
        p = os.path.join(TEKLA_NET48, f)
        print(f"  Net48/{f:<22}: {'OK' if os.path.isfile(p) else '缺失'}")

    # 判定宿主运行时：有 coreclr.dll 或 runtimeconfig 才是 .NET 8
    is_net8 = os.path.isfile(os.path.join(TEKLA_BIN, "coreclr.dll")) or os.path.isfile(
        os.path.join(TEKLA_BIN, "TeklaStructures.runtimeconfig.json")
    )
    host = ".NET 8 (coreclr)" if is_net8 else ".NET Framework 4.8"
    print(f"\nTekla 宿主  : {host}")
    print(f"应选运行时  : {'coreclr' if is_net8 else 'netfx'}")

    hr("3. 加载 Open API")
    # 顺序敏感：Net48Runtime 必须在 bin 之前
    for p in (TEKLA_BIN, TEKLA_NET48):
        if os.path.isdir(p) and p not in sys.path:
            sys.path.insert(0, p)

    os.environ["PYTHONNET_RUNTIME"] = "coreclr" if is_net8 else "netfx"
    print(f"runtime     : {os.environ['PYTHONNET_RUNTIME']}")

    try:
        import clr

        clr.AddReference("Tekla.Structures")
        clr.AddReference("Tekla.Structures.Model")
        from Tekla.Structures.Model import Model, Part  # noqa: F401

        from Tekla.Structures.Model import (Beam, Component,  # noqa: F401
                                            ComponentInput, ContourPlate,
                                            ContourPoint, PolyBeam, Position)
        from Tekla.Structures.Geometry3d import Point  # noqa: F401

        print("AddReference: OK")
        print("import      : OK")
        print("建模 API    : OK (Beam/PolyBeam/ContourPlate/Component/Position)")
    except Exception:
        print("!! 加载程序集失败（最常见原因：Net48Runtime 没排在 bin 前面）：")
        traceback.print_exc()
        return 1

    hr("4. 连接 Tekla")
    try:
        m = Model()
        status = m.GetConnectionStatus()
        print(f"GetConnectionStatus() -> {status}")
        if not status:
            print("\n>>> 连接失败。按顺序检查：")
            print("    1. Tekla Structures 是否在运行？")
            print("    2. 是否已经打开一个模型（不是停在启动页）？")
            print("    3. Python 与 Tekla 是否都是 64 位？")
            print("    注意：不存在『允许外部程序访问』这类开关，不用去找。")
            return 2

        info = m.GetInfo()
        print(f"模型名称    : {info.ModelName}")
        print(f"模型路径    : {info.ModelPath}")

        n = 0
        en = m.GetModelObjectSelector().GetAllObjects()
        while en.MoveNext():
            if isinstance(en.Current, Part):
                n += 1
        print(f"构件数量    : {n}")
        print(f"阶段        : {[(p.PhaseNumber, p.PhaseName) for p in m.GetPhases()]}")
        print("\n>>> 全部通过，可以配置 MCP 了。")
        return 0
    except Exception:
        print("!! 连接过程抛异常：")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
