# CLAUDE.md

This file provides guidance to coding agents when working with code in this repository.

## Project Overview

**zenstory** is an AI-assisted novel writing workbench with a conversational AI interface. Files (outlines, drafts, characters, lores) are the central unit, with AI conversations driving content generation.

**Architecture**: Monorepo with React frontend (apps/web) and FastAPI backend (apps/server). Three-panel layout: File tree (left), Editor (middle), AI Chat (right).

**Related Documentation**:
- `apps/server/agent/CLAUDE.md` - Detailed Agent system architecture (multi-agent workflow, tools, SSE events)

## Development Commands

### Backend (FastAPI/Python)

```bash
cd apps/server

# Install dependencies (first time only)
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt

# Start backend server (port 8000)
python3 main.py

# Run specific Alembic migration
alembic upgrade head

# Create new migration
alembic revision --autogenerate -m "description"
```

### Frontend (React/Vite)

```bash
cd apps/web

# Install dependencies
pnpm install  # or npm install

# Start development server (port 5173)
pnpm dev  # or npm run dev

# Build for production
pnpm build

# Type-check + build
pnpm run build:typecheck

# Lint
pnpm lint
```

### Running Both Services

From project root (requires two terminals):
```bash
# Terminal 1: Backend
cd apps/server && source venv/bin/activate && python3 main.py

# Terminal 2: Frontend
cd apps/web && pnpm dev
```

### Database Migrations

The backend uses Alembic for database migrations:
- Migrations are in `apps/server/alembic/versions/`
- Configuration: `apps/server/alembic.ini`
- After schema changes: `alembic revision --autogenerate -m "description"` then `alembic upgrade head`

## Architecture Overview

### Backend (apps/server)

**Tech Stack**: FastAPI, SQLModel (Pydantic + SQLAlchemy), SQLite (dev) / PostgreSQL (production), DeepSeek OpenAI-compatible API with openai-agents-python, LlamaIndex, ChromaDB

**Key Directories**:
- `main.py` - App entry point, registers all routers and middleware
- `api/` - API route handlers (auth, projects, files, versions, agent, chat, export, voice)
- `models/` - SQLModel database models (entities, file_model, file_version)
- `services/` - Business logic layer (file_version_service, snapshot_service, verification_service, export_service)
- `agent/` - AI Agent system (service.py, suggest_service.py, tools/, context/, schemas/)
- `database.py` - Database connection and session management
- `config/` - Configuration modules (logger, settings)

**Important**: The codebase uses a service layer pattern. Route handlers in `api/` should be thin and delegate business logic to `services/`.

### Frontend (apps/web)

**Tech Stack**: React 19, TypeScript, Vite, Tailwind CSS 4.x, Zustand, TanStack React Query, Tiptap editor, react-arborist (file tree)

**Key Directories**:
- `components/` - React components (Layout, FileTree, Editor, ChatPanel, MessageList, etc.)
- `contexts/` - React Context providers (AuthContext, ProjectContext, ThemeContext)
- `hooks/` - Custom hooks (useAgentStream, useVoiceInput, useExport)
- `lib/` - Utilities (api.ts, apiClient.ts, agentApi.ts, errorHandler.ts)
- `types/` - TypeScript type definitions

**State Management**:
- `AuthContext` - User authentication state
- `ProjectContext` - Active project and file state
- `Zustand` stores - Additional state management
- `TanStack Query` - Server state caching and synchronization

### Agent System

The AI Agent system is the core feature:

**Backend (apps/server/agent/)**:
- `service.py` - Main agent orchestration with streaming response
- `suggest_service.py` - AI-powered content suggestions
- `context/` - Context assembly and prioritization for RAG
- `tools/` - Agent tools (file_executor.py for file operations)
- `schemas/` - Request/response models
- `prompts/` - System prompts for different agent behaviors

**Frontend**:
- `useAgentStream.ts` - SSE (Server-Sent Events) streaming hook
- `ChatPanel.tsx` - Main AI chat interface
- `MessageList.tsx` - Message rendering with tool results
- `ToolResultCard.tsx` - Display tool execution results

### File Search

Global file search functionality for quickly finding files within the current project.

**Components**:
- `FileSearchInput` - Search input with debounce and IME support
- `SearchResultsDropdown` - Keyboard-navigable results dropdown
- `FileTypeFilter` - File type filtering dropdown

**Hook**: `useFileSearch`
- Client-side search with fuzzy matching
- 300ms debounce
- Max 50 results
- Case-insensitive

**Context**: `FileSearchContext`
- Global search state management
- Keyboard shortcut support

**Trigger**: Cmd+K (Mac) / Ctrl+K (Windows/Linux)

**Scope**: Current project files

**Matching**: Fuzzy, case-insensitive title search with relevance ranking (exact > prefix > contains)

**Features**:
- Real-time search with debouncing
- File type filtering (outline, draft, character, lore, material)
- Keyboard navigation (ArrowUp/Down, Enter, Escape)
- Global keyboard shortcut
- Mobile-responsive design
- i18n support (EN/ZH)
- IME composition support for Chinese input

## Key Configuration Files

### Backend Configuration

**Environment (.env)** - Only one LLM API key required:
```
# Required for Agent/LLM calls — all other settings have sensible defaults
DEEPSEEK_API_KEY=your-deepseek-api-key
```

Database defaults to SQLite (`./zenstory.db`). JWT secret auto-generated in development. See `apps/server/.env.example` for all options.

### Frontend Configuration

**Build Configuration** (`vite.config.ts`):
- Code splitting for optimal caching (react-vendor, ui-vendor, state-vendor, editor-vendor)
- Sitemap generation for SEO

**Linting** (`eslint.config.js`):
- TypeScript ESLint with React Hooks and React Refresh rules

## API Structure

All API routes are prefixed with `/api/v1/`:

**Authentication** (`api/auth.py`):
- `POST /register` - User registration
- `POST /login` - Login with email/password
- `POST /auth/google` - Google OAuth
- `POST /refresh` - Refresh access token

**Projects** (`api/projects.py`):
- `GET/POST /projects` - List/create projects
- `GET/PUT/DELETE /projects/{id}` - Project CRUD

**Files** (`api/files.py`):
- `GET/POST /projects/{id}/files` - List/create files
- `GET /projects/{id}/file-tree` - Get file tree structure
- `GET/PUT/DELETE /files/{id}` - File CRUD
- `POST /files/{id}/move` - Move file in tree

**Versions** (`api/versions.py`):
- `GET/POST /files/{id}/versions` - List/create versions
- `GET /files/{id}/versions/compare` - Compare two versions
- `POST /files/{id}/versions/{num}/rollback` - Rollback to version

**Agent** (`api/agent.py`):
- `POST /agent/chat` - AI chat with streaming (SSE)

**Chat** (`api/chat.py`):
- `GET/POST /projects/{id}/chats` - Chat session management
- `DELETE /chats/{id}` - Delete chat session

**Export** (`api/export.py`):
- `POST /projects/{id}/export` - Export project (docx, txt, md)

**Voice** (`api/voice.py`):
- `POST /voice/asr` - Speech-to-text (Tencent Cloud ASR)

## Important Implementation Notes

### File Model

The unified file model (`models/file_model.py`) supports multiple file types via `file_type` field:
- `outline` - Story/chapter outlines with hierarchical structure
- `draft` - Draft content with auto-save
- `character` - Character profiles with traits
- `lore` - World-building entries with categories

Files support parent-child relationships via `parent_id` for tree structure.

### Version History

Every file change creates a version snapshot (`services/file_version_service.py`):
- Automatic version creation on file update
- Diff comparison between versions
- One-click rollback to any version

### Agent Context System

The agent uses a sophisticated context assembly system (`agent/context/`):
- `assembler.py` - Collects relevant files/content
- `budget.py` - Manages token budget for context
- `prioritizer.py` - Prioritizes most relevant content

### Authentication Flow

1. JWT-based authentication with access + refresh tokens
2. Google OAuth support for social login
3. Protected routes use `Depends(get_current_user)` dependency
4. Frontend stores tokens in localStorage, auto-refreshes expired tokens

### Error Handling

**Backend**: Custom exception handler in `core/error_handler.py`:
- `APIException` - Base exception with status code
- Global handlers for HTTPException, ValidationError, and general exceptions
- Structured error responses with request ID for tracing

**Frontend**: `lib/errorHandler.ts` handles API errors:
- Token refresh on 401 errors
- User-friendly error messages
- Automatic retry logic

## Testing

No automated tests are currently implemented. When adding tests:
- Backend: pytest with TestClient from fastapi.testclient
- Frontend: React Testing Library + Vitest

## Deployment

**Production Architecture**:
- Frontend: Vercel (React + Vite)
- Backend: Railway (FastAPI + PostgreSQL)
- CDN: Cloudflare

See `README.md` for detailed deployment instructions including:
- Environment variable configuration
- Railway deployment steps
- Vercel deployment steps
- Cloudflare DNS/SSL setup

## Common Development Patterns

### Adding a New API Endpoint

1. Create route handler in appropriate `api/*.py` file
2. Keep logic minimal - delegate to `services/` layer
3. Use `Depends(get_session)` for database access
4. Add corresponding function to `apps/web/src/lib/api.ts`
5. Add TypeScript types to `apps/web/src/types/index.ts`

### Adding a New Component

1. Create in `apps/web/src/components/`
2. Use Tailwind CSS for styling (follow existing patterns)
3. Import from `lib/api.ts` for data fetching
4. Use TanStack Query mutations for writes
5. Handle loading/error states

### Database Schema Changes

1. Modify model in `apps/server/models/`
2. Create Alembic migration: `alembic revision --autogenerate -m "description"`
3. Apply migration: `alembic upgrade head`
4. Update TypeScript types in `apps/web/src/types/`

### Agent Tool Development

Tools are in `apps/server/agent/tools/`:
1. Create tool function with type hints
2. Register in `agent/service.py` tool registry
3. Update frontend `ToolResultCard.tsx` to display results

## Decision Notes (required for non-trivial changes)

Design decisions live in `.agents/notes/{proposed,implemented,rejected}/{feature,bug-fix,simplification,architecture,process,testing}/yyyy-mm-dd-topic.md`, following the DeepSeek Harness agent-notes convention (see https://github.com/czm15053/write-notes-like-deepseek). Notes are written in Chinese; section headers stay in English.

1. Before a non-trivial change (behavior, architecture, cross-file contract, process/tooling, testing strategy, on-disk/wire/config format), search `.agents/notes/` for the owning note. Update its facts in place if one exists; otherwise draft a new note under `proposed/` and move it to `implemented/` in the same commit as the code. Mechanical edits (styling, formatting, version bumps, behavior-neutral dependency patches, plain CRUD) need no note.
2. Every note has `## Problem`, `## Decision` (present tense, implemented facts only) or `## Proposal`, `## Alternatives considered` (each rejected option gets its strongest argument first, then why it lost; never invent options), and `## Consequences` (both benefits and costs).
3. Never rewrite a note into the opposite decision. Facts (paths, names, defaults) change in place; a reversed decision gets a new note that links to the old one. Do not append dated "decisions recorded" logs to `DESIGN.md`; each decision becomes its own note.
4. No `INDEX.md`. The folder is the state; search with `rg --hidden .agents/notes/`.
