# Agent Note: 注入的上下文显式标注全文、跨轮带上工作集、预算按 1M 窗口定，让 agent 不再反复重读

Status: implemented

## Problem

生产事故：agent 反复读取同一批文件。线上助手的原话是"我先读取本轮涉及的关键文件…并行返回的是预览，我改用单体 query_files 逐份取全文…卷纲被压缩了，我需要全文…检索已禁用，我改用精确读取"。约 1/3 的 agent 轮次把同一文件读了 4 次以上，这些轮次占 agent 总花费的 69%，有付费用户因此退款。

本 note 只覆盖上下文与提示词这一侧（工具返回体、parallel_execute、hybrid_search 下线、runner 的重复读取拦截与交接里的文件 id 由并行的其他修复负责）。这一侧的成因：

1. **注入的上下文看起来像被压缩过**。`MessageManager._build_context_section` 用 `_truncate_text` 处理整段原始上下文，先 `" ".join(text.split())` 把章节正文、大纲层级压成一行，再按 14000 字截断；条目标题行只有标题，没有 id，也不说是不是全文；`TokenBudget.truncate_item` 截断后以裸 `"..."` 收尾，模型分不清"原文如此"还是"被截了"。world_model truth/surface 里又给每个条目附一份 220 字单行预览，同一文件在 prompt 里出现"预览 + 压成一行的正文"两份，模型据此判断"被压缩了，需要全文"。
2. **新一轮不知道上一轮读过什么**。runner 回放历史时丢弃工具结果；`SessionLoader` 合成的面包屑只记 create/edit/delete，不记读取。用户说"继续"，模型从零开始把卷纲、角色卡、上一章再读一遍。
3. **预算是按 6k 定的**。历史窗口 `AGENT_CHAT_HISTORY_TOKEN_BUDGET`=6000、组装上下文 `max_tokens`=6000（`session_loader.py`、`service.py` 写死）、请求级台账上限 24000（注释还写着"~200k 窗口"）。deepseek-flash 实际是 1M 窗口、自动前缀缓存（命中价约为未命中的 1/50），6k 的上下文装不下一章正文，截断必然发生，于是触发第 1 条的重读。
4. **提示词在鼓励读取**。基础协议写"修改前用 query_files 读取目标"，工具列表把 hybrid_search 写成首选（实际可能已禁用），旁白规则要求每一步操作前都说一句，任务板要求每完成一项就单独调用一次 update_project；各角色提示都以"先读相关大纲、前后文…"开头，没有说交接里已经给出的内容可以直接用；审稿人被要求把错别字/标点也交接给 writer 返工。

## Decision

- **预算**（`config/agent_runtime.py`、`agent/context/budget.py`）：`AGENT_CHAT_HISTORY_TOKEN_BUDGET` 默认 32000；新增 `AGENT_CONTEXT_TOKEN_BUDGET`（默认 32000，环境变量可覆盖），`ContextAssembler.assemble`、`SessionLoader.assemble_context` / `load_session_with_compaction` 的默认值和 `service.py` 的显式传参都用它；`DEFAULT_PROMPT_TOKEN_LEDGER_CEILING` 为 160000，注释按 1M 窗口 + 前缀缓存改写。`MessageManager._CONTEXT_RAW_CHAR_LIMIT` 改为 200000 字，只作兜底，不再二次截断按预算选好的条目。
- **原始上下文保留排版**：`MessageManager._cap_raw_context` 只做 strip 与兜底封顶（尽量在换行处切，并追加 `[项目上下文超出长度上限…]` 说明），不再折叠空白。`_normalize_text` / `_truncate_text` 只用于 world_model 列表里的单行节选，节选以 `…（节选）` 收尾。
- **条目显式标注完整性**（`assembler.py`）：每个条目标题行渲染为 `标题 (id=…) [全文]`；被预算截断的为 `[已截断：显示 x/y 字；全文用 query_files(id="…")]`；检索片段为 `[检索片段，非全文；全文用 query_files(id="…")]`；素材库附件不给 id（query_files 读不到）。"相关内容详情"段头写明 `FULL_TEXT_CONTEXT_NOTICE`："标注[全文]的条目就是该文件当前完整内容（截至本请求开始；本请求内被修改过的文件除外），直接使用，不要再读取。"括号里的限定是因为系统提示每个请求只组装一次，本请求内被 agent 改过的文件，这里的 [全文] 已是改前版本。 `TokenBudget.truncate_item` 以 `TRUNCATION_SUFFIX`（"…（内容过长已截断）"）收尾，并在 metadata 记 `shown_chars` / `original_chars`。标题行多出的 id 与标注由 `TokenBudget(item_overhead=…)` 计入每个入选条目的占用，段头开销先从条目预算里预留，整块仍落在 `max_tokens` 内。
- **world_model 去重**：原始上下文未触发兜底截断时，未被截断、非检索片段的条目在 truth/surface 里只写 `标题 (id=…)（全文见项目上下文）`，保留可见性分类，不再附 220 字预览。
- **跨轮工作集**（`session_loader.py`）：`load_chat_session` 在项目归属校验通过后，从最近 2 条 assistant 消息的持久化 `tool_calls` 里收集读过（`query_files` 带 `id`，含 `parallel_execute` 的 query_files 子任务）或写过（create/edit_file，parallel 的 edit_file / write_chapter）的文件 id，失败调用、关键词搜索、被删除的文件不算。`attach_working_set` 在上下文组装之后按**当前库内容**加载这些文件（限定本项目、排除软删除和文件夹），渲染为 `<previous_turn_working_set>` 纯文本块，抬头为"以下为上一轮读取/修改过的文件当前全文（完整，非预览，截至本轮开始）；无需再次 query_files："，每份带标题、id、字数、上一轮是读还是改。最多 `AGENT_WORKING_SET_MAX_FILES`=8 份全文、合计 `AGENT_WORKING_SET_MAX_CHARS`=60000 字，超出的只列标题 + id 并标"未附全文"；已在组装上下文里以 [全文] 出现的只列一行。构建失败只记 WARNING，不影响本轮。
- **工作集放在本轮用户消息之前**：`service.py` 用 `SessionLoader.attach_working_set_to_user_content` 把工作集块拼在本轮 user 消息开头、用户原话以"【本轮用户消息】"引出放在最后，工作集 token 计入台账的 `reserved_prompt_tokens`。系统提示与历史消息不动，持久化的仍是用户原话（下一轮不会累积）。router 只看 `router_message`，不受影响。
- **面包屑记读取**：`_extract_file_actions` 把 tool_calls 归一成文件动作，面包屑新增"已读取文件《标题》(id=…)"一类（标题取自工具结果），同一行去重。仍是纯文本，不回放 tool_use/tool_result 块。
- **提示词**（`prompts/base.py`、`prompts/subagents.py`、`message_manager.py` 任务板段）：统一执行协议新增 `### 读取预算`（[全文] / 本轮结果 / 交接里的全文直接用，同一文件本轮最多读一次全文，多份并列发 query_files(id=…)，edit_file 失败优先用候选片段，读 3~5 份后开始产出）；"修改前用 query_files 读取目标"改为"修改前确保持有目标原文（上下文标注[全文]或本轮已读即可）"；hybrid_search 描述为"可能未启用，改用 query_files(query=…) 定位"；parallel_execute 说明读取可以批量，但最简单是同一回复里并列多个 query_files，params 与单体工具一致；旁白只在本轮第一次调用工具前说一句；任务板只在开始规划与全部完成时更新，中间状态与其他工具调用同批。四个角色共用 `HANDOFF_READ_RULE`（交接/待审内容里已有的正文、文件 id、结论直接用，只读缺失或截断的部分）；writer 从 quality_reviewer 返工回来只改报告列出的位置、完成后不再送审；reviewer 每次请求最多一次返工交接、只针对阻断问题，错别字/标点列入报告。
- `load_session_with_compaction` 保留原名（调用方与测试桩依赖），docstring 写明没有任何压缩；`agent/CLAUDE.md` 删去不存在的 `context/compaction.py`，补上预算、渲染标注与跨轮工作集说明。

## Alternatives considered

- **保持 6k 小预算，靠更强的"不要重读"提示词约束**。最有力的理由：每次模型调用的输入更短，单次调用成本最低，也不必担心长上下文稀释注意力。没选：事故数据里花费主要来自重读本身——每次 query_files 都让后续每一轮工具循环多带一份全文，且被截断的上下文正是模型坚持重读的理由；deepseek-flash 有自动前缀缓存，一次性带足的前缀在同一请求的后续调用里按 1/50 计价，比反复重读便宜得多。提示词约束只有在上下文真的给足、且明确标注全文时才站得住。
- **回放上一轮的原始 tool_use/tool_result 对**。最有力的理由：模型能看到自己上一轮实际拿到的内容，不需要另行设计格式，也最贴近 SDK 的原生消息结构。没选：上一轮的工具结果是当时的快照（文件之后可能已被用户或 agent 改过），而且多是预览；结构化工具块跨请求回放有孤立 tool_call_id 风险，runner 现有契约明确不回放（`extract_text_from_message_content` 的注释），`test_session_loader.py` 也锁着"历史里没有原始工具块"。按当前库内容重新加载、以纯文本注入，既新鲜又不碰这个契约。
- **把工作集放进系统提示**。最有力的理由：系统提示是模型最信任的位置，组装上下文也在那里，放在一起最直观。没选：工作集每轮都会变，放进系统提示会让其后的全部历史消息在每轮都失去前缀缓存；拼在本轮用户消息前，系统提示与历史保持不变，缓存一直命中到上一轮。也没有单独插一条 user 消息，避免连续两条 user 消息。
- **把工作集追加到历史里最后一条 assistant 消息**（与面包屑同处）。最有力的理由：和面包屑在一起，语义上就是"我上一轮做过的事"。没选：历史窗口会按 token 预算从旧到新裁剪，大块全文会把更早的对话挤掉；而且下一轮工作集移到更新的那条消息上，旧消息内容随之变化，又破坏历史前缀缓存。

## Consequences

- 收益：注入的每个文件都带 id 和明确的完整性，模型不必靠重读来确认；"继续"之类的跟进轮直接拿到上一轮读过/写过文件的最新全文；预算放大后一章正文、卷纲、几份角色卡通常能整份进入上下文；提示词不再诱导逐步预告、逐步更新任务板和为错别字返工。
- 代价：单次请求的输入 token 明显变多（组装上下文与历史各最多约 32k、工作集最多约 6 万字）。依赖前缀缓存摊薄：同一请求内的工具循环可命中，跨请求只能命中到系统提示中不变的部分，若组装上下文随查询变化，跨请求命中会在那里中断。
- 代价：工作集按文件整份附上，单份超过 60000 字的文件永远只能列标题 + id；只看最近 2 条 assistant 消息，更早读过的文件不会自动带上。
- 代价：条目标题行变长，`TokenBudget` 的 `item_overhead` 按最长的"已截断"标注估算，小预算下会比之前少放一点正文。
- 未做：`README.md` / `README_EN.md` 里"约 6000 token 的对话 / 资料预算"的说法已过时，本次范围只限 agent 后端，未修改；英文模式下工作集块与新标注仍是中文。

## Verification

只跑与改动相关的测试文件（机器较慢，未跑全量套件）：

- `tests/test_agent/test_session_loader.py`：新增 `TestCrossTurnWorkingSet` 两个用例——上两轮读/写的文件以当前库内容注入（含 parallel_execute 子任务、换行保留、软删除/跨项目/更早轮次/关键词搜索排除、面包屑出现"已读取文件"、全文不进历史、拼接后用户原话在最后）；文件数上限与上下文 [全文] 去重。
- `tests/test_agent/test_round3_assembler.py::test_items_render_id_and_explicit_completeness`：截断条目渲染 `[已截断：显示 x/y 字；全文用 query_files(id="…")]`、完整条目 `[全文]`、段头读取约定、正文换行保留；`test_assembled_block_respects_max_tokens` 在新增渲染开销后仍成立。
- `tests/test_agent/test_message_manager_context.py::test_raw_context_keeps_paragraphs_and_skips_duplicate_previews`：原始上下文段落保留、完整条目只留标题 + id、截断条目节选以"…（节选）"收尾。
- `tests/test_agent/test_context_budget.py`：截断改为 `TRUNCATION_SUFFIX` 并记录 `shown_chars` / `original_chars`。
- `tests/test_agent/test_subagent_prompts.py`、`test_writing_guidance_contract.py`：读取预算、返工上限、交接读取规则、hybrid_search 措辞、旁白规则。
- 另跑：test_context_assembler、test_context_prioritizer、test_message_manager_{metadata,permissions,skills,folder_ids}、test_prompt_alignment、test_runtime_prompt_source、tests/test_prompts、test_service、test_metrics、test_agent_service_session_id、test_db_only_writing_configuration、test_material_workflow、test_round3_{converge,graph,history,constants}、test_scenarios、test_suggest_service，以及 tests/test_api 的 agent_api_structure / agent_context_capacity / chat_history_order_postgres / materials_story_scope 与 tests/test_services 的 data_isolation / vector_metadata_projection / vector_search_launch_hardening，全部通过（postgres 用例在无 PG 时跳过）。
