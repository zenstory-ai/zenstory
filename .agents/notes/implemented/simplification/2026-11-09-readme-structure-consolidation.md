# Agent Note: 精简并重组 README 与 README_EN 结构

Status: implemented

## Problem

在 [2026-09-24-readme-for-readers-real-outputs-and-faq.md](../process/2026-09-24-readme-for-readers-real-outputs-and-faq.md) 之后的多轮迭代中，`README.md` 与 `README_EN.md` 逐步塞入了大量底层实现边界、自托管环境变量与已过时的说明：

1. 同一批规则（500 字自动送审、版本合并时间窗、双 32,000 token 预算、免费套餐额度、素材库手动附加、旧稿拆章导入）在「这是什么」「工作台里有什么」「常见问题」三处重复出现。
2. 面向创作者的功能段落夹杂了 `MATERIAL_ENABLE_*`、`INSPIRATIONS_ENABLED` 等环境变量与构建说明；「开始使用」下直接展开了三段不折叠的 Zpay 支付宝收款运维说明，与 `docs/docker-compose.md` 职责重叠。
3. 「看看它的输出」一节开头为内部审计式免责声明，第一节展示已被取代的旧版禁词表，第三节实为 40 行的 CLI 接入教程；而仓库近期交付的核心亮点（识别作者手改并同步续写、网文与短剧节奏、生成中追加指令与长任务断点续写、写作约定追加）反而未在 README 体现。
4. 常见问答中仍保留着已过时的「在线版没有自助重置密码」（`/forgot-password` 邮箱验证码重置已上线）、「AI 记忆提议」（已在 #314 移除，改为写作约定追加）以及不适合开源项目 README 的免费版限制和基础版本/导出细节。

## Decision

1. 保留 [2026-09-16-readme-masthead.md](../process/2026-09-16-readme-masthead.md) 的居中 masthead、AGPL-3.0 许可证声明（[2026-11-08-workbench-agpl-license.md](../process/2026-11-08-workbench-agpl-license.md)）与底部的「ZenStory AI 项目」表，将导航栏第三项由「看看它的输出」调整为「核心功能」（`#工作台核心功能` / `#core-features`）。
2. 按「这是什么 → 开始使用（在线使用 / 自己部署 / 接入外部 Agent）→ 工作台核心功能（多 Agent 分工 / AI 记忆与项目上下文 / 14 个内置技能与自定义技能包 / 参考小说拆解）→ 延伸阅读 → 参与贡献 → 许可证」重组 `README.md` 与 `README_EN.md`（由约 435 行收束至约 255 行），并按 `prose-gap-polish` 五步法消除机翻腔与内部代码代号（如「守卫」「墙钟时限」「L1/L2/L3」「`<author_edits>`」）。
3. 上手示例替换为贴合目标受众的长篇玄幻爽文「黄金三章」细纲与开篇写作；将自托管的模型配置与免邀请码注册收进「自己部署」小节，其余可选功能环境变量表与 Zpay 支付宝收款配置收拢至 `docs/docker-compose.md` 的「可选能力」章节。
4. 移除基础功能的「版本历史与章节导出」单列章节、旧版禁词表对照以及冗余的「常见问题」章节。

## Alternatives considered

- **继续单列「版本历史与章节导出」及「常见问题」章节**。最强理由：保留单文件版本历史截图与基础问答。被否：单文件历史、TXT 导出与只读对话均属基础能力，单列章节与正文中已讲明的机制重复；自托管的模型切换与邀请码开关直接放在「自己部署」下更顺畅。
- **把可选功能环境变量表继续留在 README 折叠块里**。最强理由：读者不用多点一次链接到 `docs/docker-compose.md`。被否：README 与 `docs/docker-compose.md` 两处同时维护同一批环境变量极易再次漂移，集中在 `docs/docker-compose.md` 更易维护。

## Consequences

- 收益：首页直接用自然中文展现项目当前的产品亮点；消除了三处重复说明、基础功能堆砌、过时问答与翻译腔用词；自托管运维配置集中在 `docs/docker-compose.md`。
- 代价：原 `#看看它的输出` / `#see-what-it-produces` 页内锚点替换为 `#工作台核心功能` / `#core-features`。
