# Agent Note: 精简并重组 README 与 README_EN 结构

Status: implemented

## Problem

在 [2026-09-24-readme-for-readers-real-outputs-and-faq.md](../process/2026-09-24-readme-for-readers-real-outputs-and-faq.md) 之后的多轮迭代中，`README.md` 与 `README_EN.md` 逐步塞入了大量底层实现边界、自托管环境变量与内部核对声明：

1. 同一批规则（500 字自动送审、版本合并时间窗、双 32,000 token 预算、免费套餐额度、素材库手动附加、旧稿拆章导入）在「这是什么」「工作台里有什么」「常见问题」三处重复出现。
2. 面向创作者的功能段落夹杂了 `MATERIAL_ENABLE_*`、`INSPIRATIONS_ENABLED` 等环境变量与构建说明；「开始使用」下直接展开了三段不折叠的 Zpay 支付宝收款运维说明，与 `docs/docker-compose.md` 职责重叠。
3. 「看看它的输出」一节开头为内部审计式免责声明，第一节展示已被取代的旧版禁词表，第三节实为 40 行的 CLI 接入教程，结构与标题脱节。

## Decision

1. 保留 [2026-09-16-readme-masthead.md](../process/2026-09-16-readme-masthead.md) 的居中 masthead、AGPL-3.0 许可证声明（[2026-11-08-workbench-agpl-license.md](../process/2026-11-08-workbench-agpl-license.md)）与底部的「ZenStory AI 项目」表，将导航栏第三项由「看看它的输出」调整为「核心功能」（`#工作台核心功能` / `#core-features`）。
2. 按「这是什么 → 开始使用（在线使用 / 自己部署 / 接入外部 Agent）→ 工作台核心功能（多 Agent 分工 / 14 个技能 / AI 记忆与素材库 / 版本历史与章节导出 / 其他特性）→ 常见问题 → 延伸阅读 → 参与贡献 → 许可证」重组 `README.md` 与 `README_EN.md`（由约 430 行收束至约 260 行）。
3. 将自托管可选功能的环境变量表、积分与邀请开关、主备模型配置、灵感库开关、素材扩展开关及 Zpay 支付宝收款配置收拢至 `docs/docker-compose.md` 的「可选能力」章节；移除「看看它的输出」中的旧版禁词表对照，将章节排序导出与外部 Agent CLI 接入分别并入「版本历史与章节导出」与「开始使用」。
4. 常见问题精简为 4 个正文中未展开的独立问题（只读讨论与撤回、主备模型切换、导出格式、自托管开放注册与密码重置），移除不适合放在开源仓库 README 里的「免费版有哪些限制？」。

## Alternatives considered

- **保留「看看它的输出」二级标题，只删掉旧禁词表**。最强理由：保留原有二级标题锚点。被否：剩余两节分别是章节序号排序与 CLI 接入命令，都不是写作模型产出的文本示例，合入各自所属的「版本历史与章节导出」与「开始使用」后动线更直接。
- **把可选功能环境变量表继续留在 README 折叠块里**。最强理由：读者不用多点一次链接到 `docs/docker-compose.md`。被否：README 与 `docs/docker-compose.md` 两处同时维护同一批环境变量极易再次漂移，集中在 `docs/docker-compose.md` 更易维护。

## Consequences

- 收益：首页阅读动线清晰，首屏只讲核心定位与上手路径；消除了三处重复说明与过时禁词表展示；自托管运维配置集中在 `docs/docker-compose.md`。
- 代价：原 `#看看它的输出` / `#see-what-it-produces` 页内锚点替换为 `#工作台核心功能` / `#core-features`。
