# Agent Note: 章节摘要的 JSON 请求合同与安全失败信息

Status: implemented

## Problem

章节摘要提示词要求 JSON，但实际请求没有 `response_format`。近期只读审计发现 5 个章节摘要收到非 JSON 响应；不能由此证明全部素材失败都来自这一原因。另一个可复现缺陷是解析异常包含模型响应，任务又记录 preview、解析结果或原始异常，Prefect 会把异常写入失败状态；API 的日志 formatter 无法保护这条链路。

本决定补充 [材料 JSON 修复](2026-10-08-material-llm-json-repair.md)、[任务错误与退额](2026-10-05-material-job-failure-codes-and-refunds.md)，并收窄 [主备切换](../architecture/2026-10-20-llm-primary-backup-failover.md) 对章节摘要的可用供应商范围。它不是全站日志隐私整改，也不推翻 [API 日志管线](../architecture/2026-10-05-backend-log-and-error-pipeline.md) 的统一脱敏设计。

## Decision

- 仅章节摘要调用显式发送 `response_format={"type":"json_object"}`。同步切换路径只在已配置槽位中选择有官方 JSON Object 合同的 `deepseek / deepseek-flash`；不发明模型别名，不向未确认支持的备用模型静默降级。异步写作、其他素材调用保持原行为。
- 无兼容槽位时，在 SDK 调用前以固定异常失败，映射既有不可重试的 `ERR_MATERIAL_LLM_UNAVAILABLE`，复用现有终态与退额规则。请求格式不会在失败后被去掉；不增加模型调用或付费重试。真实成功调用按实际供应商模型记账。
- 解析错误只保留固定失败类别、受限 finish_reason、响应长度；章节摘要、小说梗概、剧情、角色任务的相关解析/校验日志不记录正文、preview、解析数据或原始异常。保留既有 JSON / 代码围栏 / 内嵌对象 / 非截断修复兼容，截断保护、异常类别与重试规则不变。
- 使用合成响应锁定请求参数、供应商选择、失败分级、实际用量及 Prefect 失败状态里的隐私边界，不调用真实模型。

## Alternatives considered

- **只加 JSON 参数，备用仍照常切换**：改动最少，保留最多可用性。但 MiniMax 当前 OpenAI 兼容文档未给出该格式合同，无法保证切换后约束生效；文档缺失也不等于实测拒绝。此次不依赖未知行为。
- **改成强制工具调用返回摘要**：可望复用多模型结构化能力。但现有素材响应只读取 content，共同强制工具合同也未验证，会扩大适配与回归范围。
- **放宽解析或让模型重新生成**：部分任务可能恢复，但无法替代缺失的请求合同，且会扩大接受面或产生额外计费，故不采用。
- **仅依赖 API formatter 脱敏**：集中维护成本低，但 Prefect 的日志与失败状态不都经过它，因此在已证实的四个任务和解析异常源头最小修复。

## Consequences

收益是章节摘要的格式约束进入实际请求，且相关失败信息不再携带模型摘要。代价是只有 MiniMax 或未知模型的配置不能执行章节摘要，DeepSeek 不可用时也不会用无格式保证的备用完成；其他能力不收窄。JSON mode 仍可能空响应或截断，应用仍须校验；本地 mock 证据不证明线上失败率已下降。

官方依据：[DeepSeek JSON Output](https://api-docs.deepseek.com/guides/json_mode/)、[MiniMax OpenAI SDK](https://platform.minimax.io/docs/api-reference/text-openai-api)，查阅于 2026-10-24。

## Verification

合成隐私用例修改前 8 个失败，JSON 请求合同修改前 5 个失败；修复后通过。真实 OpenAI SDK 使用 MockTransport，验证序列化请求和成本守卫都保留格式参数，不产生外部请求；实际选中槽位的 usage 只计一次。最终关联回归 426 passed / 2 skipped，Agent 全套 2557 passed / 25 skipped（两组有重叠），未调用真实模型。独立代码审查通过；未验证上线后的失败率。
