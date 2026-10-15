# 流式显示缓冲不再成为可保存草稿

## Problem

create_file 建空壳后，Agent 可以直接用 edit_file 填正文，不经过 `<file>` 或 file_content_end。前端仍保留空的 streamingContent；编辑结束后的重读已经取得非空正文和新令牌，却把空显示稿与新 serverBaseline 比较并标为 dirty。切文件 flush 或三秒自动保存于是带有效新令牌将正文写空，REST CAS 正常放行。十文件本地合成回归在修复前失败，普通完整流式写入对照通过；该现象无需作者输入。

关联：[AI 写入后重读与作者草稿比较](2026-10-09-editor-follows-every-ai-write.md)、[退出前草稿恢复](2026-10-07-editor-draft-recovery-on-chunk-reload.md)。

## Decision

Editor 始终向 SimpleEditor.content 传作者/服务器正文，另传只用于展示的 streamingContent。SimpleEditor 只在文本显示、字数、段落、高度与滚动中使用显示缓冲；draft、dirty、恢复、保存队列和版本令牌只使用正文。成功且实际应用了编辑的 file_edit_end 按文件 ID 结束前端流占位，再沿既有重读路径显示已提交正文；ProjectContext 仍拒绝不匹配的结束事件。真正的作者未保存修改继续走原有三方比较，主动清空正文仍可保存。

## Alternatives considered

- **只在流式期间暂停自动保存。** 改动小、可消除定时器触发；但切文件 flush、卸载和明确保存仍可能提交已污染的 draft，故不能替代正文/显示分离。
- **拒绝空字符串保存。** 能直接挡住本例空 PUT；但会禁止作者明确清空正文，且截断的非空预览也会成为错误整篇稿，因此不采用。
- **只让 edit_end 关闭流占位。** 能让直接编辑立即可见；但其他重读与显示预览重叠时，显示稿仍能取得正文基线，因此同时分离显示与持久化输入。
- **改后端 SSE 协议为所有写入补 file_content_end。** 可集中表达文件完成；但会扩大协议范围，现有直接编辑事件已足以维护本地占位生命周期，本批不修改后端。

## Consequences

显示预览不再产生作者 dirty 草稿或绑定新保存令牌，直接编辑无需等整轮结束才显示正文。普通流式字数和滚动仍跟随预览。新增一个组件显示属性；保存 API、并发令牌、恢复选择、真实作者清空及后端 capture 规则保持原合同。验证使用本地 mock；真实 staging 与发布验收由发布批次记录，不用离线测试冒充生产结果。

## Verification

永久十文件回归在修复前为直接 edit 失败、普通 file stream 对照通过；修复后两者均通过，逐文件正文可见且没有额外 PUT。受影响的 Editor/SimpleEditor、文件流回调、生命周期与恢复测试合计 284 项通过，覆盖作者明确整篇清空、已提交正文后的流错误，以及显示预览不进入保存/flush/卸载草稿。TypeScript 与定向 ESLint 通过。新增同 fixture 的真实浏览器 mocked SSE 回归，本机已安装 Chrome 上 1 项通过，验证整轮结束前正文可见、跨文件切换及防抖窗口内没有额外 PUT；全程不调用模型。
