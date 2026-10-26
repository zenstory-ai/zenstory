# Agent Note: 供应商失败原因的最小脱敏观测

Status: implemented

关联：[主备切换](../architecture/2026-10-20-llm-primary-backup-failover.md)、[路由失败](2026-10-18-router-invalid-decision-safe-stop.md)、[调用指标](../feature/2026-10-24-admin-model-call-metrics.md)。

## Problem

最近四小时六次 MiniMax router HTTP 422 已确认，但现有日志只有 provider_failure，无法区分供应商拒绝原因。DeepSeek 和 MiniMax 的失败应在同一请求上下文里留下必要原因，不依赖原始响应或异常文本。

## Decision

在现有 llm_failover 与 SDK 模型适配层的失败边界补一条固定事件，覆盖异步/同步请求和流式中断。只保留供应商/模型、固定调用点与阶段、HTTP 状态、固定分类、固定响应结构类别和存在时的白名单 MiniMax 数字业务码；DeepSeek 依官方 HTTP 错误表分类，MiniMax 仅依 base_resp.status_code 分类。未知值保持 unknown，不从 message/status_msg/正文猜原因；只允许已知参数字段名。沿用现有 request_id / agent_run_id，无新表、平台或通用观测系统。

先补26项失败回归，再实现共同边界；最终285项相关回归通过（其中供应商边界57项）。独立审查指出诊断属性读取可能替换原异常，补3项红例后收窄为SDK异常实例数据读取，不执行任意异常属性；同时覆盖413不切换。验证同一异常继续上抛、400/413/422不新增切换、已输出的流不中途重试、真实用量/退款不改、取消和本地成本拒绝不冒充供应商失败。所有模型mock，合成载荷带隐私哨兵，检查日志字段和traceback均不泄漏。

## Alternatives considered

- **记录原始异常或响应**：最容易定位，但会泄漏作品、供应商原文或身份，拒绝。
- **在 router 单独补日志**：改动最少，但写作流、润色/建议和素材链路仍缺原因，改在现有共同边界复用。
- **根据错误文案推断原因**：能覆盖更多供应商变体，但不稳定且会把推断当事实，未知值不猜。

## Consequences

改动只增加失败元数据，不改变模型参数、提示、输出、权限、预算、用量、退额、重试或主备策略。无法保证上游总会给可识别业务码；未知错误仍须后续观测或供应商支持，不宣称历史422已修复。只提PR，不合并、不发布。

官方合同：https://api-docs.deepseek.com/quick_start/error_codes/ 与 https://platform.minimax.io/docs/api-reference/errorcode ，2026-10-25核对。

诊断在SDK实例数据中读取body/param，正文和status_msg不参与分类；SDK之外异常仅记固定类型分类和实例数字HTTP状态。httpx/httpx2连接与超时可分类，但本批不改既有切换/计量逻辑。事件为WARNING且exc_info=False，即使备用成功也保留失败供应商那次诊断；成功、取消与本地预算拒绝不新增此事件。
