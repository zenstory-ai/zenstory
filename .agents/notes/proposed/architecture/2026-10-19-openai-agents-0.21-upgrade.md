# Architecture Note: 升级 openai-agents 到 0.21.x（openai 3.x）

Status: proposed

## Problem

后端锁在 `openai-agents>=0.17.6,<0.18`（实装 0.17.8）与 `openai>=2.36,<2.44`。上游已到 openai-agents 0.24.0（2026-10-15）、openai 3.35.1。旧上限的理由是 0.17.x 构造用量对象时缺 `cache_write_tokens`，与 openai 2.44+ 冲突；我们因此停在旧版 openai，也拿不到之后 Chat Completions 路径（我们唯一使用的路径）的修复。

## Proposal

- 升到 `openai-agents>=0.21.1,<0.22` 与 `openai>=3.0.0,<4`。0.22–0.24 依赖 `starlette>=1.3.1`，而 FastAPI 为 Prefect 3.6.28 固定在 0.123.x（starlette<0.51），**在 FastAPI/Prefect 一起升级之前装不上**。
- openai 3 改用 HTTPX2：`llm_client` / `deepseek_client` 传给客户端的超时改为 `openai.Timeout`（2.x 与 3.x 都有），不再传 `httpx.Timeout`。我们自己的 httpx 调用（语音、OAuth、支付、上传、测试）不受影响，`httpx` 仍是直接依赖。
- 异步 DeepSeek 客户端、SDK 模型包装和 `LLMClient.async_client` 改为按事件循环缓存：HTTPX2 在新事件循环里复用旧循环的连接时直接抛 "Event loop is closed"（实测在同一进程连续跑多个请求时出现）。API 进程只有一个循环，生产里仍是单例。
- 测试里手工构造的 `InputTokensDetails` 补 `cache_write_tokens=0`。

## Alternatives considered

- **直接上 0.24.0。** 最强理由是最新修复（拒绝不成对的工具输出、拒绝提前截断的流、可空 token 计数归一）都在 0.22–0.23。未采用：依赖解析失败（starlette 冲突），需要先单独评估 FastAPI 0.141+ 与 Prefect 的兼容。
- **只升到 0.20.x（仍用 openai 2.x）。** 最强理由是不碰 HTTPX2。未采用：0.21.1 的 Chat Completions 推理回放修复与 DeepSeek 思考模式相关，且 0.20 之后的 0.2x 都要求 openai 3，早晚要迁移；迁移量实测很小。
- **不升级。** 最强理由是现状稳定。未采用：openai 被压在已知不兼容点之下，安全/修复都只能滞后，下一次被迫升级时跨度更大。

## Consequences

- 本地验证：涉及 SDK/客户端的 1,119 项测试在旧、新两个环境都通过；用生产 DeepSeek 密钥在升级环境跑真实请求：改名、长合集续写、写作→审稿、导出、分章字数 5 个 agent 场景全部完成，去AI味 16/16、路由 54/54。
- HTTPX2 的连接池、代理与超时行为是新的；需要观察上线后的上游超时与连接错误分布（日志已有固定分类）。staging 的模型密钥无效，无法在 staging 做真实模型验证。
- 0.22+ 的修复要等 FastAPI/Prefect 升级后再取。
