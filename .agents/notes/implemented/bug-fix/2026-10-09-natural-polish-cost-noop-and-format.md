# Bug-fix Note: 去AI味披露消耗、无改动退还、保留格式、全部拒绝不改稿

Status: implemented

## Problem

2026-10-08 新用户审计（连载、短篇、短剧三个 persona）里，编辑器底部的「去AI味」是最伤信任的入口：

- **#6 扣额度事前不说，只换引号也照扣。** `/api/v1/editor/natural-polish` 和 AI 对话共用 `reserve_ai_conversation`，只有生成出错才退还。短篇 persona 选中结尾 7 段，结果只把直引号换成弯引号，照样扣了一条 AI 消息；按钮提示只写「给选中内容去AI味」，免费用户不知道它要花今天 10 条里的一条。
- **#7 全部拒绝仍改稿、还生成版本。** `diffReview.ts::buildAtomicReviewSegments` 把「trim 后相同」的段落记成 equal，但取的是 `newBlock`（改写后的文本）；模型丢掉的 U+3000 段首缩进就这样进了「原文」，和用户点不点拒绝无关。`Editor.tsx::handleFinishReview` 又不比较结果，全部拒绝也发一次 PUT，生成一条「AI edit (reviewed)」版本。
- **#8 改得太浅，不看文件类型，删剧本 △。** 旧提示词只说「更口语、更像人写」，没有具体的 AI 腔清单；服务端拿不到 `file_type`，模型整段重写剧本时把「△ 苏晚站在大堂中央」改成了叙述句。短篇结尾那句「那句话，是他说过的，最重的一句情话」没动，倒是换了引号。
- 同批的编辑器小问题：**#31** 手机上空状态也显示「Ctrl K」；**#35** 短剧项目的空状态主按钮仍是「新建章节」（建出 `draft`）；**#22 的一部分** 底部「历史」按钮的 `title` 取 `editor:versionHistory`，那是一个对象，悬停提示失效，按钮名也和顶栏的「项目快照」分不清。

## Decision

**服务端：行级改动协议**（`services/features/natural_polish_service.py`）

- 模型不再整段重写。系统提示要求只列出改动的行，每行一组「原：<原文整行>」/「改：<改好的整行>」（英文为 `OLD:` / `NEW:`），「改：」留空表示整行删掉，一处都不用改时只输出「无改动」/ `NO CHANGES`。
- `parse_line_edits` 解析这些组；`apply_line_edits` 把它们套回原文：
  - 按「去掉空白、统一引号」后的整行内容定位（`normalize_for_noop_comparison`），对不上整行时退到「唯一包含这句话的那一行」做子串替换（引号映射是一字符对一字符，按映射后的位置切原串不会错位）；仍对不上的改动丢弃并记 WARNING 日志。
  - 没被提到的行逐字节保留。被改的行套回原行的段首和行尾空白（U+3000 缩进不丢）；引号按原文体例还原（原文只用直引号就把 “”「」 换回 "，只用弯引号就把 " 交替换成 “”，只用「」同理）。
  - 整行删除后顺带去掉多出来的空行，首尾不留多余空行。
  - 原行以「△」开头而改写没有时补回「△ 」。`file_type == "script"`（`models.file_model.FILE_TYPE_SCRIPT`）时：场景标题（`【…】`、`第N集/场/幕`、`人物：`、`Episode N`）和纯符号分隔行（如 `=====`）拒绝任何改动；「角色：」「角色（神态）：」台词行保留原前缀，只换冒号后的台词；△ 行和台词行不能整行删除。
- 模型没按协议输出（既没有「原：/改：」也不是「无改动」）时，`apply_full_rewrite` 把它当整段改写：行数相同就逐行走同一套格式守卫，行数不同只还原引号体例。
- （其中「升华句、情绪直说句删掉」和「最后一句单独点给模型」两处，已由 [2026-10-09-natural-polish-rewrite-not-delete.md](2026-10-09-natural-polish-rewrite-not-delete.md) 改为只改写，并加上整句删除守卫；下面两条记的是本篇上线时的做法。）
- 中英文提示词按具体 AI 腔清单重写：升华式收尾句（要求单独检查最后一两句）、被逗号切碎的句子、套话（几不可察、唇角一勾、眼底寒光、指节泛白、心中一震等，要求一个不留）、三连排比、情绪演完又直说、同一意象反复出现。硬约束：不改事实；只能整行改写或删除，不合并、不拆分；引号一个都不换；没命中的行不列出；英文提示词另要求输出与原文同语言。剧本另附一段格式规则（只在 `file_type == "script"` 时拼接，系统提示其余部分不变）。提示词里的示例句不取自审计样本。
- 本次检查提示（`build_attention_hints`）：服务端用正则在选中文本里找清单套话（中文：几不可察、唇角…勾、嘴角…勾/弧度、眼底寒光/寒意、眸光一闪、指节泛白、心中一震、心头一紧、空气仿佛凝固、倒吸一口凉气、一字一顿；英文：barely perceptible、knuckles turning white 等），把命中的整行（≤300 字）和命中的词列给模型，要求逐行改掉；非剧本时再把最后一句（中文 ≤80 字、英文 ≤200 字）点出来，让模型单独判断是不是升华式收尾句。提示拼在静态系统提示之后，静态部分仍是前缀；没有命中、最后一句又太长时不加任何内容。
- `natural_polish(..., file_type=None)` 新增参数；`thinking_enabled=False`、`max_tokens=16000`、温度、模型和用量归因都不变。

**服务端：无改动退还**（`api/editor.py`）

- `file_type` 取自请求体 `metadata.current_file_type`（非空字符串才传，否则 `None`）。
- 生成后用 `is_noop_rewrite` 比较原文和结果：去掉所有空白（含全角空格和换行），“”„「」 统一成 "，‘’『』 统一成 '。相同就调用 `_refund_ai_conversation(reason="no_change")`。
- `NaturalPolishResponse` 新增 `unchanged: bool = False`。只有退还成功时才返回 `unchanged=True`，此时 `text` 是请求里的原文；退还失败按正常结果返回（已扣费、`unchanged=False`），保证前端「不计入今日 AI 消息」的提示只在真的退还时出现。

**前端**

- `lib/naturalPolishApi.ts` 返回 `{ text, unchanged }`；响应里没有 `unchanged`（旧服务端）按 `false` 处理。
- `SimpleEditor.tsx`：`unchanged` 为真时不进审阅，弹 info toast `editor:naturalPolishNoChange`「这段没找到明显的 AI 腔，这次不计入今日 AI 消息」。请求结束后（成功、无改动、出错、取消都算）invalidate `subscriptionQueryKeys.quota()` 和 `quotaLite()`。按钮提示读 QuotaBadge 已缓存的 `quota()` 数据（订阅 query cache，不另发请求），`ai_conversations.limit !== -1` 时用 `editor:naturalPolishTooltipFree`「给选中内容去AI味，用 1 条今日 AI 消息（Ctrl/⌘+Shift+R）」；缓存为空或没有 QueryClientProvider 时用原提示。底部历史按钮的 `title` 改为 `versions:title`，按钮文字 `editor:history` 改为「历史版本」/ "Versions"。
- `lib/diffReview.ts::buildAtomicReviewSegments`：只差空白的段落记成 equal 时用 `oldBlock`，原文的空白和 U+3000 缩进原样保留。原文里纯空白的块不再被丢掉（记成 equal 保留），改写多出来的纯空白块不再被加进结果。原文最后一段没有段落分隔、而改写在它后面补了段落时，分隔符挂到下一个插入段前面：拒绝时原文逐字节不变，接受时两段不会粘在一起。
- `Editor.tsx::handleFinishReview`：只对「去AI味」进入的审阅跳过写库。`SimpleEditor` 的 `onEnterDiffReview` 只在去AI味时调用，`Editor` 传给它的是 `enterNaturalPolishReview`：先把 `{fileId, originalContent, modifiedContent}` 记进 `naturalPolishReviewRef`，再调用 `enterDiffReview`。完成审阅时先取出并清空这个记录，三项都和当前 `diffReviewState` 相同，且 `applyDiffReviewChanges()` 的结果和 `originalContent`、`file.content`、`editContent` 都逐字节相同，才只调用 `exitDiffReview()`，不发 PUT、不生成版本。其它审阅入口保持原行为：带 `base_updated_at` PUT 定稿，再 `setEditContent(finalContent)`。这些入口包括 Agent `edit_file` 的审阅（`useChatStreaming.ts`，AI 文本已经落库，全部拒绝要把原文写回）、自动保存撞 `stale_write` 后的冲突审阅、`handleFinishReview` 里 409 重进的审阅（这两种 `originalContent` 是服务端正文，编辑器里还是本地正文）。
- `Editor.tsx` 空状态：当前项目 `project_type === 'screenplay'` 时主卡片建 `file_type='script'`（父目录 `<projectId>-script-folder`，或按 `FOLDER_TYPE_MAP` 找「剧本/Scripts」），标题用 `editor:fileTree.newScript`「新建剧本」，描述用 `editor:emptyStateDescriptionScript`「打开一个文件，或新建剧本、大纲。」。快捷键提示在手机布局下不渲染；`navigator.userAgentData.platform` 或 `navigator.platform` 是 Mac/iPhone/iPad 时显示「⌘」「K」，其余平台显示「Ctrl」「K」。手机判断用 `useMobileLayout().isMobile`：`Layout` 正是用 `useIsMobile()` 决定是否套 `MobileLayoutProvider isMobile`，编辑器读同一个信号，且不在渲染时直接调用 `matchMedia`。

## Alternatives considered

- **保留整段重写，只加强提示词。** 最强理由：改动最小，模型看到的是完整上下文，可以做跨段调整；也是本包最初的规格。没采用：前两轮真实模型探测（下文 r1、r2）里，模型在提示词明令「引号一个都不换」后仍把短篇和短剧的直引号换成弯引号，还删掉了一整行「△ 硬切黑屏。」，留下「唇角一勾」「指节泛白」，短篇的升华收尾句两次都没动——格式完全依赖模型自觉，提示词写得再硬也守不住。行级协议把格式交给代码保证，模型只负责「改哪几句、改成什么」，输出也更短。规格里「没有需要改的就原样输出」相应改成「只输出：无改动」，效果相同（结果等于原文，走无改动退还）。
- **服务端拿整段改写结果和原文做行级 diff，再套格式守卫。** 最强理由：不用改输出格式，模型照常整段输出，兼容任何模型。没采用：模型一旦合并或拆分段落，diff 对齐就会把改动挂到错误的行上；行级协议用模型自己抄的原句定位，对不上就丢弃，宁可少改也不改错。整段输出仍作为兜底，只在行数相同时逐行守卫。
- **前端 `diffReview` 只修 noop 分支（规格原文）。** 最强理由：最小改动，单测好写。没采用的部分：只修 noop 分支，原文里纯空白的块在「全部拒绝」时仍会丢、改写多出的纯空白块仍会被加进去，「全部拒绝后逐字节不变」做不到；最后一段补了新段落时还会把两段粘在一起。这几处同属「拒绝应当恢复原文」，一并修掉。
- **把温度从默认的 1.0 调低，提高召回的稳定性。** 最强理由：同一段剧本两次请求一次改掉「指节泛白」、一次漏掉，正是采样随机性；调低温度是一行改动。没采用：温度低只让输出更确定，不保证确定地「找到」套话；套话本身能用正则找全，直接点给模型更可靠，也不必改动其它调用方共享的温度默认值和既有单测。
- **完成审阅时只按内容判断要不要写库：定稿 == `originalContent` == `file.content` == `editContent`。** 最强理由：不用给审阅打来源标记，`ProjectContext` 和 `DiffReviewState` 都不用动，规则也直观，就是「屏幕和服务端都不会变就不写」。没采用：Agent 的 `edit_file` 一结束就进入审阅，编辑器要等 `editorRefreshVersion` 触发的重新加载（100ms 延迟加一次 GET）才拿到 AI 文本。这段时间里 `file.content` 和 `editContent` 仍是原文，三者相等，全部拒绝会被当成「没变」跳过写库，服务端留着用户刚拒绝的 AI 文本。客户端看到的正文可能已经过时，只看内容不能判断服务端状态，所以必须同时确认审阅来自去AI味：去AI味的审阅之前，服务端没有任何写入。
- **按钮提示用 `useQuery` 自己拉额度。** 最强理由：缓存为空时也能显示准确提示。没采用：规格要求复用额度缓存；桌面端 ChatPanel 的 QuotaBadge 一直挂着、缓存总是热的，手机上悬停提示本身看不到；`useQuery` 还要求所有渲染 `SimpleEditor` 的地方都有 QueryClientProvider，现有生命周期测试没有。
- **手机判断直接用 `useIsMobile()`（规格原文）。** 最强理由：和 `Layout` 用同一个 hook，语义直接。没采用：它在渲染时调用 `window.matchMedia`，现有编辑器生命周期测试 `vi.resetAllMocks()` 后 `matchMedia` 返回 `undefined` 会让整个编辑器渲染崩掉；`useMobileLayout()` 读的是 `Layout` 用 `useIsMobile()` 算好后注入的值，生产上结果相同。

## Consequences

- 收益：选中文本里没被改到的行和原文逐字节一致；剧本的 △、场景标题、台词前缀，正文的 U+3000 缩进和引号体例都由代码守住。没找到 AI 腔、或只差空白和引号时不扣额度，前端明确告诉用户「这次不计入」。全部拒绝不再改稿、不再多一条版本。免费用户点之前就知道要花一条 AI 消息。
- 代价：模型只能整行改写或删除，不能合并或拆分段落，也不能跨段调整结构；「去AI味」本来就只做句子级修改，影响有限。
- 代价：模型抄错原句（改了字再抄）时那一处改动会被丢弃，结果可能比模型实际想改的少；丢弃只记日志，用户看不到。全部丢弃时结果等于原文，会按「无改动」退还。
- 代价（已由 rewrite-not-delete 那篇去掉这条提示）：「最后一句」提示会把模型的注意力拉到结尾，本来干净的结尾句也可能被顺手改一下（最终复跑里干净样本的最后一句被改写，事实未变，但改动没有必要，也因此按有改动扣了一条）。用户可以在审阅里拒绝；拒绝后不写库，但这条 AI 消息不退。
- 代价：套话正则只覆盖清单里的写法，同义变体（「指尖发白」）仍靠模型自己发现。
- 代价：退还只退 AI 消息额度，这次调用的真实模型成本照样记入用量台账和成本兜底。
- 代价：无改动判定把 “”「」 视为同一种引号，模型只把直角引号换成弯引号也算「无改动」并退还——这类改动按提示词本来就不该发生。
- 代价：去AI味审阅的来源靠 `Editor` 里的一个 ref 记录，没有写进 `DiffReviewState`。之后的审阅如果 fileId、原文、改写三项都和一次去AI味审阅完全相同，也会被当成去AI味审阅，实际上不会出现。审阅前有还没自动保存的本地编辑时（`file.content` ≠ `editContent`），全部拒绝仍按原行为 PUT，这些编辑会记成一条「AI edit (reviewed)」版本。
- 代价：编辑器空状态的手机判断依赖 `MobileLayoutProvider`；将来如果手机布局不再套这个 provider，快捷键提示会在手机上重新出现。

## Verification

**自动化测试**

- `apps/server/tests/test_api/test_editor_natural_polish.py`：无改动时退还且额度不变（真实额度表，从 0 到 0）；只换引号也判为 `unchanged`；有改动时照扣（0 到 1）；`metadata.current_file_type` 作为 `file_type` 传进 service；剧本规则只拼在 script 提示词后面；行级协议的缩进保留、引号还原、剧本结构守卫、△/台词行不可删、「无改动」和对不上的改动、子串替换、整段兜底。新增用例在改动前的代码上全部失败。
- `apps/web`：`diffReview.test`（U+3000 缩进多段文本全部拒绝后逐字节不变、缩进差异按原文保留、补段不粘连）；`Editor.test`（去AI味审阅全部拒绝不调用 `fileApi.update`；Agent 编辑审阅全部拒绝仍把原文 PUT 回去，编辑器显示原文，即使编辑器还没重新加载 AI 文本也一样；保存冲突审阅全部拒绝后编辑器显示服务端正文，并带新令牌写回。后三条在只按 `originalContent` 跳过写库的代码上都失败；短剧空状态「新建剧本」并创建 script、手机不显示 Ctrl K、Mac 显示 ⌘）；`SimpleEditor.test`（`unchanged` 不进审阅并弹 toast、结束后 invalidate 两个额度 query、免费用户提示、历史按钮 title 为字符串键）；`naturalPolishApi.test`（缺 `unchanged` 按 false）。前端全量 vitest（250 个文件）、`tsc -p tsconfig.app.json`、`lint`、`lint:tokens`、`lint:i18n-keys`、`build:typecheck` 通过；后端 `ruff check api/ services/` 通过。

**真实模型检查**（owner 授权的 DeepSeek key，只注入本地测试进程；经本地 FastAPI + 临时 SQLite 走完整的 `/api/v1/editor/natural-polish`，含真实额度扣减与退还；不进 CI，脚本不入库）

样本：短篇结尾 8 段（`shortstory/logs/story-v1.txt`，从「后来每年清明」到「最重的一句情话」，直引号）、短剧第 1 集（`drama/outputs/r2-ep1.txt`，`file_type=script`，27 行 △、17 行「角色：」）、连载第三章（`serial/outputs/ch3-v2.txt`，U+3000 缩进、弯引号，98 段）、一段自写的干净文学段落（3 段）、一段自写的英文正文（3 段，带 barely perceptible smile / knuckles turning white / In that moment she finally understood）。

断言：①短篇结尾那句升华被改写或删掉，清明/南城/栀子/七年/「我不爱你了」仍在；②短剧 △ 行数、场景标题行、「角色：」行数与原文相同；③连载逐行段首空白和空行结构相同，引号只用原文出现过的；④干净段落 `unchanged=true` 且额度回到 0，或改动 ≤1 段；⑤英文输出无中文、段落数不变；⑥原文含套话的样本，输出里套话计数为 0。

| 轮次 | 提示词 | 短篇 | 短剧 | 连载 | 干净 | 英文 |
| --- | --- | --- | --- | --- | --- | --- |
| r1 | 整段重写 + AI 腔清单 | FAIL：收尾句没动 | FAIL：套话 6→1（「指节泛白」留着） | PASS（`unchanged`，一处没改） | PASS（`unchanged`，退还） | PASS |
| r2 | 整段重写 + 加强措辞 | FAIL：收尾句没动；直引号被换成弯引号 | FAIL：套话 6→3；直引号被换成弯引号；△ 27→26（删了「△ 硬切黑屏。」） | PASS（`unchanged`） | PASS（`unchanged`，退还） | PASS |
| r3 | 行级协议 | PASS：删掉「那句话，是他说过的，最重的一句情话」，三连排比收成「最笨最疼」，事实全在 | PASS：△ 27/27、台词 17/17、标题不变，套话 6→0（「指节泛白」→「指甲掐进湿烂的纸里」，两处「唇角一勾」删掉） | PASS：16 段去掉切碎的逗号（如「他的脸，一下子白了」→「他的脸一下子白了」），缩进/空行逐行一致，只用 “” | PASS（`unchanged`，额度 0） | PASS：删掉 barely perceptible smile、knuckles turning white、sadness 直说和结尾感悟，3 段 |
| r4 | 同 r3 复跑（检验稳定性） | FAIL：收尾句没动（只收了三连排比） | FAIL：套话 6→1（「指节泛白」漏改）；格式 △ 27/27、台词 17/17 仍守住 | PASS（改 2 段，格式一致） | PASS（改 1 段） | PASS |
| r5 | 行级协议 + 本次检查提示（**最终实现**） | PASS：删掉收尾句，「最笨、最疼、最不体面」→「最不体面」，事实全在，8→7 段 | PASS：△ 27/27、台词 17/17、标题不变，套话 6→0（「指节泛白」→「指头攥得发白」，「唇角几不可察地一勾」→「嘴角动了一下」） | PASS：15 段去掉切碎的逗号，98 段缩进/空行逐行一致，只用 “” | PASS：改 1 段（最后一句「她又把它端了出来」→「那盘萝卜又出现在桌上」，见 Consequences），扣 1 条 | PASS：删掉两处套话、sadness 直说和结尾感悟，3 段 |

r4 说明只靠提示词时召回在两次请求之间不稳定，r5 的「本次检查提示」就是为此加的；格式断言（②③⑤）在 r3–r5 的行级协议下每次都通过；整段重写时 r1 把短篇的直引号换成了弯引号，r2 把短剧的直引号换成弯引号并删掉一行 △。r1–r5 共 25 次模型请求，全部经本地 `/api/v1/editor/natural-polish`；最终源码上 r5 五个样本全部通过。

集成复跑（各工作包合入 `f7e87f1` 后，本地 uvicorn :8191 + 临时 SQLite，免费用户真实额度表，5 次请求）：短篇删掉「那句话，是他说过的，最重的一句情话」、三连排比收成「最笨的方式」，清明/南城/栀子/七年/「我不爱你了」都在，额度 0→1；短剧 △ 27/27、台词 17/17、标题不变，套话 5→0；连载 195 行段首空白逐行一致、只用 “”，改 10 段；干净段落第一次把「院子里一半亮一半暗」删成「院子一半亮一半暗」（扣 1 条，见 Consequences），同一段第二次返回 `unchanged=true`，额度 4→4，退还生效。

**去AI味只改写不删句复跑**（2026-10-09，第二轮审计 N1 的修复，见 [rewrite-not-delete](2026-10-09-natural-polish-rewrite-not-delete.md)）

方法：
- owner 授权的 DeepSeek key 只注入本地进程。
- 直接调用 `natural_polish_service.natural_polish`，模型 `deepseek-flash`，`thinking_enabled=False`，不记用量台账。
- 用 `is_noop_rewrite` 判定是否会按无改动退还。
- 脚本不入库。
- 共 12 次请求：4 个样本各 3 轮。

4 个样本：
- **主题句**：第二轮短篇 `outputs/v1-汇款单.txt` 第 429–439 行，即审计里那段 211 字附近的 6 段，从「我又想起葬礼上」到「我恨错了人。」，带 U+3000 缩进。
- **结尾**：第一轮短篇 `logs/story-v1.txt` 第 500–514 行，从「后来每年清明」到「最重的一句情话。」，直引号。
- **剧本**：第二轮短剧 `outputs/deslop-before.txt`，`file_type=script`，9 行 △、3 行台词，含「几不可察」「一字一句」。
- **干净**：自写的 3 段，母亲剥蒜、萝卜炖排骨。

每个样本的期望：
- 主题句：「她宁可让我恨她，也不肯让我知道，我恨错了人。」原样还在。
- 结尾：收尾句的意思还在，清明、南城、栀子、七年、「我不爱你了」都在，排比或切碎的逗号有改善。
- 剧本：△ 行数、台词前缀和行数都不变，套话清零。
- 干净：`unchanged=true`。

| 轮次 | 代码 | 主题句 | 结尾 | 剧本 | 干净 |
| --- | --- | --- | --- | --- | --- |
| r1 | 新提示词 + 守卫 | **发现解析问题**：模型 6 行全抄成「原：」，没有任何「改：」，整段改写兜底把「原：」写进了正文。主题句在 | PASS：「最笨、最疼、最不体面」→「最笨也最疼」；收尾句只去掉切碎的逗号，成了「那句话是他说过的最重的一句情话」；事实全在 | PASS：△ 9/9，台词前缀 3/3，「几不可察地一颤」→「在袖口里蜷了一下」，「一字一句」→「抬眼看向他」 | FAIL：把「又说排骨是…」的「又说」删掉，扣 1 条 |
| r2 | 加「原：」-only 解析，加「不为读着顺去删字」 | PASS：主题句原样，只改了「熬着最深的夜」等小处。另把「什么都有一肚子的委屈可以说」改成「什么委屈都咽得下去」，意思走样，见 rewrite-not-delete 的 Consequences | `unchanged`：模型只抄了「原：」，排比没动 | PASS：△ 9/9，台词 3/3，套话 1→0 | PASS（`unchanged`；模型只抄「原：」，按新解析算无改动） |
| r3 | 加中文三连排比提示（**最终实现**） | PASS：主题句原样（模型给的「改：」和原文相同） | PASS：排比改成「最笨的方式……疼得让人受不了，也不体面」，收尾句原样，事实全在 | PASS：△ 9/9，台词 3/3，「几不可察地一颤」→「一颤」 | PASS（`unchanged`） |

各项结果：
- 主题句 3/3 保留。
- △、台词前缀和缩进 12/12 次保持不变。
- 套话：剧本 3/3 清零。
- 干净段落：最终代码上 2/2 原样返回。r1 那次是加「不为读着顺去删字」之前。
- 结尾：收尾句 3/3 仍在。排比 3 次里改了 2 次。

12 次请求里，模型一次都没有再整句删掉主题句，所以真实运行没有触发句子守卫；守卫由单元测试和接口测试覆盖。
