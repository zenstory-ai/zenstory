"""Failures to establish recovery records before committing an AI change."""


class FileWriteRecoveryError(RuntimeError):
    error_type = "write_recovery_unavailable"

    def __init__(self, file_id: str, *, action: str) -> None:
        self.file_id = file_id
        self.action = action
        super().__init__("这次改动没能保存恢复记录，没有写入；原文和标题保持不变，可以稍后重试。")


class PreWriteHistoryUnavailableError(FileWriteRecoveryError):
    error_type = "pre_write_history_unavailable"

    def __init__(self, file_id: str, *, action: str) -> None:
        self.file_id = file_id
        self.action = action
        RuntimeError.__init__(self, "原文没能先存进历史版本，这次改动没有写入，原文保持不变；可以稍后重试。")
