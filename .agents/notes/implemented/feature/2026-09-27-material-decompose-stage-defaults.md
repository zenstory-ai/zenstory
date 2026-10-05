# Agent Note: 素材拆解按阶段细分开关，默认关闭情节点与故事聚合链以降低 token 消耗

Status: implemented

## Problem

素材库拆解（Prefect `novel_ingestion_v3`）的 LLM 调用链很长。按 30 万字、约 100 章的书估算，原文整本要被送进 LLM 三次：每章摘要（1a）、每章情节点（1b）、每章角色提及（1c）；情节点之后还有一条故事聚合链（框架识别 → 跨块合并 → 情节聚合 → 孤儿情节分配 → 故事线），共 6 类 LLM 调用。

`config/material_settings.py` 虽然已有 `MATERIAL_ENABLE_*` 开关，但存在三个问题：

- 颗粒度粗：`ENABLE_ENTITY_EXTRACTION` 同时控制角色提及、角色整合、金手指/世界观三件事，没法只保留世界观。
- 开关之间没有依赖约束：只关故事线时，故事仍然会生成，却因为读取路径只认 `story_line_id` 而全部不可见，token 白花。
- 前端不知道哪些阶段没跑，被关掉的阶段显示成空文件夹，用户以为拆解失败。

拆解目前没有 token 统计（`flows/utils/clients/llm.py` 只把 usage 写进日志），计费是每本书固定扣 1 次。

## Decision

1. **开关仍是 worker 进程的环境变量**（前缀 `MATERIAL_`，见 `config/material_settings.py`），改默认值要重新部署 worker。
2. **拆分实体开关**：`ENABLE_CHARACTER_EXTRACTION`（角色提及 + 角色整合）与 `ENABLE_META_EXTRACTION`（金手指 + 世界观），默认都开。旧的 `ENABLE_ENTITY_EXTRACTION` 保留为已弃用字段：显式设置时只填充**未显式设置**的新开关，并打印一次弃用警告；新开关显式设置时以新开关为准。
3. **默认值**：章节摘要、全书梗概、角色、金手指/世界观开启；情节点（`ENABLE_PLOT_EXTRACTION`）、故事聚合（`ENABLE_STORY_AGGREGATION`）、故事线（`ENABLE_STORYLINE_GENERATION`）、关系、Neo4j 关闭。默认配置下每章原文从送进 LLM 三次降为两次（章节摘要与角色提及仍各读一次整章），情节点抽取与其下游的整条故事聚合链不再运行。
4. **单一决策点**：纯函数 `resolve_enabled_stages(settings)` 按依赖计算实际生效的阶段，所有阶段闸门（v3、v2 续跑路径、章节/故事/关系子流程）只读它：梗概依赖摘要；故事依赖摘要与情节点；故事线依赖故事；关系依赖情节点与角色；Neo4j 只在关系生效时写入。显式请求却因依赖被剔除的阶段，在加载配置时与每次运行时各记一条 WARNING；只是默认开启而被剔除的不告警。
5. **每次任务留快照**：流程开始（以及续跑）时把生效阶段写入 `IngestionJob.stage_progress["enabled_stages"]`，键为 `chapter_summaries / plots / characters / meta / synopsis / stories / storylines / relationships`。写入时与同一本书此前所有快照按位取「或」，语义是「该阶段可能产出过数据」，因此重试不会把部署前已产出的数据标成未启用；缺少 `storylines` 键的旧快照以 `stories` 代替。`GET /materials/list` 与 `GET /materials/{id}` 返回 `enabled_stages`（旧任务为 `null`），详情接口另返回 `plots_count`、`stories_count`、`relationships_count`。
6. **前端**：`MaterialDetailPage` 只有在阶段未启用**且**没有数据时，才把对应文件夹显示为「未启用」并禁止展开；快照为 `null` 时行为与以前一致；故事线文件夹有 `storylines` 键时看它，否则看 `stories`。从未被任何流程写入的时间线：文件夹、前端 API、`/timeline` 接口、`timeline_service.py` 与未被引用的 `timeline_tasks.py` 已删除，数据表保留。
7. 修正 `handle_orphan_plots.py` 的参数名（调用端传 `novel_id=`、定义为 `_novel_id`），孤儿情节分配恢复生效。

故事可见性与跨书合并的根因修复见 `../bug-fix/2026-09-27-material-stories-scoped-to-novel.md`；在它之前故事与故事线只能作为一个整体开关，修复后才拆成两个键。

## Alternatives considered

- **管理员后台可调的全局开关（存数据库，即时生效）+ 上传时用户可勾选进阶拆解**。最强理由：调默认值不用重新部署，用户能按需付出 token 换更完整的拆解。被否：用户选择了最小改动；需要新表、后台页面和上传表单改动，而当前真正的问题（默认太贵、开关太粗、前端不知情）靠环境变量就能解决。
- **按订阅方案权益控制进阶拆解**。最强理由：高阶方案可以卖更完整的拆解。被否：牵涉定价与升级文案，不是降低 token 的必要条件；将来可以在 `resolve_enabled_stages` 之上叠加方案维度。
- **只保留章节摘要与梗概**。最强理由：原文只送一次，最省。被否：角色与世界观是对话附加素材、导入项目时用得最多的产物，全关后素材库对写作的帮助明显变少。
- **保留情节点、只关故事聚合链**。最强理由：情节点数据仍在。被否：原文仍送三次，节省有限。

## Consequences

- 收益：默认配置下不再调用每章情节点抽取（按章、输入整章原文）以及整条故事聚合链；阶段依赖集中在一个纯函数里，避免「生成了却看不见」；前端能区分「未启用」和「结果为空」。
- 代价：默认拆解不再产出情节点、剧情、故事线，依赖它们的功能（附加剧情到对话、故事线浏览）在默认部署下没有数据；付费墙与 teaser 文案（`materials.json` 的 `teaserTitle` / `teaserFeatureOne` / `teaserCardSynopsis*`）因此只宣传章节梗概、角色、世界观、金手指，不提剧情线；想要完整拆解的部署方需要显式打开三个开关。节省幅度目前只能估算，要实测需要先给拆解加 token 统计（尚未实现）。

## Verification

- `tests/test_core/test_material_settings.py`：依赖规则、默认值、旧变量覆盖。
- `tests/test_flows/integration/test_stage_gating_defaults.py`：默认配置跑真实的阶段一与故事子流程，任何情节点/故事/关系任务或多余的 LLM 调用都会让测试失败，任务最终状态为 `completed`，快照完整。
- `tests/test_api/test_materials_enabled_stages.py`：快照经 API 返回，旧任务为 `null`。
- `apps/web/src/pages/__tests__/MaterialDetailPage.test.tsx`：「未启用」渲染、`null` 快照兼容、缺少 `storylines` 键的旧快照回退到 `stories`。
