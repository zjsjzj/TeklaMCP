"""把 JSON 文件重新格式化为标准 2 空格缩进（原地覆盖）。

install.ps1 用它整理生成的 MCP 配置 —— PowerShell 5.1 的 ConvertTo-Json
排版会错位，直接调 python 又会被 PS 吞掉参数里的引号，所以单独放个文件。

用法：
    python format_json.py <配置文件路径>
"""

import io
import json
import sys


def main(argv):
    if len(argv) < 2:
        print("用法: python format_json.py <json文件>", file=sys.stderr)
        return 2

    path = argv[1]
    try:
        with io.open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        print(f"解析失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
