# 外文写作体验打磨：英文静态提示词桶、双语运行模板、技能目录本地化与对称旁白过滤

## Problem

生产 14 天审计发现，外语写作会话（覆盖 `en` 以及 `id/pt/vi/es/ru/ko/fr/pl/ar` 等多语种作者）存在系统性的中文泄漏与体验断层：

1. **硬编码中文文件名与中文静态提示词强引力**：`agent/loop/prompts.py` 硬编码在「大纲」目录建「分章大纲」/「分集大纲」与「写作要求」，且 `prompts/base.py`、`prompts/__init__.py`、`loop/prompts.py` 全部为中文（包括未限中文的全角双空格 `　　` 首行缩进、短剧 `2-1 公司大堂 日 内` / `△` 模板），直接诱导模型在外语项目里创建中文大纲文件名、用长段中文思考并连锁带偏正文与回复语言。
2. **单语中文运行与上下文模板**：`loop/engine.py`（空文件与整章替换纠偏模板、状态卡 `reason`）、`loop/run_support.py`（`STEERING_FOLLOWUP_CONTEXT`）、`core/output_truncation.py`、`openai_agents/runner.py`（`LOOP_SOFT_LANDING_PROMPT` / `LOOP_TIME_LANDING_PROMPT`）、`loop/compaction.py`（硬性要求中文交接摘要）、`context/assembler.py`（`[全文]`、`项目状态 [核心信息]`、`【写作风格】`、`【当前写作约定】`）缺少英文变体。
3. **非对称过程旁白过滤**：`AgentTextShaper` 仅对含汉字的用户消息过滤紧接工具调用前的 ≤120 字英文过程句（`drop_english_narration`），对外语用户完全关闭，导致模型吐出的中文过程旁白（「我先看一下大纲。」）直透前端。
4. **官方技能 L1 目录与多语言建议断层**：注入系统提示的 L1 技能目录全中文且未携带 `PublicSkill.source`，`skill_matched` 事件也发出中文名，加载中文官方技能正文时缺乏「仅用方法论、严禁切换到中文产出」的语言围栏；`suggest_service.py` 强制非 `zh` 全部输出英文而非跟随作者最近消息语言；`service.py` 新建会话默认标题硬编码为「AI 助手对话」。

## Decision

严格遵循 **KV-Cache 双静态桶安全**与 **`AGENTS.md` 语义判断边界（零自然语言关键词/正则意图猜测）**，实施以下改造：

1. **完整英文静态提示词桶（`force_en=True`）并清除中文硬编码文件名**：
   - `agent/loop/prompts.py`：`zh` 桶移除硬编码的「分章大纲」「分集大纲」「写作要求」，改为在逻辑目录内沿用作者/稿件语言命名；新增 `LOOP_INPUT_LAYOUT_SECTION_EN`、`LOOP_WORKING_RULES_EN`、`COMPACT_PROMPT_EN`、`SUMMARY_PREFIX_EN`、`KEPT_USER_TRUNCATED_SUFFIX_EN`。`COMPACT_PROMPT` 与 `COMPACT_PROMPT_EN` 均要求沿用本会话作者与稿件的语言，不再强制输出中文。
   - `agent/prompts/base.py` 与 `agent/prompts/__init__.py`：保留 DB-only 写配置门禁（缺失/未激活仍报 503），新增完整精炼的英文静态协议前缀、英文类型框架（`ENGLISH_PROJECT_TYPE_CONFIG`）与英文题材规范（`GENRE_NORMS_EN`）；全角 `　　` 缩进与中文剧本格式（`2-1 公司大堂 日 内` / `△`）严格限定为中文稿件；强化聊天回复、新建文件标题、文件正文、AI 记忆与精炼内部思考的全链路语言一致。
   - `agent/core/message_manager.py`：`force_en=True` 时装配英文静态前缀、英文循环规则与本地化 L1 技能目录；仅维持 `zh` 与 `en` 两个跨轮不变的静态 KV 缓存桶，绝不按单轮消息动态改写 `system_prompt`。强化 `_build_language_section`，覆盖 `en/id/pt/vi/es/ru/ko/fr/pl/ar` 等任意语言。
2. **补齐全部运行模板英文变体与对称 `drop_cjk_narration`**：
   - `loop/history.py` 与 `loop/compaction.py`：同时识别 `SUMMARY_PREFIX` 与 `SUMMARY_PREFIX_EN`（含 `_SYNTHETIC_USER_PREFIXES`），按 `english=force_en` 选用英文压缩提示、重试提示、摘要前缀、归档回执与中断说明。
   - `loop/engine.py`、`loop/run_support.py`、`core/output_truncation.py`、`openai_agents/runner.py`、`context/assembler.py`、`loop/turn_context.py`：补齐空文件/整章替换纠偏、状态卡 `reason`、截断续跑、插话续跑、软着陆/时限着陆以及 `<turn_context>` 分段标题与 `[Full]` 标注的英文变体。
   - `core/author_facing_text.py` 与 `loop/engine.py`：新增纯展示层有界语法判定 `is_cjk_process_narration` 与 `AgentTextShaper(..., drop_cjk_narration=...)`，仅当作者原始消息不含汉字（`not text_contains_cjk`）时过滤紧接工具调用前 ≤120 字的短中文过程旁白，绝不触碰正文、`<file>` 块或最终总结。
3. **官方技能 L1 本地化、语言围栏、默认会话标题与多语言建议**：
   - `skills/active_skills.py` 与 `skills/context_injector.py`：`ActiveSkill` 携带 `public_source` / `has_custom_name` / `is_official`；`force_en=True` 时通过 `config/official_skill_translations.py`（`localized_skill_text`）渲染英文官方技能名与简介、英文表头与语言围栏，并跳过已翻译官方技能的中文原始 tag；`find_active_skill` 同步支持官方技能英文名查找。
   - `service.py`：`skill_matched` 事件在 `force_en=True` 时对官方技能发出本地化名称；显式选择的中文/官方技能与 `ui_action=natural_polish` 均附加简短语言围栏；新建 `ChatSession` 默认标题改为 `zh` 用 `AI 助手对话`、非 `zh` 用 `AI Assistant`。
   - `suggest_service.py`：将 `ENGLISH_OUTPUT_RULE` 升级为多语言跟随规则（严格跟随作者最近消息的语言，覆盖 `en/id/pt/vi/es/ru/ko/fr/pl/ar` 等，仅在不明时回落英文），并对所有非 `zh` 语言生效。

## Alternatives considered

1. **按每条作者消息语种动态生成或改写 `system_prompt`（例如为印尼语/西班牙语/俄语各自拼接系统提示）**：
   - 优点：理论上可以让每种小语种都拿到本语种的系统提示词。
   - 拒绝原因：一是需要引入自然语言语种检测启发式并随单轮切换改写 `system_prompt`，既违背 `AGENTS.md` 禁止自然语言启发式路由的原则，也会在作者混用语言或首轮短句时击穿上游 KV 前缀缓存（miss 单价高、且放大跨轮延迟）；二是现有两桶（`zh` / `en`）+ 强多语言一致性指令已能稳定驱动模型输出任意母语，维护成本和缓存碎片化远低得多。
2. **仅翻译 `loop/prompts.py`，继续沿用数据库里的中文 `SystemPromptConfig` 拼进 `force_en=True` 前缀**：
   - 优点：改动面更小，直接复用数据库文本。
   - 拒绝原因：生产数据库里的 `SystemPromptConfig` 3 行全为大段中文，保留在 `force_en=True` 静态前缀头部会持续把模型拉回中文思考与中文命名，且无法清除中文目录/体例引力。改用 `ENGLISH_PROJECT_TYPE_CONFIG`（仍严格门控数据库活跃行存在，缺行照旧 503）能彻底净化英文静态前缀且不破坏 DB-only 配置契约。
3. **关闭中文官方技能给英文用户，或把全部官方技能正文人工翻译并双份入库**：
   - 优点：彻底消除任何中文指令进入上下文。
   - 拒绝原因：人工维护 14 个官方技能（及资源文件）的双语正文会带来严重漂移风险，且 `AGENTS.md` 明确 `official_skill_translations.py` 只映射展示名与简介。通过 L1 英文目录 + `find_active_skill` 英文名解析 + 明确的语言围栏指令，既保留全部官方方法论能力，又防止模型切换输出语言。

## Consequences

- **收益**：
  - 英文及所有非中文多语种作者拥有完整的英文静态前缀桶、英文运行/压缩/着陆/上下文模板与本地化 L1 官方技能目录，消除中文大纲文件名、全角缩进、中文剧本格式与中文过程旁白泄漏。
  - 跨轮与跨项目严格保持 `zh` / `en` 两个静态 KV 缓存前缀，0 跨轮前缀击穿；上线后已有活跃英文会话在首轮自然切换至新 `system_prompt` SHA-256（`wire_prefix_cache` 自动 fail-open 冷回放一次后恢复热命中）。
  - 零自然语言意图启发式；`drop_cjk_narration` 与 `drop_english_narration` 对称，仅作用于纯展示层短旁白。
- **成本与注意项**：
  - 英文静态协议与类型配置位于代码（`prompts/base.py`、`prompts/__init__.py`），后续若在后台大幅调整中文 `SystemPromptConfig` 的产品规则，需要同步评估 `ENGLISH_PROJECT_TYPE_CONFIG` 是否需要对齐。
  - `load_skill` 工具本体（`agent/tools/mcp_tools.py`）与前端引导/工具卡/默认目录文案由并行批次推进；本批通过 `active_skills.find_active_skill` 已提前打通官方技能英文名解析。
