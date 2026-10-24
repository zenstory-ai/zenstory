"""结构化作者范围约束和实际产物缺失时的有界补写提示。"""

from __future__ import annotations

SCOPE_DIRECTIVE_CLARIFY_FIRST = (
    "作者这次的要求很笼统，没说改哪部分、想达到什么效果。本轮不修改任何文件（写文件工具会被拒绝），"
    "也不要交接给其他角色：先看一眼相关内容，然后用一句话问作者想改哪部分、想要什么效果，"
    "给 2–3 个具体选项（例如某一章或某一集的开头 / 台词 / 节奏 / 结尾钩子，或上一轮提过的建议），"
    "让作者能直接回复。不要先动手改、再问。"
)


PLANNING_AGENTS: frozenset[str] = frozenset({"planner", "hook_designer"})

SCOPE_DIRECTIVE_WITH_CONTENT_AFTER_PLANNING = (
    "作者本轮已经要求写正文：规划 / 设计完成后直接交接 writer 写正文，"
    "结尾不要问作者要不要开始写。"
)

PLANNED_HANDOFF_SCOPE_NOTE = (
    "上一位已经完成的部分（见已完成列表）不要重做，也不要修改刚交付的文件；"
    "只补作者要求的范围里还没完成的部分，不追加作者没要的产出（如分镜表、拍摄执行版、额外文档）。"
    "作者要的已经全部交付时，用一句话确认后直接结束。"
)



def chat_plan_correction_context() -> str:
    """补写已请求、但没有真实非空大纲产物的交接说明。"""
    return (
        "[系统提醒] 作者本轮请求的大纲尚未写进可读取的非空大纲文件。"
        "请在本轮已授权的范围内完成大纲交付：已有对应文件时用 edit_file，"
        "没有时用 create_file 创建 outline 类型文件，再用 <file>…</file> 完整写入。"
        "不要重做已有交付、扩大范围或写正文；写完简要告诉作者文件位置。"
    )
