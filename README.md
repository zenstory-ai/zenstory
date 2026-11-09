<p align="center">
  <img src="https://raw.githubusercontent.com/zenstory-ai/zenstory/main/apps/web/public/brand/zenstory-ai-mark.svg" alt="" width="76" height="76">
</p>

<h1 align="center">ZenStory 工作台</h1>

<p align="center">
  <b>对话即创作：在浏览器里写小说与短剧，AI 直接新建、改写你的大纲、正文和角色卡。</b>
</p>

<p align="center">
  <a href="https://zenstory.ai/zh/workbench"><b>项目主页</b></a>
  &nbsp;·&nbsp;
  <a href="#开始使用"><b>开始使用</b></a>
  &nbsp;·&nbsp;
  <a href="#工作台核心功能"><b>核心功能</b></a>
  &nbsp;·&nbsp;
  <a href="README_EN.md"><b>English</b></a>
</p>

<p align="center">
  <a href="https://github.com/zenstory-ai/zenstory/stargazers"><img alt="Stars" src="https://img.shields.io/github/stars/zenstory-ai/zenstory?style=flat-square&color=22D3EE&logo=github&logoColor=white&label=Stars"></a>
  <img alt="Skills 14" src="https://img.shields.io/badge/Skills-14-081431?style=flat-square">
  <a href="https://app.zenstory.ai/"><img alt="app.zenstory.ai" src="https://img.shields.io/website?style=flat-square&url=https%3A%2F%2Fapp.zenstory.ai&label=app.zenstory.ai"></a>
  <a href="./LICENSE"><img alt="License AGPL-3.0" src="https://img.shields.io/badge/License-AGPL--3.0-1F6FEB?style=flat-square"></a>
</p>

<p align="center">
  <a href="https://app.zenstory.ai/"><img alt="打开工作台" src="https://img.shields.io/badge/%E6%89%93%E5%BC%80%E5%B7%A5%E4%BD%9C%E5%8F%B0-1F6FEB?style=for-the-badge&logo=vercel&logoColor=white"></a>
  <a href="https://github.com/zenstory-ai/zenstory/issues"><img alt="GitHub Issues" src="https://img.shields.io/badge/GitHub%20Issues-181717?style=for-the-badge&logo=github&logoColor=white"></a>
</p>

> **在线使用：[app.zenstory.ai](https://app.zenstory.ai/)，注册或登录后即可创作。**

![ZenStory 三栏工作台](docs/screenshots/workspace.png)

工作区从左到右依次是文件树、编辑器和 AI 对话。

## 这是什么

ZenStory 工作台是一个以文件为中心的 AI 写作环境，覆盖**长篇小说、短篇小说和短剧剧本**。大纲、正文、角色卡、世界观设定和参考素材都是项目里的结构化文件；你在右侧提要求，AI 直接新建或改写文件并流式呈现在编辑器里，不用在聊天窗口和文档软件之间来回复制粘贴。可以在 [app.zenstory.ai](https://app.zenstory.ai) 在线使用，也可以按 AGPL-3.0 许可自己部署。

- **对话直接落盘，且感知你的手改**：Agent 边写边将正文流式落入文件，对话卡片支持一键撤销；你在编辑器里亲手改过的段落会以 `<author_edits>` 净差异自动同步给 AI，且受保护的手改稿件不会被对话误删或整份覆盖。
- **自研多 Agent 编排与严格的范围守卫**：由意图路由调度大纲规划师、爽点设计师、内容创作者和质量审稿人。只要求细纲就停在规划；要求写正文时才动笔，并在「高质量」模式下按实际落库字数自动送审、合并输出唯一总结。
- **贴合网文与短剧的原生文风**：内置网文与竖屏短剧叙事规范（第一屏进冲突、爽点当场兑现、对白争锋推进、章末留钩子）；编辑器选中文字点「去AI味」可逐段对比、逐段接受或拒绝。
- **前缀缓存友好的上下文与跨轮工作集**：每轮在双 32,000 token 预算内自动组装「AI 记忆」、跨轮工作集（最近读写过的文件全文）、相邻章节及关联角色与设定，并将稳定资料固定为 `<project_snapshot>` 以最大化命中模型 Prompt Cache。
- **开放 Agent 技能生态与 CLI 接入**：内置 14 个渐进式加载的官方写作技能，兼容开放 `SKILL.md` 格式导入导出；通过 `npx zenstory` 命令行，Claude Code、Codex、OpenClaw 等外部 Agent 也能直接读写你的项目。

## 开始使用

### 在线使用

打开 [app.zenstory.ai](https://app.zenstory.ai) 注册即可创作。新建一个「长篇小说」项目后，第一条请求可以先让 AI 规划「黄金三章」细纲，确认节奏后再动笔写正文：

> 我想写一本玄幻爽文，先规划黄金三章细纲，暂不写正文。男主萧无涯是诸天万界最大的商会之主，十年来倾尽灵石、极品丹药和护山大阵把青云女帝洛清寒捧上神坛。开篇女帝证道大典，她为了刚入门的剑道白月光叶尘，当众嘲讽萧无涯满身铜臭、不配与她并肩，直接将他逐出宗门。萧无涯当场笑纳，一键切断宗门所有灵石、丹药和阵法供应。请按黄金三章给出每章的核心冲突、打脸爽点和章末卡点钩子。

细纲敲定后，再让 AI 落盘并写正文（第三行的「钩子设计：」需先在「技能 → 发现技能」里启用该技能）：

```text
把刚才的黄金三章细纲存进「大纲」文件夹，命名为「黄金三章细纲」。
按「黄金三章细纲」写第一章，存进「正文」文件夹里的「第1章 铜臭味？那就断了你的成仙路」，写完这一章就停。
钩子设计：只改「第1章 铜臭味？那就断了你的成仙路」最后两段，卡在护山大阵灵力归零、全宗长老傻眼的瞬间。
```

更多用法见[使用文档](https://zenstory.ai/docs)。

### 自己部署

```bash
export DEEPSEEK_API_KEY=your-key          # 也可以写进仓库根目录的 .env
docker compose up -d --build

# 创建首个管理员账号（无需邀请码和邮箱验证，密码至少 12 位）
docker compose exec -e ZENSTORY_ADMIN_EMAIL=you@example.com \
  -e ZENSTORY_ADMIN_PASSWORD='CHANGE-ME-12CHARS' server python scripts/create_admin.py
```

打开 <http://localhost:5173> 登录，API 文档在 <http://localhost:8000/docs>。默认使用 SQLite，数据库与上传文件保存在 Docker 卷中，`docker compose down` 和重新构建不会丢失数据。

- **写作配置**：项目系统提示词（`novel`、`short`、`screenplay`）和官方技能保存在数据库中，首次启动后请在管理后台配置并重新加载，维护方式见 [docs/agent-writing-guidance.md](docs/agent-writing-guidance.md)。
- **套餐初始化**：运行 `docker compose exec server python scripts/seed_subscription_plans.py` 初始化套餐，即可在管理后台「订阅管理」给账号开通 Pro。
- **生产部署与可选功能**：PostgreSQL + Redis 完整部署、免邀请码注册、邮箱验证与密码重置（Resend）、主备模型切换（DeepSeek / MiniMax）、Google 登录、腾讯云语音输入、素材拆解（Prefect Worker）、灵感库以及支付宝 Zpay 收款等配置，详见 [docs/docker-compose.md](docs/docker-compose.md)。

<details>
<summary>不用 Docker，本地开发</summary>

需要 Node.js 20.19+、Python 3.12 和 pnpm：

```bash
# 终端 1：后端（端口 8000）
cd apps/server
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # 填上 DEEPSEEK_API_KEY
python3 main.py

# 终端 2：前端（端口 5173）
cd apps/web
pnpm install
cp .env.example .env.local
pnpm dev
```

首个账号同样在 `apps/server` 下带上 `ZENSTORY_ADMIN_EMAIL` 和 `ZENSTORY_ADMIN_PASSWORD` 运行 `python scripts/create_admin.py` 创建。

</details>

### 接入外部 Agent（Claude Code / Codex / OpenClaw）

在工作台「设置 → Agent」里创建 API Key，然后在终端运行（需要 Node.js 20+）：

```bash
npx zenstory login           # 按提示粘贴 Key（隐藏输入，不进 shell 历史；自托管加 --api-base https://你的服务器/api/v1）
npx zenstory skill install   # 默认装到 Claude Code；Codex、OpenClaw 加 --target codex / --target openclaw
npx zenstory whoami          # 确认登录状态和读写权限
```

装好技能后，外部 Agent 使用自己的模型写作，通过 `zenstory` CLI 读取组装好的项目上下文并写回章节：

```bash
dir=$(mktemp -d)
zenstory files list <项目ID> --type draft --fields id,title --json           # 列出草稿
zenstory context <项目ID> --file <章节ID> --query "接下来写什么" --json        # 获取组装好的项目上下文
zenstory files get <章节ID> --fields updated_at --json                       # 记录当前版本时间戳
zenstory files get <章节ID> -o "$dir/<章节ID>.md"                            # 导出到本地临时文件
zenstory files put <章节ID> --content-file "$dir/<章节ID>.md" --if-updated-at <updated_at>   # 校验无并发冲突后写回
```

完整命令与 HTTP 接口说明见 [apps/cli/README.md](apps/cli/README.md) 及 <https://api.zenstory.ai/skill.md>。

## 工作台核心功能

<table>
  <tr>
    <td><img src="docs/screenshots/ai-chat.png" alt="打开章节，在右侧对话框写请求" width="100%"></td>
    <td><img src="docs/screenshots/file-tree.png" alt="长篇项目的文件树" width="100%"></td>
  </tr>
  <tr>
    <td align="center">打开章节，在右边写请求</td>
    <td align="center">长篇项目默认五个文件夹：设定、角色、素材、大纲、正文</td>
  </tr>
</table>

### 多 Agent 编排与运行守卫

```mermaid
flowchart LR
    R["路由<br/>本轮请求与必要的上一轮上下文"] -->|quick| W["内容创作者"]
    R -->|standard| P["大纲规划师"] -->|要求正文时| W
    R -->|full| P2["大纲规划师"] --> H["爽点设计师"] -->|要求正文时| W
    R -->|hook_focus| H2["爽点设计师"] -->|要求正文时| W
    R -->|review_only| Q["质量审稿人"]
    W -. 高质量模式：成功写入正文累计满 500 字 .-> Q
    Q -. 有问题 .-> W
```

| 流程 | 谁来做 | 触发场景 |
| --- | --- | --- |
| `quick` | 内容创作者直接写 | 续写、扩写等日常请求，路由拿不准时也走这条 |
| `standard` | 先规划；同时要求正文时再写 | 要大纲、规划、梗概、章节结构 |
| `full` | 规划 → 爽点设计 → 写作 | 明确要完整流程、重要章节或高潮戏 |
| `hook_focus` | 先设计爽点、反转或节奏；要求改稿时再写 | 觉得不够爽、太平淡、要加反转 |
| `review_only` | 只审稿，不改文件 | 要检查问题、评估质量或看追更指数 |

- **「快速」与「高质量」模式**：两者使用同一模型。「快速」模式跳过按字数自动送审，适合日常连贯快写；「高质量」模式在成功写入的正文累计达到 500 字时自动送交审稿人核对作者要求、事实连续性与因果（最多两轮审查），并扣留前置角色的中间寒暄，只向作者呈现一份最终总结。
- **生成中插话（Steering）与长任务续接**：AI 写作过程中可随时在输入框追加指令，当前正在执行的 Agent 不会被中途打断，本轮结束后由下一接力角色或当前角色补一轮无缝处理。长任务接近运行墙钟时限时会自动收尾并给出进度卡，点「继续」即可从断点接着写；回复末尾给出的改稿方向也会直接转为输入框上方的快捷建议按钮。

### 上下文快照、跨轮工作集与 AI 记忆

- **双 32,000 token 预算与优先级分档**：每轮对话除携带最近约 32,000 token 的聊天记录和跨轮工作集（最近两轮读写过的文件全文，最多 8 份 / 6 万字）外，另用独立的 32,000 token 预算按优先级（焦点文件与选区引用 → 角色卡、核心设定与上一章 → 同目录章节与实时关键词检索片段）组装项目资料。
- **Prompt Cache 稳定前缀**：相对稳定的项目资料会被打包为 `<project_snapshot>` 固定在系统提示之后，本轮变动与当前章节通过 `<turn_context>` 附在消息末尾，跨轮与跨 Agent 交接时最大化复用模型前缀缓存。
- **AI 记忆与写作约定**：面板包含「项目简介、写作风格、当前阶段、写作约定」四栏。你在对话里明确让 AI 记住的规则（如「记住：女主第五章才出场」）会只追加不删改地写入「写作约定」，并在对话结果卡中列出回执。

### 14 个内置技能与自定义技能包

| 分类 | 技能 |
| --- | --- |
| 写作 | 续写正文 · 开头生成 · 短篇写作 · 短剧剧本正文生成 |
| 情节 | 创建大纲 · 钩子设计 · 短剧改编分析 · 短剧故事概要生成 · 短剧分集集纲生成 |
| 文风 | 润色修改 · 节奏控制 · 去AI味 |
| 角色 | 创建角色 |
| 世界观 | 世界观设定 |

启用技能后，AI 每轮先看到已启用技能的名称与用途（L1 目录），匹配需求时再按需加载完整方法（L2）与参考资源（L3）；也可以在输入框打 `/` 或从侧栏点选技能显式附加到当前消息（最多 3 个）。自定义技能遵循开放 Agent Skills 的 `SKILL.md` 规范，支持 `.zip` / `.md` 导入导出（自动过滤可执行脚本）。

### 参考小说拆解（素材库）与主备模型容灾

- **参考小说素材库**：基于 Prefect 异步流水线，上传一本 30 万字以内的 TXT 参考小说，后台自动拆出每章摘要、全书概要、角色档案、世界观和金手指。在项目「参考小说库」面板可将条目「附加到聊天」（每条消息最多 5 个）或导入为项目文件供检索参考。
- **DeepSeek / MiniMax 主备切换与双凭证分流**：服务端支持配置 DeepSeek 与 MiniMax 主备供应商（首包输出前故障当场无感切换、会话级固定保缓存、全局熔断），并支持 MiniMax 主次 API Key 按权重分流与独立冷却；流式计费在用户主动停止、超时或异常且无实质正文产出时自动退还当次额度。

## 常见问题

### 只想和 AI 讨论、不想让它改文件怎么办？

在请求里写明「暂不创建或修改文件」，路由识别为只读请求后会禁用所有文件写入工具。如果 AI 已经修改了文件，直接在对话的编辑卡片上点「撤销」，或打开编辑器底部的「历史」回滚即可。

### 能换别的大模型吗？

自托管环境可以用环境变量在 DeepSeek 和 MiniMax 之间配置主用与备用模型：`LLM_PRIMARY_PROVIDER` / `LLM_PRIMARY_MODEL`、`LLM_BACKUP_PROVIDER` / `LLM_BACKUP_MODEL`，Key 分别为 `DEEPSEEK_API_KEY`、`MINIMAX_API_KEY`（MiniMax 另支持 `MINIMAX_SECONDARY_API_KEY` 按权重分流）。完整说明见 [`apps/server/agent/core/llm_providers.py`](apps/server/agent/core/llm_providers.py)（新增模型需先在 `apps/server/services/usage/pricing.py` 配置价格）。

### 能导出 Word，或者直接发布到小说平台吗？

目前「导出正文」生成按章节排好序的带 BOM UTF-8 TXT 文件（`项目名_正文.txt`），单个大纲或设定可用「下载当前文件」保存。需要 Word 排版或发布到小说平台时，请将导出的文件放入本地软件或手动上传（[#13](https://github.com/zenstory-ai/zenstory/issues/13)）。

### 自托管怎么关闭注册邀请码？

自托管默认要求邀请码注册（管理员在后台「邀请系统」生成）；如需免邀请码注册，在后端配置 `AUTH_REGISTER_INVITE_CODE_OPTIONAL=true`（前端可选加 `VITE_INVITE_CODE_OPTIONAL=true`），详见 [docs/docker-compose.md](docs/docker-compose.md)（[#1](https://github.com/zenstory-ai/zenstory/issues/1)）。

## 延伸阅读

- [快速开始](https://zenstory.ai/docs/getting-started/quick-start)：从空项目到第一份能改的提纲。
- [第一篇短篇](https://zenstory.ai/docs/getting-started/first-project)：一个原创短篇的文件安排和三段范围明确的请求。
- [AI 创作助手](https://zenstory.ai/docs/user-guide/ai-assistant)：焦点文件、引用文字，以及中断后怎么接着写。
- [AI 记忆](https://zenstory.ai/docs/advanced/ai-memory)：四栏怎么填，写错了怎么改。
- [版本历史](https://zenstory.ai/docs/user-guide/version-history)：查看、比较和恢复单个文件的版本。
- [从参考片段写到自己的场景](https://zenstory.ai/docs/advanced/material-analysis)：拆完参考小说，怎样写出自己的戏。
- [写作环境对比](https://zenstory.ai/zh/compare/writing-workflows)：普通聊天、Oh Story skill 包、DSH 插件和 ZenStory 工作台各适合什么。
- [AI 续写怎么保持文风](https://zenstory.ai/zh/oh-story/preserve-author-voice)：把文风拆成说得清的几点，附一段自己的样本。

## 参与贡献

- **GitHub Issues**：[提交 Bug](https://github.com/zenstory-ai/zenstory/issues/new?template=bug_report.md) · [功能建议](https://github.com/zenstory-ai/zenstory/issues/new?template=feature_request.md)。在线版也可以从项目页右上角「…」→「问题反馈」提交并附截图。
- 改代码前请先阅读 [CONTRIBUTING.md](CONTRIBUTING.md) 与仓库文档索引 [docs/README.md](docs/README.md)。写作 Agent 的工作流、工具和 SSE 事件说明见 [`apps/server/agent/AGENTS.md`](apps/server/agent/AGENTS.md)。

<details>
<summary>项目结构与技术栈</summary>

```text
zenstory/
├── apps/web/                 # React 前端
│   ├── src/components/       #   Layout、sidebar/FileTreePane、Editor、ChatPanel、skills …
│   ├── src/pages/            #   Dashboard、项目、素材库、定价 …
│   └── docs/                 #   使用文档源文件，发布到 zenstory.ai/docs
├── apps/cli/                 # zenstory 命令行工具与外部 Agent 技能包
└── apps/server/              # FastAPI 后端
    ├── api/                  #   路由：auth、projects、files、agent、agent_api、materials、admin …
    ├── agent/                #   写作 Agent
    │   ├── graph/            #     路由、交接、自动审稿的调度循环
    │   ├── tools/            #     文件、项目、技能和交接工具
    │   ├── context/          #     每轮上下文快照、预算和优先级分档
    │   ├── prompts/          #     共享协议、网文/短剧题材规范与角色提示
    │   └── skills/           #     渐进式技能加载与注入
    ├── flows/                #   素材拆解流水线（Prefect）
    ├── services/             #   业务逻辑与流式计费账本
    └── models/               #   数据模型
```

| 层 | 技术 |
| --- | --- |
| 前端 | React 19 · TypeScript · Vite 7 · Tailwind CSS 4 · Zustand · TanStack Query |
| 后端 | FastAPI · SQLModel · Server-Sent Events |
| AI | DeepSeek / MiniMax 主备切换与双凭证分流 · openai-agents-python |
| 检索 | 数据库关键词匹配、相关片段与行号；文件编辑后立即可搜，无索引任务 |
| 数据 | SQLite（默认）/ PostgreSQL · Redis · Alembic |
| 部署 | Docker Compose；在线版前端在 Vercel，后端与 Prefect 在 Railway |

</details>

## 许可证

[GNU AGPL-3.0](LICENSE) &copy; 2024-2026 ZenStory

本仓库采用 GNU Affero General Public License v3.0。你可以自由使用、修改和自部署；如果把修改后的版本作为网络服务提供给他人，需要按 AGPL 向这些用户公开对应的源代码。

ZenStory 名称与标识不在许可范围内：fork 或自部署的服务请使用自己的名称。需要商业授权（不受 AGPL 义务约束）请联系维护者。

`apps/cli` 命令行客户端仍按 MIT 许可提供，见 [apps/cli/LICENSE](apps/cli/LICENSE)。

## ZenStory AI 项目

ZenStory 工作台由 [ZenStory AI](https://zenstory.ai/zh) 维护——一套可在 AI Agent 中使用的开源故事创作、改编与制作工具（GitHub 组织：[zenstory-ai](https://github.com/zenstory-ai)）。同组织项目：

| 项目 | 用途 |
| --- | --- |
| [oh-story-claudecode](https://github.com/zenstory-ai/oh-story-claudecode) | 网文写作 skill 包：扫榜、拆文、写作、去AI味、封面图 |
| [drama-skills](https://github.com/zenstory-ai/drama-skills) | AI 短剧 / 漫剧创作 skill 合集：剧本、资产、分镜、图片/视频提示词、独立审查 |
| [novel-to-game](https://github.com/zenstory-ai/novel-to-game) | 小说改编游戏 skill 合集：原著拆解、目标运行环境构建与实机运行验收 |
| [video-recap-skills](https://github.com/zenstory-ai/video-recap-skills) | 将支持的视频文件制作成中文解说，可选导出可编辑的剪映/CapCut 草稿 |
| [oh-story-dsh](https://github.com/zenstory-ai/oh-story-dsh) | DeepSeek Harness 社区插件，提供小说、短剧、游戏和视频解说工作台 |
| [zenstory](https://github.com/zenstory-ai/zenstory) | 对话即创作的 AI 小说写作工作台，在线版 [app.zenstory.ai](https://app.zenstory.ai)（本仓库） |
