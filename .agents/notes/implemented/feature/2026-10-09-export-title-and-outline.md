# Agent Note: 导出 TXT 第一行写作品名；可选「导出大纲和正文」

Status: implemented

## Problem

2026-10-09 新用户审计 P2-15：短剧作者点顶栏「导出正文（TXT）」，得到的文件只有第 1–3 集剧本，没有剧名，也没有核心大纲和分集大纲。她要交给甲方的恰好是「大纲 + 前三集」，只能自己再拼一次。长篇作者投稿时同样常要附大纲。

## Decision

- `GET /api/v1/projects/{id}/export/drafts`：文件第一行是作品名（项目名），空一行后接原来的正文/剧本内容；新增查询参数 `include_outline`（默认 `false`）。为 `true` 时先输出「【大纲】」和全部非空大纲文件（与文件树相同的顺序），用 `---` 分隔后再输出「【正文】」（短剧为「【剧本】」）和各章；文件名为 `{作品名}_大纲和正文.txt`。角色卡、设定、素材不导出。只有大纲没有正文时，带大纲的导出仍能下载（只含大纲），不带大纲的导出照旧返回「没有可导出的正文」。
- `export_service.export_drafts_to_txt` 增加关键字参数 `title`、`include_outline`，不传时输出与之前完全相同；排序抽成 `_sort_by_sequence` 供正文和大纲共用。
- 前端保留顶栏一键「导出正文（TXT）」；在「更多」菜单和手机菜单里加「导出大纲和正文（TXT）」，成功提示「大纲和正文已导出为 TXT 文件」。`exportApi.exportDrafts(projectId, { includeOutline })`。

## Alternatives considered

- **点导出时先弹一个选项框（勾选是否含大纲）。** 最强理由：所有选项集中在一处，以后加格式也有位置。被否：现在最常用的「只要正文」会多一次点击，已有的导出 E2E 都假定点击即下载；两个并列入口同样让作者看得到这个选项。
- **默认就把大纲一起导出。** 最强理由：短剧交稿通常要大纲。被否：投稿正文的作者不想要大纲，拿到后还得手工删；大纲可能是写给自己的草稿。默认行为保持只导出正文。
- **作品名放在文件名里就够了。** 最强理由：文件名已经是 `{作品名}_正文.txt`。被否：文本一旦复制到别处，文件名就丢了；第一行写作品名是投稿稿件的常见格式。

## Consequences

- 收益：导出的稿件自带作品名；需要交「大纲 + 正文」的作者一次就能拿到完整文件。
- 代价：所有导出的第一行多了作品名，依赖「文件第一行就是第一章标题」的外部处理需要跳过一行。

## Verification

- pytest `tests/test_api/test_export_title_and_outline.py`（默认首行作品名且不含大纲；含大纲时大纲在【剧本】前、空大纲与角色卡不导出、文件名；只有大纲时含大纲的导出可用）及原有 `test_export.py`、`test_export_service.py`、`test_export_first_paragraph_indent.py` 共 50 个通过。
- vitest `Header.test.tsx`（「更多」菜单的新入口调用 `exportDrafts({ includeOutline: true })` 并给出成功提示）、`api.test.ts`（只在选择时附带 `include_outline=true`）、`useExport.test.ts`。
