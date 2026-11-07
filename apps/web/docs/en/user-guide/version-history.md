# Version history

Click **History** in the editor's bottom status bar to read older versions of the open file, compare two versions side by side, or restore the file to an earlier state. Before acting, decide whether you want to recover a single sentence or replace the whole file.

## How file versions are recorded

- **Scoped to each file**: Version history is tracked per file. Restoring a version updates only the currently open file—it does not change other files in the project or bring back deleted files or folders.
- **Coalescing and plan limits**: Saved body changes are recorded in history, and consecutive manual saves within a short window (including small edits) are merged into the same version automatically. Your plan's version allowance counts manual versions (**10 per file** on Free, **100 per file** on Pro), while AI edits and system backups do not consume that allowance. When a file's manual version allowance is full, consecutive saves that can coalesce into the latest manual version still merge; if merging is unavailable, your text saves normally without adding a new version.
- **Version badges and counts**: Each row shows its version number, timestamp, source badge (**Created**, **Edited**, **AI edit**, **Reviewed**, or **Restored**, plus **Current text** on the latest entry), along with that version's total word/character count and added/removed line counts.

## View and compare file versions

1. Open the target file in the editor and click **History** in the bottom status bar.
2. Click **View Content** on any version row to read its full text and timestamp.
3. **Compare two versions**: Click a version row in the list to select it (click it again to deselect, up to **2** versions); once two versions are selected, click the **Compare** button in the top bar to inspect added, removed, and replaced passages between the older and newer versions.

On phones, clicking **View Content** or **Compare** switches to the content panel; tap the close button (`×`, **Close preview**) in the top right of the panel to return to the list and pick other versions. When older records exist, click **Load More** at the bottom of the list.

To recover just a sentence or paragraph without rolling back later work, open **View Content** on the older version, copy the lines you want, close **History**, and paste them into your current draft.

## Restore the whole file

Click **Restore this version** on the version you want and confirm **Restore** in the dialog. Restoring replaces the file's current body text, so save or copy any unsaved edits in the editor first.

Before overwriting the file, the server automatically records a backup version of any current prose that has not entered history yet; if that backup cannot be created, the restore is cancelled so your current text is never lost. After restoring, check both the editor and the history list to confirm the result. Keep separate local exports for important milestones rather than relying on version history alone.

## When text is missing from history

If you cannot find a passage in **History**, check that you are signed into the right account and project, verify that you opened the right file, and check whether the text is still sitting in an unsaved editor draft or only appeared as a candidate suggestion inside the AI chat reply without being written to the file. Never delete a file to test recovery.

For independent local backups, see [Export your manuscript](export.md). For unsaved draft recovery and save conflicts, see [Using the editor](editor.md).
