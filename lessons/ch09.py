"""第 09 章最小原型：把一份已知的项目说明加入 system。"""


def project_prompt(workspace):
    instructions = (workspace / "MINICODE.md").read_text(encoding="utf-8")
    return "你是项目助手。请遵守下面的项目约定：\n" + instructions
