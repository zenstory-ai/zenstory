# Agent Note: 精简并重组 README 与 README_EN 结构

Status: implemented

## Problem

在 [2026-09-24-readme-for-readers-real-outputs-and-faq.md](../process/2026-09-24-readme-for-readers-real-outputs-and-faq.md) 之后的多轮迭代中，`README.md` 与 `README_EN.md` 逐步塞入了大量底层实现边界、自托管环境变量与已过时的说明：

1. 同一批规则（500 字自动送审、版本合并时间窗、双 32,000 token 预算、免费套餐额度、素材库手动附加、旧稿拆章导入）在「这是什么」「工作台里有什么」「常见问题」三处重复出现。
2. 面向创作者的功能段落夹杂了 `MATERIAL_ENABLE_*`、`INSPIRATIONS_ENABLED` 等环境变量与构建说明；「开始使用」下直接展开了三段不折叠的 Zpay 支付宝收款运维说明，与 `docs/docker-compose.md` 职责重叠。
3. 「看看它的输出」一节开头为内部审计式免责声明，第一节展示已被取代的旧版禁词表，第三节实为 40 行的 CLI 接入教程；而仓库近期交付的核心亮点（感知 `<author_edits>` 手改净差异与稿件防误删保护、网文/短剧原生题材规范、Prompt Cache 友好的 `<project_snapshot>` 与跨轮工作集、生成中插话与长任务断点续写、DeepSeek/MiniMax 主备无感切换与双凭证分流、无实质产出自动退还额度）反而未在 README 体现。
4. 常见问答中仍保留着已过时的「在线版没有自助重置密码」（`/forgot-password` 邮箱验证码重置已上线）、「AI 记忆提议」（已在 #314 移除，改为写作约定追加与回执）以及不适合开源项目 README 的免费版限制和基础版本/导出细节。

## Decision

1. 保留 [2026-09-16-readme-masthead.md](../process/2026-09-16-readme-masthead.md) 的居中 masthead、AGPL-3.0 许可证声明（[2026-11-08-workbench-agpl-license.md](../process/2026-11-08-workbench-agpl-license.md)）与底部的「ZenStory AI 项目」表，将导航栏第三项由「看看它的输出」调整为「核心功能」（`#工作台核心功能` / `#core-features`）。
2. 按「这是什么 → 开始使用（在线使用 / 自己部署 / 接入外部 Agent）→ 工作台核心功能（多 Agent 编排与运行守卫 / 上下文快照、跨轮工作集与 AI 记忆 / 14 个内置技能与自定义技能包 / 参考小说拆解与主备模型容灾）→ 常见问题 → 延伸阅读 → 参与贡献 → 许可证」重组 `README.md` 与 `README_EN.md`（由约 435 行收束至约 240 行）。
3. 上手示例替换为贴合目标受众的长篇玄幻爽文「黄金三章」细纲与开篇写作；将自托管可选功能的环境变量表、积分与邀请开关、主备模型配置、灵感库开关、素材扩展开关及 Zpay 支付宝收款配置收拢至 `docs/docker-compose.md` 的「可选能力」章节。
4. 移除基础功能的「版本历史与章节导出」单列章节、旧版禁词表对照、免费版限制问答以及过时的忘记密码问答，保留 4 个高频独立问题（只读讨论与撤回、主备模型切换、导出格式、自托管关闭邀请码）。

## Alternatives considered

- **继续单列「版本历史与章节导出」章节**。最强理由：保留单文件版本历史截图与章节序号混排说明。被否：单文件历史与 TXT 导出属于编辑器基础能力（且项目级快照与双导出入口已在 #355、#356 精简），单列一节会稀释多 Agent 编排、上下文快照、主备模型切换等核心亮点；使用细节已在 `zenstory.ai/docs/user-guide/version-history` 维护。
- **把可选功能环境变量表继续留在 README 折叠块里**。最强理由：读者不用多点一次链接到 `docs/docker-compose.md`。被否：README 与 `docs/docker-compose.md` 两处同时维护同一批环境变量极易再次漂移，集中在 `docs/docker-compose.md` 更易维护。

## Consequences

- 收益：首页直接展现项目当前真正的技术与产品亮点；消除了三处重复说明、基础功能堆砌与过时问答（忘记密码、AI 记忆提议）；自托管运维配置集中在 `docs/docker-compose.md`。
- 代价：原 `#看看它的输出` / `#see-what-it-produces` 页内锚点替换为 `#工作台核心功能` / `#core-features`。
