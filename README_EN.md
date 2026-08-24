<div align="center">

<img src="apps/web/public/favicon.svg" alt="ZenStory Logo" width="64" height="64">

# ZenStory

**Where Conversation Meets Creation — The AI Agent-Powered Commercial Novel-Writing Workbench**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![GitHub Stars](https://img.shields.io/github/stars/zenstory-ai/zenstory?style=social)](https://github.com/zenstory-ai/zenstory)
[![Website](https://img.shields.io/website?url=https%3A%2F%2Fzenstory.ai&label=zenstory.ai)](https://zenstory.ai/)
![React](https://img.shields.io/badge/React_19-61DAFB?logo=react&logoColor=black)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![Python](https://img.shields.io/badge/Python_3.12+-3776AB?logo=python&logoColor=white)

ZenStory's Agent operates your creative files directly — building character cards, decomposing reference material, planning outlines, and writing chapter by chapter — all inside one conversation, instead of copy-pasting generated text back into an editor.

**2,000+ Creators · 12M Words Generated · 4.9 Rating**

[zenstory.ai](https://zenstory.ai/) · [Quick Start](#quick-start) · [Architecture](#project-architecture) · [中文文档](README.md)

</div>

---

<table>
  <tr>
    <td><img src="docs/screenshots/workspace.png" alt="ZenStory Three-Panel Workspace" width="100%"></td>
  </tr>
  <tr>
    <td align="center"><b>Three-Panel Workspace — File Tree · Editor · AI Chat — the Agent reads and writes your files directly</b></td>
  </tr>
</table>

---

## Table of Contents

- [Why ZenStory](#why-zenstory) — how it differs from traditional AI writing tools
- [Core Capabilities](#core-capabilities) — the 8 capabilities (file-operating chat · multi-agent · material library · skills · Agent API …)
- [Tech Stack](#tech-stack) — frontend / backend / AI / retrieval / deployment at a glance
- [Quick Start](#quick-start) — one-line Docker or local development
- [Project Architecture](#project-architecture) — monorepo layout
- [Roadmap](#roadmap) — shipped capabilities and what's next
- [Contributing](#contributing) — bugs, feature requests, PRs

---

## Why ZenStory

Most AI writing tools stop at "chat box + copy-paste." ZenStory turns the AI into a collaborator that actually does the work:

| Traditional AI Writing Tools | ZenStory |
|:---|:---|
| Chat box + copy-paste | Agent creates, edits, and organizes your creative files directly |
| No context memory, starts from scratch every time | Understands character relationships and world rules — coherent, no continuity errors |
| Single model, one-shot Q&A | Multi-agent pipeline (plan → design → write → review) |
| Stuck when inspiration runs dry | AI decomposes reference works + context-aware inspiration |
| Closed; no external tooling | Agent API lets tools like Claude Code / OpenClaw connect directly |

---

## Core Capabilities

### 1. Conversation x File System — A New Creative Paradigm

The AI doesn't stay in the chat box — it operates your creative files through a complete tool chain:

<table>
  <tr>
    <td align="center"><b>AI Chat-Driven Creation</b></td>
    <td align="center"><b>Intelligent File Tree</b></td>
  </tr>
  <tr>
    <td><img src="docs/screenshots/ai-chat.png" alt="AI Chat-Driven Creation" width="100%"></td>
    <td><img src="docs/screenshots/file-tree.png" alt="File Tree Management" width="100%"></td>
  </tr>
</table>

- **Conversational file operations** — "Create a villain with a dark personality and a tragic past" — the Agent creates a character card and fills in the details
- **Context-aware** — The AI understands character relationships, world rules, and existing chapters — no continuity errors; long conversations are auto-compacted without losing the thread
- **Diff review mode** — AI changes are shown as inline diffs and applied only after your approval — you stay in control
- **Streaming + live steering** — Watch the AI write in real time, and inject direction mid-generation
- **9 Agent tools** — create / edit / delete files, query files, hybrid material search, update project status, agent handoff, request clarification, parallel execution — covering the whole workflow

### 2. Multi-Agent Collaborative Writing Engine

One intent router plus four specialized agents form a complete AI writing team (custom orchestration, not a black box):

```
┌─────────┐    ┌─────────┐    ┌──────────┐    ┌─────────┐    ┌──────────────────┐
│ Router  │───►│ Planner │───►│  Hook    │───►│ Writer  │───►│ Quality Reviewer │
│ Intent  │    │ Story   │    │ Designer │    │ Content │    │  Consistency &   │
│ Routing │    │ Planner │    │ Twists   │    │ Creator │    │  Quality Gate    │
└─────────┘    └─────────┘    └──────────┘    └─────────┘    └──────────────────┘
```

| Role | Responsibility | When It Activates |
|-------|------|------------------|
| **Router** | Intent classification, optimal workflow selection | Every request |
| **Planner** | Story structure, chapter pacing | Complex creative tasks |
| **Hook Designer** | Plot twists, suspense, climaxes | When engagement needs a boost |
| **Writer** | Content creation with adaptive style | All creative tasks |
| **Quality Reviewer** | Consistency checking, quality assurance | Auto-triggered for long content |

The Router assesses task complexity and routes to one of **five workflows** — Quick Write · Standard · Full Collaboration · Hook Focus · Review-Only. Agents support intelligent handoffs for true multi-round collaboration.

### 3. Material Library — AI-Powered Reference Decomposition

Upload reference novels you admire, and the AI automatically decomposes them into **8 categories of structured creative elements**:

| Dimension | Description |
|-----------|-------------|
| **Chapter Summaries** | Core events and plot progression per chapter |
| **Character Profiles** | Names, aliases, archetypes, ability systems |
| **Character Relationships** | Complex relationship networks |
| **Plot Points** | Key events per chapter (conflict / turning point / reveal / dialogue) |
| **Story Arcs** | Cross-chapter aggregated plot arcs (setup–development–climax–resolution) |
| **World Building** | Power systems, world structure, key factions |
| **Golden Fingers** | Special abilities — name, type, evolution history |
| **Event Timeline** | Chronological ordering of all events |

Decomposed materials are searchable via **Hybrid RAG** — semantic vector + keyword dual engines fused with RRF for pinpoint accuracy. The AI automatically references relevant material while writing to keep style and setting consistent.

### 4. Inspiration Library — Break Through Writer's Block

<table>
  <tr>
    <td><img src="apps/web/public/docs-images/advanced/workflow-tips.png" alt="Inspiration Library" width="100%"></td>
  </tr>
  <tr>
    <td align="center"><b>Context-aware inspiration cards — copy to project and start writing instantly</b></td>
  </tr>
</table>

- **Context-aware** — Generates precise inspiration based on your project type (novel / short / screenplay) and existing content
- **Featured picks** — Editor-curated high-quality inspiration templates covering all creative scenarios
- **One-click reuse** — Copy inspiration directly to your project and start creating immediately
- **Material synergy** — The richer your material library, the more precise the recommendations

### 5. Skills System & Marketplace

13 built-in professional writing skills, ready to use:

| Category | Skills |
|----------|--------|
| **Writing** | Continue Writing · Scene Description · Dialogue Generation · Opening Creation |
| **Plot** | Conflict Design · Suspense Design · Reversal Design · Rhythm Control |
| **Style** | Immersion Enhancement · Text Polishing |
| **Setup** | Character Creation · Outline Generation · World Building |

<table>
  <tr>
    <td><img src="apps/web/public/docs-images/user-guide/skills.png" alt="Skills Marketplace" width="100%"></td>
  </tr>
  <tr>
    <td align="center"><b>Skills Marketplace — Markdown-defined skills, one-click sharing, community-driven</b></td>
  </tr>
</table>

Skills are defined in Markdown, with custom creation and one-click sharing to the marketplace. Admin moderation and community contributions make it grow stronger with use.

### 6. Agent API — Connect External AI Tools to Your Writing Workspace

Generate an API key, paste it into Claude Code or OpenClaw, and let external AI agents read and write your novel projects directly:

```text
# In Claude Code or OpenClaw, the agent can:
# - Read project files and character settings
# - Create new chapters and characters
# - Search the material library
# - Edit existing content
```

API keys are stored hashed and support per-project scoping — this isn't just "AI-assisted writing," it's **AI ecosystem interconnection**: any AI tool you love can become a ZenStory creative engine.

### 7. Professional Writing Workbench

<table>
  <tr>
    <td align="center"><b>Tiptap Rich Text Editor</b></td>
    <td align="center"><b>Version Comparison & Rollback</b></td>
  </tr>
  <tr>
    <td><img src="apps/web/public/docs-images/user-guide/editor.png" alt="Editor" width="100%"></td>
    <td><img src="docs/screenshots/version-history.png" alt="Version History" width="100%"></td>
  </tr>
</table>

- **Six file types** — Outline, Draft, Script, Character, Lore, Snippet — each with a specialized editing interface
- **Smart sorting** — Auto-detects Chinese and Arabic chapter numbers ("第一章" / "Chapter 1") and orders accordingly
- **Version snapshots** — Auto-save on every edit, one-click diff between any two versions, rollback support
- **Plain-text export** — One-click export to TXT, with chapters auto-merged in order
- **Voice input** — Speech-to-text (Tencent Cloud ASR), long-press recording, mobile-friendly
- **Global search** — Cmd+K to quickly search across all project files
- **Dark mode** — Eye-friendly writing, follows system preference
- **Bilingual** — Full i18n for English and Chinese interfaces

### 8. Commercial-Grade Operations

ZenStory isn't a demo — it's a complete, operable product:

- **Subscription tiers** — Free / Pro plans, with a dynamically configurable plan catalog
- **Quota management** — Fine-grained control over word count, agent tasks, project count, material uploads
- **Points system** — Daily check-in, referral rewards, redemption codes; points redeemable for subscription time
- **Writing streak** — Track daily writing, streak protection, freeze mechanism
- **Analytics** — Word-count trends, chapter completion, AI collaboration metrics, project health
- **Admin dashboard** — User management, subscription management, skill review, prompt editing, data dashboards
- **Project types** — Novel, Short Story, Screenplay — each with dedicated creative guidance

---

## Tech Stack

| Layer | Technologies |
|------|------|
| **Frontend** | React 19 · TypeScript · Vite · Tailwind CSS · Tiptap · Zustand · TanStack Query |
| **Backend** | FastAPI · SQLModel · Pydantic · SSE streaming |
| **AI** | DeepSeek `deepseek-v4-flash` (OpenAI-compatible) · openai-agents-python writing agent |
| **Retrieval** | LlamaIndex · ChromaDB · vector + keyword hybrid search (RRF fusion) |
| **Data** | SQLite (dev) / PostgreSQL (prod) · Redis · Alembic migrations |
| **Deploy** | Docker Compose · Vercel (frontend) · Railway (backend) · Cloudflare |

---

## Quick Start

### Prerequisites

- Node.js >= 18
- Python >= 3.12
- pnpm

### Option 1: Docker Compose (Recommended)

One API key, one command:

```bash
export DEEPSEEK_API_KEY=your-key
docker compose up -d --build
```

Open:

- Frontend: http://localhost:5173
- API Docs: http://localhost:8000/docs

> Data persists in Docker volumes. Works with SQLite out of the box.

### Option 2: Local Development

```bash
# 1. Backend
cd apps/server
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # Edit .env to add your API key
python3 main.py

# 2. Frontend (new terminal)
cd apps/web
pnpm install
cp .env.example .env.local
pnpm dev
```

> Only one DeepSeek API key is needed. For production deployment (PostgreSQL + Redis), see [docs/docker-compose.md](docs/docker-compose.md).

---

## Project Architecture

A monorepo: React frontend (`apps/web`) + FastAPI backend (`apps/server`). Files are the core unit of creation, with AI conversations driving content generation.

```
zenstory/
├── apps/
│   ├── web/                    # React Frontend
│   │   ├── src/components/     # Components (Layout/FileTree/Editor/ChatPanel/Skills/...)
│   │   ├── src/hooks/          # Hooks (useAgentStream/useVoiceInput/useMaterialLibrary/...)
│   │   ├── src/contexts/       # Contexts (Auth/Project/Theme)
│   │   └── src/pages/          # Pages (Dashboard/Project/Pricing/Materials/...)
│   └── server/                 # FastAPI Backend
│       ├── api/                # API routes (auth/projects/files/agent/chat/export/voice)
│       ├── agent/              # AI Agent System ★
│       │   ├── graph/          #   Multi-agent orchestration (custom asyncio workflow)
│       │   ├── tools/          #   9 agent tools (create_file/edit_file/hybrid_search/...)
│       │   ├── context/        #   Context assembly + token budget + long-chat compaction
│       │   ├── prompts/        #   Project-type prompts + agent-specific prompts
│       │   └── skills/         #   Skill loading / matching / injection (incl. 13 built-ins)
│       ├── models/             # Data models (File/Material/Subscription/Skill/...)
│       ├── services/           # Business logic layer
│       │   ├── material/       #   Material decomposition (15 sub-modules)
│       │   └── features/       #   Export / snapshot / verification services
│       └── config/             # Configuration (Logger/Settings/AgentRuntime)
└── docs/                       # Documentation and screenshots
```

> For the detailed Agent architecture (multi-agent workflow, tools, SSE events) see [`apps/server/agent/CLAUDE.md`](apps/server/agent/CLAUDE.md).

---

## Roadmap

- [x] Multi-agent collaborative writing engine (Router / Planner / Hook Designer / Writer / Quality Reviewer)
- [x] Material library with AI decomposition (8 structured element types + hybrid RAG)
- [x] Inspiration library (context-aware + one-click reuse)
- [x] Skills system & marketplace (13 built-in + custom + community sharing)
- [x] Agent API (Claude Code / OpenClaw direct connection)
- [x] Version snapshots & diff comparison
- [x] Voice input
- [x] Commercial features (subscriptions / quotas / points / admin dashboard)
- [ ] More export formats (Word / Markdown)
- [ ] Native mobile app

---

## Contributing

Contributions are welcome!

- [Report a Bug](https://github.com/zenstory-ai/zenstory/issues/new?template=bug_report.md)
- [Request a Feature](https://github.com/zenstory-ai/zenstory/issues/new?template=feature_request.md)
- Submit a Pull Request

Please read [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

[MIT License](LICENSE) &copy; 2024-2026 ZenStory
