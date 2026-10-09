# Agent Note: 编辑器跟随本轮 AI 的每一次写入；有未保存修改时进入对比而不是覆盖

Status: implemented

## Problem

新手审计（PR163 审计 P1-4）：在第 1 章页面发「接着写第二章」，AI 先用 create_file 流式写出第 2 章 v1，同一轮里写作者又改了几处（v2）。这一轮结束后编辑器一直停在 v1，字数和 AI 回复里报的不一致；作者在 v1 上打字，就是在旧稿上改。

服务端日志定位了根因：第二次写入发生在 `parallel_execute` 的子任务里（`parallel_execute completed: 2/2 tasks`）。stream adapter 只为顶层 `edit_file` 发 `file_edit_*` 事件，`parallel_execute` / `update_file` 成功后前端只刷新文件树，从不让编辑器重读打开着的文件，所以日志里只有 `GET file-tree`，没有 `GET file`。

另外，原来的 AI 刷新路径（`triggerEditorRefresh` → `loadData`）在作者有未保存修改时会直接把编辑器显示的正文换成服务端版本，作者刚写的字从屏幕上消失（草稿还在 SimpleEditor 内部，要等下一次保存撞 409 才以对比的形式出现），属于「静默覆盖」。

审计里还记录了一次「打字后页面卡死、renderer CPU 100%」。在 mocked Chromium（Playwright，真实页面、按审计的事件顺序喂 SSE）和 vitest 里都没有复现：过期编辑器里打字后，自动保存带旧令牌撞 409，进入对比，页面正常响应。真实章节的段落对比耗时也只有毫秒级。卡死根因未定；本次修复去掉了触发它的前置状态（编辑器停在旧版本、作者在旧稿上打字）。

## Decision

- `useChatStreaming`：`update_file`、`parallel_execute` 成功后，让编辑器重读作者当前打开的文件（`refreshOpenFileFromServer`，正在流式写入的那份除外，它有自己的收尾重读）。一轮结束（`onComplete`）或出错（`onError`）时，只要本轮写过文件或服务端确认有改动，再兜底重读一次。
- `Editor`：AI / 撤销触发的刷新改走 `syncOpenFileFromServer`：
  - 服务端版本与编辑器当前基线相同：什么都不做。
  - 编辑器没有未保存修改：跟随服务端版本（正文、标题、保存令牌）。
  - 有未保存修改：不替换作者的文字；服务端版本作为对比基线和新的保存令牌，打开对比审阅（基线 = AI 的最新版本，提案 = 作者的文字），并提示「AI 刚改过这个文件，你还有没保存的修改。两边的内容都在，请在对比里逐处选择要保留哪一份。」已经在对比审阅中时不重复进入。
- `SimpleEditor`：对比审阅打开期间不排防抖自动保存。审阅完成时由 `handleFinishReview` 带服务端令牌整体写回；此前若继续按旧令牌自动保存，只会撞 409 并把作者已做的逐处选择重置。
- 回归：`Editor.aiRoundSync.test.tsx` 用真实 ProjectProvider + useChatStreaming 回调 + Editor/SimpleEditor 复现「同轮 create 再 edit / parallel_execute，然后打字」；`e2e/editor-ai-round-sync-mocked.spec.ts` 在真实页面里喂同样的 SSE 序列，并覆盖「作者有未保存修改时进入对比、不发 PUT」。`Editor.lifecycle.test.tsx` 原来断言「AI 刷新把编辑器显示换成服务端正文」的评估用例改为断言新约定。

## Alternatives considered

- **服务端为 parallel_execute 子任务补发 file_edit_* 事件。** 最强理由：前端无需特判，进度条也能显示子任务改了哪几处。被否：子任务结果被截断后没有可靠的逐条编辑明细，还要改事件协议；前端「写入后重读打开的文件」对任何写入路径都成立，以后新增的写工具也不会漏。服务端补事件可以以后单独做。
- **有未保存修改时仍然显示服务端版本，等保存撞 409 再进对比（原行为）。** 最强理由：改动最小，令牌语义不变。被否：在保存之前作者看到自己刚写的字消失了，继续打字时打在服务端版本上，之前的修改只剩在内部草稿里，很容易被后续输入覆盖掉。
- **每次刷新都直接进对比。** 最强理由：作者对每次 AI 改动都有确认机会。被否：没有未保存修改时，对比只是在复述 AI 已经落库的改动，打断写作；AI 改动在版本历史里可回看。

## Consequences

- 收益：同一轮里 AI 多次写同一章时，编辑器停在最后一次写入之后；作者手里的未保存文字不会被替换，冲突以对比的形式出现；对比期间不会被自动保存打断。
- 代价：每次 parallel_execute / update_file 成功和每轮结束各多一次 `GET /files/{id}`（只针对打开着的文件）；对比打开期间作者的修改要等完成对比才落库。
- 未解决：审计中的一次性页面卡死未能复现，若再次出现需要带 performance profile 重新定位。
