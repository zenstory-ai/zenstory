# Export your manuscript, not the whole project

ZenStory's Export Manuscript action combines saved, non-deleted draft and script files from the current project into one TXT. Use it for a manuscript handoff, offline reading or a point-in-time prose copy. It excludes characters, reference materials, chat and complete version history, so it is not a project-restoration package.

Open the intended project in the [browser workbench](https://app.zenstory.ai/dashboard). This guide continues the [Before Closing short-story example](https://zenstory.ai/docs/getting-started/first-project). Filenames and prose below are teaching examples, not an actual export of a user's work.

## 1. Decide what you want to take away

Export selects by **file type**, not simply by looking inside a folder named Drafts:

| Project content | Included in the current TXT? |
| --- | --- |
| Non-deleted `draft` files | Yes, including drafts in other folders |
| Non-deleted `script` files | Yes, including screenplay projects and mixed file types |
| Characters, lore, outlines and materials | No, even if a title or folder contains the word “draft” |
| Deleted files | No |
| Unsaved editor text or candidate prose only in chat | Export does not automatically turn it into a saved manuscript |
| Chat, attachment provenance, file history and project snapshots | Not part of this combined prose file |

Renaming a character file “Final Draft” does not change its type. Conversely, planning notes stored in a `draft` can be exported with the prose. Before a handoff, inspect both the file type and what you put inside it.

## 2. Save first, then download from the project

1. Open the correct project and save the changes you intend to deliver. Finish pending AI writes or diff review and inspect the target file; a chat message saying “done” is not enough.
2. On desktop, use the header's download icon, labeled **Export Manuscript**. On mobile, choose **Export Manuscript** from the menu.
3. Let the browser finish downloading, then open the file. Duration depends on the network, project and service; no fixed number of seconds is promised.
4. The server suggests `{project name}_正文.txt`. If the browser cannot read the filename information, it may use a default such as `Export.txt`; check the actual download record.

The export request reads saved server content. It does not press **Save** in the editor for you. Avoid continuing a large rewrite during export and then assuming those later changes are in the downloaded copy.

## 3. Why might chapter order differ from drag-and-drop order?

Current sorting uses file ordering, title and sequence information, with creation time among the fallback information. For recognized chapter-like draft or script titles—such as `第1章`, `第2集`, `第3场` or `Chapter 4`—the title sequence can determine the effective order. **Drag order is not always dominant, and creation time is not the only rule.**

- Use clear, consistent numbering and avoid two drafts with the same chapter number.
- A leading number can also provide a sequence hint. Do not assume any English title is recognized; `Scene Two`, for example, is not a promised numbering format.
- If the result is wrong, inspect titles and file placement, then export again. Do not delete project files to rearrange an export.

### A simple handoff example

Suppose you have saved two scenes as draft files and titled them **1. The claim** and **2. Identification**. The organization of the download looks like this; the two prose lines illustrate structure, not a complete story:

```text
1. The claim

Lin He did not hand over the gray canvas bag.

---

2. Identification

“Take another moment—what was in the inner pocket?”
```

Each file contributes its title, a blank line and its text. Files are separated by `---`. Leading and trailing body whitespace is trimmed; internal line breaks remain text. This does not produce a typeset document with page headers, images and font styling.

## 4. How can I share two scenes without changing the original project?

The current entry exports all eligible draft and script files; it does not offer a chapter-selection step. To show an editor only two scenes:

1. Download the complete prose and keep an unchanged copy, for example `Before_Closing_prose_2026-09-12.txt`.
2. Save another local copy as `Before_Closing_two_scenes_for_review.txt`.
3. Remove unwanted scenes, writing notes and information you should not share **only from the review copy**. Open it before you send it yourself.

You choose those names after downloading; the workbench does not automatically create that dated archive. Do not temporarily delete later chapters from the project to produce a shorter export, or overwrite your only full download.

## 5. How do offline edits get back into the workbench?

A downloaded TXT is an independent copy, not a two-way synchronized file. Edit a local copy if useful. When returning, open the corresponding current draft, check whether you or AI changed it in the meantime, then copy the chosen passages into the right file and save.

If you revised two sentences in Scene 2, apply those two sentences rather than pasting the entire merged book into one scene. Preserve both versions before reconciling overlapping edits. Matching filenames do not make the workbench automatically recognize an offline revision.

For older text, see [version history and recovery scope](https://zenstory.ai/docs/user-guide/version-history). A TXT copy, a file version and a project snapshot are different things.

## 6. Handle download and format problems

| Symptom | First action |
| --- | --- |
| No exportable prose | Check for non-deleted `draft` or `script` files, not just outlines or chat candidates |
| Access, sign-in or plan-format error | Check the account and project, then follow the current message; repeated clicks do not bypass a restriction |
| No visible download | Check page errors and the browser's download list; preserve unsaved text before addressing connectivity |
| Garbled text | The server emits UTF-8 with BOM; select a matching encoding in your software and retain the original download |
| Need Word, PDF or EPUB | This entry emits TXT. Use trusted local software for layout or conversion if needed; a separate converter is not a built-in ZenStory feature |

TXT does not preserve rich-text styling or image layout. It does not guarantee a match with every publishing platform's word count, chapter separators or upload rules. Read the beginning, chapter boundaries and ending before handoff to catch missing passages, duplicates and unintended notes. This guide makes no promise about future export formats.

## Next steps

- [Editor guide](https://zenstory.ai/docs/user-guide/editor): saving, local edits and conflicts.
- [Version history](https://zenstory.ai/docs/user-guide/version-history): file-level versus project-level recovery.
- [AI assistant](https://zenstory.ai/docs/user-guide/ai-assistant): explicitly put adopted prose into the target file rather than leaving it in chat.

## Implementation sources

Source checked on 2026-09-12. These fixed-version references explain export; no authenticated manuscript export or publishing-platform compatibility test was performed.

- [Desktop project export control](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/Header.tsx#L228-L241)
- [Mobile-menu export control](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/Header.tsx#L326-L337)
- [Export access and format restrictions](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/server/api/export.py#L56-L100)
- [Exported file types and ordering call](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/server/services/features/export_service.py#L110-L143)
- [TXT composition](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/server/services/features/export_service.py#L197-L203)
- [Effective order and chapter-title precedence](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/server/utils/title_sequence.py#L216-L252)
- [Supported title-sequence forms](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/server/utils/title_sequence.py#L32-L49)
- [Output encoding and server filename](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/server/api/export.py#L129-L143)
- [Browser filename and download behavior](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/lib/api.ts#L335-L364)
