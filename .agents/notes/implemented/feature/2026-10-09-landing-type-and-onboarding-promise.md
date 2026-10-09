# Agent Note: 入口选的作品类型贯通到工作台，引导页不空许诺，工作区去掉原生弹窗

Status: implemented

## Problem

2026-10-08 新用户审计（shortstory、drama、serial 三个 persona）发现入口和工作区有几处信任与顺手问题：

- 官网「短剧与网剧脚本」等类型卡片只带 `source` 跳注册，作品类型在注册、验证、引导之后丢失；`DashboardHome` 写死 `useState("novel")`，短篇和短剧作者进来默认是长篇（#13）。
- 引导问卷只有探索者、连载、职业、同人、工作室五类身份，没有短篇作者和编剧（#35）。
- 引导页右栏写「首页会为你推荐」，但工作台的「今天可以做的事」和「新手上手清单」两个面板默认关闭（`VITE_DASHBOARD_*` flag 为 false），保存后首页什么也不推荐（#14）。
- 390 宽手机上，项目切换器外层 `max-w-[calc(100vw-170px)]` 和 ☰ 重叠，点 ☰ 会点到项目名；下拉用 `left-1/2 -translate-x-1/2` 相对按钮居中，长项目名时被推出屏幕（#15）。
- 工作台首页看不到今天还剩几条 AI 消息（#30 的一部分）。
- 重新打开项目时编辑器是空的，要在文件树里重新找上次写的章节（#31 的一部分）。
- 删除项目（工作台卡片、项目切换器）和删除文件（桌面、移动文件树）用 `window.confirm`，「至少保留一个项目」和「引用素材已达上限」用 `window.alert`，和应用其他地方的对话框风格不一致，在部分内嵌浏览器里还会卡住页面（#31 的一部分）。

## Decision

- **作品类型偏好**：新增 `apps/web/src/lib/preferredProjectType.ts`，在 localStorage 的 `zenstory_preferred_project_type` 存 `{type, ts}`，只接受 `novel`、`short`、`screenplay`，超过 7 天视为失效并删除，读写都包 try/catch（存储不可用时只是丢掉偏好）。按设备存而不按用户存，因为官网点卡片时还没有用户。
  - `HomePage` 的类型卡片点击时先写入偏好，再走原来的 `handleGetStarted`。
  - `DashboardHome` 初始 tab 为 `getPreferredProjectType() ?? "novel"`；通过快速创建、创建弹窗或「今天可以做的事」成功建出项目后清掉偏好，失败时保留。
- **引导页新身份**：`OnboardingPersonaPage` 增加 `short_story`（标签「短篇」，标题「短篇作者」，说明「写盐言、番茄这类网文短篇，一篇写完就投。」）和 `screenwriter`（「短剧」「短剧编剧」「写竖屏短剧、网剧剧本。」），英文为 Short-story writer / Short-drama screenwriter，`preview.items` 两种语言都补上。服务端 `api/persona.py` 的 `ALLOWED_PERSONA_IDS` 加上这两个 id；存储仍是 JSON 字符串列，不改 schema；推荐列表不为它们新增条目。
  - 保存成功且不是「跳过」时，如果没有仍有效的偏好（例如官网卡片刚写入的），按作者选择顺序取第一个 `short_story` / `screenwriter`，写入 `short` / `screenplay`。已有的官网卡片偏好不被覆盖：那是对作品类型的直接选择，身份只是推断。
  - 前后端部署错开时的兜底：保存返回 400 或 422、且请求里有新 id 时，去掉新 id 重试一次；去掉后一个身份都不剩就以 `skipped: true` 保存，避免作者卡在引导页。偏好照常写入。请求里没有新 id 的校验错误不重试，重试最多一次。
- **预览不空许诺**：只有 `dashboardOnboardingFlags.todayActionPlanEnabled || firstDayActivationGuideEnabled` 为真时才渲染「首页会为你推荐」面板；关闭时外层 grid 不加第二列，问卷占满宽度，不留空栏。
- **工作台额度**：`DashboardHome` 把现有 `<QuotaBadge />`（默认样式）放在页头 `DashboardPageHeader` 的 `action` 位置，也就是问候标题旁、想法输入卡片正上方；没有新文案，Pro 沿用组件现有的「AI 消息不限条数」。
- **移动端顶栏**：`Header` 的项目切换器外层改为 `min-w-0 flex-1 md:flex-none md:max-w-[420px]`，去掉 calc；`ProjectSwitcher` 根节点加 `min-w-0 max-w-full`、触发按钮加 `max-w-full`，标题继续 truncate；移动端下拉改为 `fixed left-4 right-4 top-12`，不再 translate；祖先节点不加 `overflow-hidden`，避免裁掉下拉。☰ 所在的容器保持 `shrink-0`。
- **恢复上次打开的文件**：新增 `apps/web/src/lib/lastOpenedFile.ts`，按 `zenstory_last_opened_file_v1:{userId}:{projectId}` 存 `{id, ts}`，30 天过期，读写包 try/catch。`App.tsx` 的 `ProjectEditor`：
  - 路由项目已激活且 `selectedItem` 是文件（不是文件夹）时写入。
  - 路由项目已激活、URL 没有 `?file=`、`selectedItem` 为空时，每个项目只尝试一次：读记录，用 `fileApi.get` 校验存在、属于本项目、未删除，通过后 `setSelectedItem`；等待期间作者自己打开了别的文件就不覆盖。`?file=` 永远优先。
  - 文件不属于本项目、已删除或接口返回 4xx 时清掉记录；网络错误或 5xx 保留记录，下次再试（不因一次断网丢掉）。请求被项目切换打断时允许重新尝试。
  - `FileTreePane` 和 `MobileFileTree` 在选中文件第一次出现在树里时展开它的所有上级文件夹（`findAncestorFolderIds`），每个选中 id 只展开一次，之后作者收起文件夹不会被再次展开。
- **替换原生弹窗**：`DashboardHome` 删除项目、`ProjectSwitcher` 删除项目、`FileTreePane` / `MobileFileTree` 删除文件都改用 `ConfirmDialog`（danger 样式，确认中禁用按钮）。用现有 key：项目为标题 `dashboard:projects.deleteProject` / `editor:projectSwitcher.deleteProject`，正文 `dashboard:projects.deleteConfirm` / `editor:projectSwitcher.confirmDelete`；文件为标题 `common:confirmDelete`、正文为文件名；按钮 `common:delete`、`common:cancel`。「至少保留一个项目」和「引用素材已达上限」改为 `toast.info`（现有 key）。文件树的待删除对话框记住发起时的项目，切换项目后不再显示。

## Alternatives considered

- **把作品类型写进注册链接参数（`?type=`），一路透传到工作台**。最强理由：不依赖 localStorage，跨设备打开验证邮件也能带上。被否：注册、邮箱验证、OAuth 回调、引导页四段跳转都要改参数透传，涉及其他工作包的文件；验证邮件在另一台设备打开的情况少，丢偏好也只是回到原来的默认长篇。
- **关闭 flag 时把预览文案改成不承诺推荐的说法，而不是隐藏面板**。最强理由：保留右栏视觉平衡，作者仍能看到「选了什么」。被否：面板的全部内容都是「首页会推荐什么」，换文案等于新写一块没有功能对应的说明；审计决定是推荐面板关闭时不显示。
- **新身份也让服务端生成推荐条目**。最强理由：flag 打开时推荐更贴合短篇和短剧。被否：推荐面板目前默认关闭，没有实际展示面；作品类型偏好已经让工作台默认选中对应 tab，推荐条目留到面板重新启用时再设计。
- **上次文件记录在任何失败时都清掉**（规格原文「失败就清掉」）。最强理由：实现最简单，不会反复请求一个坏 id。被否：断网或服务端 5xx 时清掉会让作者下次回来又是空编辑器；只有明确的 4xx、跨项目、已删除才说明记录失效。每个项目每次进入只请求一次，不会形成重试风暴。
- **用浏览器原生 `confirm` 保留零依赖的简单实现**。最强理由：阻塞式确认不需要状态管理，测试也简单。被否：样式与应用不一致，部分内嵌浏览器和自动化环境里原生对话框会卡住页面；仓库里已有 `ConfirmDialog`，其他页面（项目列表、素材、技能）都在用。

## Consequences

- 收益：从官网短篇、短剧卡片或引导页短篇、编剧身份进来的作者，第一个项目默认就是对应类型；引导页不再承诺首页没有的推荐；手机顶栏的 ☰ 能点中，项目下拉留在屏幕内；工作台能看到今日 AI 消息；重开项目回到上次的章节并展开到它；删除确认和提示都在应用内完成。
- 代价：作品类型偏好按设备存，换设备完成注册时不生效；前端新身份在旧服务端上会走一次额外的保存请求，只选了新身份的作者在这段时间会被记为「跳过」（偏好照常写入，工作台 tab 不受影响）。上次文件恢复每次进入项目多一次 `GET /files/{id}`。`QuotaBadge` 在额度接近用完时的升级曝光埋点现在也会在工作台首页触发，来源沿用组件原有的 `settings_subscription_upgrade`，看埋点时要注意曝光面变多。`FileTree.lifecycle`、`files.spec.ts`、`projects.spec.ts` 中依赖原生对话框的用例改为操作应用内对话框。

## Verification

- pytest：`tests/test_api/test_persona_new_ids.py`（新 id 能保存且仍以 JSON 字符串存储；未知 id 返回 400）与 `tests/test_api/test_persona.py` 共 10 个通过。
- vitest：HomePage（点卡片先写偏好再跳转）、DashboardHome（初始 tab 取偏好、成功后清除、失败保留、过期或非法值回到长篇、QuotaBadge 在标题旁、删除走 ConfirmDialog）、OnboardingPersonaPage（预览随 flag 显示或隐藏且不留空栏、新身份写入偏好且以第一个为准、不覆盖已有偏好、跳过与其他身份不写、422/400 只去掉新 id 重试一次、无新 id 不重试）、Header 与 ProjectSwitcher（移动端 class、删除走 ConfirmDialog、最后一个项目用 toast）、App.lastOpenedFile（恢复、`?file=` 优先、跨项目和 404 清记录、网络错误保留、已有选择不覆盖、按用户和项目写入且不记文件夹）、FileTreePane / MobileFileTree / FileTree.lifecycle（删除走 ConfirmDialog、素材上限用 toast、展开到选中文件、切项目丢弃未确认的对话框）。全量 vitest 通过。
- mocked e2e（`responsive-workspace-mocked.spec.ts`，chromium，本地 Vite 5186）：390×844、30 字项目名下 ☰ 中心点 `elementFromPoint` 命中菜单按钮，下拉面板完全在视口内，页面无横向滚动；把 `Header.tsx` 和 `ProjectSwitcher.tsx` 换回旧版本时该用例在 `elementFromPoint` 断言失败。其余 16 个用例通过。
- 390 与 1440 截图检查：工作区顶栏与项目下拉、删除确认框、工作台（QuotaBadge、偏好 tab 为短剧剧本）、引导页（flag 关闭时无右栏、新身份卡片）均正常，重开项目时恢复到上次的章节并在树中高亮。
- `tsc -p tsconfig.app.json`、`pnpm lint`、`lint:tokens`、`lint:i18n-keys`、`build:typecheck` 通过。
