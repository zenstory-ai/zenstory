# Agent Note: 素材拆解 LLM 前缀缓存优化（静态 System Prompt、按书粘性路由与首项预热）

Status: implemented

本文配合 [空闲滑动 TTL 会话粘性 Key 路由与软着陆 wire_prefix_cache](2026-11-21-cache-soft-land-and-session-key-sticky.md) 与 [前缀缓存稳定性与 wire-prefix 缓存](2026-11-20-agent-prompt-cache-stability.md)，将写作 Agent 已验证的前缀缓存工程约束延伸至 Prefect 素材拆解管线，同时完整保留 [章节摘要的 JSON 请求合同与安全失败信息](../bug-fix/2026-10-24-material-summary-json-and-safe-errors.md) 及免费试拆的全部风控不变量。

## Problem

生产用量审计暴露出素材拆解（`source="material"`）的 Prompt Prefix Cache 命中率长期极低：

1. **近 7 天整体缓存命中率偏低**：`MiniMax-M3.1-Flash` 素材拆解缓存命中率仅 **5.20%**，`DeepSeek` 仅 **6.54%**；2026-11-24 出现的多起单章超大文本上传（258k–293k 字符）命中率更是低至 **0.22%–0.68%**（单章仅发起一次调用，本身缺乏跨章复用前提，但即使在多章/多角色正常书目上，以下四类代码缺陷也直接破坏了跨调用的公共系统前缀命中）。
2. **`MaterialLLMClient` 未向 `llm_failover` 传递 `session_id` 导致双 Key 轮转击穿**：`MaterialLLMClient._call_model` 虽已调用 `resolve_material_owner` 解析出 `novel_id`，却未传入 `session_id`。在生产开启 `MINIMAX_SECONDARY_API_KEY`（默认权重 20%）后，`llm_providers.configured_providers(routing_key=None, rotate_if_unkeyed=True)` 会在每次调用间按 20% 比例轮换主/次 Key，导致同一本书的拆解请求跨凭证打散。近 14 天 MiniMax 素材调用分布显示，**114 / 1110 次调用缓存命中为 0**，直接对应双 Key 无粘性轮换。
3. **动态业务字段注入在 `system_prompt` 头部破坏共享前缀**：
   - `character_consolidation.j2`（`web_long` 与 `web_short`）在第 12 行就把 `{{ character_name }}`、`{{ chapter_range }}`、`{{ chapter_mentions }}` 拼入 `system_prompt`，排在约 1,500 tokens 的静态规则、输出 schema 与示例之前。线上直方图证实，每次 `build_character_entity_task` 在 ~1,800 tokens 的系统提示中仅命中 **128–186 tokens**，而非预期的 ~1,600 tokens。
   - `plot_extraction.j2`（`web_long` 与 `web_short`）把每章变化的 `word_count`、`min_plots`、`max_plots`、`density_min`、`density_max` 注入 `system_prompt`，每换一章就击穿约 1,500 tokens 的系统提示缓存。
   - `summary_based_aggregation.py` 调用 `create_story_framework_identification_prompt(num_chapters=len(chapter_summaries))`，将每个分块不同的 `num_chapters` 注入 `story_framework_identification.j2` 第 14 行，跨分块无法共享系统前缀。
4. **冷启动立即 3 路并发扇出导致齐齐冷 miss**：`chapter_extraction_flow.py`（摘要、情节点、角色提及）、`character_entity_build_flow.py`（角色实体）与 `story_aggregate_flow.py`（剧情框架聚合）在首个任务尚未回写服务端 KV-cache 时即同时提交 `MAX_CONCURRENT_CHAPTERS=3` 或 `MAX_CONCURRENT_WORKFLOWS=3` 个并发任务，产生 3 次并行冷 miss；对仅有 3 章的免费试拆而言，直接造成 **0/3 命中**。

## Decision

分两层实施最小、可回归验证的修复，不引入新依赖、不改动公开 API 合同与计费边界：

### 1. 提示词与调用点层（静态 `system_prompt` + 动态字段下沉至 `user_message`）

- **`character_consolidation.j2`（`web_long` / `web_short`）与 `character_tasks_v2.py`**：模板仅在显式传入 `character_name` 或 `chapter_mentions` 时渲染 `# 输入信息` 区块（保持旧调用方与单测兼容）；`prompt_loader.py` 新增 `format_character_consolidation_user_message(...)` 并将 `create_character_consolidation_prompt` 参数改为可选默认 `None`；`build_character_entity_task` 以无参方式获取字节级完全静态的 `system_prompt`，把角色名、章节范围与提及记录移入 `user_message`。
- **`plot_extraction.j2`（`web_long` / `web_short`）与 `plot_tasks.py`**：保留传入 `word_count` 时的既有渲染结果以兼容 `test_prompt_loader.py`；当 `word_count` 省略/`None` 时输出完全静态的通用提取规则；`extract_chapter_plots_task` 改为无参调用 `create_plot_extraction_prompt()`，并在 `user_message` 中显式附带本章字数、目标情节点数量区间与参考密度。
- **`summary_based_aggregation.py`**：`identify_story_frameworks_from_summaries` 改为无参调用 `create_story_framework_identification_prompt()`（复用模板已有的 `{% if num_chapters %}` 条件块），将分块章节数与目标剧情框架数量区间放入 `user_message`。

### 2. 管线路由与并发调度层（按书粘性 `session_id` + 首项预热波次）

- **按小说确定性 `session_id`（`apps/server/flows/utils/clients/llm.py`）**：
  - `MaterialLLMClient._call_model` 尊重调用方显式传入的 `session_id`；未显式传入时，若 `resolve_material_owner` 解析出 `resolved_novel_id` 则派生 `f"material:novel:{resolved_novel_id}"`，否则若存在 `usage_chapter_id` 则退化为 `f"material:chapter:{usage_chapter_id}"`，均缺失时保持 `None`，并将其传给 `llm_failover.create_chat_completion(session_id=...)`。
  - 同一本书内所有章节/角色/剧情调用共享同一个 `session_id`，自动接入既有 Redis 空闲滑动 TTL 双 Key 粘性路由（无 Redis 时确定性 HMAC），彻底消除单书内 20% 概率的 MiniMax 双 Key 轮换。
  - **JSON 合同隔离验证**：当章节摘要携带 `response_format={"type": "json_object"}` 时，`llm_failover.create_chat_completion` 会先把候选列表过滤为 `[deepseek]`（长度 1），`record_success` 在 `len(ordered) == 1` 时不会写入槽位 pin，因此同一 `session_id` 下后续非 JSON 素材调用（情节点、角色提及、角色实体、剧情聚合等）不会被错误 pin 到 DeepSeek，仍留在主供应商 MiniMax。
  - 完整保留免费试拆全部护栏（`_trial_output_limit`、`material_trial_call`、`TRIAL_OUTPUT_TOKENS` / `TRIAL_MERGED_OUTPUT_TOKENS`、`thinking_enabled=False`、`max_retries=0`、`material_job_scope`）。
- **首项预热后再分批并发扇出（`chapter_extraction_flow.py`、`character_entity_build_flow.py`、`story_aggregate_flow.py`）**：
  - 新增局部辅助函数 `_warmup_batches(items, batch_size)`：当 `batch_size > 1` 且待处理项数 `>= 2` 时，将执行拆为 `[items[:1], items[1:1+batch], items[1+batch:1+2*batch], ...]`。
  - 首项单独提交并等待 `future.result()` 完成后，再按并发上限批量提交后续波次；首项发生非致命错误时沿用既有单条失败记录并继续执行后续波次，发生致命停止错误（`is_llm_fatal_stop` / `MaterialPipelineError`）时立即中止且不提交后续波次。
  - 保持既有任务签名、处理顺序、checkpoint 字段、进度同步、`monitor.measure` 与全失败判定语义不变。

## Alternatives considered

- **移除章节摘要的 `response_format={"type": "json_object"}`，把摘要与角色提及合并为一次 MiniMax 调用**：最强理由是可将章节正文输入从两次 LLM 调用压缩为一次，并让单章在 MiniMax 上完全共享正文前缀，理论上对大章节收益最大。未采用：[2026-10-24 章节摘要 JSON 合同](../bug-fix/2026-10-24-material-summary-json-and-safe-errors.md) 明确要求在协议层发送官方支持的 `json_object` 约束，而 MiniMax OpenAI 兼容文档未承诺该合同；同时合并输出 schema 会显著扩大提示词、校验器、截断重试与试拆预算的变更与回归面，不适合在本批基础缓存修复中推翻既有合同。
- **将多个角色实体合并进单次 LLM 合并请求（Batching Characters）**：最强理由是减少请求次数并进一步摊薄 `system_prompt` 成本。未采用：多角色合批会增大单次输出长度与截断/JSON 损坏半径，导致单角色解析失败连带丢弃同批多个角色，且破坏现有按角色幂等重试与试拆角色上限控制；将 `system_prompt` 静态化后单角色调用即可稳定命中 ~1,600 tokens 前缀，风险远低于合批。
- **向 MiniMax / DeepSeek 请求体注入未文档化的 `prompt_cache_key` 字段**：最强理由是试图绕开凭证或服务端路由差异显式绑定缓存桶。未采用：两家供应商当前 OpenAI 兼容接口均未正式承诺该字段，可能触发 400/422 参数拒绝或静默无效；复用已有且经过生产验证的 `session_id` 粘性双 Key 路由即可解决凭证切换问题，不依赖未文档化字段。
- **把 `MAX_CONCURRENT_CHAPTERS` / `MAX_CONCURRENT_WORKFLOWS` 全面降为 1（完全串行）**：最强理由是实现最简单且确保 100% 顺序命中。未采用：对 20–100 章付费书目会把端到端拆解耗时拉长近 3 倍；仅首项串行预热、后续保持 3 路并发，只增加约 1 次单请求延迟即可让后续所有并发波次命中温缓存。

## Consequences

- **收益**：
  - 同一本书的所有 MiniMax 素材调用锁定到同一 API Key 槽位，消除因双 Key 轮转导致的 `hit=0` 断崖。
  - 角色实体构建、情节点提取、剧情框架识别三类高频任务的 `system_prompt` 变为跨调用字节级稳定前缀，可完整复用 ~1,500 tokens 的系统规则与示例。
  - 冷启动并发波次不再 3 路齐齐冷 miss（3 章试拆由 0/3 提升为至多 2/3 命中；10–30 个角色的实体构建由首批 3 路冷 miss 降为仅首项 1 次冷 miss），且致命账户/预算错误可在首项即快速止损，避免无效并发扣费。
- **代价与局限**：
  - 每个并发阶段（摘要、情节点、角色提及、角色实体、剧情框架聚合）的端到端墙钟时间会增加约一次首项往返延迟（与剩余批次并发并行度相比总体可控）。
  - 单章超大文本（如 258k–293k 字符、仅 1 章）因只有单次章节调用且正文占比 >99%，无法通过跨章复用系统提示显著拉高命中率，这属于输入形态固有上限。
  - 本地单测与集成测试全部基于合成数据与 mock 验证，实际线上命中率提升幅度需在部署 staging / production 后通过用量账本复测确认。
