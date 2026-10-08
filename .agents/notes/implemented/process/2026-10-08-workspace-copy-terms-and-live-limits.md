# Agent Note: 工作区文案统一术语，付费墙次数从套餐目录读取

Status: implemented

## Problem

2026-10-08 的文案清单（C14–C34）核对后，工作区、编辑器、素材库、技能和通用组件里还有几类问题：

- 同一个东西有好几套名字。默认文件夹叫「设定 / 正文 / 素材」，文件树占位符、聊天里的文件类型标签和 `common:fileTypes` 却写「世界观 / 手稿 / 参考素材」；项目统计页在三个入口分别叫「数据统计」「项目统计」；作品类型在英文里是 Long-form Novel、Screenplay、Mini-drama Script 三种叫法；订阅入口叫「订阅中心」，升级写「升级会员」「开通会员」「升级套餐」。
- 素材库付费墙把「每月可拆解 5 次」写死在 locale 和组件 fallback 里。5 次是套餐数据（`DEFAULT_PRO_PLAN_FEATURES` 和生产库 plan 行），不是代码常量，改了套餐，文案也不会跟着变。
- 几处功能性文案是错的或缺失：在「剧本」文件夹里新建文件，占位符显示「新建项目」；导出正文后没有任何反馈；`LoadingSpinner` 用 `t('common.loading')` 在默认命名空间 `common` 下找不到 key，中文界面显示英文 fallback；底部导航、清空搜索、关闭侧边栏的 aria-label 写死英文；自定义技能空状态没有说明技能是什么。
- 配额文案只给「升级」一条路。项目数和自定义技能数满了，可以先删掉不用的；文件历史版本满了，正文其实照常保存，原文案却让人以为稿子没存上。

## Decision

- 文件类型统一为：大纲 / 正文 / 角色卡 / 设定 / 素材（英文 Outline / Manuscript / Character Sheet / Setting / Material）。改动的 key 有 `chat:fileType.*`、`chat:action.new_snippet`、`common:fileTypes.{lore,snippet}`、`editor:fileTree.{newLore,newSnippet,uploadMaterial,searchMaterials}`。参考小说库的空状态写「还没有参考小说」。素材拆解产出里的「世界观」是拆解维度，不是文件类型，保持不变。
- 字数单位写「字 / 千字」（`chat:size.*`），英文写 chars。上下文来源层级 `constraint` / `inspiration` 显示为「设定 / 参考」，避免和默认关闭的灵感库混淆。
- 历史版本：名称统一为「历史版本」，按钮写「恢复到此版本」，`beforeAI` 写「AI 修改前」。恢复确认按后端行为分开写：项目快照恢复（`VersionHistoryPanel.handleRollback` → `versionApi.rollback`，用 `editor:versionHistory.confirmRollback`，`snapshot_service` 会先建 pre_rollback 快照）写「当前内容会先自动存成一个版本，随时可以换回来」；单文件恢复走 `rollback_to_version`，它只把旧内容追加成一个新版本，版本额度满了连这一步也跳过，不会先存当前正文，所以两个调用点都不承诺能换回来：文件历史面板（`versions:rollbackConfirm`）写「当前正文会换成这个版本的内容，已有的历史版本都会保留」，聊天里撤销 AI 修改（`ChatPanel.handleUndo` → `fileVersionApi.rollback`，用 `editor:versionHistory.confirmUndoAIEdit`）写「撤销这次 AI 修改？正文会换回修改前的内容，已有的历史版本都会保留。」。空状态分开说明版本从哪里来：项目快照在 AI 改完文件后自动生成，文件版本在保存后生成。版本满了的提示（`editor:versionHistory.fileVersionLimit*`、`editor:saveVersionQuotaExceeded`、`versions:quota.*`）先说「正文照常保存，只是不再生成新版本」，再提开通 Pro，不写具体版本数，因为套餐目录接口不返回 `file_versions_per_file`。
- 配额出口：项目数满了写「可以先删除不再需要的项目，或开通 Pro，项目不限」；自定义技能满了写「删掉不用的技能就能腾出名额，或开通 Pro 建更多技能」。标题已说明「已达上限」，说明里不再重复。升级按钮统一写「开通 Pro」。
- 素材库付费墙的次数从套餐目录读取：新 hook `apps/web/src/hooks/useProMaterialDecompositionsLimit.ts` 复用 BillingPage / PricingPage 的 `["public-subscription-catalog"]` 查询，取 `name === "pro"` 档的 `material_decompositions_monthly`，插值到 `materials:teaserDescription` 和 `materials:quota.uploadDescription` 的 `{{limit}}`。目录还在加载、请求失败，或值不是正数（例如 `-1` 不限）时，改用不带数字的 `teaserDescriptionNoLimit` / `quota.uploadDescriptionNoLimit`。只有在显示付费墙时才发这个请求：MaterialsPage 只在预览态启用，弹窗只在打开后启用。
- 编辑器顶栏的导出按钮写「导出正文（TXT）」，导出成功后弹 `editor:header.exportSuccess`「正文已导出为 TXT 文件」；没有打开项目时直接返回，不弹成功提示。
- `FileTreePane` 和 `MobileFileTree` 的新建占位符补上 `script`（「新建剧本」），找不到类型时改用「新文件名称」，不再借用「新建项目」。
- `LoadingSpinner` 的 `PageLoader` / `InlineLoader` 改用 `t('common:loading', '加载中...')`。底部导航、清空搜索、关闭侧边栏的 aria-label 改走 i18n（`editor:bottomTabs.ariaLabel`、`common:clearSearch`、`common:closeSidebar`），英文值不变。
- 作品类型统一为「长篇小说 / 短篇小说 / 短剧剧本」，英文 Novel / Short Story / Short Drama Script。改动范围是前端 `chat` / `dashboard` / `common` / `inspirations` / `admin` 的 locale，以及 `apps/server/config/project_templates.py` 的英文 `name`。这些名字只通过 `/project-templates` 返回给界面显示，没有代码或测试按名字匹配。
- 其他：`seo-config.ts` 的「仪表盘」改为「工作台」，「您」改为「你」，中英文之间加空格；管理后台套餐 features 的 JSON 示例只保留后端会执行的 key（`ai_conversations_per_day`、`max_projects`、`file_versions_per_file`、`materials_library_access`、`material_decompositions`、`custom_skills`、`inspiration_copies_monthly`）；邀请码空状态写明触发条件：好友用邀请码注册并验证邮箱后，双方都得积分（见 `referral_service.complete_referral_and_reward`，由 `api/verification.py` 触发）。

## Alternatives considered

- **付费墙继续写死「每月 5 次」**。最强理由：不多发一个请求，首屏文案稳定，测试也简单。被否：5 次是套餐数据，后台改了套餐，界面会向用户承诺错误的次数；目录查询和定价页、订阅页共用缓存，多出来的只是在付费墙首次出现时发一次公开 GET。
- **两种恢复确认合成一句「当前内容会先自动存成一个版本」**（清单 C19 的原提议）。最强理由：同一个操作只用一种说法，作者不用分辨两套措辞。被否：单文件恢复不会额外存当前内容；版本额度满了时，当前正文可能根本不在历史里，这句话会让作者以为能换回来。
- **文件版本满了时写「开通 Pro 每个文件可保留 100 个版本」**。最强理由：给出具体数字，升级理由更明确。被否：前端拿不到这个数（目录接口没有 `file_versions_per_file`），写死就会重复素材库次数写死的问题。
- **作品类型英文名只改前端，后端模板保持 Long-form Novel / Mini-drama Script**。最强理由：后端不动，部署顺序不会出问题。被否：`/project-templates` 的英文名会直接显示给用户，三种叫法会继续并存；这个名字没有任何代码或测试按值匹配，改起来没有兼容风险。

## Consequences

- 收益：同一个概念在文件树、聊天、搜索筛选、模板里用同一个名字；付费墙次数跟着套餐数据走；版本满了不再让作者以为稿子没保存；导出有完成反馈；读屏用户在中文界面听到中文标签。
- 代价：付费墙首次出现时多一个 `/api/v1/subscription/catalog` 请求，目录还没加载完时先显示不带数字的说明，加载完后再换成带数字的句子。`MaterialsPage.test.tsx` 新增 `getCatalog` mock；`round3_frontend_chatpanel.test.tsx` 断言撤销 AI 修改用 `confirmUndoAIEdit` 而不是快照恢复的 `confirmRollback`；`FileSearchInput`、`FileTreePane`、`FileTree.lifecycle`、`BottomTabs`、`MobileSidebar`、`LoadingSpinner` 测试的 aria-label 和 loading 断言随之改为新 key 或中文；e2e `responsive-workspace-mocked`、`versions`、`LoginPage` 的选择器改为新文案。
- 未做：`chat:panel.fileVersionLimit*` 和 `errors:ERR_QUOTA_FILE_VERSIONS_EXCEEDED`（与额度用完文案相邻，属于另一组改动），`home.json` 的作品类型名（首页文案属于另一组改动），`dashboard:dashboard.title`「项目数据统计」和工作台下拉导航的 aria-label（dashboard 命名空间不在本次范围）。
