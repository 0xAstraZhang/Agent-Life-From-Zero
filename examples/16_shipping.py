"""运行最终 CLI 的离线 JSON 入口。"""
from minicode.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["--demo", "--json"]))
