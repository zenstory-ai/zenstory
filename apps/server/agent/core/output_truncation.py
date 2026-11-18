"""Small replay hints derived from persisted assistant stop metadata."""

OUTPUT_TRUNCATED_REASON = "output_truncated"
OUTPUT_TRUNCATED_HISTORY = (
    "[output_truncated] 上一步在思考阶段达到输出上限，未交付该步骤的正文或工具操作。"
)
OUTPUT_TRUNCATED_RESUME = (
    "上一步在思考阶段达到输出上限，作者没有收到该步骤的交付。"
    "这次请简短思考，直接调用工具或开始写；任务较大时先完成授权范围内的第一部分，"
    "说明已经交付和剩余的部分，不承诺本轮一定全部完成。"
)
