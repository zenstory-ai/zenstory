# Agent Note: AI 在「等你回复」时不显示「写第一章正文」下一步卡

Status: implemented

收窄 [feature/2026-10-09-first-chapter-next-step](../feature/2026-10-09-first-chapter-next-step.md) 与 [feature/2026-10-09-next-step-card-remember-dismiss-and-one-entry](../feature/2026-10-09-next-step-card-remember-dismiss-and-one-entry.md) 的显示条件：只在「最新一轮以结构化澄清结束」时让位，其余规则不变。

证据：用户截图与真实项目只读核对（`audit-runs/20261009-180227-r5-fix-release-supervisor/real-project-656ae4e6/`）、只读根因调查（同目录 progress.md 21:35 条目）、r5 审计 r5serial/r5materials 日志。

## Problem

用户反馈「写第一章」提示一直循环弹。截图里 AI 回复「第一章现在还写不了，问题出在大纲本身」，下面是黄色「等你回复」澄清卡（问是否按默认设定先写第一章、是否先补第一章细纲），同屏底部却是「框架已就绪，可以开始写第一章了。」和「写第一章正文 / 先不用」。

**已证实**

1. 卡片是否显示只看文件：`services/project_progress.py` 的 `framework_ready`（大纲/角色/设定至少一份非空且没有非空正文）；`ChatPanel` 的 `showNextStep` 只再看「已关闭/生成中/额度」，不看最后一轮是不是在等作者回答。卡片在每轮结束和每次打开项目时刷新，澄清卡又随消息元数据持久化、刷新后回放，所以两者同屏会在每轮之后、每次刷新/重登后重复出现。
2. 真实项目 656ae4e6 的消息记录：作者两次（2026-10-10 03:55:17Z、03:56:01Z）发出卡片固定文案「按大纲写第一章正文」，writer 两次都以 `clarification_needed` 结束；文件没变、没有正文，`framework_ready` 仍为 true，卡片再次出现。卡片文案回答不了 AI 的问题（它问的是「按默认设定可以吗 / 逐条改」），按一次就花一条消息再被问一遍。
3. 「先不用」按项目记在 localStorage、跨刷新与重登有效，不是循环原因。

**上游内容（不在本次修复）**：该项目的细纲/卷纲/底层文档是在建项目后 1–3 秒经文件创建接口带内容写入的（v1 `change_type=create`、`change_source=system`），内容是重复占位句；不是 AI 生成，具体来自哪个客户端无法从数据判定。AI 拒绝按占位细纲落笔并给出默认设定、请作者确认，属于预期行为。

## Decision

- `lib/nextStep.ts` 新增 `isClarificationStop`（与「等你回复」渲染同一判定：`workflow_stopped` 且非 `user_stopped`，`reason=clarification_needed`，或旧数据无 reason 但带问题/细节）和 `latestRoundAwaitsReply(messages, streamItems)`：优先看正在进行/刚结束这一轮的流式条目，否则看最后一条消息——必须是助手消息，按显示顺序取最后一个回合结束项（`workflow_stopped`/`workflow_complete`/`iteration_exhausted`），回退到 `statusCards` 的最后一张。
- `MessageList` 两处（直播条目、历史状态卡）改用同一个 `isClarificationStop`，卡片隐藏规则与黄色「等你回复」不会各自漂移。
- `ChatPanel.showNextStep` 增加 `!isLoadingHistory && !latestRoundAwaitsReply(messages, streamRenderItems)`：历史没载入时不显示，最新一轮在等作者回答时不显示。作者回答后，只要下一轮没有再以澄清结束，卡片回到既有 `framework_ready` 规则；写出非空正文后照旧消失。卡片隐藏时，建议芯片里与卡片同义的「写第一章」类选项不再被过滤，可以作为对澄清的回答。
- 规划师以纯文字问「要我按这份大纲开始写第一章吗？」不是结构化澄清，卡片照旧显示（保留 10-09 的决定）。

不改：`framework_ready` 口径（单一角色卡也算框架、空正文不算写过）、「先不用」存储、按钮文案、路由继承、writer 的澄清行为与提示词。不加循环计数、不隐藏全部 CTA、不自动忽略澄清、不替作者补设定。

## Alternatives considered

- **服务端 `framework_ready` 读最后一条消息的澄清状态。** 最强理由：一个字段说清「能不能写」，作品卡片也能用。被否：进度接口是作品卡片与统计共用的纯文件口径，混入会话状态会让卡片进度随对话变化；前端已有持久化的澄清卡，判定放在显示层改动最小，也没有前后端部署时差。
- **点「写第一章正文」时如果有待答澄清，改发「可以，按你给的默认设定写」。** 最强理由：一键继续。被否：替作者回答设定问题，违背「不自动忽略澄清、不虚构设定」。
- **要求 `framework_ready` 必须有非空大纲文件。** 最强理由：只有角色卡时「按大纲写」名不副实（r5serial 10:55）。被否（本次不做）：改变 10-09 的既定口径并影响作品卡片，且与本次循环无因果。
- **检测连续几轮同一澄清后隐藏卡片。** 被否：是治标的循环检测。

## Consequences

- 截图那种状态下只剩「等你回复」和可作为回答的芯片，作者回答（例如「可以」或逐条改）后 writer 按回答继续；不会再被卡片引导去发一条回答不了问题的固定文案。
- 卡片仍是浏览器本地的「先不用」；新设备首次会再显示一次。
- 剩余（未改）：手动保存正文后卡片要到下一轮结束或重新打开项目才刷新；规划师若用结构化澄清提出「开始写第一章吗」，卡片这一轮会让位，由「等你回复」承担同一个问题。

## Verification

- `pnpm --dir apps/web exec vitest run src/lib/__tests__/nextStep.test.ts src/components/__tests__/ChatPanel.mount.test.tsx`：历史回放澄清待答 → 不显示；作者回答且下一轮正常结束 → 恢复显示；纯文字提问 → 显示；直播一轮以澄清结束 → 隐藏。去掉新条件时历史与直播两例失败。
