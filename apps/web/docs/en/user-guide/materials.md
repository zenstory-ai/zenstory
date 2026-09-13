# Materials Library: Upload, Read and Reference Text

ZenStory's materials library breaks reference fiction into browsable chapter, character, plot and world details to help you locate material to read. It is separate from manuscript files: **uploading reference material does not import a continuation-ready writing project, or put the entire book into every AI conversation.**

For the path from source evidence to your own scene, go to the [original material-analysis example](../advanced/material-analysis.md). This page covers the usage path, not a guarantee of processing speed, completeness or writing quality.

## 1. Prepare and upload TXT

Open the materials library in the workbench, use its upload control, select the file and check its title before submitting. Upload only text you have the right to use, and retain the original independently.

| Constraint | Current implementation |
|---|---|
| Format | `.txt`; this upload does not directly accept PDF, Word or EPUB. |
| File size | Up to 100MB, implemented as 100 × 1024 × 1024 bytes. |
| Text length | Up to 300,000 decoded characters, checked alongside file size—not 300,000 English words. |
| Access | Requires login and is subject to material-feature access and decomposition quota. Follow your account's current interface and returned messages. |

Keep clear chapter headings and order. If you select only part of an over-limit book, label that scope; analysis of an excerpt is not evidence that the whole book was read. Do not remove context that changes a character's choice just to reach a target size.

## 2. Separate upload success from finished analysis

An accepted upload creates an asynchronous decomposition task. `pending` means waiting, not that every result is available; `processing` means work is continuing. This guide provides no fixed minutes-per-word-count schedule.

Task states also include `completed`, `completed_with_errors` and `failed`. These are implementation names, not a promise that every screen displays identical labels. A completion timestamp does not establish that every chapter and category was analyzed fully and correctly. Inspect the current status, error information and actual results before deciding what to use.

When a failed card offers Retry, it can submit a new decomposition task. This is not guaranteed exact-point resume or a quota-free operation. Running tasks cannot be retried repeatedly. Read the error before treating deletion and repeated re-uploading as a default repair.

## 3. Open a category and return to the passage

Enter material details, expand the category you need and select an item. Categories load on demand; a just-opened page or unopened folder is not evidence that no results exist.

| Result type | What to inspect |
|---|---|
| Chapter | Summary, plot points and available source text; check whether a decisive action was summarized away. |
| Character | Names, aliases and descriptions; tie a personality interpretation to an actual action. |
| Plot, story or storyline | Description, type and related chapters; read earlier or later text when needed. |
| World and special ability | Available systems, structure, factions or ability development; ask how a limit changes a choice, not just what label it has. |

These are display capabilities, not a promise that every material contains every result. Relationship or timeline categories do not establish a complete interactive graph. Structured descriptions can omit or misread evidence; “the analysis does not list a rule” does not mean “the source has no such rule.”

This guide does not describe the result viewer as an analysis-card editor or bulk exporter. Keep your own interpretations in separate reading notes with source location and inference labels. Changing those notes is not the same as correcting the library's extracted analysis.

## 4. Attach the items the chat actually needs

Use the material-attachment control in project chat to select relevant items and confirm they are attached. Supply a short quotation when needed. Material attachments and text quotations participate in context assembly, with scope and budget limits—not automatic injection of the entire library.

A bounded request could be:

```text
Use the attached item, but analyze only the source passage quoted below.
I want to understand why the character changes their action.
Separate explicit evidence, your interpretation and questions requiring more reading.
Do not generate my story or modify project files yet.
If the item is insufficient, identify the missing evidence.
[Insert the source passage and chapter location here.]
```

“Analysis only” states the task scope; it is not enforced read-only permission. See the [AI assistant guide](ai-assistant.md) for context and file-writing boundaries. Naming a material does not establish that the model has read it all. The passages used matter more than the number of books attached.

## 5. Turn reading into a writing decision

You do not have to keep collecting labels. The [material-analysis walkthrough](../advanced/material-analysis.md) uses an original recording scene to locate evidence for a changed goal, then designs a different toy-car practice scene that retains the new choice's cost.

Choose the function you want to learn, then decide your own characters, actions and outcome. Keep reference notes separate from accepted story design. Update progress using the [project-memory example](../advanced/ai-memory.md) only after prose is actually saved.

## Implementation sources

Source checked on 2026-09-13. These pinned references support format, task, display and context details; they are not account-run or analysis-quality tests.

- [Upload format, size and character limits](https://github.com/zenstory-ai/zenstory/blob/306059d9418dbdd6612d67574816ad960f58e51c/apps/server/api/materials/upload.py#L287-L331)
- [Creating a task and dispatching asynchronous decomposition](https://github.com/zenstory-ai/zenstory/blob/306059d9418dbdd6612d67574816ad960f58e51c/apps/server/api/materials/upload.py#L374-L417)
- [Failed-task retry, running-task limits and quota checks](https://github.com/zenstory-ai/zenstory/blob/306059d9418dbdd6612d67574816ad960f58e51c/apps/server/api/materials/upload.py#L429-L500)
- [Task completion states and error information](https://github.com/zenstory-ai/zenstory/blob/306059d9418dbdd6612d67574816ad960f58e51c/apps/server/services/material/ingestion_jobs_service.py#L55-L105)
- [Loading categories when folders expand](https://github.com/zenstory-ai/zenstory/blob/306059d9418dbdd6612d67574816ad960f58e51c/apps/web/src/pages/MaterialDetailPage.tsx#L82-L209)
- [Result viewer](https://github.com/zenstory-ai/zenstory/blob/306059d9418dbdd6612d67574816ad960f58e51c/apps/web/src/components/materials/MaterialViewer.tsx#L123-L198)
- [Explicit material attachments and text quotations](https://github.com/zenstory-ai/zenstory/blob/306059d9418dbdd6612d67574816ad960f58e51c/apps/server/agent/context/assembler.py#L149-L203)
