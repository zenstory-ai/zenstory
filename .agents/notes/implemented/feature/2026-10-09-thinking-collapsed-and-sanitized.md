# Agent Note: 「AI 思考过程」默认折叠，显示前去掉内部名字和标记

Status: implemented

## Problem

2026-10-08 新用户审计（三个 persona，#4）：聊天里的「AI 思考过程」默认展开，内容多是英文，而且直接写着内部规则和标识：`handoff_to_agent`、`edit_file`、`` `writer` ``、文件 UUID、整段 `<file …>` 原文、`[TASK_COMPLETE]`。作者把它当成 AI 的正式回复来读，既看不懂，也会以为产品出了问题。

展开状态无法靠「改默认值」解决：`ThinkingContent` 以前在每次挂载时都把当前状态写回 `zenstory_thinking_expanded`，所以几乎每个老用户的浏览器里都存着 `"true"`，这个值并不代表作者选择过展开。

## Decision

- `ThinkingContent` 的存储 key 改为 `zenstory_thinking_expanded_v2`，旧 key 一律忽略。默认折叠；只有作者点击切换时才写入 localStorage，挂载时不写。折叠状态下仍显示标题「AI 思考过程」和流式动画的三个点，按钮带 `aria-expanded`。
- 渲染前用新的 `lib/thinkingDisplay.ts` 的 `sanitizeThinkingForDisplay(text, t)` 生成显示用的副本，只影响显示，不改服务端，也不改持久化的 reasoning：
  - 含下划线的内部标识（`create_file`、`update_file`、`edit_file`、`delete_file`、`query_files`、`hybrid_search`、`update_project`、`parallel_execute`、`load_skill`、`read_skill_resource`、`hook_designer`、`quality_reviewer`，带不带反引号都算）换成 `chat:tool.*`、`chat:workflow.agents.*` 里已有的名字；没有对应 key 的（`handoff_to_agent`、`request_clarification`）写「AI」。嵌在更长的 snake_case 词里的不替换。
  - `planner`、`writer` 也是普通英文单词，只在被反引号包住时替换，正文里的 writer 保持原样。
  - UUID 换成「…」。
  - 去掉 `[TASK_COMPLETE]`、`[NEEDS_CLARIFICATION]`、`[任务完成]`、`[需要澄清]`（复用 `lib/utils.ts` 的 `stripAgentControlMarkers`），并去掉行尾因此留下的空白。
  - 含 `<file` 或 `</file>` 的行整行去掉。
  - 清理后没有内容时，整个面板不渲染。
- 设置里的全局「显示思考过程」开关（`useThinkingVisibility`）不变。

## Alternatives considered

- **只把默认值改成折叠，沿用旧 key。** 最强理由：不丢失真正选择过展开的老用户的偏好。被否：旧组件挂载时就写 `"true"`，分不出谁是主动展开、谁只是打开过页面；沿用旧 key 等于对绝大多数老用户什么都没改。真正想看的作者点一次就会记住。
- **服务端过滤或让模型用中文思考。** 最强理由：从源头解决，历史、导出和其他客户端都受益。被否：reasoning 是模型的原始输出，服务端改写会影响排障和审计；让模型改用中文思考要改提示词并做真实模型验证。决策表把它列为 next，本期只做显示层清理。
- **完全隐藏思考过程。** 最强理由：最简单，彻底不泄露。被否：一部分作者愿意看 AI 是怎么理解需求的，长时间生成时它也是「还在干活」的信号；设置里已有全局开关，默认折叠加清理足够。
- **把所有 snake_case 词都换成「AI」。** 最强理由：不用维护名单，新工具自动覆盖。被否：思考里也会出现作者自己的变量名、文件名或英文片段，一刀切会误伤；名单和 `chat:tool.*` 一一对应，新增工具时一起加。

## Consequences

- 收益：新用户和老用户默认都看不到展开的思考过程；展开后读到的是作者能懂的名字，没有 id、标记和原始文件块。
- 代价：老用户之前主动展开过的偏好被重置，需要再点一次。替换名单是写死的，新增工具或 agent 时要同步 `thinkingDisplay.ts`，否则新名字会原样出现。显示的内容和存储的 reasoning 不再逐字一致，排障时要看原始数据。整行去掉 `<file` 行时，如果模型在同一行里还写了别的话，这些话也会被去掉。

## Verification

- `NODE_OPTIONS='--max-old-space-size=8192' npx vitest run src/lib/__tests__/thinkingDisplay.test.ts src/components/__tests__/ThinkingContent.test.tsx src/components/__tests__/MessageList.test.tsx`：工具名、角色名、反引号里的 planner / writer 被替换而普通单词 writer 保留，嵌在长标识里的不替换，UUID、中英控制标记、`<file>` 行被去掉，普通文本不变；ThinkingContent 默认折叠且挂载不写存储、忽略旧 key 的 `"true"`、切换后才写入 v2 key、折叠时保留标题和流式动画、显示的是清理后的内容、只剩内部标记时不渲染。新用例在改动前的组件上失败。
- `npx tsc --noEmit -p tsconfig.app.json`、`pnpm lint`、`pnpm lint:i18n-keys`、`pnpm run build:typecheck` 均通过。
