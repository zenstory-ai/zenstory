# 修复 `encrypted_content` 字符膨胀导致的误裁剪、滞回失效与 2 MiB 线前缀溢出

## Problem

生产巡检 #8（2026-11-28）对 MiniMax `/v1/responses` 活跃会话的 Redis `agent:wire_prefix:*` 载荷与 `llm_usage_event` 实测发现，Responses API 返回的 `type="reasoning"` 项携带大体积不透明 base64 `encrypted_content` 密文（占 `json.dumps(items)` 原始字符数的 42%–77%，把 `chars/token` 拉高到 `2.6–6.8`，且不可被 gzip 压缩），在三处路径上持续击穿热 KV 缓存：

1. **`compaction.py:_trim_for_budget()` 提前折断热缓存（~2.45M miss tokens/24h）**：按原始 `len(json.dumps(items))` 对齐 `700k * 1.5 = 1,050,000` 字符上限，导致 ~300k–360k 真实 token 的热 Stage-2 压缩请求提前触发旧工具折叠，11/33 次热压缩命中率从 96.4% 跌至 15.4%。
2. **`IntraRunToolOutputTrimmer` 提前触发且滞回失效（~1.35M miss tokens/24h）**：`DEFAULT_MAX_INPUT_CHARS = 1_500_000` 把 `encrypted_content` 计入预算导致在 233k–324k 真实 token 即误触发；更糟的是当不可裁剪内容（如大量 `reasoning` 项或长正文）本身已超过 `max_input_chars`、所有可折叠载荷远小于 `required`（450k）时，每追加一个新小工具输出都会立即被折叠（`3→4→5→6→7→8→9`），滞回完全失效并在轮内步步击穿前缀缓存。
3. **`AGENT_WIRE_PREFIX_CACHE_MAX_BYTES = 2 MiB` 溢出**：不可压缩的 `encrypted_content` 使 395k token 会话压缩后达到 2.66 MB，超过 2 MiB 上限被拒写并删键，下一轮被迫冷回放；而整个生产 Redis 实际内存占用仅 ~3.05 MB。

本记录调整 [2026-11-22 Tier 0 配额安全优化](../architecture/2026-11-22-tier0-minimax-quota-safety.md) 与 [2026-11-23 MiniMax Responses API 迁移](../architecture/2026-11-23-minimax-responses-api-prompt-cache-key.md) 中的 `2 MiB` 默认上限与计量口径（旧记录保留原文不改写）。

## Decision

1. **预算计量排除不透明 `encrypted_content`（绝不修改发往上游的项）**：
   - `apps/server/agent/openai_agents/intra_run_trimmer.py`：`_item_chars()` 在计量任何包含 `"encrypted_content"` 键的字典项时构造不含该键的临时视图计长，`_input_chars()` 统一按 `sum(_item_chars(item)) + 2 * len(items)` 计算（无密文时与 `len(json.dumps(items, ensure_ascii=False, default=str))` 逐字节相等），原始 `reasoning` 项与密文逐字节保留。
   - `apps/server/agent/loop/history.py`：`estimate_provider_items_tokens()` 仅统计 `content` / `summary` / `arguments` / `output`，并在文档中明确约定不计入 `encrypted_content`。
2. **Stage-2 压缩 `_trim_for_budget()` 改由真实供应商 token 预算门控**：
   - `apps/server/agent/loop/compaction.py`：`compact_history()` 在调用 `IntraRunToolOutputTrimmer` 前先计算 `source_effective_tokens = effective_history_provider_tokens(history) + cold_fixed_tokens`（热缓存命中时 `last_prompt_tokens` 已含系统提示与工具 schema，`cold_fixed_tokens = 0`；冷回放时补齐静态前缀）。
   - 若 `source_effective_tokens <= budget_tokens`（如 300k–600k <= 700k），`_trim_for_budget` 直接原样返回 `source_items`，零前缀改写；仅当真实超预算或 400/413/422 超窗减半预算重试（`force_trim=True`）时才调用 `IntraRunToolOutputTrimmer` 折叠旧工具载荷。
3. **`IntraRunToolOutputTrimmer` 不可裁剪超限时的半台阶滞回守卫**：
   - `apps/server/agent/openai_agents/intra_run_trimmer.py`：`trim_items()` 先规划最旧优先候选并累计 `total_savable`。当 `total_savable >= required` 时与原行为一致；当 `total_savable < required` 时，令 `effective_required = ((2 * total_savable) // step) * step // 2`（向下量化到半台阶 `step / 2`）：零碎可折叠量不足半台阶时直接返回原 `items` 零改写，累计跨过半台阶后才批量折叠一次并在下一个半台阶内保持前缀冻结。该目标对 `(chars_before, total_savable)` 无状态、确定且严格单调非降。
4. **`AGENT_WIRE_PREFIX_CACHE_MAX_BYTES` 默认上限由 `2 MiB` 上调至 `4 MiB`**：
   - `apps/server/config/agent_runtime.py` 默认改为 `4 * 1024 * 1024`（保留环境变量覆盖），覆盖 400k+ token 带 `encrypted_content` 的活跃会话。

## Alternatives considered

1. **直接抬高 `DEFAULT_MAX_INPUT_CHARS`（例如调到 3M 或 5M）而不排除 `encrypted_content`**：
   - 最强论据：改一个常量即可，不需要动 `_item_chars` / `_input_chars`。
   - 拒绝原因：不同会话的 `encrypted_content` 占比在 42%–77% 之间剧烈波动，调高固定字符上限会让纯文本/少思考会话在真正超窗时失去安全阀保护，又无法保证高思考密度会话不误触发。从计量根源排除密文字段才让字符预算重新反映真实上下文体积。
2. **在 `IntraRunToolOutputTrimmer` 中保存可变实例状态（记录上次折叠位置或跳过计数）**：
   - 最强论据：可以直接记住上一次折叠后的水位，阻止下一次重复折叠。
   - 拒绝原因：`IntraRunToolOutputTrimmer` 在每次 run、重放及 `compaction.py` 中都按纯函数方式调用，引入可变状态会破坏确定性与多 run 复用一致性。按 `((2 * total_savable) // step) * step // 2` 做无状态量化既严格单调又完全确定。
3. **继续维持 `AGENT_WIRE_PREFIX_CACHE_MAX_BYTES = 2 MiB` 并在入缓存前剥离 `encrypted_content`**：
   - 最强论据：可压低 Redis 内存占用。
   - 拒绝原因：MiniMax `/v1/responses` 依赖原样回放 `encrypted_content` 维持前缀缓存与连续推理状态，剥离密文会直接破坏缓存命中。生产 Redis 总内存仅 ~3 MB，上调默认上限至 4 MiB 风险极低且立竿见影。

## Consequences

- **收益**：消除 Stage-2 正常热压缩的误裁剪（回收 ~2.45M miss tokens/24h）、消除 `IntraRunToolOutputTrimmer` 因密文误触发及不可裁剪超限导致的步步抖动（回收 ~1.35M miss tokens/24h），并防止 400k+ token Responses 会话因不可压缩密文溢出 2 MiB `wire_prefix_cache` 上限。
- **代价**：单活跃会话在 Redis 中的短时 TTL（20 分钟）封顶占用由 2 MiB 增至 4 MiB；当不可裁剪文本自身已超 `max_input_chars` 时，零碎小工具输出会延迟至累计可省体积达 `step / 2`（默认 225k 字符）才批量折叠。
