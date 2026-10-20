# Feature Note: 前端默认快速模式，快速模式保留路由、只关自动质检

Status: implemented

## Problem

2026-10-19/20 生产 24 小时 DeepSeek 花费约 ¥44。所有 246 轮都是「高质量模式」：前端 `ChatPanel` 默认 `quality`，作者没选过就一直是它。高质量模式 = LLM 路由 + 自动质检门（writer 写够 500 字正文就交给 quality_reviewer，最多两轮，76% 的审查会返回 writer 返修）。

- 自动质检触发的 58 轮占轮数 25%，却占 agent 花费 48%；平均每轮 ¥0.35，纯 writer 轮 ¥0.12。审查 + 返修约 ¥11/天。
- 路由只要 ¥0.56/天（约 ¥0.002/轮）。

原来的快速模式（`generation_mode=fast`）同时关掉了路由：固定从 writer 开始，`routing_metadata` 里没有 `read_only` / `write_content` / `scope` / `requested_file_types`。这些字段是「只读请求不改文件」「只做规划不写正文」「作品第一条消息先写再给方向」「补问作答沿用上一轮路由」的来源。快速模式近三周只有 6 次真实使用，几乎没有生产验证。直接把它设为默认，会让大多数作者的提问落到有写权限的 writer 上。

## Decision

1. 前端没有保存过选择的项目默认 `fast`（`ChatPanel.DEFAULT_GENERATION_MODE`，`MessageInput` 的默认值同步）。作者切换过的按项目存在 `localStorage`（`zenstory_generation_mode_<projectId>`）的选择照旧生效，切到高质量模式的作者不受影响。
2. 后端 `run_writing_workflow_streaming`：`generation_mode=fast` 时 `router_strategy="llm"`、`enable_graph_auto_review=False`。路由、补问沿用、计划交接、作者主动要求的审稿（`review_only`）都照常；只是写完不自动送审，writer 自己交给审稿人仍按原快速模式规则丢弃，系统指令里的 `FAST_MODE_DIRECTIVE` 不变。
3. 路由思考提示改为「快速模式：正在理解你的要求...」。`router_off` 只剩环境变量 `AGENT_ROUTER_STRATEGY=off` 一种来源。

## Alternatives considered

- **直接把原快速模式设为默认。** 最强理由：每轮还能再省一次路由调用和约 1 秒延迟。未采用：省下的只有 ¥0.56/天，换来的是绝大多数对话失去只读 / 范围 / 首轮保护，走的是近三周只跑过 6 次的路径。
- **保持高质量默认，只把质检做便宜。** 最强理由：审查确实抓到真问题（返修理由多是前后对不上）。部分采用：质检本身的浪费（凑字数、返修重读）另外修；但作者没选过模式时每轮都付审查 + 返修，仍是最大的一块固定成本，交给作者在需要时选高质量。
- **新增第三档「轻量」。** 未采用：两档已经够表达「要不要自动审稿」，多一档只增加界面和测试面。

## Consequences

- 收益：没选过模式的作者不再每轮自动审稿，预计省 ¥10–13/天（按 10-19/20 用量）；只读、范围和首轮判断不变。
- 代价：默认作者写完不再自动审稿，前后矛盾这类问题要作者自己发现或主动要求审稿 / 切换高质量；作者在一条消息里要求「写完再审」时快速模式不会自动送审（需要下一条消息要求审稿）。
- 代价：每轮多一次路由调用的延迟（约 1 秒），对原来主动选快速模式的少数作者是可见变化。
