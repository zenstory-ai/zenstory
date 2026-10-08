# Agent Note: 工作台用户可见文案面向作者，不展示工程细节

Status: implemented

## Problem

PR #139 删除了订阅页的微信 AIchuangzuo999 兑换码联系引导。owner 同时要求对整个前端（首页、定价、登录注册、Dashboard、项目工作台、技能、设置、会员支付、Admin，以及弹窗、表单、提示、空状态、加载与错误状态，中英文两套）做一次文案 review 并直接修改：去掉防御性、免责式、重复解释式的说明，不向作者展示服务器、验签、到账判定、Agent/工作流、上下文、检索分数、错误码等工程细节；必要的限制、费用、不可逆操作和错误恢复信息保留，并写成可执行的短句；不新增未经验证的功能承诺；兑换码入口与兑换开通功能必须保留。

审阅中发现几类问题：聊天气泡把后端 handoff 事件的 `target_agent`（`planner`、`quality_reviewer` 等内部 id）原样填进"交接给 {{agent}}"，创作计划标签显示 `quick`/`standard` 和 `planner → writer`；文案承诺了产品没有的功能（首页"团队协作"套餐卡、"不怕误删""实时同步"、积分兑换 Pro 的"优先功能体验"、首页"导出前自动整理章节"、版本恢复提示"当前内容会被保存为新版本"与后端行为不符）；聊天面板会显示 `invalid_handoff` 一类内部停止原因、"查看技术详情"折叠区、引用来源的内部 ID 与相关度分数；`translateError` 对没有翻译的 `ERR_*` 代码原样返回，作者会看到 `ERR_REDEMPTION_...` 这类代码；OAuth 回调页直接显示提供方错误码（如 `access_denied`）；若干 key 缺失导致界面显示原始 key 名（`projects.deleteConfirm`、`saveFailed`）或中文界面显示英文 fallback。

## Decision

- 文案规则：按作者要完成的任务命名按钮和入口；标题已说清的事，副标题不再复述；错误提示说明发生了什么、下一步怎么做；配额数字、价格、"删除后无法恢复"、"已付款请不要重复支付"、订单号和"刷新支付结果"等影响判断的信息保留。
- 功能承诺只写已核实的事实：首页定价预告只保留免费版与专业版两栏；积分兑换 Pro 列"AI 消息不限条数""项目数不限""每月 5 次素材拆解"（与 `DEFAULT_PRO_PLAN_FEATURES` 一致）；文件版本恢复提示写"当前正文会被替换为该版本内容，已有版本记录不会删除"；项目快照恢复保留"当前内容会先自动保存为一个版本"（`snapshot_service` 会先建 pre_rollback 快照）。
- 付款异常的求助渠道统一为站内既有的 `support@zenstory.ai`；在线支付不可用时，提示按弹窗所在页面区分（`PaymentCheckoutModal` 的 `redeemEntry` prop）：订阅权益页有自己的「兑换码」按钮，传 `redeemEntry="on-page"`，显示 `dashboard:billing.paymentUnavailableOnPage`"点页面上的「兑换码」也能开通"；定价页没有兑换码按钮，用默认值 `billing-page`，显示 `dashboard:billing.paymentUnavailable`"可以在「订阅权益」页点「兑换码」开通"，并附「去订阅权益页」链接到 `/dashboard/billing?plan=pro`（支付关闭时该链接直接打开兑换弹窗）。下单接口返回 `ERR_PAYMENT_UNAVAILABLE` 时弹窗显示同一条按页面区分的提示；`errors:ERR_PAYMENT_UNAVAILABLE` 本身不知道所在页面，写成指向订阅权益页的版本。兑换码按钮、`?plan=pro` 自动打开兑换弹窗、`RedeemCodeModal` 全流程和 `settings:subscription.*` 兑换相关 key 均保留；兑换成功改为显示本地化的"兑换成功！Pro 已增加 {{days}} 天。"，不再显示后端英文 message。
- `translateError`（`apps/web/src/lib/errorHandler.ts`）遇到没有翻译的 `ERR_*` 代码时返回 `errors:ERR_INTERNAL_SERVER_ERROR`；非代码文本仍原样返回。
- 聊天面板不再渲染 `workflow.stopReason`、`workflow.viewTechnicalDetails` 和 `context.relevanceScore`，引用来源不显示内部 ID；对应 key 已删除。
- 聊天面板显示 AI 角色时用 `chat:workflow.agents.*` 的本地化角色名（大纲规划师、爽点设计师、内容创作者、质量审稿人），映射在 `apps/web/src/lib/agentDisplayName.ts`：交接气泡写作"接下来由{{agent}}继续：{{reason}}"（没有 reason 时用 `handoffMessageShort`），实时流（`useChatStreaming.ts`）与历史回放（`chatDisplayEvents.ts`）共用同一个格式化函数；目标角色不在映射表内时不显示这条气泡。创作计划标签显示 `initial_agent` 加 `workflow_agents` 映射后的角色链，不再显示 `quick`/`standard` 等内部计划名和 `planner → writer` 这类内部 id；Agent 选择标签优先按 `agent_type` 取本地化名，英文界面不再显示后端写死的中文名。OAuth 回调页对非 `ERR_` 的提供方错误码和内部检查失败统一显示 `auth:errors.oauthFailed`。
- 隐私政策 7.1「数据导出」（`apps/web/public/locales/{zh,en}/privacy.json` 的 `rights.dataExport.content`）改了实质内容，这是本次唯一改动的法律条文。原文写"我们提供便捷的导出功能，允许您以结构化格式下载数据"，并列出大纲、草稿、角色、世界观、聊天记录；但 `apps/server/api/export.py` 只有 `GET /projects/{id}/export/drafts`，只能把正文合并导出为 TXT，原文与现状不符。现文改为：项目中可直接将正文导出为 TXT；大纲、角色、世界观条目、聊天记录等其他数据，作者通过隐私政策中的邮箱（`support@zenstory.ai`）申请，我们以结构化格式提供。这一改动新增了一项运营义务：收到申请后要人工整理并以结构化格式交付这些数据，并受同节"收到请求后一个月内予以回复"的承诺约束。合并前需 owner 确认接受这项义务。隐私政策和服务条款的其他条文没有改动。
- 素材库文案（`materials` 命名空间、`editor:fileTree` 的素材 key、`errors:ERR_MATERIAL_*` 与文件大小/字数/编码错误、`settings:subscription.features.material_*`）按同一规则改写：只写默认开启的产出（章节梗概、角色、世界观、金手指，见 `config/material_settings.py`），不提"剧情线/故事线"；保留 20MB、30 万字、需要章节标题、免费版不含素材库、每月拆解次数等限制（付费墙里的次数从套餐目录的 Pro 档读取，见 `2026-10-08-workspace-copy-terms-and-live-limits.md`）；配额条写明"每次上传或重试用 1 次"（上传与重试都扣 `material_decompose`）；删除确认写明已添加到项目的文件会保留（导入时内容复制成项目文件）。失败文案沿用 `2026-10-05-material-job-failure-codes-and-refunds.md` 的约定，不承诺"已退回次数"；新增 `ERR_MATERIAL_META_EXTRACTION_FAILED`（金手指和世界观没拆出来、其余已完成、重试可补全）和前端兜底码 `ERR_MATERIAL_UPLOAD_FAILED` 的中英文。上传弹窗的标题输入框改用 `uploadModal.titleLabel` 作标签（原先误用了弹窗标题）。无引用的 key 已删除：`materials:uploadModal.{selectedFile,titleOptional,titleHint,success,errors.noFile,errors.uploadFailed}`、`materials:detail.{cheat,traits,plotType,relationshipType,sentiment*,goldenfingerType,eventDescription,significance,noData,idLabel}`、`materials:quota.decomposeDescription`、`editor:fileTree.{referenceLibraryGoUpload,importSuccess,importFailed}`。
- 元件中 `t(key, fallback)` 的 fallback 与 zh locale 保持一致；缺失的 key（如 `dashboard:projects.deleteConfirm`、`editor:saveFailed`、`admin:codes.confirmDeactivate`）已补齐中英文。

## Alternatives considered

- **未知错误码继续原样返回**（原行为）。最强理由：原始代码便于用户截图反馈、便于排查。被否：作者看到 `ERR_*` 无法理解也无法行动；排查所需的 request ID 已经由后端错误响应和日志承载，不依赖界面显示代码。
- **保留"查看技术详情"折叠区和停止原因**（原行为）。最强理由：默认折叠，不打扰大多数作者，又给好奇的用户留了线索。被否：内容是 Agent/工具调用上限等工程说明，对作者没有可执行的下一步；卡片上的"继续完成 / 拆成小步骤 / 我先手动改"三个操作已经覆盖恢复路径。
- **隐私政策 7.1 保持原文不动**。最强理由：法律条文由 owner 定稿，文案 review 不应改动法律承诺，也不会新增任何运营义务。被否：原文承诺的"以结构化格式下载全部数据"的导出功能并不存在，继续展示等于对作者做出不实承诺；改成"正文可直接导出、其他数据邮件申请"与现状一致，同时保住作者取得全部数据的权利。
- **保留首页"团队协作"套餐卡**（原状）。最强理由：为未来的团队版预热。被否：后端只有 free/pro 两档，也没有多人协作功能，属于未经验证的功能承诺。
- **给定价页也加一个「兑换码」按钮，支付不可用时统一写"点页面上的「兑换码」"**。最强理由：一句文案到处适用，定价页上就能兑换，少跳一页。被否：定价页是未登录也能看的营销页，再放一个兑换入口要处理未登录跳转和 `RedeemCodeModal` 的刷新逻辑，范围超出文案修改；订阅权益页已有完整的兑换流程，`?plan=pro` 在支付关闭时直接打开兑换弹窗，一个链接就够。

## Consequences

- 收益：作者能直接看懂每个页面能做什么、出了问题怎么恢复；界面不再出现内部代码、英文 fallback 或原始 key；文案与后端实际行为一致。
- 代价：后端新增 agent 类型时，前端映射表要同步补上，否则这类交接气泡不显示。以后在别的页面打开 `PaymentCheckoutModal` 时，只有页面自带「兑换码」按钮才能传 `redeemEntry="on-page"`，否则保持默认。隐私政策 7.1 承诺按邮件申请以结构化格式提供正文以外的数据，目前没有自助导出，需要运营人工处理（另一选择是补齐自助导出功能后再改回"可直接下载"）。错误界面不再显示具体错误码，排查需要依赖后端日志与 request ID；`errorHandler.test.ts`、`MessageList.test.tsx`、`OAuthCallback.test.tsx` 等测试的断言随之改为断言通用文案或断言内部信息不出现；多个 Playwright spec 的选择器改为新文案。
- 未做：Agent `workflow_stopped` 的 message 仍含工程用语，后端 `core/error_codes.py` 里素材错误码的中英文短句未同步改写（前端只用 `errors` 命名空间翻译），`public/docs` 文档正文本次未审阅。首页社会证明数字（2000+ 创作者等）已在 `2026-10-08-dashboard-home-copy.md` 中删除。
