# Agent Note: 素材拆解的 LLM JSON 先修复再判失败

Status: implemented

## Problem

10-08 生产日志：08:54–09:21 之间素材拆解出现十几次 `LLMOutputError: 无法从响应中提取有效的 JSON (finish_reason=stop)`，覆盖「章节角色提及提取」和「角色实体构建」两步，多部小说的章节被记为失败（例如一次拆解 117 章里 5 章失败、另一次 2 章、3 章失败），角色实体（如「胧月岩介」「贾亭西」）直接丢失。完整响应显示模型输出的是带 ```json 围栏的正常结构，只是在中文描述里直接写了英文双引号（`以"禹韭"为名`、`被认为"还不够格"`），整段 JSON 因此非法。`DeepSeekClient.extract_json_from_response` 只做三次严格 `json.loads`（整体 / 代码块 / 花括号片段），全部失败就抛错；按既有决策 JSON 解析失败不重试，于是整章/整个角色被丢弃。

## Decision

`flows/utils/clients/llm.py`：三次严格解析都失败后，对花括号片段调用 `json_repair.loads(..., skip_json_loads=True)` 修复一次，结果是非空 dict 才返回，并记一条 warning；否则仍抛 `LLMOutputError`。`finish_reason == "length"` 的截断输出不修复，仍按失败处理。`json-repair` 已是 `requirements.txt` 依赖（agent 工具参数解析在用），worker 镜像同一份 requirements，无新增依赖。回归用例：`tests/test_flows/unit/test_llm_client.py` 的内引号样本与截断样本。

## Alternatives considered

- **JSON 解析失败时重试一次 LLM 调用**。最强理由：模型下一次多半会正确转义，不依赖启发式修复。被否：每次重试都是一次付费调用，且 AGENTS.md 明确不自动重试付费调用；既有决策也已因成本把 JSON 失败设为不重试。
- **在提示词里强调「字符串内引号必须转义或用中文引号」**。最强理由：从源头减少非法输出。被否：只能降低概率不能消除，模型在长中文描述里仍会写英文引号；可以作为后续补充，但解析端兜底仍然必要。
- **启用 DeepSeek 的 JSON mode（response_format）**。最强理由：服务端保证合法 JSON。被否：需要逐个改调用点并验证各提示词的输出结构，范围超出本次修复；未验证 deepseek-flash 在该模式下对长中文输出的行为。

## Consequences

- 收益：内引号这类可确定修复的输出不再整章/整角色丢失，不增加任何模型调用。
- 代价：`json_repair` 是启发式修复，极端情况下可能把字段边界猜错（例如字符串里出现 `", "` 这类序列）；只接受非空对象并保留截断输出失败，限制了误修范围。修复次数目前只在 worker 日志里有 warning，没有指标。

## Verification

`cd apps/server && .venv/bin/python -m pytest -q -n 0 --no-cov tests/test_flows/unit/test_llm_client.py tests/test_flows/unit/test_llm_failure_policy.py`；生产样本（10-08 08:57 章节 494、09:15 章节 686）结构同测试用例。
