<!-- Last synced with README.md: 2026-11-09 -->

<p align="center">
  <img src="https://raw.githubusercontent.com/zenstory-ai/zenstory/main/apps/web/public/brand/zenstory-ai-mark.svg" alt="" width="76" height="76">
</p>

<h1 align="center">ZenStory Workbench</h1>

<p align="center">
  <b>Chat to create: write fiction in your browser while the AI creates and edits your outlines, chapters and character sheets.</b>
</p>

<p align="center">
  <a href="https://zenstory.ai/workbench"><b>Project page</b></a>
  &nbsp;·&nbsp;
  <a href="#get-started"><b>Get started</b></a>
  &nbsp;·&nbsp;
  <a href="#core-features"><b>Core features</b></a>
  &nbsp;·&nbsp;
  <a href="README.md"><b>中文</b></a>
</p>

<p align="center">
  <a href="https://github.com/zenstory-ai/zenstory/stargazers"><img alt="Stars" src="https://img.shields.io/github/stars/zenstory-ai/zenstory?style=flat-square&color=22D3EE&logo=github&logoColor=white&label=Stars"></a>
  <img alt="Skills 14" src="https://img.shields.io/badge/Skills-14-081431?style=flat-square">
  <a href="https://app.zenstory.ai/"><img alt="app.zenstory.ai" src="https://img.shields.io/website?style=flat-square&url=https%3A%2F%2Fapp.zenstory.ai&label=app.zenstory.ai"></a>
  <a href="./LICENSE"><img alt="License AGPL-3.0" src="https://img.shields.io/badge/License-AGPL--3.0-1F6FEB?style=flat-square"></a>
</p>

<p align="center">
  <a href="https://app.zenstory.ai/"><img alt="Open the workbench" src="https://img.shields.io/badge/Open%20the%20workbench-1F6FEB?style=for-the-badge&logo=vercel&logoColor=white"></a>
  <a href="https://github.com/zenstory-ai/zenstory/issues"><img alt="GitHub Issues" src="https://img.shields.io/badge/GitHub%20Issues-181717?style=for-the-badge&logo=github&logoColor=white"></a>
</p>

> **Use the hosted app at [app.zenstory.ai](https://app.zenstory.ai/): sign up or sign in to start creating.**

![The ZenStory three-panel workbench](docs/screenshots/workspace.png)

The workspace puts the file tree on the left, the editor in the middle and AI chat on the right.

## What it is

ZenStory Workbench is a browser-based writing environment for long-form novels, short stories and short-drama screenplays. Outlines, chapters, character sheets, worldbuilding lore and reference materials live as files in your project. You talk to the AI on the right, and it creates and edits those files directly without copy-paste between a chat window and an editor. Use it online at [app.zenstory.ai](https://app.zenstory.ai), or host it yourself under the AGPL-3.0 license.

- **Edit files directly from chat**: ask for "a villain, brooding, with a tragic past" and the Agent creates a character sheet in the Characters folder, streaming it into the editor as it writes. Continuing or rewriting a chapter lands in the file the same way.
- **One router, four specialist agents**: the Planner, Hook Designer, Writer and Quality Reviewer hand off work within your requested scope — an outline request stops at the outline, and prose is written only when requested.
- **Version history for revisions**: AI edits from chat record separate `AI Edited` versions that can be undone with one click on the edit card, or compared line by line and rolled back in the file's History panel.
- **Project context assembled every turn**: each turn automatically packs AI Memory, the project file list, the open file, adjacent chapters, and recently edited characters and lore, rather than relying on long chat history alone.
- **External agents can plug in**: generate an API key in Settings, sign in with `npx zenstory` and install the standard Agent Skill so tools like Claude Code, Codex and OpenClaw can read and write your project files directly.

## Get started

### Hosted

Sign up at [app.zenstory.ai](https://app.zenstory.ai); no invite code is needed. The free plan includes 3 AI messages per day (reset at 00:00 Beijing time, UTC+8) and up to 3 projects; see the [pricing page](https://app.zenstory.ai/pricing) for plan comparisons.

Create a **Long-form Novel** project. A good first request asks the AI to plan a three-chapter opening outline before writing prose:

> I want to write a xianxia web novel. Plan a three-chapter opening outline first and do not write prose yet. The protagonist Xiao Wuya runs the largest merchant guild across the realms and has spent ten years funding Empress Luo Qinghan's rise with spirit stones, top-grade pills and mountain-warding arrays. At her ascension ceremony, to favor her new sword-prodigy favorite Ye Chen, she publicly mocks Xiao Wuya as reeking of copper coins and banishes him from the sect. Unbothered, Xiao Wuya immediately cuts off all spirit stone, pill and array supplies to her sect. Give the core conflict, reversal payoff and cliffhanger hook for each of the first three chapters.

Once the outline is settled, name the target files and let the AI write (the third line uses the Design Hooks skill, stored as `钩子设计`, added under **Skills → Discover** first):

```text
Save the three-chapter outline in the Outlines folder as "Opening 3-Chapter Outline".
Following "Opening 3-Chapter Outline", write chapter one as "Chapter 1: Reeking of Copper? Then I Cut Off Your Path to Immortality" in the Drafts folder and stop after that chapter.
钩子设计：rewrite only the last two paragraphs of Chapter 1 so it ends right as the sect's grand array drains to zero and the elders freeze in panic.
```

See the [user documentation](https://zenstory.ai/docs#en) for more workflows.

### Self-hosted

```bash
export DEEPSEEK_API_KEY=your-key          # or place it in .env at the repository root
docker compose up -d --build

# Create the first admin account (no invite code or email verification required; password must be 12+ chars)
docker compose exec -e ZENSTORY_ADMIN_EMAIL=you@example.com \
  -e ZENSTORY_ADMIN_PASSWORD='CHANGE-ME-12CHARS' server python scripts/create_admin.py
```

Sign in at <http://localhost:5173>; the API reference is at <http://localhost:8000/docs>. By default the app uses SQLite stored in a Docker volume alongside uploaded files, so `docker compose down` and image rebuilds preserve your data.

- **Writing configuration**: project system prompts (`novel`, `short`, `screenplay`) and official skills live in the database. Configure and reload them in the admin console after first launch; see [docs/agent-writing-guidance.md](docs/agent-writing-guidance.md).
- **Plan quotas**: new accounts (including the admin) start on the free plan (3 AI messages/day, up to 3 projects). To unlock higher limits, run `docker compose exec server python scripts/seed_subscription_plans.py` to seed the plans, then assign Pro under **Subscriptions** in the admin console.
- **Production deployment & optional features**: for PostgreSQL + Redis setup, optional invite codes, points/referral flags, email verification (Resend), primary/backup model failover (DeepSeek / MiniMax), Google sign-in, Tencent Cloud voice input, material-library Prefect workers, the Inspiration Library, and Zpay Alipay checkout, see [docs/docker-compose.md](docs/docker-compose.md) (in Chinese).

<details>
<summary>Local development without Docker</summary>

Requires Node.js 20.19+, Python 3.12 and pnpm:

```bash
# Terminal 1: backend (port 8000)
cd apps/server
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # set DEEPSEEK_API_KEY
python3 main.py

# Terminal 2: frontend (port 5173)
cd apps/web
pnpm install
cp .env.example .env.local
pnpm dev
```

Create the first admin account from `apps/server` by running `python scripts/create_admin.py` with `ZENSTORY_ADMIN_EMAIL` and `ZENSTORY_ADMIN_PASSWORD` set.

</details>

### Connect external agents (Claude Code / Codex / OpenClaw)

Create an API key under **Settings → Agent**, then run in your terminal (Node.js 20+):

```bash
npx zenstory login           # paste the key when prompted (hidden input; add --api-base https://your-server/api/v1 if self-hosted)
npx zenstory skill install   # installs for Claude Code by default; add --target codex or --target openclaw for others
npx zenstory whoami          # verify login status and read/write scopes
```

Once installed, the external agent writes with its own model while using the `zenstory` CLI to fetch project context and save chapters back:

```bash
dir=$(mktemp -d)
zenstory files list <projectId> --type draft --fields id,title --json                # list drafts in file-tree order
zenstory context <projectId> --file <chapterId> --query "what happens next" --json   # fetch assembled project context
zenstory files get <chapterId> --fields updated_at --json                            # record current timestamp token
zenstory files get <chapterId> -o "$dir/<chapterId>.md"                              # download chapter to a temp file
zenstory files put <chapterId> --content-file "$dir/<chapterId>.md" --if-updated-at <updated_at>   # write back with lock check
```

See [apps/cli/README.md](apps/cli/README.md) and <https://api.zenstory.ai/skill.md?lang=en> for the full CLI and HTTP reference.

## Core features

<table>
  <tr>
    <td><img src="docs/screenshots/ai-chat.png" alt="A chapter open, with a request typed in the chat" width="100%"></td>
    <td><img src="docs/screenshots/file-tree.png" alt="The file tree of a novel project" width="100%"></td>
  </tr>
  <tr>
    <td align="center">A chapter open, with a request typed on the right</td>
    <td align="center">A novel project starts with five folders (World Building, Characters, Materials, Outlines, Drafts)</td>
  </tr>
</table>

### Multi-agent workflow

```mermaid
flowchart LR
    R["Router<br/>current request & relevant previous-turn context"] -->|quick| W["Writer"]
    R -->|standard| P["Planner"] -->|when prose requested| W
    R -->|full| P2["Planner"] --> H["Hook Designer"] -->|when prose requested| W
    R -->|hook_focus| H2["Hook Designer"] -->|when prose requested| W
    R -->|review_only| Q["Quality Reviewer"]
    W -. Quality mode: successful manuscript writes reach the default 500 characters .-> Q
    Q -. problems found .-> W
```

| Path | Who works | When it runs |
| --- | --- | --- |
| `quick` | Writer directly | Everyday continuation and expansion; also the fallback when routing is uncertain |
| `standard` | Planner first; Writer only if prose is requested | Outlines, plans, synopses or chapter structures |
| `full` | Planner → Hook Designer → Writer | Full workflow requests, key chapters or climaxes |
| `hook_focus` | Hook Designer first; Writer when revision is requested | Strengthening payoffs, twists or pacing |
| `review_only` | Quality Reviewer only (read-only) | Continuity checks, evaluations or read-on assessments |

- **Quality vs. Fast mode**: both use the same model. **Quality** runs the router and triggers automatic review once successful manuscript writes reach 500 characters, checking author requirements, continuity and causality (at most two review rounds per request). **Fast** skips routing and length-triggered auto-review, sending the request straight to the Writer.
- **Follow-up instructions while generating**: you can send an additional instruction while the AI is working. The currently running agent finishes its turn uninterrupted, and the next agent in line (or one extra round of the current agent) picks up the follow-up.

### 14 built-in skills and custom skills

The app shows these English names in the English interface; the Chinese names are what the Chinese interface shows.

| Category | Built-in skills |
| --- | --- |
| Writing | Continue the Draft 续写正文 · Write an Opening 开头生成 · Write a Short Story 短篇写作 · Write Short-Drama Episodes 短剧剧本正文生成 |
| Plot | Create an Outline 创建大纲 · Design Hooks 钩子设计 · Short-Drama Adaptation Analysis 短剧改编分析 · Short-Drama Synopsis 短剧故事概要生成 · Short-Drama Episode Outlines 短剧分集集纲生成 |
| Style | Polish Prose 润色修改 · Control Pacing 节奏控制 · Remove AI Tone 去AI味 |
| Character | Create a Character 创建角色 |
| Worldbuilding | Build the World 世界观设定 |

Enable skills under **Skills → Discover**. Each turn, the AI sees the names and purposes of enabled skills and loads full instructions on demand; you can also type `/` in chat or pick skills from the sidebar to attach up to 3 directly to a message. Custom skills use the open Agent Skills `SKILL.md` format (with optional `references/` resources) and can be imported or exported as `.zip` or `.md` packages.

### AI Memory, context and material library

- **Dual ~32,000-token context budgets**: each turn sends the most recent ~32,000 tokens of conversation (plus full text of files read or edited in the last turn or two) alongside a separate ~32,000-token budget for project context — the four **AI Memory** fields (Project Summary, Writing Style, Current Phase, Notes), the project file list, the active file, the previous chapter and recently edited files in the same folder, plus recently edited character and lore files. Keep lasting rules concise in AI Memory and put detailed settings in character and lore sheets; AI updates to Notes are saved as proposals for you to confirm.
- **Reference Novel Material Library**: upload a reference novel as TXT (up to 300,000 characters) and the backend extracts per-chapter summaries, a whole-book synopsis, character profiles, worldbuilding settings and protagonist edges (金手指). The AI does not browse the library automatically; use **Attach to Chat** (up to 5 entries per message) in the project's Novel References panel, or import entries into the project as searchable files. On the hosted app, the full library requires Pro (environments with the free trial enabled offer a one-time opening-chapter trial).

### Version history and export

![Version history of a single file](docs/screenshots/version-history.png)

- **File version history & undo**: autosave runs ~3 seconds after you stop typing, and consecutive manual saves within 10 minutes coalesce into the latest manual version. AI edits from chat record separate `AI Edited` versions (which do not consume manual-version slots) and can be undone from the chat edit card or compared line by line and rolled back via **History** at the bottom of the editor (unversioned current prose is backed up before restoration). Selecting text in the editor and clicking **Humanize** opens a paragraph-by-paragraph diff that saves only after you accept or reject each change.
- **Manuscript import, chapter sorting & export**: click **+** next to the Drafts folder (**Upload Draft**) to import up to 20 `.txt` or `.md` files at once (up to 5 MB / 500,000 characters each). Files with two or more chapter headings (`第X章`, `卷X`, `序章`, `楔子`, `番外`, `Chapter N`) are automatically split into chapter files. Both the file tree and **Export Manuscript** sort Chinese and Arabic chapter numbers into natural order (e.g. `第一章` → `第2章` → `第十章` → `第一百零二章`, with an unnumbered prologue placed first if created in an empty folder) and merge chapters and scripts into a single UTF-8 with BOM `.txt` file. Outlines, character sheets and lore can be saved individually via **Download current file**.

### Other capabilities

- **Three project types**: choose **Long-form Novel**, **Short Story**, or **Screenplay** (short drama) when creating a project, each with tailored default folders and AI writing briefs.
- **Quick search & voice input**: press `Cmd+K` (`Ctrl+K` on Windows) to search files by title; use built-in voice input (Tencent Cloud ASR) in the chat box.
- **Cross-device & bilingual UI**: switch between Chinese and English interfaces and dark/light themes in Settings; mobile browsers automatically adapt to a three-tab bottom layout (**Files / Editor / AI**).

## FAQ

### How do I discuss ideas with the AI without letting it edit my files?

Include "do not create or modify files" in your prompt — when the router classifies a request as read-only, file-write tools are blocked. If the AI already edited a file, click **Undo** on the edit card in chat or roll back from **History** at the bottom of the editor.

### Can I use a different model?

Self-hosted deployments can configure a primary and a backup provider between DeepSeek and MiniMax via environment variables: `LLM_PRIMARY_PROVIDER` / `LLM_PRIMARY_MODEL`, `LLM_BACKUP_PROVIDER` / `LLM_BACKUP_MODEL`, with keys in `DEEPSEEK_API_KEY` and `MINIMAX_API_KEY`. When the primary is unavailable (connection failure, timeout, rate limit, 5xx, invalid key), that chat window switches to the backup. See [`apps/server/agent/core/llm_providers.py`](apps/server/agent/core/llm_providers.py) (new models require a price entry in `apps/server/services/usage/pricing.py`).

### Can I export to Word or publish directly to web-novel platforms?

**Export Manuscript** outputs a single UTF-8 with BOM `.txt` file (`<project>_正文.txt`, ready to open in Windows Notepad). For Word formatting or platform publishing, import that TXT into your local word processor or upload it manually ([#13](https://github.com/zenstory-ai/zenstory/issues/13)).

### How do I allow open sign-ups when self-hosting, or reset a forgotten password?

Self-hosted instances require an invite code by default (generated on the admin console's **Referral System** page). To make invite codes optional, set `AUTH_REGISTER_INVITE_CODE_OPTIONAL=true` on the backend (and optionally `VITE_INVITE_CODE_OPTIONAL=true` on the frontend); see [docs/docker-compose.md](docs/docker-compose.md) ([#1](https://github.com/zenstory-ai/zenstory/issues/1)). The hosted app does not yet offer self-service password reset — email `support@zenstory.ai` from your registered address.

## Further reading

- [Quick start](https://zenstory.ai/docs/getting-started/quick-start#en): from an empty project to a first outline you can edit.
- [Your first short story](https://zenstory.ai/docs/getting-started/first-project#en): an original short story's file plan and three scoped requests.
- [AI assistant](https://zenstory.ai/docs/user-guide/ai-assistant#en): focused files, quotes, and picking up after an interruption.
- [AI Memory](https://zenstory.ai/docs/advanced/ai-memory#en): what to put in the four fields and how to correct them.
- [Version history](https://zenstory.ai/docs/user-guide/version-history#en): view, compare and restore one file's versions.
- [From a reference passage to your own scene](https://zenstory.ai/docs/advanced/material-analysis#en): after analysing a reference novel, how to write a scene of your own.
- [Writing environments compared](https://zenstory.ai/compare/writing-workflows): plain chat, the Oh Story skill pack, the DSH plugin and ZenStory Workbench, and what each suits.
- [Keeping your voice when AI continues](https://zenstory.ai/oh-story/preserve-author-voice): describe your voice in concrete terms and give a short sample of your own.

## Contributing

- **GitHub Issues**: [report a bug](https://github.com/zenstory-ai/zenstory/issues/new?template=bug_report.md) · [request a feature](https://github.com/zenstory-ai/zenstory/issues/new?template=feature_request.md). In the hosted app you can also use the **…** menu at the top right of the project page → **Feedback** to attach a screenshot.
- Read [CONTRIBUTING.md](CONTRIBUTING.md) and the repository documentation index [docs/en/README.md](docs/en/README.md) before changing code. The multi-agent workflow, tools and SSE events are documented in [`apps/server/agent/AGENTS.md`](apps/server/agent/AGENTS.md).

<details>
<summary>Project layout and tech stack</summary>

```text
zenstory/
├── apps/web/                 # React frontend
│   ├── src/components/       #   Layout, sidebar/FileTreePane, Editor, ChatPanel, skills …
│   ├── src/pages/            #   Dashboard, project, materials, pricing …
│   └── docs/                 #   docs source, published to zenstory.ai/docs
├── apps/cli/                 # zenstory CLI and external Agent Skill package
└── apps/server/              # FastAPI backend
    ├── api/                  #   routes: auth, projects, files, agent, agent_api, materials, admin …
    ├── agent/                #   the writing agents
    │   ├── graph/            #     orchestration loop: routing, handoffs, auto-review
    │   ├── tools/            #     file, project, skill and handoff tools
    │   ├── context/          #     per-turn context assembly, budget and priorities
    │   ├── prompts/          #     shared protocols and role prompts; project writing configuration is in DB
    │   └── skills/           #     skill loading and injection; official instructions live in DB
    ├── flows/                #   material decomposition pipeline (Prefect)
    ├── services/             #   business logic
    └── models/               #   data models
```

| Layer | Technologies |
| --- | --- |
| Frontend | React 19 · TypeScript · Vite 7 · Tailwind CSS 4 · Zustand · TanStack Query |
| Backend | FastAPI · SQLModel · Server-Sent Events |
| AI | DeepSeek `deepseek-flash` (default, with optional DeepSeek / MiniMax primary/backup failover) · openai-agents-python |
| Retrieval | Database keyword search with snippets and line numbers; edits are searchable immediately, without indexing |
| Data | SQLite (default) / PostgreSQL · Redis · Alembic |
| Deploy | Docker Compose; the hosted app runs the frontend on Vercel and the API plus Prefect on Railway |

</details>

## License

[GNU AGPL-3.0](LICENSE) &copy; 2024-2026 ZenStory

This repository is licensed under the GNU Affero General Public License v3.0. You may use, modify and self-host it; if you offer a modified version to others as a network service, the AGPL requires you to make the corresponding source available to those users.

The ZenStory name and logo are not licensed: forks and self-hosted services should use their own name. For a commercial license without the AGPL obligations, contact the maintainers.

The `apps/cli` command-line client stays under the MIT license; see [apps/cli/LICENSE](apps/cli/LICENSE).

## Part of ZenStory AI

ZenStory Workbench is maintained by [ZenStory AI](https://zenstory.ai) — open-source, agent-native tools for creating, adapting and producing stories (GitHub org: [zenstory-ai](https://github.com/zenstory-ai)). Sibling projects:

| Project | What it does |
| --- | --- |
| [oh-story-claudecode](https://github.com/zenstory-ai/oh-story-claudecode) | Web-fiction writing skill pack: chart scanning, deconstruction, drafting, de-AI-flavor, covers |
| [drama-skills](https://github.com/zenstory-ai/drama-skills) | AI short-drama / motion-comic suite: scripts, assets, storyboards, image & video prompts, review |
| [novel-to-game](https://github.com/zenstory-ai/novel-to-game) | Agent skills for source-grounded novel adaptation, target-runtime builds, and evidence-based QA |
| [video-recap-skills](https://github.com/zenstory-ai/video-recap-skills) | Create Chinese-narration recaps from supported video files, with optional editable JianYing/CapCut draft export |
| [oh-story-dsh](https://github.com/zenstory-ai/oh-story-dsh) | Community DeepSeek Harness plugin with novel, short-drama, game and video-recap workbenches |
| [zenstory](https://github.com/zenstory-ai/zenstory) | Chat-to-create AI novel-writing workbench, hosted at [app.zenstory.ai](https://app.zenstory.ai) (this repo) |
