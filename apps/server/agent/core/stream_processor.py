"""
Stream processor for file content streaming.

Handles the state machine for streaming file content from LLM output
to the database. Manages <file> and </file> marker parsing and
buffer size limits.

State transitions:
    IDLE -> WAITING_START (when pending_file_write is set)
    WAITING_START -> WRITING (when <file> marker is found)
    WRITING -> IDLE (when </file> marker is found, buffer limit exceeded, or stream ends)
"""

import re
from dataclasses import dataclass
from enum import StrEnum

from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

# Streaming markers
FILE_START_MARKER = "<file>"
FILE_END_MARKER = "</file>"

# Upper bound on the length of a (whitespace/case) marker variant, e.g.
# "<  /  file  >". We retain this many trailing chars as "possibly part of an
# end marker" so a variant split across stream chunks is never flushed as file
# content before it can be reassembled and matched.
MAX_MARKER_LEN = 32

# 容错正则表达式 - 匹配各种 <file> 变体（避免误匹配 <filex>/<filename>）
FILE_START_PATTERN = re.compile(
    r'<\s*[Ff][Ii][Ll][Ee]\b(?:\s+[^>]*)?>',
    re.IGNORECASE
)
FILE_END_PATTERN = re.compile(
    r'<\s*/\s*[Ff][Ii][Ll][Ee]\b\s*>',
    re.IGNORECASE
)

# 边界情况：内容中合法包含 <file> 文本的模式
ESCAPED_FILE_PATTERNS = [
    r'`<\s*/?\s*[Ff][Ii][Ll][Ee]\b[^>]*>`',  # 行内代码
    r'```[\s\S]*?<\s*/?\s*[Ff][Ii][Ll][Ee]\b[^>]*>[\s\S]*?```',  # 代码块
    r'&lt;file&gt;',       # HTML 转义
]

# 代码块围栏定界符（与 ESCAPED_FILE_PATTERNS 中代码块模式的定界一致）
FENCE = "```"

# 标记判定结果：真实标记 / 代码上下文中的字面量 / 需要更多 chunk 才能判定
_MARKER_REAL = "real"
_MARKER_PROTECTED = "protected"
_MARKER_AMBIGUOUS = "ambiguous"


def _classify_marker(
    buffer: str,
    match: "re.Match[str]",
    prev_char: str,
    fence_open: bool,
    at_eof: bool,
    is_end_marker: bool = False,
) -> str:
    """判定 buffer 中一个 <file>/</file> 命中是真实标记还是代码上下文中的字面量。

    与 ESCAPED_FILE_PATTERNS 共用同一套语义：紧邻反引号的行内代码、闭合 ```
    围栏内的命中都是字面量。流式场景下反引号/围栏可能尚未闭合，此时返回
    ambiguous 表示需等待后续 chunk；at_eof 时不会再有后续内容，按闭合永远
    不会发生处理（即视为真实标记）。

    prev_char / fence_open 提供 buffer 之前已被消费内容的上下文（紧邻的
    前一个字符、``` 围栏的开合奇偶性），使判定不受 flush 边界影响。

    围栏保护必须是**局部**判定：只有"缓冲后面还能看到 ```"是不够的，那个
    ``` 完全可能属于 </file> 之后的聊天叙述（模型举例、给格式模板）。正文里
    ``` 数量为奇数时（写了开围栏忘了闭围栏，或用单个 ``` 作分隔）fence_open
    会恒为真，真实结束标记因此被判成字面量，捕获永不结束，最终把整段叙述当
    正文落库。所以对结束标记额外要求：把当前命中判为字面量之后，围栏闭合处
    之后还必须存在另一个 </file> 候选可以充当真正的结束标记；否则宁可按真实
    标记处理（is_end_marker=True 分支）。开始标记不受此约束——WAITING_START
    在流结束时另有 at_eof 兜底复扫（见 _search_start_marker），保守判定不会
    造成正文丢失。
    """
    start, end = match.start(), match.end()
    if fence_open:
        fence_close = buffer.find(FENCE, end)
        if fence_close < 0:
            # 围栏尚未闭合，后续 chunk 仍可能补上
            return _MARKER_REAL if at_eof else _MARKER_AMBIGUOUS
        if not is_end_marker:
            return _MARKER_PROTECTED
        if FILE_END_PATTERN.search(buffer, fence_close + len(FENCE)) is not None:
            # 围栏闭合之后还有别的结束标记候选：当前命中确实被围栏包住
            return _MARKER_PROTECTED
        # 之后再无候选：判为字面量将导致文件捕获永不结束
        return _MARKER_REAL if at_eof else _MARKER_AMBIGUOUS
    before = buffer[start - 1] if start > 0 else prev_char
    if before == "`":
        if end < len(buffer):
            return _MARKER_PROTECTED if buffer[end] == "`" else _MARKER_REAL
        return _MARKER_REAL if at_eof else _MARKER_AMBIGUOUS
    return _MARKER_REAL

# Buffer size limit (1MB)
BUFFER_MAX_SIZE = 1024 * 1024

# 正文以真实 </file> 收尾后，模型偶尔在同一回复里再补一对空的 <file></file>
# （仅空白隔开）。处理器此时已回到 IDLE，这对标签会原样进对话气泡。只在
# "刚收尾、尚未出现任何非空白文字"的窗口里识别并丢弃这一对；窗口内一旦出现
# 别的文字（叙述、反引号、围栏、非空块）就立即关闭，原样输出。
_EMPTY_FILE_PAIR_COMPACT = "<file></file>"
_EMPTY_FILE_PAIR_PATTERN = re.compile(r"<\s*file\s*>\s*<\s*/\s*file\s*>", re.IGNORECASE)

# 悬置（ambiguous）标记候选的等待上限：一个 </file> 候选因反引号/围栏未闭合被
# 挂起后，若其后又累积了这么多字符仍无法判定，就按真实结束标记处理。没有这个
# 上限时，一个永远等不到闭合围栏的候选会把其后的全部叙述扣在缓冲里，直到流
# 结束才一次性落库。
AMBIGUOUS_MARKER_MAX_PENDING = 4096

# Turn-control / workflow markers that belong to the model's chat narration and
# must never be persisted as file content. If an unterminated <file> body carries
# one of these, it means chat text (handoff summaries, a trailing [TASK_COMPLETE],
# cross-agent narration) leaked into the file-write state instead of real prose.
CONTROL_MARKERS: tuple[str, ...] = (
    "[task_complete]",
    "[workflow_stopped]",
    "[clarification_needed]",
)


def _contains_control_marker(text: str) -> bool:
    """True when text carries a turn-control marker (i.e. chat narration, not prose)."""
    lowered = text.lower()
    return any(marker in lowered for marker in CONTROL_MARKERS)


def normalize_file_markers(content: str) -> str:
    """
    将各种 <file> 变体标准化为正确格式。

    处理的变体：
    - <File>, <FILE> -> <file>
    - < file >, <  file  > -> <file>
    - <file name="xxx"> -> <file>
    - </File>, </FILE> -> </file>

    边界情况处理：
    - 代码块中的 <file> 不处理
    - 行内代码中的 `<file>` 不处理
    """
    original = content

    # 1. 保护需要保留的内容（用占位符替换）
    placeholders = {}
    placeholder_idx = 0

    for pattern in ESCAPED_FILE_PATTERNS:
        for match in re.finditer(pattern, content):
            placeholder = f"__FILE_PLACEHOLDER_{placeholder_idx}__"
            placeholders[placeholder] = match.group(0)
            content = content.replace(match.group(0), placeholder, 1)
            placeholder_idx += 1

    # 2. 标准化开始和结束标记
    content = FILE_START_PATTERN.sub('<file>', content)
    content = FILE_END_PATTERN.sub('</file>', content)

    # 3. 恢复被保护的内容
    for placeholder, original_text in placeholders.items():
        content = content.replace(placeholder, original_text)

    # 4. 记录变体情况
    if content != original:
        log_with_context(
            logger,
            30,  # WARNING
            "Normalized non-standard file markers",
            original_snippet=original[:100],
        )

    return content


class StreamState(StrEnum):
    """State of the stream processor."""

    IDLE = "idle"
    WAITING_START = "waiting_start"
    WRITING = "writing"
    # After a >1MB file body was force-completed we keep consuming (and
    # discarding) the rest of the body until </file>, so the overflow tail is
    # NOT rerouted into the chat transcript.
    DRAINING = "draining"


@dataclass
class StreamResult:
    """Result of processing a content chunk."""

    # Content to yield as normal conversation
    conversation_content: str = ""
    # Content to yield after file streaming is complete (preserves ordering)
    conversation_content_after_file: str = ""
    # Content to yield as file content
    file_content: str = ""
    # File ID for file content events
    file_id: str = ""
    # Whether file writing is complete
    file_complete: bool = False
    # Content length for history summary
    content_length: int = 0
    # Whether buffer limit was exceeded
    buffer_exceeded: bool = False
    # Final accumulated content (only set when file_complete=True)
    final_content: str = ""
    # 该次完成是否来自"流结束时没等到 </file> 的自动补全"。正文很可能只写了
    # 一半（模型漏写结尾标记是本项目公认的高频故障），调用方据此决定要不要
    # 用它整体覆盖一个原本已有正文的文件（见 StreamAdapter._complete_file_write）。
    auto_completed: bool = False


@dataclass
class StreamProcessor:
    """
    State machine for streaming file content.

    Manages the parsing of <file> and </file> markers in LLM output
    and routes content appropriately.
    """

    project_id: str = ""
    user_id: str | None = None

    # Current state
    state: StreamState = StreamState.IDLE

    # File being written
    file_id: str = ""

    # Buffers
    content_buffer: str = ""  # Confirmed file content
    temp_buffer: str = ""  # Buffer for potential partial markers
    history_buffer: str = ""  # Content for LLM history

    # 跨 flush 的标记判定上下文：temp_buffer 之前最后一个已消费字符（判断
    # 行内代码的前置反引号）、已消费内容中 ``` 围栏的开合奇偶性
    prev_char: str = ""
    fence_open: bool = False

    # 刚以真实 </file> 收尾的窗口（见 _EMPTY_FILE_PAIR_PATTERN）及其中暂扣的
    # "可能是空标签对"的前缀
    after_close: bool = False
    idle_hold: str = ""

    def reset(self) -> None:
        """Reset processor to idle state."""
        self.state = StreamState.IDLE
        self.file_id = ""
        self.content_buffer = ""
        self.temp_buffer = ""
        self.history_buffer = ""
        self.prev_char = ""
        self.fence_open = False
        self.after_close = False
        self.idle_hold = ""

    def start_file_write(self, file_id: str) -> None:
        """
        Start a new file write operation.

        Called when an empty file is created and we expect
        the LLM to output content with <file>...</file> markers.
        """
        self.state = StreamState.WAITING_START
        self.file_id = file_id
        self.content_buffer = ""
        self.temp_buffer = ""
        self.history_buffer = ""
        self.prev_char = ""
        self.fence_open = False
        self.after_close = False
        self.idle_hold = ""

        log_with_context(
            logger,
            20,  # INFO
            "Stream processor started file write mode",
            project_id=self.project_id,
            user_id=self.user_id,
            file_id=file_id,
        )

    @property
    def is_active(self) -> bool:
        """Check if processor is actively handling file content.

        真实 </file> 收尾后的窗口也算活跃：适配器在 MESSAGE_END / 出错 / 下一次
        create_file 前 / 流结束时都会据此调用 finalize_on_stream_end，把窗口里
        暂扣的文字交还对话，保证不丢字。
        """
        return self.state != StreamState.IDLE or self.after_close or bool(self.idle_hold)

    def process_content(self, content: str) -> StreamResult:
        """
        Process a content chunk from LLM output.

        Returns a StreamResult indicating what to do with the content.
        """
        # 首先标准化 file 标记
        content = normalize_file_markers(content)

        if self.state == StreamState.IDLE:
            # Not in file writing mode - return as conversation
            if self.after_close:
                return StreamResult(conversation_content=self._filter_after_close(content))
            return StreamResult(conversation_content=content)

        if self.state == StreamState.WAITING_START:
            return self._process_waiting_start(content)

        if self.state == StreamState.WRITING:
            return self._process_writing(content)

        if self.state == StreamState.DRAINING:
            return self._process_draining(content)

        return StreamResult(conversation_content=content)

    def _filter_after_close(self, text: str) -> str:
        """收尾窗口内：丢弃仅由空白隔开的空 <file></file>，其余原样返回。

        标签对可能被切成 "<"、"file"、">\n" 这样的碎 chunk（逐 chunk 的
        normalize_file_markers 看不到跨 chunk 的整体），所以仍可能拼成空标签对
        的前缀先暂扣在 idle_hold，凑齐两个 ">" 后再判定：精确匹配才丢弃，否则
        关窗原样输出。暂扣长度有上限，窗口关闭/流结束时由 _release_after_close
        交还。
        """
        buf = self.idle_hold + text
        self.idle_hold = ""
        out = ""
        while buf and self.after_close:
            stripped = buf.lstrip()
            out += buf[: len(buf) - len(stripped)]
            buf = stripped
            if not buf:
                break
            first_close = buf.find(">")
            second_close = buf.find(">", first_close + 1) if first_close >= 0 else -1
            if second_close < 0:
                compact = re.sub(r"\s+", "", buf).lower()
                if _EMPTY_FILE_PAIR_COMPACT.startswith(compact) and len(buf) <= MAX_MARKER_LEN * 2:
                    self.idle_hold = buf  # 还可能是空标签对，等后续 chunk
                    return out
                self.after_close = False
                break
            if _EMPTY_FILE_PAIR_PATTERN.fullmatch(buf[: second_close + 1]):
                log_with_context(
                    logger,
                    30,  # WARNING
                    "Dropping empty <file></file> pair emitted after a completed file",
                    project_id=self.project_id,
                    user_id=self.user_id,
                )
                buf = buf[second_close + 1:]
                continue
            self.after_close = False
        return out + buf

    def _release_after_close(self) -> str:
        """关闭收尾窗口，交还暂扣的文字（流/agent 结束、新捕获开始前）。"""
        held = self.idle_hold
        self.idle_hold = ""
        self.after_close = False
        return held

    def _process_waiting_start(self, content: str) -> StreamResult:
        """Process content while waiting for <file> marker."""
        self.temp_buffer += content

        # Match against the accumulated buffer with the fault-tolerant regex (not
        # the exact literal), so a case/whitespace variant marker split across
        # chunks (e.g. "<FI" + "LE>") is still detected once reassembled.
        # 叙述中处于行内代码/``` 围栏内的 <file> 是字面量，跳过继续向后找；
        # 尚无法判定（反引号/围栏未闭合）时继续缓冲等待后续 chunk。
        start_match = self._find_real_start_marker()
        if start_match:
            # Found start marker - split content around the matched span
            before_marker = self.temp_buffer[: start_match.start()]
            after_marker = self.temp_buffer[start_match.end():]

            # Transition to writing state
            self.state = StreamState.WRITING
            self.temp_buffer = after_marker
            self.prev_char = ""
            self.fence_open = False

            log_with_context(
                logger,
                20,  # INFO
                "Found <file> marker, starting file content",
                project_id=self.project_id,
                user_id=self.user_id,
                file_id=self.file_id,
            )

            # Continue processing the remainder in the same chunk.
            # This is required for "<file>...</file>" arriving in one delta.
            result = self._process_writing("")
            if before_marker:
                result.conversation_content = before_marker + result.conversation_content
            return result

        # Check buffer size limit
        if len(self.temp_buffer) > BUFFER_MAX_SIZE:
            log_with_context(
                logger,
                40,  # ERROR
                "temp_buffer exceeded max size while waiting for <file> marker",
                project_id=self.project_id,
                user_id=self.user_id,
                buffer_size=len(self.temp_buffer),
                max_size=BUFFER_MAX_SIZE,
            )
            # Output buffered content as normal text and reset
            buffered = self.temp_buffer
            self.reset()
            return StreamResult(
                conversation_content=buffered,
                buffer_exceeded=True,
            )

        # Keep buffering
        return StreamResult()

    def _find_real_start_marker(self, at_eof: bool = False) -> "re.Match[str] | None":
        """在 WAITING_START 缓冲中找第一个真实的 <file> 开始标记。

        WAITING_START 不 flush，缓冲从头累积，围栏/反引号上下文直接在
        缓冲内计算。命中为字面量则跳过；命中歧义（需等待后续 chunk）则
        返回 None 继续缓冲。

        at_eof 时不会再有后续 chunk：歧义按「闭合永不发生」判为真实标记；
        若首轮仍无命中，再放宽 ``` 围栏保护兜底扫一遍（见
        _search_start_marker）。
        """
        match = self._search_start_marker(at_eof=at_eof, ignore_fence=False)
        if match is None and at_eof:
            return self._search_start_marker(at_eof=True, ignore_fence=True)
        return match

    def _search_start_marker(
        self, at_eof: bool, ignore_fence: bool
    ) -> "re.Match[str] | None":
        """扫描 WAITING_START 缓冲中的 <file> 候选。

        ignore_fence 是流结束时的兜底：模型常照抄提示词范例，把整块
        <file>…</file> 包在 ``` 围栏里输出，此时围栏保护会让开始标记永远
        判为字面量。create_file 已产出空文件、缓冲本就应当是文件正文，把带
        标记的整章正文倒进聊天严格劣于当作正文写入。为避免把纯格式说明写进
        文件，兜底只认围栏内成对出现的 <file>…</file>；行内代码保护不放宽。
        """
        buffer = self.temp_buffer
        pos = 0
        fence_open = False
        counted = 0
        while True:
            match = FILE_START_PATTERN.search(buffer, pos)
            if match is None:
                return None
            if ignore_fence:
                if FILE_END_PATTERN.search(buffer, match.end()) is None:
                    return None
            else:
                if buffer.count(FENCE, counted, match.start()) % 2 == 1:
                    fence_open = not fence_open
                counted = match.start()
            verdict = _classify_marker(
                buffer, match, "", fence_open, at_eof=at_eof, is_end_marker=False
            )
            if verdict == _MARKER_REAL:
                return match
            if verdict == _MARKER_AMBIGUOUS:
                return None
            pos = match.end()

    def _process_writing(self, content: str) -> StreamResult:
        """Process content while writing file."""
        self.temp_buffer += content
        return self._scan_writing_buffer(at_eof=False)

    def _scan_writing_buffer(self, at_eof: bool) -> StreamResult:
        """扫描 WRITING 缓冲：跳过代码上下文中的字面量标记，转义真实的
        嵌套 <file>，在真实 </file> 处完成文件。"""
        buffer = self.temp_buffer
        pos = 0
        fence_open = self.fence_open
        counted = 0
        while True:
            start_match = FILE_START_PATTERN.search(buffer, pos)
            end_match = FILE_END_PATTERN.search(buffer, pos)
            if start_match is None and end_match is None:
                break
            if end_match is None or (
                start_match is not None and start_match.start() < end_match.start()
            ):
                kind, match = "start", start_match
            else:
                kind, match = "end", end_match
            if buffer.count(FENCE, counted, match.start()) % 2 == 1:
                fence_open = not fence_open
            counted = match.start()
            verdict = _classify_marker(
                buffer, match, self.prev_char, fence_open, at_eof,
                is_end_marker=(kind == "end"),
            )
            if verdict == _MARKER_AMBIGUOUS and (
                len(buffer) - match.start() > AMBIGUOUS_MARKER_MAX_PENDING
            ):
                # 悬置太久：候选之后已累积超过上限仍等不到闭合的反引号/围栏，
                # 按流结束语义降级为真实标记，避免捕获无限期挂起。
                log_with_context(
                    logger,
                    30,  # WARNING
                    "Marker candidate suspended too long, treating it as a real marker",
                    project_id=self.project_id,
                    user_id=self.user_id,
                    file_id=self.file_id,
                    marker_kind=kind,
                    pending_length=len(buffer) - match.start(),
                )
                verdict = _classify_marker(
                    buffer, match, self.prev_char, fence_open, at_eof=True,
                    is_end_marker=(kind == "end"),
                )
            if verdict == _MARKER_PROTECTED:
                pos = match.end()
                continue
            if verdict == _MARKER_AMBIGUOUS:
                # 反引号/围栏是否闭合尚无法判定：flush 命中点之前的内容，
                # 其余留在缓冲等待后续 chunk。挂起的尾部同样受总量上限
                # 约束，超限时整体走溢出强制完成，防止缓冲无界增长。
                self.temp_buffer = buffer
                if len(self.content_buffer) + len(buffer) > BUFFER_MAX_SIZE:
                    return self._flush_file_content(len(buffer))
                return self._flush_file_content(match.start())
            if kind == "end":
                self.temp_buffer = buffer
                return self._handle_end_marker(match)
            # 真实的嵌套 <file> 标记（异常情况），容忍大小写/空白变体
            # 这可能是 LLM 幻觉或用户在内容中讨论 XML 标签
            log_with_context(
                logger,
                30,  # WARNING
                "Detected nested <file> marker in content, escaping it",
                project_id=self.project_id,
                user_id=self.user_id,
                file_id=self.file_id,
            )
            buffer = buffer[: match.start()] + '&lt;file&gt;' + buffer[match.end():]
            pos = match.start() + len('&lt;file&gt;')
            counted = pos

        self.temp_buffer = buffer
        if at_eof:
            return StreamResult()

        # No end marker yet - check if we have safe content to send. Reserve a
        # full marker-variant's worth of trailing chars (not just len('</file>'))
        # so a split whitespace variant like "< /file >" is never flushed before
        # it can be reassembled and matched.
        if len(buffer) > MAX_MARKER_LEN:
            return self._flush_file_content(len(buffer) - MAX_MARKER_LEN)

        # Buffer too small, just accumulate
        return StreamResult()

    def _handle_end_marker(self, end_match: "re.Match[str]") -> StreamResult:
        """Handle finding the </file> end marker."""
        before_marker = self.temp_buffer[: end_match.start()]
        after_marker = self.temp_buffer[end_match.end():]

        # Add remaining content to buffers
        if before_marker:
            self.content_buffer += before_marker
            self.history_buffer += before_marker

        # Calculate final content length and save final content
        final_content = self.content_buffer
        content_length = len(final_content)
        file_id = self.file_id
        file_content = before_marker

        log_with_context(
            logger,
            20,  # INFO
            "Found </file> marker, completing file write",
            project_id=self.project_id,
            user_id=self.user_id,
            file_id=file_id,
            content_length=content_length,
        )

        # Reset state
        self.reset()
        # 只有真实 </file> 收尾才打开窗口；自动补全 / DRAINING / WAITING_START
        # 的收尾路径都不经过这里
        self.after_close = True
        after_marker = self._filter_after_close(after_marker)

        return StreamResult(
            file_content=file_content,
            file_id=file_id,
            file_complete=True,
            content_length=content_length,
            final_content=final_content,
            conversation_content_after_file=after_marker if after_marker else "",
        )

    def _adjust_cut_for_backtick_run(self, cut: int) -> int:
        """把切分点左移出反引号连续段，避免 ``` 围栏被 flush 边界拆散后
        无法再按整体统计开合奇偶性。"""
        while (
            0 < cut < len(self.temp_buffer)
            and self.temp_buffer[cut] == "`"
            and self.temp_buffer[cut - 1] == "`"
        ):
            cut -= 1
        return cut

    def _flush_file_content(self, cut: int) -> StreamResult:
        """Send content that's safe (not potentially part of end marker)."""
        cut = self._adjust_cut_for_backtick_run(cut)
        safe_content = self.temp_buffer[:cut]
        if not safe_content:
            return StreamResult()

        # Check content_buffer size limit
        new_content_size = len(self.content_buffer) + len(safe_content)
        if new_content_size > BUFFER_MAX_SIZE:
            log_with_context(
                logger,
                40,  # ERROR
                "content_buffer exceeded max size while waiting for </file> marker",
                project_id=self.project_id,
                user_id=self.user_id,
                buffer_size=new_content_size,
                max_size=BUFFER_MAX_SIZE,
            )
            # Force-complete the file at the size cap, then DRAIN the rest of the
            # body until </file> instead of resetting to IDLE — a reset would
            # reroute the (still-streaming) overflow tail into the chat transcript.
            final_content = self.content_buffer + safe_content
            content_length = len(final_content)
            file_id = self.file_id

            if safe_content.count(FENCE) % 2 == 1:
                self.fence_open = not self.fence_open
            self.prev_char = safe_content[-1]
            self._begin_draining()

            return StreamResult(
                file_content=safe_content,
                file_id=file_id,
                file_complete=True,
                content_length=content_length,
                buffer_exceeded=True,
                final_content=final_content,
            )

        # Add to buffers and keep remaining in temp
        self.content_buffer += safe_content
        self.history_buffer += safe_content
        self.temp_buffer = self.temp_buffer[cut:]
        if safe_content.count(FENCE) % 2 == 1:
            self.fence_open = not self.fence_open
        self.prev_char = safe_content[-1]

        return StreamResult(
            file_content=safe_content,
            file_id=self.file_id,
        )

    def _begin_draining(self) -> None:
        """Enter DRAINING after a force-completed (oversized) file.

        The file content is already persisted up to the cap; from here we only
        need enough tail buffer to detect the closing </file>.
        """
        self.state = StreamState.DRAINING
        self.content_buffer = ""
        self.history_buffer = ""
        self.temp_buffer = ""

    def _discard_drained(self, cut: int) -> None:
        """丢弃 drain 缓冲的前段，并维护标记判定所需的跨段上下文。"""
        cut = self._adjust_cut_for_backtick_run(cut)
        dropped = self.temp_buffer[:cut]
        if not dropped:
            return
        if dropped.count(FENCE) % 2 == 1:
            self.fence_open = not self.fence_open
        self.prev_char = dropped[-1]
        self.temp_buffer = self.temp_buffer[cut:]

    def _process_draining(self, content: str) -> StreamResult:
        """Swallow the overflow tail of a force-completed file until </file>.

        Content here is the discarded remainder of an oversized body; only text
        AFTER the closing </file> is surfaced as normal conversation, so nothing
        leaks into the chat transcript.
        """
        self.temp_buffer += content
        buffer = self.temp_buffer
        pos = 0
        fence_open = self.fence_open
        counted = 0
        while True:
            end_match = FILE_END_PATTERN.search(buffer, pos)
            if end_match is None:
                break
            if buffer.count(FENCE, counted, end_match.start()) % 2 == 1:
                fence_open = not fence_open
            counted = end_match.start()
            verdict = _classify_marker(
                buffer, end_match, self.prev_char, fence_open, at_eof=False,
                is_end_marker=True,
            )
            if verdict == _MARKER_PROTECTED:
                pos = end_match.end()
                continue
            if verdict == _MARKER_AMBIGUOUS:
                # 保留待判定的尾部，前段照常丢弃；歧义迟迟不消除时按未闭合
                # 围栏内容处理（连同候选一起丢弃），保证 drain 缓冲有界
                if len(buffer) > BUFFER_MAX_SIZE:
                    self._discard_drained(len(buffer) - MAX_MARKER_LEN)
                else:
                    self._discard_drained(end_match.start())
                return StreamResult()
            after_marker = buffer[end_match.end():]
            self.reset()
            return StreamResult(conversation_content=after_marker or "")

        # Bound the drain buffer — only the tail is needed to spot a split </file>.
        if len(self.temp_buffer) > MAX_MARKER_LEN:
            self._discard_drained(len(self.temp_buffer) - MAX_MARKER_LEN)
        return StreamResult()

    def finalize_on_stream_end(self) -> StreamResult:
        """
        Finalize buffered content when upstream stream ends.

        Behavior:
        - WAITING_START: flush buffered text back to conversation
        - WRITING: auto-complete file content even without </file>
        """
        if self.state == StreamState.IDLE:
            return StreamResult(conversation_content=self._release_after_close())

        if self.state == StreamState.DRAINING:
            # File was already force-completed at the size cap; discard the
            # remaining (unterminated) overflow tail rather than leaking it.
            self.reset()
            return StreamResult()

        if self.state == StreamState.WAITING_START:
            # 流结束后悬置的反引号/围栏不会再闭合：先按 at_eof 语义复扫开始
            # 标记，否则叙述里一个未闭合的 ``` 就会让整章正文（连同 <file>
            # 原始标记）当作对话倒进聊天、文件留空。命中后转入 WRITING 并复用
            # 下方 WRITING 分支（含 control-marker 污染守卫）完成文件。
            start_match = self._find_real_start_marker(at_eof=True)
            if start_match is not None:
                before_marker = self.temp_buffer[: start_match.start()]
                self.state = StreamState.WRITING
                self.temp_buffer = self.temp_buffer[start_match.end():]
                self.prev_char = ""
                self.fence_open = False
                result = self.finalize_on_stream_end()
                if before_marker:
                    result.conversation_content = (
                        before_marker + result.conversation_content
                    )
                return result

            buffered = self.temp_buffer
            log_with_context(
                logger,
                30,  # WARNING
                "Stream ended before <file> marker, flushing buffered text",
                project_id=self.project_id,
                user_id=self.user_id,
                file_id=self.file_id,
                buffered_length=len(buffered),
            )
            self.reset()
            return StreamResult(conversation_content=buffered)

        if self.state == StreamState.WRITING:
            # 流结束后悬置的反引号/围栏不会再闭合：先按 at_eof 语义重扫缓冲，
            # 此前因歧义挂起的 </file> 到这里可确认为真实结束标记
            scanned = self._scan_writing_buffer(at_eof=True)
            if scanned.file_complete:
                scanned.conversation_content_after_file += self._release_after_close()
                return scanned

            trailing = self.temp_buffer
            if trailing:
                self.content_buffer += trailing
                self.history_buffer += trailing

            final_content = self.content_buffer
            file_id = self.file_id
            content_length = len(final_content)

            # Contamination guard: an unterminated <file> whose buffered body
            # carries turn-control markers is chat narration / cross-agent
            # handoff text that leaked into the file-write state — NOT chapter
            # prose. Surface it as conversation and leave the (empty) file
            # untouched rather than persisting the narration as the file's
            # content. Legitimately truncated prose (no control marker) is still
            # auto-completed below.
            if _contains_control_marker(final_content):
                log_with_context(
                    logger,
                    30,  # WARNING
                    "Stream ended with an unterminated <file> containing control "
                    "markers; discarding buffered narration instead of writing it "
                    "to the file",
                    project_id=self.project_id,
                    user_id=self.user_id,
                    file_id=file_id,
                    content_length=content_length,
                )
                self.reset()
                return StreamResult(conversation_content=final_content)

            # 同族守卫：正文里还留着 </file> 字面量，说明某个结束标记被判成了
            # 代码块字面量（围栏奇偶性异常），其后的聊天叙述被一路吞进了正文。
            # 这种缓冲不是干净的章节正文，不能当成截断正文补全落库。
            if FILE_END_PATTERN.search(final_content) is not None:
                log_with_context(
                    logger,
                    30,  # WARNING
                    "Stream ended with an unterminated <file> whose body still "
                    "contains a </file> literal; refusing to persist it",
                    project_id=self.project_id,
                    user_id=self.user_id,
                    file_id=file_id,
                    content_length=content_length,
                )
                self.reset()
                return StreamResult(conversation_content=final_content)

            log_with_context(
                logger,
                30,  # WARNING
                "Stream ended without </file>, auto-completing file write",
                project_id=self.project_id,
                user_id=self.user_id,
                file_id=file_id,
                content_length=content_length,
            )

            self.reset()
            return StreamResult(
                file_content=trailing,
                file_id=file_id,
                file_complete=True,
                content_length=content_length,
                final_content=final_content,
                auto_completed=True,
            )

        return StreamResult()

    def get_final_content(self) -> str:
        """Get the final accumulated file content."""
        return self.content_buffer

    def get_history_buffer(self) -> str:
        """Get the content buffer for LLM history."""
        return self.history_buffer
