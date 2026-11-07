# Using the editor

The center editor is where you write and review outlines, character sheets, worldbuilding notes, novel chapters, and short-drama scripts. It includes debounced auto-save, local draft recovery, passage quoting, Humanize, and side-by-side diff review.

## 1. Empty state actions and writing area

### 1. Starting from an empty editor

When a project opens with no file selected, the center pane offers four starting actions:

- **Let AI write the opening**: Focuses the right-hand AI chat input and highlights the starter cards on desktop, or switches directly to the **AI** tab on mobile.
- **New Chapter / New Script** and **New Outline**: Creates a blank file in **Drafts** (or **Scripts** in screenplay projects) or **Outlines** and opens it right away.
- **More options**: Expands cards for **New Character** and **New Worldbuilding**.
- **Import draft files (txt / md)**: Lets you pick up to 20 `.txt` or `.md` files from your device, splits recognizable chapter headings into the **Drafts** folder, and opens Chapter 1.

### 2. Title bar, body area, and status bar metrics

When a file is open, the editor consists of three parts:

- **Title bar**: Click the top input to edit the file title (up to 255 characters). Saving a new title updates the file tree and chapter ordering automatically.
- **Plain-text body editor**: Edits plain text directly (symbols such as `#` or `**` remain literal characters and are not rendered as rich text). On touch screens, pinch with two fingers to zoom between 50% and 250% (tap the zoom percentage in the status bar to reset to 100%). While the AI is streaming content into the open file, an **AI is writing...** banner appears at the top and the view follows the bottom unless you scroll up.
- **Status bar counts**:
  - **Words**: Counts Chinese characters and English words only, excluding punctuation and whitespace (so the number will read lower than platforms that count punctuation; hover or tap **Words** to see this note).
  - **Paragraphs**: Counts non-empty lines, matching manuscript layouts where paragraphs are indented without blank lines between them.

## 2. Auto-save, manual save, and local draft recovery

### 1. Auto-save and manual save

- **Auto-save**: As soon as you edit the title or body, the bottom status bar shows **Unsaved**. Three seconds after you stop typing, the editor saves to the cloud and updates the indicator to **Saved just now** (or the elapsed time since the last save). Auto-save pauses automatically while the AI is editing the file, while Humanize is running, or while Diff Review is open.
- **Manual save**: Press `Ctrl/Cmd + S` or click **Save** in the bottom-right corner at any time.
- **When a file version is recorded**: Saved body changes are recorded in **History**, and consecutive saves within a short window may be merged into a single version; title-only edits save the new title without creating an extra version. If a file has reached your plan's manual version limit (10 versions per file on Free, 100 on Pro) and cannot merge into the latest version, your body text still saves normally without adding a new version.

### 2. Local draft protection across reloads and disconnects

If a tab closes, reloads, or loses connection before your latest edits are saved to the cloud, ZenStory stores an unsaved draft snapshot in `localStorage` scoped to your account, project, and file:

- **When the cloud file is unchanged**: Reopening the file restores your unsaved text automatically and shows a **Restored your last unsaved draft** banner at the top. Keep typing or click **Save** to save it to the cloud, or click **Discard draft** on the banner to revert to the cloud copy.
- **When the cloud file has a newer version**: If the AI or another device updated the file while you were away, ZenStory never overwrites the newer cloud copy automatically. Instead, it displays the latest cloud version and shows a **Found an unsaved draft, and this file has a newer version** banner so you can choose **Use draft** or **Discard draft**.
- **When the file updates while you are typing**: If the AI or another tab saves a new version while you have unsaved edits open, ZenStory preserves every word you typed and opens a side-by-side comparison (your text on the left, the merged server version on the right, with any overlapping edits defaulting to keep your words) so you can review each change and click **Finish Review**.

## 3. Quoting passages and Humanize

### 1. Quote selected text to AI chat (`Ctrl/Cmd + Shift + Q`)

Highlight any passage in the editor and wait 0.3 seconds for the floating **Quote** toolbar to appear (or press `Ctrl/Cmd + Shift + Q` immediately):

- Clicking **Quote** attaches the selected text and its source file title to the **Quoted** row above the AI chat input (up to **5** quotes per message).
- Type your revision instructions in chat and send to have the AI rewrite that exact passage in context.

### 2. Humanize selected text (`Ctrl/Cmd + Shift + R`)

To smooth out stiff, repetitive, or overly formal phrasing in a passage without leaving the editor:

1. Select the passage you want to polish (up to **6,000 characters** at a time; split longer sections into smaller selections).
2. Click **Humanize** in the bottom status bar or press `Ctrl/Cmd + Shift + R`.
   - **Quota rule**: On the Free plan, the button notes **(uses 1 AI message)**; when the system determines that the run produced no substantive output and refunds the message, it is not counted toward today's allowance. Pro includes unlimited daily AI messages.
3. Once the rewrite finishes, the editor opens **Diff Review** so you can inspect every change before it touches your manuscript.
4. If a temporary model or matching failure occurs, your original text stays untouched and a banner appears above the status bar—click **Try again** on the banner to re-run Humanize on the same selection.

## 4. Diff Review mode

After **Humanize** finishes or when a concurrent-edit comparison opens, the editor enters a side-by-side split view:

- **Split comparison**: The left pane shows **Original** and the right pane shows **Modified**, while the top toolbar summarizes the number of deletions and additions.
- **Per-change actions**: On each change card, click `✓` to accept the change, `✗` to reject it (keeping your original text), or the reset icon to undo your decision.
- **Batch actions and shortcuts**:
  - Click **Accept All** (`Shift + Y`) to accept every change;
  - Click **Reject All** (`Shift + N`) to keep all original text;
  - Click **Finish Review** (`Enter`) to save the reviewed text and record a version (if you reject every change in a Humanize review, exiting leaves the file untouched without creating an extra version);
  - Press `Esc` to reject all changes and exit review immediately.

For help writing or revising with AI, see [AI assistant](ai-assistant.md). To compare earlier drafts or restore a file, see [Version history](version-history.md). To save a local copy, see [Export](export.md).
