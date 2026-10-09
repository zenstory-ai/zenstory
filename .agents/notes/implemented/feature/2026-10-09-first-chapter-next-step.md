# Agent Note: 拿到框架后一键写第一章；建议与入口有统计

Status: implemented

本 note 部分推翻了 `bug-fix/2026-10-03-agent-handoff-respects-user-scope.md` 中「只要规划就直接结束，不在结尾追问是否开始写作」这一条：规划师在「项目还没有任何正文、这一轮交付了框架或大纲」时可以只问一句是否写第一章。交接、范围裁剪与只读约束不变。

## Problem

10-03 至 10-09 的新用户复盘：只有框架、没有正文的 31 个作者里只有 2 人第二天之后回来，有正文的 34 人里有 6 人；35/71 的作者主动要过正文，12 人第一条消息就要。但拿到框架之后，三层都在把作者往「继续规划」推：

1. `PLANNER_PROMPT` 要求只规划时「不追问是否开始写作」，所以 #162 的「确认上一轮提议的下一步就去写」几乎没有机会触发。
2. 建议提示词三个类型都写着「只讨论/规划时不催写正文」，建议芯片偏向补设定。
3. 点建议只填进输入框，还要再按一次发送；也没有统计，无法知道建议有没有人用。

付费墙这边：没建项目就离开的作者里，12 人是从素材页点去定价页的。他们想拆书，免费版一次也试不了，页面也没说可以先在对话里贴一章让 AI 拆。

## Decision

- **规划师只问一句**：项目里还没有任何正文、这一轮交付的是故事框架或大纲时，最后单独一行问「要我按这份大纲开始写第一章吗？」（剧本「第 1 集」，短篇「开篇」）。以问号收尾会让计划交接停下等回答；作者回「好」由路由的「确认提议的下一步」规则送到 writer。其余情况仍不追问。
- **建议**：三个类型都去掉「不催写正文」，改为「框架/大纲已有、还没有任何正文时，第一条建议必须是开始写第一章 / 开篇 / 第 1 集」。
- **下一步按钮**：新增 `GET /api/v1/projects/{id}/next-step`（`services/project_next_step.py`，不调模型）：大纲 / 角色 / 设定里至少一份非空，且 draft / script 都为空或不存在时，返回 `{kind: "write_first_chapter", label, message}`，文案按项目类型与 `Accept-Language`。ChatPanel 在打开项目和每轮结束后刷新，非生成中、额度未用完时在输入框上方显示 `NextStepCard`：一句「故事框架已经有了，可以开始写正文了」、主按钮（如「写第一章正文」）点击直接发送，「先不用」本次会话内对该项目不再显示。
- **入口统计**：前端 `metadata.entry` 标明消息从哪里发出：`suggestion`（建议原样发出，编辑过不算）、`next_step`、`dashboard_idea`；输入框直接发送不带 entry（只在统计里记为 `typed`）。`ai_chat_submitted` 带 `entry`；新增 `ai_suggestion_picked`（`via` click/tab、`index`、`source` ready/fallback）、`ai_next_step_clicked`、`ai_next_step_dismissed`。服务端 `stream_request received` 日志记 `entry`（只认上述已知值）。entry 暂不随消息落库。
- **素材付费墙**：预览页加一句「免费版也可以先试：在作品的对话里贴一章参考正文，让 AI 拆人物、节奏和爽点…」，不改权益。

## Alternatives considered

- **让所有建议芯片点击即发送。** 最强理由：省一次点击，作者大多直接采用建议。被否：免费版一天 10 条，芯片是模型生成的、常需要改两个字，误点直接扣一条；只有「写第一章」这一个确定性入口值得一键发送。
- **在前端用文件树判断「有框架没正文」。** 最强理由：不加接口。被否：文件树只在编辑器侧边栏组件里加载，不是共享状态，内容是否为空也不在树节点里；一个只查计数的接口更可靠，测试也在真实边界上。
- **把下一步塞进 `/suggest` 的返回。** 最强理由：少一个请求，复用现有缓存。被否：`/suggest` 有小时与每日上限、会降级成本地兜底，还有 10 分钟本地缓存；下一步是确定性的，不该跟着建议一起失效或过期。
- **给免费用户一次真实的拆书试用。** 最强理由：最直接回应「冲着拆书来」的作者。被否（本批不做）：每次约 ¥2，需要一次性计数器、拆分访问权限、只拆前 N 章的新参数，还会改动额度与退款记账；10-08 拆的书多为「部分完成」，原因未查清前不宜放给新用户。先用文案引导验证需求。

## Consequences

- 收益：拿到框架的作者在聊天里就能看到并一键开写；规划师主动提议、建议芯片也指向第一章。建议是否被采用、下一步按钮的点击和关闭第一次可以统计。
- 代价：规划师的提问会让计划交接停下（这正是想要的），但作者如果本来就想继续规划，需要多回一句；`next-step` 每轮结束多一次轻量请求。entry 只在统计与日志里，数据库分析暂时看不到。素材页文案不能替代真实试用，转化效果待观察。

## Verification

- 后端：`cd apps/server && .venv/bin/pytest tests/test_api/test_project_next_step.py -q --no-cov`：没有文件 / 只有大纲 / 三种项目类型 / 空正文文件 / 已有正文或剧本，以及非作者访问。
- 前端：`cd apps/web && pnpm exec vitest run src/components/__tests__/ChatPanel.mount.test.tsx src/components/__tests__/MessageInput.test.tsx src/components/__tests__/ChatPanel.newSessionLifetime.test.tsx src/components/__tests__/ChatPanel.projectSwitch.test.tsx`：下一步按钮一键发送并带 `entry=next_step`、关闭后不再出现；建议原样发出带 `entry=suggestion`，编辑后不带。
