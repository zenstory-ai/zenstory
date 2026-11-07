# File tree and file management

The **Files** tab in the left project sidebar (the bottom **Files** tab on mobile) organizes your outlines, characters, worldbuilding, drafts or scripts, and reference materials into default folders. Every file created or updated by you or the AI assistant stays synced here in real time.

## 1. Default folders and file types

When you create a project, ZenStory sets up root folders based on your project format (root folders cannot be renamed or deleted, and the number next to each folder shows how many files it holds):

| Root folder | File type | Purpose and `+` button behavior |
| --- | --- | --- |
| **Outlines / Concept / Episode Outlines** | `outline` | Master outlines, story concepts, or episode outlines; click `+` to create a blank outline file |
| **Characters** | `character` | Protagonist, antagonist, and supporting character sheets; click `+` to create a blank character file |
| **World Building** | `lore` | Power systems, factions, geography, and world rules (in Long-form Novel and Short-drama Script projects); click `+` to create a blank worldbuilding file |
| **Drafts** / **Scripts** | `draft` / `script` | Novel chapters or short-drama script episodes; clicking `+` on **Drafts** imports local `.txt` / `.md` manuscript files, while clicking `+` on **Scripts** creates a blank script file |
| **Materials** | `snippet` | Project-scoped reference notes or items imported from your Reference Library; click `+` to upload a `.txt` / `.md` material file |

In a brand-new project with no files yet, empty folders stay clean without repeating placeholder text. Once the project has at least one file, expanding an empty folder shows **Folder is empty** (or **No materials yet, click + to upload** under **Materials**).

## 2. Create files and import manuscripts

### 1. Create an outline, character, worldbuilding, or script file

- **From the file tree**: Hover over **Outlines**, **Characters**, **Worldbuilding**, or **Scripts** (always visible on touch screens), click `+`, type a file name (up to 255 characters), and press `Enter` to create (`Esc` or clicking outside cancels).
- **From the empty editor**: When no file is open, click **New Chapter** (or **New Script** in screenplay projects), **New Outline**, or expand **More options** and click **New Character** or **New Worldbuilding** to create the file in its matching folder and open it immediately.

### 2. Import `.txt` or `.md` chapters into Drafts

To bring existing manuscript files into the current project's **Drafts** folder, either:

- Click `+` next to the **Drafts** folder in the file tree (always visible on phones), or
- Click **Import draft files (txt / md)** at the bottom of the empty editor.

**Import limits and chapter-splitting rules**:
- You can pick up to **20** `.txt` or `.md` files at a time; each file can be up to **5MB** and **500,000 characters** in UTF-8 or GBK encoding (`.md` files are read as plain text).
- ZenStory detects chapter headings on their own lines (`Chapter X`, `Prologue`, `Epilogue`, `第X章/节/回`, `卷X`, `序章`, `楔子`, `番外`, or `1. Title`) and splits the file into ordered chapter files. If a file has no recognizable chapter headings, it is saved as a single chapter file named after the file.
- Manuscript import is pure text processing—it **does not call the AI or use your daily AI messages**.

### 3. Upload reference files into Materials

Click `+` next to the **Materials** folder to upload a `.txt` or `.md` reference file into the current project:

- Each file can be up to **2MB** and **200,000 characters** in UTF-8 or GBK encoding.
- Files over **20,000 characters** are automatically split along line breaks into segments of up to 20,000 characters so you can attach specific parts to chat.
- **Attach to AI chat**: Hover any file under **Materials** and click the speech-bubble plus icon (**Add to chat**) to pin it above the chat input (up to **5** attached materials per message; click the check icon to **Remove from chat**).

## 3. Open, search, and navigate with the keyboard

- **Open a file**: Click any file in the tree to open it in the center editor (on phones, tapping a file switches automatically from **Files** to **Editor**). When you reopen a project, ZenStory expands the folder and restores the file you last had open.
- **Search project files (`Ctrl/Cmd + K`)**: Click the search box at the top of the file tree or press `Ctrl + K` (`⌘ + K` on macOS) to search across both **file titles** and **body content** in the current project. Use `↑` / `↓` to move through matching snippets, `Enter` to open the selected file, and `Esc` to close search.
- **Tree keyboard navigation**: When a row in the file tree has focus, use `↑` / `↓` to move between visible rows, `→` to expand a folder or move to its first child, `←` to collapse a folder or jump to its parent folder, `Home` / `End` to jump to the first or last row, and `Enter` or `Space` to open the focused item.

## 4. Rename and delete files

- **Rename a file**: Open the file in the editor and edit the **title field** at the top of the editor (up to 255 characters; keep body prose in the main text area rather than the title field). When the editor auto-saves after 3 seconds or you press `Ctrl/Cmd + S`, the file tree updates the title and re-sorts numbered chapters automatically.
- **Delete a file**: Hover the file row in the tree, click the trash icon on the right, and click **Delete** in the confirmation dialog.
- **Unsaved draft protection for deleted files**: If an open file had unsaved local edits when it was deleted, the top of the editor expands a **“…” was deleted — your unsaved draft was kept** banner so you can copy the text or click **Download draft (.txt)** to save it to your device.
