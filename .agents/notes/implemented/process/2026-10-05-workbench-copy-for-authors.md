# Agent Note: 工作台用户可见文案面向作者，不展示工程细节

Status: implemented

## Problem

PR #139 删除了订阅页的微信 AIchuangzuo999 兑换码联系引导。owner 同时要求对整个前端（首页、定价、登录注册、Dashboard、项目工作台、技能、设置、会员支付、Admin，以及弹窗、表单、提示、空状态、加载与错误状态，中英文两套）做一次文案 review 并直接修改：去掉防御性、免责式、重复解释式的说明，不向作者展示服务器、验签、到账判定、Agent/工作流、上下文、检索分数、错误码等工程细节；必要的限制、费用、不可逆操作和错误恢复信息保留，并写成可执行的短句；不新增未经验证的功能承诺；兑换码入口与兑换开通功能必须保留。

审阅中发现几类问题：文案承诺了产品没有的功能（首页"团队协作"套餐卡、"不怕误删""实时同步"、积分兑换 Pro 的"优先功能体验"、首页"导出前自动整理章节"、版本恢复提示"当前内容会被保存为新版本"与后端行为不符）；聊天面板会显示 `invalid_handoff` 一类内部停止原因、"查看技术详情"折叠区、引用来源的内部 ID 与相关度分数；`translateError` 对没有翻译的 `ERR_*` 代码原样返回，作者会看到 `ERR_REDEMPTION_...` 这类代码；OAuth 回调页直接显示提供方错误码（如 `access_denied`）；若干 key 缺失导致界面显示原始 key 名（`projects.deleteConfirm`、`saveFailed`）或中文界面显示英文 fallback。

## Decision

- 文案规则：按作者要完成的任务命名按钮和入口；标题已说清的事，副标题不再复述；错误提示说明发生了什么、下一步怎么做；配额数字、价格、"删除后无法恢复"、"已付款请不要重复支付"、订单号和"刷新支付结果"等影响判断的信息保留。
- 功能承诺只写已核实的事实：首页定价预告只保留免费版与专业版两栏；积分兑换 Pro 只列"更多 AI 写作额度""可创建更多项目"；文件版本恢复提示写"当前正文会被替换为该版本内容，已有版本记录不会删除"；项目快照恢复保留"当前内容会先自动保存为一个版本"（`snapshot_service` 会先建 pre_rollback 快照）。
- 付款异常的求助渠道统一为站内既有的 `support@zenstory.ai`；在线支付不可用时，提示（`dashboard:billing.paymentUnavailable` 与 `errors:ERR_PAYMENT_UNAVAILABLE`）指向「订阅权益」页的「兑换码」按钮。兑换码按钮、`?plan=pro` 自动打开兑换弹窗、`RedeemCodeModal` 全流程和 `settings:subscription.*` 兑换相关 key 均保留；兑换成功改为显示本地化的"兑换成功，本次兑换 {{days}} 天会员。"，不再显示后端英文 message。
- `translateError`（`apps/web/src/lib/errorHandler.ts`）遇到没有翻译的 `ERR_*` 代码时返回 `errors:ERR_INTERNAL_SERVER_ERROR`；非代码文本仍原样返回。
- 聊天面板不再渲染 `workflow.stopReason`、`workflow.viewTechnicalDetails` 和 `context.relevanceScore`，引用来源不显示内部 ID；对应 key 已删除。OAuth 回调页对非 `ERR_` 的提供方错误码和内部检查失败统一显示 `auth:errors.oauthFailed`。
- 元件中 `t(key, fallback)` 的 fallback 与 zh locale 保持一致；缺失的 key（如 `dashboard:projects.deleteConfirm`、`editor:saveFailed`、`admin:codes.confirmDeactivate`）已补齐中英文。

## Alternatives considered

- **未知错误码继续原样返回**（原行为）。最强理由：原始代码便于用户截图反馈、便于排查。被否：作者看到 `ERR_*` 无法理解也无法行动；排查所需的 request ID 已经由后端错误响应和日志承载，不依赖界面显示代码。
- **保留"查看技术详情"折叠区和停止原因**（原行为）。最强理由：默认折叠，不打扰大多数作者，又给好奇的用户留了线索。被否：内容是 Agent/工具调用上限等工程说明，对作者没有可执行的下一步；卡片上的"继续完成 / 拆成小步骤 / 我先手动改"三个操作已经覆盖恢复路径。
- **保留首页"团队协作"套餐卡**（原状）。最强理由：为未来的团队版预热。被否：后端只有 free/pro 两档，也没有多人协作功能，属于未经验证的功能承诺。

## Consequences

- 收益：作者能直接看懂每个页面能做什么、出了问题怎么恢复；界面不再出现内部代码、英文 fallback 或原始 key；文案与后端实际行为一致。
- 代价：错误界面不再显示具体错误码，排查需要依赖后端日志与 request ID；`errorHandler.test.ts`、`MessageList.test.tsx`、`OAuthCallback.test.tsx` 等测试的断言随之改为断言通用文案或断言内部信息不出现；多个 Playwright spec 的选择器改为新文案。
- 未做：后端兑换接口与限频的 `detail` 仍是英文句子（应改为返回 `ERR_REDEMPTION_*` 代码），Agent `workflow_stopped` 的 message 仍含工程用语，素材库相关文案由另一个 PR 处理，`public/docs` 文档正文本次未审阅，首页社会证明数字（2000+ 创作者等）未核实来源也未改动。
