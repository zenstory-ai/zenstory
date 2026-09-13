# Version history: compare first, restore deliberately

ZenStory exposes two histories with different scopes: versions of one file and snapshots of a project. Use comparison to recover a passage whenever possible. A rollback replaces current state; it is not the safest way to retrieve one sentence, and neither history is a promise that every keystroke is preserved.

For the working story and file layout, see [Write your first short story in ZenStory](https://zenstory.ai/docs/getting-started/first-project). For saving and diff review, see [Edit and review text](https://zenstory.ai/docs/user-guide/editor).

## Know which history you opened

| History | Open it from | What it represents | What comparison shows |
| --- | --- | --- | --- |
| **File versions** | Open a file, then choose **History** in the editor status bar | Retained content states for that one file | A textual diff between two selected versions |
| **Project snapshots** | Choose the history icon in the project header | Project file metadata plus references to file versions at a point in time | Files added, removed or moved to another version number |

A project snapshot is broader, but it is not an independent archive containing a fresh full copy of every file. The current implementation stores metadata and references to file-version records. **Saved edits without a new file version may not be recoverable from a project snapshot.** Use snapshots to inspect project-wide change, not as a substitute for an external backup.

## Saving and history are different events

The editor schedules a save after roughly three seconds without further edits, and a manual save can submit sooner. That saves the current file. It does not mean every input event creates a version.

For user edits, the current editor uses the net content-length difference from its last saved baseline, not the number of characters you edited. A small net difference or same-length replacement can save without a version. A per-file plan quota can also make content save successfully while its history snapshot is skipped. Therefore:

- treat **Saved** as confirmation of current content, not proof of a new history entry;
- check the version list before assuming a milestone exists;
- do not rely on unlimited versions or indefinite retention; policy and allowances apply.

Project snapshots follow another rule. After a completed AI turn, the browser attempts an automatic snapshot when files changed or the response contains meaningful AI content. Snapshot failure is treated as non-critical, so a completed conversation does not guarantee a corresponding snapshot. Check the snapshots and referenced file versions that actually exist rather than relying on a conversation-completed message alone.

## Compare two file versions

Open the target file and choose **History**. The list shows version number, time, change type, word count, line changes and any stored summary. Select exactly two rows, then choose **Compare**. The comparison pane is the implemented way to inspect old and new text together.

The row action labelled as viewing content does request that version's content from the API, but the editor callback that would display a standalone preview is currently a deferred TODO. Do not rely on that icon for a full preview. Select two versions and use **Compare** instead.

### Example: recover one sentence, not the whole file

In *Before Closing*, suppose the current **Drafts / Scene 1 — The Claim** contains pacing and dialogue revisions you want to keep, but an earlier version has this accurate sentence:

> Lin He knew only that Shen Min had named the inner-pocket item incorrectly; he had no evidence for why.

Use this checklist:

1. Confirm the current file is saved, then copy any irreplaceable current passage to an independent note.
2. Open **History** for that draft.
3. Select the earlier version and a later version; choose **Compare**.
4. Locate the sentence in the text diff and copy only that sentence.
5. Close history, paste it into the current draft, adjust the surrounding paragraph manually, and save.
6. Reopen history only if you need to confirm whether the new save also produced a version.

This keeps the later draft while restoring the one useful line. It also preserves the story boundary: Lin He knows the answer was wrong, not whether Shen Min lied or why she was mistaken.

## Understand file rollback before using it

The rollback button asks for confirmation and then calls the file-version rollback API. On success, the backend overwrites the current file with the selected version's content. It may create a new `restore` history entry when the per-file quota permits, but that new entry contains the restored content. The backend does **not** first create a guaranteed version of the outgoing current content.

Before file rollback, independently preserve the current passage. After a successful rollback the editor reloads, so do not leave unsaved text in the page and assume it will survive. Use rollback only when replacing the whole file is the intended result; for a sentence or paragraph, compare and copy instead.

## Treat project rollback as a wide operation

In project history, select two snapshots and compare them before considering rollback. The comparison dialog summarizes files added, removed and modified between the snapshots, including old and new version numbers. It is a project inventory comparison, not a full prose diff for every modified file. Open important files and use file-version comparison when wording matters.

A successful project rollback first creates a pre-rollback snapshot, then restores the selected snapshot's scope. That safety snapshot follows the same reference-based design: if current content was saved without its own file version, do not assume the snapshot captured that exact outgoing text. For a full-project snapshot, restoration can recreate missing files, undelete older files, restore titles, types, parent folders, ordering and content, and soft-delete current files that did not exist in the selected snapshot. That can change many parts of the project at once. The pre-rollback snapshot also uses file-version references; it is not proof that every recent edit has an independent copy.

Before committing to project rollback:

- finish or preserve all unsaved editor work;
- make an independent copy of every current file or passage you cannot reconstruct;
- compare the two snapshots and list affected files;
- inspect important modified files individually;
- proceed only when replacing the project-wide state is the actual goal.

Do not delete a file merely to test restoration. Use read-only comparison to understand the scope, and reserve rollback for a real recovery decision.

## Next steps

- [Export a manuscript](https://zenstory.ai/docs/user-guide/export): keep a current prose copy and understand what TXT excludes.
- [Editor guide](https://zenstory.ai/docs/user-guide/editor): deliberately incorporate the recovered passage into your current draft.

## Reviewed source

Source checked on 2026-09-12. The sentence is an original teaching example; no authenticated comparison or rollback of real user content was performed.

- [Editor save timing and conditions for requesting or skipping a file version](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/SimpleEditor.tsx#L509-L589)
- [Single-version preview callback is deferred; rollback closes history and reloads](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/SimpleEditor.tsx#L681-L695)
- [Loading, selecting, comparing, viewing and rolling back file versions](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/FileVersionHistory.tsx#L68-L150)
- [Implemented two-version textual comparison pane](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/FileVersionHistory.tsx#L321-L406)
- [File rollback replaces content and conditionally records the restored state](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/server/services/features/file_version_service.py#L412-L499)
- [Version quota can skip a snapshot without failing the content save](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/server/api/files.py#L899-L927)
- [Project snapshot listing, description, comparison and rollback actions](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/VersionHistoryPanel.tsx#L80-L175)
- [Project comparison displays added, removed and modified files](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/SnapshotComparisonDialog.tsx#L138-L285)
- [Automatic project-snapshot attempt after qualifying AI turns](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/hooks/useChatStreaming.ts#L1483-L1538)
- [Project snapshots store file metadata and references to latest file versions](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/server/services/features/snapshot_service.py#L526-L571)
- [Project rollback creates a pre-rollback snapshot before restoration](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/server/services/features/snapshot_service.py#L180-L227)
- [Full-project restoration can recreate, undelete, restore and hide files](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/server/services/features/snapshot_service.py#L594-L753)
- [Existing versions versus missing-version backfill](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/server/services/features/snapshot_service.py#L504-L537)
