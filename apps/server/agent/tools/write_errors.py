"""Failures to establish history anchors before committing an AI change."""


class PreWriteHistoryUnavailableError(RuntimeError):
    error_type = "pre_write_history_unavailable"

    def __init__(self, file_id: str, *, action: str) -> None:
        self.file_id = file_id
        self.action = action
        super().__init__("原文没能先存进历史版本，这次改动没有写入，原文保持不变；可以稍后重试。")
