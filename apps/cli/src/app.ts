import { randomBytes } from 'node:crypto';
import { chmodSync, lstatSync, mkdirSync, readFileSync, renameSync, rmSync, writeFileSync, type Stats } from 'node:fs';
import { basename, dirname, join } from 'node:path';

import {
  bool,
  commandHelp,
  formatOptions,
  GLOBAL_OPTIONS,
  int,
  list,
  parseCommandArgs,
  requirePositionals,
  route,
  str,
  type CommandDef,
  type OptionValues,
} from './args.js';
import { CliError, EXIT, RATE_LIMIT_SUMMARY, ZenstoryClient, type FetchLike } from './client.js';
import {
  API_KEY_PREFIX,
  assertSafeAuth,
  backupDir,
  configPath,
  configPermissionWarning,
  DEFAULT_API_BASE,
  deleteConfig,
  maskKey,
  normalizeApiBase,
  readConfig,
  resolveAuth,
  validateApiBase,
  writeConfig,
  type Env,
} from './config.js';
import { formatKeyValues, formatTable, toJson, truncate } from './output.js';
import { bundledSkillDir, installSkill, SKILL_TARGETS } from './skill.js';

// ==================== Types (mirror apps/server/api/agent_api.py & vector_search.py) ====================

interface Project {
  id: string;
  name: string;
  description: string | null;
  project_type: string | null;
  owner_id: string;
  created_at: string;
  updated_at: string;
}

interface Folder {
  id: string;
  title: string;
  file_type: string;
  order: number;
}

/** POST /agent/projects response: the project plus the default folders created with it. */
interface CreatedProject extends Project {
  folders?: Folder[];
}

interface FileVersion {
  id: string;
  file_id: string;
  project_id: string;
  version_number: number;
  is_base_version: boolean;
  word_count: number;
  char_count: number;
  change_type: string;
  change_source: string;
  change_summary: string | null;
  lines_added: number;
  lines_removed: number;
  created_at: string;
}

interface FileVersionList {
  versions: FileVersion[];
  total: number;
  limit: number;
  offset: number;
  file_id: string;
  file_title: string;
}

interface FileVersionDetail extends FileVersion {
  content: string;
}

interface RollbackResult {
  success: boolean;
  message: string;
  file_id: string;
  restored_version: number;
  new_version_number: number | null;
  snapshot_created: boolean;
  version_quota_exceeded: boolean;
  updated_at: string;
}

interface ZsFile {
  id?: string;
  project_id?: string;
  title?: string;
  content?: string;
  file_type?: string;
  parent_id?: string | null;
  order?: number;
  file_metadata?: string | null;
  created_at?: string;
  updated_at?: string;
  /** Set on create/PUT: content saved, but no version recorded (per-file version quota full). */
  version_quota_exceeded?: boolean;
}

interface FileList {
  files: ZsFile[];
  total: number;
  limit: number;
  offset: number;
}

interface SearchResult {
  id: string;
  title: string;
  file_type: string;
  content: string | null;
  score: number;
  snippet?: string | null;
  line_start?: number | null;
  fused_score?: number | null;
  sources?: string[] | null;
  metadata: Record<string, unknown> | null;
}

interface SearchResponse {
  query: string;
  results: SearchResult[];
  result_count: number;
}

interface WritingContext {
  items: Array<{ type: string; title: string; content_snippet: string; source_file_id: string; relevance: number }>;
  refs: string[];
  total_available: number;
  returned: number;
  token_estimate: number;
}

// ==================== Context ====================

export interface Writer {
  write(chunk: string): unknown;
}

export interface Io {
  stdout: Writer;
  stderr: Writer;
  env: Env;
  fetch?: FetchLike;
  readStdin: () => Promise<string>;
  /** Read a secret from the terminal with echo off. Undefined when stdin is not a TTY. */
  promptSecret?: (prompt: string) => Promise<string>;
  home?: string;
  cwd?: string;
}

interface Ctx {
  io: Io;
  json: boolean;
  version: string;
  client: () => ZenstoryClient;
  print: (text: string) => void;
  printJson: (data: unknown) => void;
  note: (text: string) => void;
}

export const FILE_TYPES = ['outline', 'draft', 'character', 'lore', 'snippet', 'script', 'document', 'folder'] as const;
/** Friendly aliases: the web UI calls snippets "materials" (素材). */
const FILE_TYPE_ALIASES: Record<string, string> = { material: 'snippet', materials: 'snippet' };
const PROJECT_TYPES = ['novel', 'short', 'screenplay'];
const PROJECT_LANGS = ['zh', 'en'];
/** File.order is a 32-bit INTEGER column on the server. */
const MAX_ORDER = 2_147_483_647;
const LIST_FIELDS_DEFAULT = 'id,title,file_type,parent_id,order,updated_at';
const TREE_FIELDS = 'id,title,file_type,parent_id,order';
const PAGE_MAX = 200;
const VERSIONS_PAGE_MAX = 100;
const SCOPE_PROBE_PROJECT_ID = 'zenstory-cli-scope-probe';
const AUTH_SCOPE_DENIED = 'ERR_AUTH_SCOPE_DENIED';

/** Default folder-title language for `projects create`: ZENSTORY_LANG, then LC_ALL, then LANG; zh* → zh, else en. */
export function defaultProjectLang(env: Env): string {
  const locale = [env.ZENSTORY_LANG, env.LC_ALL, env.LANG].map((v) => v?.trim()).find(Boolean) ?? '';
  return locale.toLowerCase().startsWith('zh') ? 'zh' : 'en';
}

const VERSION_QUOTA_WARNING =
  'Warning: the content was saved, but no version was recorded because the plan\'s per-file version quota is full';

export function normalizeFileType(raw: string): string {
  const t = raw.trim().toLowerCase();
  const resolved = FILE_TYPE_ALIASES[t] ?? t;
  if (!(FILE_TYPES as readonly string[]).includes(resolved)) {
    throw new CliError(`Unknown file type "${raw}". Valid types: ${FILE_TYPES.join(', ')} (alias: material → snippet).`, EXIT.USAGE);
  }
  return resolved;
}

function readVersion(): string {
  try {
    const pkg = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8')) as { version?: string };
    return pkg.version ?? '0.0.0';
  } catch {
    return '0.0.0';
  }
}

async function readContent(ctx: Ctx, values: OptionValues): Promise<string | undefined> {
  const file = str(values, 'content-file');
  const inline = str(values, 'content');
  if (file !== undefined && inline !== undefined) {
    throw new CliError('Use either --content-file or --content, not both.', EXIT.USAGE);
  }
  if (file !== undefined) {
    if (file === '-') return ctx.io.readStdin();
    try {
      return readFileSync(file, 'utf8');
    } catch (err) {
      throw new CliError(`Cannot read --content-file ${file}: ${(err as Error).message}`, EXIT.USAGE);
    }
  }
  if (inline !== undefined) return inline === '-' ? ctx.io.readStdin() : inline;
  return undefined;
}

function parseMetadata(values: OptionValues): Record<string, unknown> | undefined {
  const raw = str(values, 'metadata');
  if (raw === undefined) return undefined;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) return parsed as Record<string, unknown>;
  } catch {
    // fall through
  }
  throw new CliError('--metadata must be a JSON object, e.g. \'{"chapter_number": 3}\'.', EXIT.USAGE);
}

function enc(id: string): string {
  return encodeURIComponent(id);
}

/** A 1-based version number given as a positional argument. */
function versionNumber(raw: string): number {
  const n = Number(raw);
  if (!Number.isInteger(n) || n < 1) {
    throw new CliError(`Version number must be a positive integer (got "${raw}"). See \`zenstory files versions <fileId>\`.`, EXIT.USAGE);
  }
  return n;
}

function requireYes(values: OptionValues, what: string): void {
  if (!bool(values, 'yes')) {
    throw new CliError(`Refusing to delete ${what} without --yes. Deletion is a soft delete but hides it from the project.`, EXIT.USAGE);
  }
}

async function fetchAllFiles(
  client: ZenstoryClient,
  projectId: string,
  query: Record<string, string | undefined>,
): Promise<FileList> {
  const files: ZsFile[] = [];
  const seen = new Set<string>();
  let offset = 0;
  let total = 0;
  for (;;) {
    const page = await client.get<FileList>(`/agent/projects/${enc(projectId)}/files`, {
      ...query,
      limit: PAGE_MAX,
      offset,
    });
    // Offset paging can repeat a row when files change between pages; keep the first copy.
    for (const f of page.files) {
      if (f.id !== undefined) {
        if (seen.has(f.id)) continue;
        seen.add(f.id);
      }
      files.push(f);
    }
    total = page.total;
    offset += page.files.length;
    if (page.files.length === 0 || offset >= total) break;
  }
  return { files, total, limit: files.length, offset: 0 };
}

interface TreeNode extends ZsFile {
  children: TreeNode[];
}

function compareCodePoints(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0;
}

export function buildTree(files: ZsFile[]): TreeNode[] {
  const nodes = new Map<string, TreeNode>();
  for (const f of files) if (f.id) nodes.set(f.id, { ...f, children: [] });
  const roots: TreeNode[] = [];
  for (const node of nodes.values()) {
    const parent = node.parent_id ? nodes.get(node.parent_id) : undefined;
    if (parent) parent.children.push(node);
    else roots.push(node);
  }
  // Nodes in a parent cycle (A→B→A, or A→A) are unreachable from any root: cut each cycle at
  // its first unvisited node and show that node as an extra root, so nothing is dropped.
  const visited = new Set<TreeNode>();
  const mark = (n: TreeNode) => {
    if (visited.has(n)) return;
    visited.add(n);
    n.children.forEach(mark);
  };
  roots.forEach(mark);
  for (const node of nodes.values()) {
    if (visited.has(node)) continue;
    const parent = nodes.get(node.parent_id as string);
    if (parent) parent.children = parent.children.filter((c) => c !== node);
    roots.push(node);
    mark(node);
  }
  const sort = (arr: TreeNode[]) => {
    // 同 order 时按标题的码点顺序、再按 id 排序：localeCompare 的结果随运行环境的语言设置变化，
    // 会让同一份数据在不同机器上排出不同的树。
    arr.sort((a, b) => (a.order ?? 0) - (b.order ?? 0) || compareCodePoints(String(a.title), String(b.title)) || compareCodePoints(String(a.id ?? ''), String(b.id ?? '')));
    arr.forEach((n) => sort(n.children));
  };
  sort(roots);
  return roots;
}

function renderTree(nodes: TreeNode[], depth = 0): string[] {
  return nodes.flatMap((n) => [
    `${'  '.repeat(depth)}${n.title}${n.file_type === 'folder' ? '/' : ''}  [${n.file_type}] ${n.id}`,
    ...renderTree(n.children, depth + 1),
  ]);
}

function lstatOrNull(path: string): Stats | null {
  try {
    return lstatSync(path);
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === 'ENOENT') return null;
    throw err;
  }
}

/**
 * Write `content` to `path` with mode 0600 via a temp file in the same directory + rename.
 * Refuses symlinks (never followed) and non-regular files; refuses an existing file unless `force`.
 */
export function writePrivateFile(path: string, content: string, force: boolean): void {
  const st = lstatOrNull(path);
  if (st) {
    if (st.isSymbolicLink()) throw new CliError(`Refusing to write ${path}: it is a symbolic link.`, EXIT.USAGE);
    if (!st.isFile()) throw new CliError(`Refusing to write ${path}: it is not a regular file.`, EXIT.USAGE);
    if (!force) throw new CliError(`${path} already exists. Pass --force to overwrite it.`, EXIT.USAGE);
  }
  const tmp = join(dirname(path), `.${basename(path)}.${process.pid}-${randomBytes(4).toString('hex')}.tmp`);
  try {
    writeFileSync(tmp, content, { encoding: 'utf8', mode: 0o600, flag: 'wx' });
    chmodSync(tmp, 0o600);
    renameSync(tmp, path);
  } catch (err) {
    rmSync(tmp, { force: true });
    throw new CliError(`Cannot write ${path}: ${(err as Error).message}`, EXIT.ERROR);
  }
}

/** Save the current server content before `files put` replaces it; returns the backup path. */
function writeBackup(env: Env, fileId: string, content: string): string {
  const dir = backupDir(env);
  mkdirSync(dir, { recursive: true, mode: 0o700 });
  chmodSync(dir, 0o700);
  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  const path = join(dir, `${fileId.replace(/[^A-Za-z0-9_-]/g, '_')}-${stamp}.md`);
  writePrivateFile(path, content, false);
  return path;
}

function sameTimestamp(a: string | undefined, b: string): boolean {
  if (a === undefined) return false;
  if (a === b) return true;
  // API timestamps can be naive UTC, and Date.parse alone loses sub-ms digits.
  const toMicroseconds = (value: string): bigint | undefined => {
    const match = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,6}))?(Z|[+-]\d{2}:\d{2})?$/.exec(value);
    if (!match) return undefined;
    const fraction = (match[2] ?? '').padEnd(6, '0');
    const milliseconds = Date.parse(`${match[1]}.${fraction.slice(0, 3)}${match[3] ?? 'Z'}`);
    if (Number.isNaN(milliseconds)) return undefined;
    return BigInt(milliseconds) * 1000n + BigInt(fraction.slice(3));
  };
  const ta = toMicroseconds(a);
  return ta !== undefined && ta === toMicroseconds(b);
}

/** Hide anything that looks like an Agent API key in error text. */
export function redactKeys(text: string): string {
  return text.replace(/\beg_[A-Za-z0-9_-]{4,}/g, (m) => maskKey(m));
}

const NOT_LOGGED_IN =
  'Not logged in. Create an Agent API key in ZenStory Settings → Agent, then run ' +
  '`npx -y zenstory login` and paste the key when prompted (or set ZENSTORY_API_KEY).';

function apiBaseHint(err: CliError): CliError {
  return new CliError(
    `The API answered 404 for GET /agent/projects. Is the API base correct? It must include the /api/v1 prefix ` +
      `(e.g. ${DEFAULT_API_BASE}); set it with --api-base or ZENSTORY_API_BASE.`,
    EXIT.USAGE,
    { status: err.status, code: err.code },
  );
}

// ==================== Auth helpers ====================

async function probeScopes(client: ZenstoryClient): Promise<{ read: boolean; write: boolean | null; projectCount: number | null }> {
  let read = false;
  let projectCount: number | null = null;
  try {
    const projects = await client.get<Project[]>('/agent/projects');
    read = true;
    projectCount = projects.length;
  } catch (err) {
    if (!(err instanceof CliError && err.status === 403 && err.code === AUTH_SCOPE_DENIED)) throw err;
  }

  // Side-effect-free write check: PUT on a project id that cannot exist. The scope check runs
  // before the project lookup, so 404 means "write allowed", 403 "lacks required scope" means no.
  let write: boolean | null = null;
  try {
    await client.put(`/agent/projects/${SCOPE_PROBE_PROJECT_ID}`, {});
    write = true;
  } catch (err) {
    if (!(err instanceof CliError)) throw err;
    if (err.status === 404) write = true;
    else if (err.status === 403 && err.code === AUTH_SCOPE_DENIED) write = false;
    else if (err.status === 401) throw err;
    else write = null;
  }
  return { read, write, projectCount };
}

// ==================== Commands ====================

const contentOptions = {
  'content-file': { type: 'string', valueName: 'path', description: 'Read content from a UTF-8 file ("-" = stdin)' },
  content: { type: 'string', valueName: 'text', description: 'Inline content, or "-" to read from stdin' },
} as const;

export const COMMANDS: CommandDef<Ctx>[] = [
  // ---------- auth ----------
  {
    name: 'login',
    summary: 'Validate an Agent API key and save it locally (~/.config/zenstory/config.json, mode 0600).',
    options: {
      key: { type: 'string', valueName: '-', description: 'Read the key from stdin ("-"); without --key you are prompted' },
      'api-base': { type: 'string', valueName: 'url', description: `API base URL (default ${DEFAULT_API_BASE})` },
    },
    details:
      'Create a key in ZenStory Settings → Agent, run `zenstory login` and paste the key when prompted\n' +
      '(input is hidden). Non-interactive: printf %s "$KEY" | zenstory login --key -\n' +
      'Validation calls GET /agent/projects. The saved key is bound to the API base it was saved for;\n' +
      'use --api-base for a self-hosted server (https, or http only for localhost).',
    async run(ctx, values) {
      let key = str(values, 'key');
      if (key === '-') {
        key = (await ctx.io.readStdin()).trim();
      } else if (key === undefined) {
        if (!ctx.io.promptSecret) {
          throw new CliError(
            'Missing API key and stdin is not a terminal, so it cannot be prompted for. ' +
              'Pipe it instead: printf %s "$ZENSTORY_KEY" | zenstory login --key -',
            EXIT.USAGE,
          );
        }
        key = await ctx.io.promptSecret('Paste API key: ');
      } else {
        ctx.note('Warning: a key passed as an argument can end up in your shell history. Prefer `zenstory login` (prompt) or `--key -` (stdin).');
      }
      key = key.trim();
      if (!key) throw new CliError('No API key entered. Usage: zenstory login [--key -] [--api-base URL]', EXIT.USAGE);
      if (!key.startsWith(API_KEY_PREFIX)) {
        throw new CliError(`That does not look like a ZenStory Agent API key (expected prefix "${API_KEY_PREFIX}").`, EXIT.USAGE);
      }
      const stored = readConfig(ctx.io.env);
      const explicitBase = str(values, 'api-base')?.trim() || undefined;
      const envBase = ctx.io.env.ZENSTORY_API_BASE?.trim() || undefined;
      // Only an explicit --api-base (or the base the saved key was already bound to) is persisted.
      const boundBase = explicitBase ?? (stored.apiBase?.trim() || undefined);
      const apiBase = validateApiBase(boundBase ?? DEFAULT_API_BASE);
      if (envBase && !explicitBase && normalizeApiBase(envBase) !== apiBase) {
        throw new CliError(
          `ZENSTORY_API_BASE is set to ${normalizeApiBase(envBase)}, but the key would be saved for ${apiBase}. ` +
            `Run \`zenstory login --api-base ${normalizeApiBase(envBase)}\` to bind the key to that server, or unset ZENSTORY_API_BASE.`,
          EXIT.USAGE,
        );
      }
      const client = new ZenstoryClient({ apiKey: key, apiBase, fetch: ctx.io.fetch, userAgent: `zenstory-cli/${ctx.version}` });

      let projectCount: number | null = null;
      try {
        projectCount = (await client.get<Project[]>('/agent/projects')).length;
      } catch (err) {
        if (err instanceof CliError && err.status === 404) throw apiBaseHint(err);
        // Only the stable scope-denial code proves that authentication succeeded.
        // Other 403s can mean an inactive user or an unrelated permission boundary.
        if (!(err instanceof CliError && err.status === 403 && err.code === AUTH_SCOPE_DENIED)) throw err;
        ctx.note('Warning: key is valid but lacks the "read" scope; most commands will fail.');
      }

      const path = writeConfig(boundBase ? { apiKey: key, apiBase } : { apiKey: key }, ctx.io.env);
      if (ctx.io.env.ZENSTORY_API_KEY) ctx.note('Note: ZENSTORY_API_KEY is set and overrides the saved key.');
      if (ctx.json) {
        ctx.printJson({ ok: true, apiBase, key: maskKey(key), configPath: path, projectCount });
      } else {
        ctx.print(
          `Logged in to ${apiBase} with key ${maskKey(key)}.` +
            (projectCount !== null ? ` ${projectCount} project(s) visible.` : '') +
            `\nSaved to ${path}`,
        );
      }
    },
  },
  {
    name: 'logout',
    summary: 'Remove the locally saved API key.',
    async run(ctx) {
      const removed = deleteConfig(ctx.io.env);
      const envSet = Boolean(ctx.io.env.ZENSTORY_API_KEY);
      if (ctx.json) {
        ctx.printJson({ ok: true, removed, configPath: configPath(ctx.io.env), envKeyStillSet: envSet });
        return;
      }
      ctx.print(removed ? `Removed ${configPath(ctx.io.env)}` : 'No saved key found.');
      if (envSet) ctx.note('Note: ZENSTORY_API_KEY is still set in your environment.');
    },
  },
  {
    name: 'whoami',
    summary: 'Show the API base, masked key, and which scopes (read/write) the key has.',
    details:
      'Checks: GET /agent/projects (read) and a no-op PUT /agent/projects/<nonexistent> (write; 404 = allowed).\n' +
      'Exits 3 when no key is configured or the key is rejected.',
    async run(ctx) {
      const auth = resolveAuth(ctx.io.env);
      if (!auth.apiKey) throw new CliError(NOT_LOGGED_IN, EXIT.AUTH);
      let scopes: Awaited<ReturnType<typeof probeScopes>>;
      try {
        scopes = await probeScopes(ctx.client());
      } catch (err) {
        if (err instanceof CliError && err.status === 404) throw apiBaseHint(err);
        throw err;
      }
      const result = {
        apiBase: auth.apiBase,
        apiBaseSource: auth.baseSource,
        key: maskKey(auth.apiKey),
        keySource: auth.keySource,
        configPath: configPath(ctx.io.env),
        scopes: { read: scopes.read, write: scopes.write },
        projectCount: scopes.projectCount,
      };
      if (ctx.json) {
        ctx.printJson(result);
        return;
      }
      const yn = (v: boolean | null) => (v === null ? 'unknown' : v ? 'yes' : 'no');
      ctx.print(
        formatKeyValues([
          ['API base', `${auth.apiBase} (${auth.baseSource})`],
          ['Key', `${maskKey(auth.apiKey)} (${auth.keySource === 'env' ? 'ZENSTORY_API_KEY' : configPath(ctx.io.env)})`],
          ['Read scope', yn(scopes.read)],
          ['Write scope', yn(scopes.write)],
          ['Projects', scopes.projectCount ?? '-'],
        ]),
      );
    },
  },

  // ---------- projects ----------
  {
    name: 'projects list',
    summary: 'List projects visible to the key.  [GET /agent/projects]',
    async run(ctx) {
      const projects = await ctx.client().get<Project[]>('/agent/projects');
      if (ctx.json) return ctx.printJson(projects);
      if (projects.length === 0) return ctx.print('No projects. Create one with `zenstory projects create --name ...`.');
      ctx.print(
        formatTable(projects, [
          { header: 'ID', get: (p) => p.id },
          { header: 'TYPE', get: (p) => p.project_type },
          { header: 'UPDATED', get: (p) => p.updated_at?.slice(0, 19) },
          { header: 'NAME', get: (p) => p.name, max: 50 },
        ]),
      );
    },
  },
  {
    name: 'projects get',
    summary: 'Show one project.  [GET /agent/projects/{id}]',
    args: ['<projectId>'],
    async run(ctx, _values, pos) {
      requirePositionals(this, pos, 1);
      const p = await ctx.client().get<Project>(`/agent/projects/${enc(pos[0])}`);
      if (ctx.json) return ctx.printJson(p);
      ctx.print(
        formatKeyValues([
          ['ID', p.id],
          ['Name', p.name],
          ['Type', p.project_type],
          ['Description', p.description],
          ['Created', p.created_at],
          ['Updated', p.updated_at],
        ]),
      );
    },
  },
  {
    name: 'projects create',
    summary: 'Create a project.  [POST /agent/projects]',
    options: {
      name: { type: 'string', valueName: 'name', description: 'Project name (1-100 chars, required)' },
      description: { type: 'string', valueName: 'text', description: 'Description (≤500 chars)' },
      type: { type: 'string', valueName: 'type', description: 'novel | short | screenplay (default novel)' },
      lang: {
        type: 'string',
        valueName: 'zh|en',
        description: 'Language of the default folder titles (default: from ZENSTORY_LANG, LC_ALL or LANG; zh* → zh, else en)',
      },
    },
    details:
      'The project gets the same default folders as in the web app (e.g. <projectId>-draft-folder,\n' +
      '<projectId>-outline-folder, <projectId>-character-folder); they are printed after creation.\n' +
      'Fails with 402 when the plan\'s project limit is reached, and with 403 when the API key is limited\n' +
      'to specific projects (such a key could not access the new project).',
    async run(ctx, values) {
      const name = str(values, 'name');
      if (!name) throw new CliError('Missing --name.', EXIT.USAGE);
      const type = str(values, 'type') ?? 'novel';
      if (!PROJECT_TYPES.includes(type)) throw new CliError(`--type must be one of: ${PROJECT_TYPES.join(', ')}`, EXIT.USAGE);
      const lang = str(values, 'lang') ?? defaultProjectLang(ctx.io.env);
      if (!PROJECT_LANGS.includes(lang)) throw new CliError(`--lang must be one of: ${PROJECT_LANGS.join(', ')}`, EXIT.USAGE);
      const p = await ctx.client().post<CreatedProject>(
        '/agent/projects',
        { name, description: str(values, 'description'), project_type: type },
        { 'Accept-Language': lang },
      );
      if (ctx.json) return ctx.printJson(p);
      ctx.print(`Created project "${p.name}" (${p.project_type}) ${p.id}`);
      if (p.folders?.length) {
        ctx.print(`Folders:\n${p.folders.map((f) => `  ${f.title}/  ${f.id}`).join('\n')}`);
      }
    },
  },
  {
    name: 'projects update',
    summary: 'Rename a project or change its description.  [PUT /agent/projects/{id}]',
    args: ['<projectId>'],
    options: {
      name: { type: 'string', valueName: 'name', description: 'New name' },
      description: { type: 'string', valueName: 'text', description: 'New description' },
    },
    async run(ctx, values, pos) {
      requirePositionals(this, pos, 1);
      const name = str(values, 'name');
      const description = str(values, 'description');
      if (name === undefined && description === undefined) {
        throw new CliError('Nothing to update: pass --name and/or --description.', EXIT.USAGE);
      }
      const p = await ctx.client().put<Project>(`/agent/projects/${enc(pos[0])}`, { name, description });
      if (ctx.json) return ctx.printJson(p);
      ctx.print(`Updated project "${p.name}" ${p.id}`);
    },
  },
  {
    name: 'projects delete',
    summary: 'Soft-delete a project (requires --yes).  [DELETE /agent/projects/{id}]',
    args: ['<projectId>'],
    options: { yes: { type: 'boolean', short: 'y', description: 'Confirm deletion' } },
    async run(ctx, values, pos) {
      requirePositionals(this, pos, 1);
      requireYes(values, `project ${pos[0]}`);
      const res = await ctx.client().delete<{ message: string }>(`/agent/projects/${enc(pos[0])}`);
      if (ctx.json) return ctx.printJson({ ok: true, id: pos[0], ...res });
      ctx.print(`Deleted project ${pos[0]}`);
    },
  },

  // ---------- files ----------
  {
    name: 'files list',
    summary: 'List files in a project (content omitted by default).  [GET /agent/projects/{id}/files]',
    args: ['<projectId>'],
    options: {
      type: { type: 'string', valueName: 'type', description: `Filter by file type: ${FILE_TYPES.join('|')}` },
      parent: { type: 'string', valueName: 'id', description: 'Only direct children of this folder id' },
      fields: { type: 'string', valueName: 'csv', description: `Fields to return (default ${LIST_FIELDS_DEFAULT}; add "content" for bodies)` },
      limit: { type: 'string', valueName: 'n', description: 'Page size 1-200 (default 50)' },
      offset: { type: 'string', valueName: 'n', description: 'Pagination offset (default 0)' },
      all: { type: 'boolean', description: 'Fetch every page (cannot be combined with --limit/--offset)' },
    },
    async run(ctx, values, pos) {
      requirePositionals(this, pos, 1);
      if (bool(values, 'all') && (str(values, 'limit') !== undefined || str(values, 'offset') !== undefined)) {
        throw new CliError('--all fetches every page; do not combine it with --limit or --offset.', EXIT.USAGE);
      }
      const typeRaw = str(values, 'type');
      const query = {
        file_type: typeRaw ? normalizeFileType(typeRaw) : undefined,
        parent_id: str(values, 'parent'),
        fields: str(values, 'fields') ?? LIST_FIELDS_DEFAULT,
      };
      const client = ctx.client();
      const page = bool(values, 'all')
        ? await fetchAllFiles(client, pos[0], query)
        : await client.get<FileList>(`/agent/projects/${enc(pos[0])}/files`, {
            ...query,
            limit: int(values, 'limit', 1, PAGE_MAX),
            offset: int(values, 'offset', 0, Number.MAX_SAFE_INTEGER),
          });
      if (ctx.json) return ctx.printJson(page);
      if (page.files.length === 0) return ctx.print(`No files (total ${page.total}).`);
      ctx.print(
        formatTable(page.files, [
          { header: 'ID', get: (f) => f.id },
          { header: 'TYPE', get: (f) => f.file_type },
          { header: 'PARENT', get: (f) => f.parent_id ?? '-' },
          { header: 'TITLE', get: (f) => f.title, max: 60 },
        ]),
      );
      const shown = page.offset + page.files.length;
      if (shown < page.total) ctx.print(`\nShowing ${page.offset + 1}-${shown} of ${page.total}. Use --offset ${shown} or --all.`);
      else ctx.print(`\n${page.total} file(s).`);
    },
  },
  {
    name: 'files tree',
    summary: 'Show the project folder tree (fetches all files without content).  [GET /agent/projects/{id}/files]',
    args: ['<projectId>'],
    async run(ctx, _values, pos) {
      requirePositionals(this, pos, 1);
      const all = await fetchAllFiles(ctx.client(), pos[0], { fields: TREE_FIELDS });
      const tree = buildTree(all.files);
      if (ctx.json) return ctx.printJson({ tree, total: all.total });
      ctx.print(tree.length ? renderTree(tree).join('\n') : 'Project is empty.');
    },
  },
  {
    name: 'files get',
    summary: 'Print a file\'s content (use --json for the full object).  [GET /agent/files/{id}]',
    args: ['<fileId>'],
    options: {
      output: {
        type: 'string',
        short: 'o',
        valueName: 'path',
        description: 'Write the content to a new file (mode 0600) instead of stdout; symlinks are refused',
      },
      force: { type: 'boolean', description: 'Allow -o to overwrite an existing regular file' },
      fields: { type: 'string', valueName: 'csv', description: 'Only return these fields (e.g. id,title,updated_at)' },
    },
    async run(ctx, values, pos) {
      requirePositionals(this, pos, 1);
      const file = await ctx.client().get<ZsFile>(`/agent/files/${enc(pos[0])}`, { fields: str(values, 'fields') });
      const output = str(values, 'output');
      if (output !== undefined) {
        if (typeof file.content !== 'string') throw new CliError('--output needs the content field.', EXIT.USAGE);
        writePrivateFile(output, file.content, bool(values, 'force'));
        const summary = { id: file.id, title: file.title, updated_at: file.updated_at, path: output, chars: file.content.length };
        if (ctx.json) return ctx.printJson(summary);
        return ctx.print(`Wrote ${summary.chars} chars of "${file.title}" to ${output}`);
      }
      if (ctx.json) return ctx.printJson(file);
      if (typeof file.content === 'string') {
        ctx.io.stdout.write(file.content);
        if (file.content && !file.content.endsWith('\n')) ctx.io.stdout.write('\n');
        return;
      }
      ctx.print(formatKeyValues(Object.entries(file)));
    },
  },
  {
    name: 'files create',
    summary: 'Create a file (chapter, outline, character, lore, folder...).  [POST /agent/projects/{id}/files]',
    args: ['<projectId>'],
    options: {
      title: { type: 'string', valueName: 'title', description: 'File title (required)' },
      type: { type: 'string', valueName: 'type', description: `${FILE_TYPES.join('|')} (default draft)` },
      parent: { type: 'string', valueName: 'id', description: 'Parent folder id (a folder in the same project)' },
      order: { type: 'string', valueName: 'n', description: 'Position among siblings (integer ≥ 0; default: after the last sibling)' },
      ...contentOptions,
      metadata: { type: 'string', valueName: 'json', description: 'Metadata JSON object, e.g. \'{"chapter_number":3}\'' },
    },
    details:
      'Without --order the file goes after its last sibling, or takes the chapter number found in the title/metadata.\n' +
      'draft/outline/script files with chapter-like titles (第N章 / Chapter N) always sort by N, whatever --order says.',
    async run(ctx, values, pos) {
      requirePositionals(this, pos, 1);
      const title = str(values, 'title');
      if (!title) throw new CliError('Missing --title.', EXIT.USAGE);
      const typeRaw = str(values, 'type');
      const body = {
        title,
        file_type: typeRaw ? normalizeFileType(typeRaw) : 'draft',
        content: (await readContent(ctx, values)) ?? '',
        parent_id: str(values, 'parent'),
        order: int(values, 'order', 0, MAX_ORDER),
        metadata: parseMetadata(values),
      };
      const file = await ctx.client().post<ZsFile>(`/agent/projects/${enc(pos[0])}/files`, body);
      if (file.version_quota_exceeded) ctx.note(`${VERSION_QUOTA_WARNING}.`);
      if (ctx.json) return ctx.printJson(file);
      ctx.print(`Created ${file.file_type} "${file.title}" ${file.id} (${file.content?.length ?? 0} chars)`);
    },
  },
  {
    name: 'files put',
    summary: 'Replace a file\'s content, title and/or order.  [PUT /agent/files/{id}]',
    args: ['<fileId>'],
    options: {
      ...contentOptions,
      title: { type: 'string', valueName: 'title', description: 'New title' },
      order: { type: 'string', valueName: 'n', description: 'New position among siblings (integer ≥ 0)' },
      'if-updated-at': {
        type: 'string',
        valueName: 'ts',
        description: 'Refuse unless the server copy still has this updated_at (from your last `files get --json`)',
      },
      'allow-shrink': { type: 'boolean', description: 'Allow new content shorter than 50% of the current content' },
      'allow-empty': { type: 'boolean', description: 'Allow replacing content with an empty string' },
    },
    details:
      'The content you send REPLACES the whole file. Safe workflow:\n' +
      '  1. zenstory files get <fileId> --json            (note updated_at)\n' +
      '  2. zenstory files get <fileId> -o <path>          (edit <path>)\n' +
      '  3. zenstory files put <fileId> --content-file <path> --if-updated-at <updated_at>\n' +
      'The server records a version for every content change unless the plan\'s per-file version quota is\n' +
      'full (then a warning is printed and version_quota_exceeded is true in --json): undo with\n' +
      '`files versions <fileId>` and `files rollback <fileId> <n> --yes`. As a second net, the current server\n' +
      'content is also saved to $XDG_CACHE_HOME/zenstory/backups (default ~/.cache/zenstory/backups,\n' +
      'mode 0600) and the path is printed; with the quota full, that backup is the only copy of the old content.',
    async run(ctx, values, pos) {
      requirePositionals(this, pos, 1);
      const content = await readContent(ctx, values);
      const title = str(values, 'title');
      const order = int(values, 'order', 0, MAX_ORDER);
      const ifUpdatedAt = str(values, 'if-updated-at');
      if (content === undefined && title === undefined && order === undefined) {
        throw new CliError('Nothing to update: pass --content-file, --content (or "-" for stdin), --title and/or --order.', EXIT.USAGE);
      }
      const allowEmpty = bool(values, 'allow-empty');
      if (content !== undefined && content.trim() === '' && !allowEmpty) {
        throw new CliError('Refusing to replace file content with an empty string (pass --allow-empty if intended).', EXIT.USAGE);
      }

      const client = ctx.client();
      const current =
        content !== undefined || ifUpdatedAt !== undefined
          ? await client.get<ZsFile>(`/agent/files/${enc(pos[0])}`)
          : undefined;
      if (ifUpdatedAt !== undefined && !sameTimestamp(current?.updated_at, ifUpdatedAt)) {
        throw new CliError(
          `File changed on the server (updated_at ${current?.updated_at ?? 'unknown'}, expected ${ifUpdatedAt}). ` +
            'Re-read it with `files get`, merge your edits, and retry with the new updated_at.',
          EXIT.ERROR,
          { code: 'STALE_WRITE' },
        );
      }
      const oldContent = current?.content ?? '';
      if (
        content !== undefined &&
        content.length < oldContent.length * 0.5 &&
        !bool(values, 'allow-shrink') &&
        !(allowEmpty && content.trim() === '')
      ) {
        throw new CliError(
          `Refusing to shrink "${current?.title ?? pos[0]}" from ${oldContent.length} to ${content.length} chars ` +
            '(less than 50%). `files put` replaces the whole file; pass --allow-shrink if this is intended.',
          EXIT.USAGE,
        );
      }

      let backupPath: string | null = null;
      if (content !== undefined && oldContent !== '') backupPath = writeBackup(ctx.io.env, pos[0], oldContent);

      const file = await client.put<ZsFile>(`/agent/files/${enc(pos[0])}`, {
        title, content, order, base_updated_at: ifUpdatedAt,
      });
      if (file.version_quota_exceeded) {
        ctx.note(`${VERSION_QUOTA_WARNING}${backupPath ? `; the previous content is only in ${backupPath}` : ''}.`);
      }
      if (ctx.json) return ctx.printJson({ ...file, backupPath });
      ctx.print(`Updated ${file.file_type} "${file.title}" ${file.id} (${file.content?.length ?? 0} chars)`);
      if (backupPath) ctx.print(`Previous content saved to ${backupPath}`);
    },
  },
  {
    name: 'files delete',
    summary: 'Soft-delete a file (requires --yes).  [DELETE /agent/files/{id}]',
    args: ['<fileId>'],
    options: { yes: { type: 'boolean', short: 'y', description: 'Confirm deletion' } },
    async run(ctx, values, pos) {
      requirePositionals(this, pos, 1);
      requireYes(values, `file ${pos[0]}`);
      const res = await ctx.client().delete<{ message: string }>(`/agent/files/${enc(pos[0])}`);
      if (ctx.json) return ctx.printJson({ ok: true, id: pos[0], ...res });
      ctx.print(`Deleted file ${pos[0]}`);
    },
  },
  {
    name: 'files move',
    summary: 'Move a file into a folder (or to the project root).  [POST /agent/files/{id}/move]',
    args: ['<fileId>'],
    options: {
      parent: { type: 'string', valueName: 'folderId|root', description: 'Target folder id in the same project, or "root" (required)' },
      order: { type: 'string', valueName: 'n', description: 'New position among the new siblings (integer ≥ 0)' },
    },
    details: 'The target must be a folder of the same project; a folder cannot be moved into itself or its subfolders.',
    async run(ctx, values, pos) {
      requirePositionals(this, pos, 1);
      const parent = str(values, 'parent');
      if (!parent) throw new CliError('Missing --parent <folderId|root>.', EXIT.USAGE);
      const parentId = parent === 'root' ? null : parent;
      const file = await ctx.client().post<ZsFile>(`/agent/files/${enc(pos[0])}/move`, {
        parent_id: parentId,
        order: int(values, 'order', 0, MAX_ORDER),
      });
      if (ctx.json) return ctx.printJson(file);
      ctx.print(`Moved ${file.file_type} "${file.title}" ${file.id} to ${file.parent_id ?? 'the project root'} (order ${file.order})`);
    },
  },
  {
    name: 'files versions',
    summary: 'List a file\'s version history, newest first (no content).  [GET /agent/files/{id}/versions]',
    args: ['<fileId>'],
    options: {
      limit: { type: 'string', valueName: 'n', description: `Page size 1-${VERSIONS_PAGE_MAX} (default 50)` },
      offset: { type: 'string', valueName: 'n', description: 'Pagination offset (default 0)' },
      'include-auto-save': { type: 'boolean', description: 'Also list the web editor\'s auto-save versions' },
    },
    async run(ctx, values, pos) {
      requirePositionals(this, pos, 1);
      const res = await ctx.client().get<FileVersionList>(`/agent/files/${enc(pos[0])}/versions`, {
        limit: int(values, 'limit', 1, VERSIONS_PAGE_MAX),
        offset: int(values, 'offset', 0, Number.MAX_SAFE_INTEGER),
        include_auto_save: bool(values, 'include-auto-save') || undefined,
      });
      if (ctx.json) return ctx.printJson(res);
      if (res.versions.length === 0) return ctx.print(`No versions of "${res.file_title}" (total ${res.total}).`);
      ctx.print(
        formatTable(res.versions, [
          { header: 'VERSION', get: (v) => v.version_number },
          { header: 'CREATED', get: (v) => v.created_at?.slice(0, 19) },
          { header: 'TYPE', get: (v) => v.change_type },
          { header: 'WORDS', get: (v) => v.word_count },
          { header: '+/-', get: (v) => `+${v.lines_added}/-${v.lines_removed}` },
          { header: 'SUMMARY', get: (v) => v.change_summary ?? '', max: 50 },
        ]),
      );
      const shown = res.offset + res.versions.length;
      if (shown < res.total) ctx.print(`\nShowing ${res.offset + 1}-${shown} of ${res.total}. Use --offset ${shown}.`);
      else ctx.print(`\n${res.total} version(s) of "${res.file_title}".`);
    },
  },
  {
    name: 'files version',
    summary: 'Print the content of one version (use --json for metadata too).  [GET /agent/files/{id}/versions/{n}]',
    args: ['<fileId>', '<n>'],
    options: {
      output: {
        type: 'string',
        short: 'o',
        valueName: 'path',
        description: 'Write the content to a new file (mode 0600) instead of stdout; symlinks are refused',
      },
      force: { type: 'boolean', description: 'Allow -o to overwrite an existing regular file' },
    },
    async run(ctx, values, pos) {
      requirePositionals(this, pos, 2);
      const n = versionNumber(pos[1]);
      const version = await ctx.client().get<FileVersionDetail>(`/agent/files/${enc(pos[0])}/versions/${n}`);
      const output = str(values, 'output');
      if (output !== undefined) {
        writePrivateFile(output, version.content, bool(values, 'force'));
        const summary = { file_id: version.file_id, version_number: version.version_number, path: output, chars: version.content.length };
        if (ctx.json) return ctx.printJson(summary);
        return ctx.print(`Wrote ${summary.chars} chars of version ${n} to ${output}`);
      }
      if (ctx.json) return ctx.printJson(version);
      ctx.io.stdout.write(version.content);
      if (version.content && !version.content.endsWith('\n')) ctx.io.stdout.write('\n');
    },
  },
  {
    name: 'files rollback',
    summary: 'Restore a file to an earlier version (requires --yes).  [POST /agent/files/{id}/versions/{n}/rollback]',
    args: ['<fileId>', '<n>'],
    options: { yes: { type: 'boolean', short: 'y', description: 'Confirm replacing the current content' } },
    details:
      'Replaces the current content with version <n>. History is kept: the restored content becomes a new\n' +
      'version (unless the per-file version quota is full), so a rollback can itself be undone.',
    async run(ctx, values, pos) {
      requirePositionals(this, pos, 2);
      const n = versionNumber(pos[1]);
      if (!bool(values, 'yes')) {
        throw new CliError(`Refusing to replace the content of ${pos[0]} with version ${n} without --yes.`, EXIT.USAGE);
      }
      const res = await ctx.client().post<RollbackResult>(`/agent/files/${enc(pos[0])}/versions/${n}/rollback`, {});
      if (ctx.json) return ctx.printJson(res);
      ctx.print(
        res.new_version_number !== null
          ? `Restored ${res.file_id} to version ${res.restored_version}; saved as new version ${res.new_version_number}.`
          : res.version_quota_exceeded
            ? `Restored ${res.file_id} to version ${res.restored_version}; no new version was recorded (per-file version quota full).`
            : `Restored ${res.file_id} to version ${res.restored_version}; no snapshot was recorded.`,
      );
    },
  },

  // ---------- retrieval ----------
  {
    name: 'search',
    summary: 'Hybrid (semantic + keyword) search inside a project.  [POST /agent/projects/{id}/search]',
    args: ['<projectId>', '<query...>'],
    options: {
      limit: { type: 'string', valueName: 'n', description: 'Max results 1-50 (default 10)' },
      type: { type: 'string', multiple: true, valueName: 'type', description: 'Restrict to file type(s); repeatable or comma-separated' },
      content: { type: 'boolean', description: 'Include full content of each hit (larger output)' },
    },
    async run(ctx, values, pos) {
      requirePositionals(this, pos, 2);
      const query = pos.slice(1).join(' ');
      const types = list(values, 'type').map(normalizeFileType);
      const res = await ctx.client().post<SearchResponse>(`/agent/projects/${enc(pos[0])}/search`, {
        query,
        top_k: int(values, 'limit', 1, 50),
        file_types: types.length ? types : undefined,
        include_content: bool(values, 'content') || undefined,
      });
      if (ctx.json) return ctx.printJson(res);
      if (res.results.length === 0) return ctx.print('No results. (New files are indexed in the background; retry shortly.)');
      const lines = res.results.map((r, i) => {
        const where = r.line_start != null ? ` line ${r.line_start}` : '';
        const snippet = r.snippet ?? r.content ?? '';
        return `${i + 1}. [${r.file_type}] ${r.title}  ${r.id}${where}  score=${r.score.toFixed(3)}` +
          (snippet ? `\n   ${truncate(snippet.replace(/\s+/g, ' '), 160)}` : '');
      });
      ctx.print(lines.join('\n'));
    },
  },
  {
    name: 'context',
    summary: 'AI-assembled writing context (relevant chapters, characters, lore).  [GET /agent/projects/{id}/writing-context]',
    args: ['<projectId>'],
    options: {
      file: { type: 'string', valueName: 'id', description: 'Focus file id (e.g. the chapter being written)' },
      query: { type: 'string', valueName: 'text', description: 'What you are about to write; steers retrieval' },
      'max-items': { type: 'string', valueName: 'n', description: 'Max items 1-30 (default 10)' },
    },
    details: 'Snippets are capped at 500 chars each; use `files get <source_file_id>` for full text.',
    async run(ctx, values, pos) {
      requirePositionals(this, pos, 1);
      const res = await ctx.client().get<WritingContext>(`/agent/projects/${enc(pos[0])}/writing-context`, {
        file_id: str(values, 'file'),
        query: str(values, 'query'),
        max_items: int(values, 'max-items', 1, 30),
      });
      if (ctx.json) return ctx.printJson(res);
      if (res.items.length === 0) return ctx.print('No context items.');
      const blocks = res.items.map(
        (it, i) =>
          `${i + 1}. [${it.type}] ${it.title}  ${it.source_file_id}  relevance=${Number(it.relevance).toFixed(2)}\n` +
          `   ${truncate((it.content_snippet ?? '').replace(/\s+/g, ' '), 300)}`,
      );
      ctx.print(`${blocks.join('\n')}\n\nReturned ${res.returned} of ${res.total_available} item(s), ~${res.token_estimate} tokens.`);
    },
  },

  // ---------- skill ----------
  {
    name: 'skill install',
    summary: 'Install the bundled ZenStory Agent Skill into an agent\'s skills directory.',
    options: {
      target: {
        type: 'string',
        valueName: 'target',
        description: `${Object.keys(SKILL_TARGETS).join(' | ')} | <skills dir> (default claude)`,
      },
      force: { type: 'boolean', description: 'Replace a previous ZenStory skill installation' },
    },
    details:
      'Targets: claude → ~/.claude/skills, codex/agents → ~/.agents/skills,\n' +
      'openclaw → ~/.openclaw/skills ($OPENCLAW_STATE_DIR/skills when set).\n' +
      'A directory target is treated as a skills root; the skill lands in <dir>/zenstory.\n' +
      '--force only replaces a directory that holds a previous install (SKILL.md named zenstory + references/).',
    async run(ctx, values) {
      const result = installSkill({
        target: str(values, 'target') ?? 'claude',
        force: bool(values, 'force'),
        home: ctx.io.home,
        cwd: ctx.io.cwd,
        env: ctx.io.env,
      });
      if (ctx.json) return ctx.printJson({ ok: true, ...result });
      ctx.print(`${result.replaced ? 'Replaced' : 'Installed'} ZenStory skill at ${result.destination}`);
    },
  },
  {
    name: 'skill path',
    summary: 'Print the location of the bundled skill directory.',
    async run(ctx) {
      const dir = bundledSkillDir();
      if (ctx.json) return ctx.printJson({ path: dir });
      ctx.print(dir);
    },
  },
];

// ==================== Help ====================

function rootHelp(version: string): string {
  const groups: Array<[string, string[]]> = [
    ['Auth', ['login', 'logout', 'whoami']],
    ['Projects', ['projects list', 'projects get', 'projects create', 'projects update', 'projects delete']],
    ['Files', ['files list', 'files tree', 'files get', 'files create', 'files put', 'files move', 'files delete']],
    ['Versions', ['files versions', 'files version', 'files rollback']],
    ['Retrieval', ['search', 'context']],
    ['Agent skill', ['skill install', 'skill path']],
  ];
  const byName = new Map(COMMANDS.map((c) => [c.name, c]));
  const usage = (c: CommandDef<Ctx>) => `${c.name}${c.args?.length ? ` ${c.args.join(' ')}` : ''}`;
  const width = Math.max(...COMMANDS.map((c) => usage(c).length));
  const sections = groups.map(([title, names]) => {
    const rows = names.map((n) => {
      const c = byName.get(n)!;
      return `  ${usage(c).padEnd(width)}  ${c.summary.split('  [')[0]}`;
    });
    return `${title}:\n${rows.join('\n')}`;
  });
  return [
    `zenstory ${version} — operate ZenStory novel projects from the command line (Agent API).`,
    '',
    'Usage: zenstory <command> [args] [options]',
    '',
    ...sections.flatMap((s) => [s, '']),
    'Global options:',
    formatOptions({ ...GLOBAL_OPTIONS, version: { type: 'boolean', short: 'v', description: 'Print version' } }),
    '',
    'Environment:',
    '  ZENSTORY_API_KEY   API key (overrides the saved key)',
    `  ZENSTORY_API_BASE  API base URL (default ${DEFAULT_API_BASE}); https only, except localhost`,
    '  ZENSTORY_LANG      Folder-title language for `projects create` (zh|en; else LC_ALL/LANG)',
    '  XDG_CONFIG_HOME    Config dir root (default ~/.config)',
    '  XDG_CACHE_HOME     Backup dir root for `files put` (default ~/.cache)',
    '',
    `Rate limits: ${RATE_LIMIT_SUMMARY}.`,
    'Exit codes: 0 ok, 1 error, 2 usage, 3 auth/permission, 4 not found, 5 rate limited.',
    'Run `zenstory <command> --help` for details.',
  ].join('\n');
}

function groupHelp(group: string): string {
  const cmds = COMMANDS.filter((c) => c.name.startsWith(`${group} `));
  const width = Math.max(...cmds.map((c) => c.name.length + (c.args?.join(' ').length ?? 0) + 1));
  return [
    `Usage: zenstory ${group} <subcommand> [options]`,
    '',
    ...cmds.map((c) => `  ${`${c.name} ${c.args?.join(' ') ?? ''}`.trimEnd().padEnd(width)}  ${c.summary}`),
  ].join('\n');
}

// ==================== Entry ====================

export async function run(argv: string[], io: Io): Promise<number> {
  const version = readVersion();
  const wantsJson = argv.includes('--json');
  const print = (text: string) => void io.stdout.write(`${text}\n`);

  try {
    if (argv[0] === '--version' || argv[0] === '-v' || argv[0] === 'version') {
      print(version);
      return EXIT.OK;
    }
    if (argv[0] === 'help') argv = [...argv.slice(1), '--help'];

    const r = route(argv, COMMANDS);
    if (r.kind === 'root') {
      print(rootHelp(version));
      return argv.length === 0 || argv.includes('--help') || argv.includes('-h') ? EXIT.OK : EXIT.USAGE;
    }
    if (r.kind === 'unknown') throw new CliError(`Unknown command "${r.token}". Run \`zenstory --help\`.`, EXIT.USAGE);
    if (r.kind === 'group') {
      if (r.unknown) throw new CliError(`Unknown subcommand "${r.group} ${r.unknown}".\n\n${groupHelp(r.group)}`, EXIT.USAGE);
      print(groupHelp(r.group));
      return EXIT.OK;
    }

    const { command } = r;
    const { values, positionals } = parseCommandArgs(command, r.rest);
    if (bool(values, 'help')) {
      print(commandHelp(command));
      return EXIT.OK;
    }

    let client: ZenstoryClient | undefined;
    const ctx: Ctx = {
      io,
      json: bool(values, 'json'),
      version,
      print,
      printJson: (data) => print(toJson(data)),
      note: (text) => void io.stderr.write(`${text}\n`),
      client: () => {
        if (client) return client;
        const auth = resolveAuth(io.env);
        if (!auth.apiKey) throw new CliError(NOT_LOGGED_IN, EXIT.AUTH);
        assertSafeAuth(auth, { allowBaseOverride: bool(values, 'allow-base-override') });
        if (auth.keySource === 'config') {
          const warning = configPermissionWarning(io.env);
          if (warning) io.stderr.write(`${warning}\n`);
        }
        client = new ZenstoryClient({ apiKey: auth.apiKey, apiBase: auth.apiBase, fetch: io.fetch, userAgent: `zenstory-cli/${version}` });
        return client;
      },
    };
    await command.run.call(command, ctx, values, positionals);
    return EXIT.OK;
  } catch (err) {
    const e = err instanceof CliError ? err : new CliError(err instanceof Error ? err.message : String(err), EXIT.ERROR);
    const message = redactKeys(e.message);
    if (wantsJson) {
      io.stderr.write(`${toJson({ error: { message, status: e.status ?? null, code: e.code ?? null, exitCode: e.exitCode } })}\n`);
    } else {
      io.stderr.write(`Error: ${message}\n`);
    }
    return e.exitCode;
  }
}
