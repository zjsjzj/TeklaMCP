"""v2.1.0 建模工具冒烟脚本：在真实 Tekla 模型里建 1 柱 1 梁 1 板。

默认只预览（不写模型）；确认后加 --confirm：
    .venv\\Scripts\\python.exe demo_modeling.py
    .venv\\Scripts\\python.exe demo_modeling.py --confirm
    .venv\\Scripts\\python.exe demo_modeling.py --confirm --clean   # 建完再删掉

脚本直接调用 tekla_server.py 里的 MCP 工具函数，不需要 MCP 客户端。
"""

import argparse
import json
import sys

import tekla_server as ts

PARTS = [
    {"type": "column", "profile": "HEA240", "material": "S355", "class": "2",
     "name": "冒烟-柱", "start": "0,0,0", "end": "0,0,6000"},
    {"type": "beam", "profile": "IPE300", "material": "S355", "class": "3",
     "name": "冒烟-梁", "start": "0,0,6000", "end": "6000,0,6000"},
    {"type": "contour_plate", "profile": "PL20", "material": "S355",
     "class": "4", "name": "冒烟-底板",
     "points": [[-200, -200, 0], [200, -200, 0], [200, 200, 0], [-200, 200, 0]]},
]


def _print(title, payload):
    print(f"\n=== {title} ===")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Tekla MCP 建模工具冒烟")
    parser.add_argument("--confirm", action="store_true",
                        help="真的插入模型并提交（默认只预览）")
    parser.add_argument("--clean", action="store_true",
                        help="建完后删除本次创建的构件")
    args = parser.parse_args()

    payload = json.dumps(PARTS, ensure_ascii=False)
    preview = ts.tekla_create_parts(payload)
    _print("预览", preview)
    if preview.get("error") or preview.get("invalid"):
        print("\n预览未通过，先修参数。", file=sys.stderr)
        return 1
    if not args.confirm:
        print("\n只做了预览；确认无误后加 --confirm。")
        return 0

    created = ts.tekla_create_parts(payload, confirm=True)
    _print("创建结果", created)
    if not created.get("created"):
        return 1

    guids = [item["guid"] for item in created.get("created_items", [])]
    if guids:
        _print("回查详情", ts.tekla_get_object_details(",".join(guids)))

    if args.clean and guids:
        _print("清理", ts.tekla_delete_objects(",".join(guids), confirm=True))
    elif guids:
        print("\n构件已保留；核对后用 tekla_delete_objects 删除。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
