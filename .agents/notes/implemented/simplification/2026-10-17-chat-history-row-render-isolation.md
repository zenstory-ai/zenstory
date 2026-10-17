# 聊天流式更新隔离历史消息行

## Problem

`MessageList` 在每次流式内容刷新时重新执行全部 `Row`。每行接收整份消息数组、全局 streaming/thinking/pending 状态，重复清洗正文、过滤工具及格式化时间；列表层 memo 无法隔离内部历史行。已有决定 `2026-04-29-chat-message-list-without-virtualization.md` 要求保留稳定非虚拟化 DOM，不能以删消息或富文本高度测量换速度。

## Decision

已增加真实 `MessageList` 的 Markdown 子组件执行计数回归与行为对照；旧实现 4 项性能断言先失败、2 项行为对照通过。将 Row 所需输入收窄为单条不可变 message、前后 role、当前行 streaming/pending 状态和既有回调，再用 React 默认浅比较 memo；不维护额外缓存或自定义字段比较清单。非活动行不接收活动轮的 thinking 状态，消息对象、相邻分组、回调或 React context 变化仍正常更新。

回归覆盖历史消息不丢失、相邻角色头像/时间分组、反馈新回调、语言/登录头像/移动布局 context、流式正文与结束后的消息更新。只本地 commit，不推送、不部署、不改云配置。

## Alternatives considered

- **列表虚拟化。** 可限制超长会话 DOM，但富文本高度修正曾造成闪动，且不是本轮重复 React 执行的最小修复；保持原稳定 DOM 设计。
- **给原 Row 写自定义 comparator。** 改动表面较少，但必须从整份数组和所有回调/全局状态推导依赖，遗漏任一字段会形成旧闭包或陈旧 UI；选择显式窄 props 和默认浅比较。
- **全局缓存清洗结果。** 可减少每次消息数组替换的正文扫描，但增加文本生命周期和缓存失效问题；本批先消除流式刷新历史行的已知重复计算，剩余扫描单独测量。

## Consequences

预期历史行的额外执行从消息数乘流式帧数降为仅变化行；不减少 DOM/消息、流式刷新频率、Markdown 内容、上下文或模型输出。大列表父层仍需遍历，不声称已消除全部聊天性能开销；性能回归计数不是浏览器首帧或生产内存测量。

## Verification

- 使用 100 条历史助手消息和 20 次新流式正文刷新：Markdown 子组件执行从 2,100 次降为 100 次，消除这份夹具中 2,000 次未变化历史子树的额外执行（含初次渲染的总量下降 95.24%）。所有历史文本与最新流式正文仍可见；这是组件工作量计数，不是浏览器帧时长或真实 Markdown 解析耗时测量。
- 新旧消息对象替换、活动行 thinking/streaming 迁移、pending 反馈隔离、新回调、邻接角色分组和 auth/mobile/i18n context 均通过。既有调用方以不可变消息更新满足浅比较依赖，不添加自定义 comparator。
- MessageList 与新性能回归合计 68 项通过；前端全量 Vitest 为 4,675 passed、1 skipped（297 files）；ESLint、token/i18n 检查、TypeScript build 与 Vite/org/docs 构建通过。happy-dom 存在既有 localhost 资源请求/teardown 噪声，不把它当成真实浏览器体验证据。
- 原始红测、计数、静态构建和全量测试日志保存在本地 OMX performance-goal `frontend-backend-local-20261017` 工作记录；未做真实付费模型调用，也未执行生产或 staging 操作。
