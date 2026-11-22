# Tier 0 零质量风险 MiniMax 配额安全优化

## Problem

生产环境（2026-11-16 至 2026-11-22）的 `llm_usage_event` 与 `chat_message.tool_calls` 审计发现了五处零质量收益、却显著推高 MiniMax 周配额消耗与长会话超窗风险的机制缺陷，以及一处编辑器等待态无障碍缺陷：

1. **瞬时 `provider_error` 触发 +100k 压缩退避**：此前只要 `compaction_skipped` 记录了 `at_prompt_tokens`，无论失败原因是模型输出不可用还是上游瞬时 5xx，后续轮次都必须再增长 `AGENT_LOOP_COMPACT_RETRY_GROWTH_TOKENS`（100k）才允许重试。生产中曾因单次上游瞬时失败把长会话一路拖到 767k–984k token。
2. **`wire_prefix_cache` 明文 JSON 触顶 2 MiB**：~550k–612k token 的中文会话序列化后约 3 MB > `AGENT_WIRE_PREFIX_CACHE_MAX_BYTES`（2 MiB），导致 `store_wire_prefix` 跳过并删键，下一轮轮前压缩被迫回退 DB 重建并吃满 `0.0%` 缓存冷 miss。
3. **`should_compact` 漏算本轮 `<turn_context>` 与用户消息**：上一轮停在 `291k–299k`（<= 300k）的会话，叠加本轮 ~12k `<turn_context>` 后第 1 步即从 `304k–363k` 起跳，未触发轮前压缩。
4. **不按 `id`/`ids` 的 `query_files(response_mode="full")` 多章正文炸弹**：7 天内 217 次 `list_full` 拉入 4.03M 字符（单次最高 189.5k 字符 / 10 章全文），其中 79.3% 还带了 `query` 搜索词，既制造单轮 `125k → 612k` 爆发，又诱发 Lost-in-the-Middle 与超时。
5. **大目录纯列表 `list_summary` 正文预览冗余**：超过 20 行的纯目录列表（无 `query`）对每章 `draft`/`script` 都吐出 200 字预览，在 100+ 章节的大项目里造成无谓上下文膨胀。
6. **编辑器流式等待态 `<textarea>` 缺少可访问名称**：`SimpleEditor.tsx` 在 `isStreaming && !displayContent` 时为隐藏作者占位文案而将 `placeholder` 置空，导致严格 axe-core `label` 校验失败。

## Decision

在**严格保持** `AGENT_LOOP_COMPACT_AT_TOKENS`（300k）、`AGENT_TOOL_CALL_MAX_ITERATIONS`（60）、`IntraRunToolOutputTrimmer`（1,500,000 字符）以及单文件 `query_files(id=...)` 全文读取行为不变的前提下，落地 6 项零写作质量风险的 Tier 0 改动：

1. **区分压缩跳过原因的退避策略**（`agent/loop/history.py`、`agent/loop/compaction.py`、`config/agent_runtime.py`）：`LoopHistory` 保留 `compaction_skipped_reason`；`should_compact` 仅对非 `provider_error`（如 `empty_or_truncated_summary` 及无 `reason` 的历史记录）应用 `AGENT_LOOP_COMPACT_RETRY_GROWTH_TOKENS`（+100k）退避，瞬时 `provider_error` 下一轮立即重试。
2. **Redis `wire_prefix_cache` 启用无损 `gzip` + base64 压缩**（`agent/loop/wire_prefix_cache.py`、`config/agent_runtime.py`）：鉴于全局 Redis 连接池使用 `decode_responses=True`，写入端对紧凑 JSON UTF-8 字节做 `gzip.compress(level=6)` 后以 ASCII 安全前缀 `zsgz1:` + base64 存储，并按压缩后实际入库字节数核验 `AGENT_WIRE_PREFIX_CACHE_MAX_BYTES`（~3 MB 原始中文会话压缩后 <1.2 MB，稳居 2 MiB 以内）。读取端同时支持 `zsgz1:` 压缩格式、原始 gzip 字节及滚动发布期间残留的旧纯文本 JSON；损坏载荷统一 fail-open 回退 DB。
3. **`should_compact` 纳入本轮 `<turn_context>` + 用户消息估算**（`agent/loop/compaction.py`、`agent/service.py`）：`should_compact` 新增 `extra_tokens: int = 0`，`AgentService.process_stream` 传入 `estimate_provider_text_tokens(user_content)`，在历史与本轮新增之和越过 300k 前完成轮前压缩（`skipped_record` 仍记录历史本身的实测/同量纲输入 token）。
4. **治理不带 `id`/`ids` 的 `query_files(response_mode="full")`**（`agent/tools/mcp_tools.py`、`agent/tools/file_ops/serialization.py`、`agent/tools/file_ops/crud.py`）：
   - 同时省略 `id` 和 `ids` 且带有 `query` 时，直接降级为 `summary` 模式（走 SQL 投影并附 `matches` 片段，省略有命中时的 `content_preview`），返回 `note` 引导按 `query_files(ids=[...])` 或 `query_files(id=...)` 精确读全文。
   - 同时省略 `id` 和 `ids` 且不带 `query` 时，总返回正文 `<= 25,000` 字符（`QUERY_FILES_LIST_FULL_MAX_TOTAL_CHARS`）仍放行全文（保留小体量人物卡/设定集一枪读完）；超出 `25,000` 字符则自动降级为 `summary` 并附 `note` 引导按 `ids`/`id` 精确读取。
   - `_note_file_read` 仅在按 `id`/`ids` 精确读取且返回包含完整 `content` 时记录快照，降级后的 summary 行绝不写入 `_ReadSnapshots`。
5. **大目录纯列表 `list_summary` 选择性瘦身**（`agent/tools/file_ops/serialization.py`、`agent/tools/file_ops/crud.py`）：完整保留 `matches` 且继续在有正文命中时省略 `content_preview`；在无 `id`/`ids`、无 `query` 且未显式指定 `content_preview_chars`、返回超过 20 行（`QUERY_FILES_LARGE_LISTING_ROW_THRESHOLD`）的纯目录 listing 中，`draft`/`script` 预览收紧至 60 字符，`outline`/`character`/`lore` 等资料保持 100 字符（SQL 投影仍延用单条无正文水化查询）。
6. **`SimpleEditor.tsx` 补齐 `<textarea>` `aria-label`**（`apps/web/src/components/SimpleEditor.tsx`）：在 `.editor-content` `<textarea>` 上固定绑定 `aria-label={t('editor:placeholder.contentPlaceholder')}`，等待态置空 `placeholder` 时仍满足无障碍 `label` 要求。

## Alternatives considered

1. **直接把 `AGENT_LOOP_COMPACT_AT_TOKENS` 降到 `150k` 或把 `IntraRunToolOutputTrimmer` 降回 `600k`**：
   - 最强论据：可更快压低长会话峰值 token 账单。
   - 拒绝原因：生产回放表明 150k 会导致平均每 1.55 轮触发一次压缩并引发 2.85× 重读反弹与文风漂移；600k 轮内裁剪会直接破坏 MiniMax 前缀缓存并制造重复读取循环。因此 Tier 0 不改动任何压缩阈值或轮内裁剪阈值，把阈值调优留给观察数据后的后续批次。
2. **在 `wire_prefix_cache` 中直接往 Redis 写原始二进制 gzip 字节或把 `AGENT_WIRE_PREFIX_CACHE_MAX_BYTES` 调大到 4 MiB**：
   - 最强论据：原始二进制没有 33% base64 膨胀；直接抬高上限到 4 MiB 改动更少。
   - 拒绝原因：项目全局 Redis 连接池启用了 `decode_responses=True`，直接写入原始 gzip 字节会在 `get()` 阶段触发 `UnicodeDecodeError`；单纯把明文上限翻倍到 4 MiB 会线性放大 Redis 内存占用与网络传输。采用 `zsgz1:` + gzip + base64 可在复用现有连接池、零 Redis 内存扩增的前提下把 ~3 MB 中文 JSON 压到 ~1.1 MB。
3. **对单文件 `query_files(id=...)` 在 25k–30k 处自动截断分页**：
   - 最强论据：可进一步削减超大单章或总纲单次读入。
   - 拒绝原因：生产中 `id_full > 20k` 有 61%–78% 在同轮紧跟 `edit_file`，截断会导致模型看不见章节结尾或总纲后半段，反而触发额外整段前缀重送。单文件读取在 Tier 0 保持 100% 原样。

## Consequences

- **收益**：消除 `provider_error` 导致的 767k–984k 失控长会话；消除 ~600k 会话 `wire_prefix_cache` 溢出导致的 `0.0%` 命中冷压缩；堵住 `291k–299k + <turn_context>` 越过 300k 不压缩的边界漏网；根除 `list_full` 单步 189k 多章正文炸弹并收紧大目录 `draft`/`script` 冗余预览，同时保持前缀缓存与写作质量零回退；修复 `SimpleEditor` 流式等待态 axe-core `label` 违规。
- **代价**：`wire_prefix_cache` 每次读写增加少量 CPU `gzip` 编解码开销（异步卸载在工作线程中，单次约数毫秒）；极少数依赖「不传 `id`/`ids` 一次性拉取 >25k 字符全文」的旧习惯需改为 `query_files(ids=[...])`（通过清晰的 `note` 引导）。
