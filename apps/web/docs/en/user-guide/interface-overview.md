# Interface overview

After signing in to ZenStory, you work across two main surfaces: the **dashboard** (where you manage your projects, Reference Library, skills, and subscription) and the **project workspace** (the three-pane writing environment).

## 1. Dashboard header and home

### 1. Dashboard navigation bar

- **Left navigation**: Links to **Home**, **My Projects**, **Materials**, **Skills**, and **Plans & Benefits**. On mobile screens, tap the menu button in the top left to open the same navigation links.
- **Right action bar**: Displays your daily free AI message badge (on the Free plan; click to open Plans & Benefits), **Help** (opens the documentation center), **Feedback** (submit a report with up to 3 screenshots), **Settings** (gear icon), and your user menu.

### 2. Dashboard home (`/dashboard`)

From top to bottom, the dashboard home includes:

- **Pro renewal reminder banner**: When an active Pro plan is within 7 days of expiring, a banner shows the remaining days or expiration time with **Renew Pro** and **Dismiss** actions.
- **Creation box and first-work card**: Choose **Long-form Novel**, **Short Story**, or **Short-drama Script**, enter a one-line idea (or click an example prompt below to fill it in), and click **Start Writing**—the workbench opens the work and sends your idea to the AI assistant (leaving the box blank opens a clean workspace). On a newly verified account, you can also click **Open it** directly on the **Your first work is ready** card at the top.
- **Manuscript import action**: Click **Have a draft already? Import it (txt / md)** directly below the creation box to upload a `.txt` or `.md` manuscript. ZenStory creates a project of the selected format named after your file, splits recognizable chapter headings into chapter files without calling the AI or using daily AI messages, and opens Chapter 1.
- **Recent Projects**: Shows your most recently updated works—click a card to open the project or click the delete icon in the top-right corner of a card to remove it. A search bar appears automatically once you have more than 6 projects, and **View All** opens the full **My Projects** page.

## 2. Project workspace layout

Opening a project brings up the top toolbar and the left, center, and right workspace panes on desktop:

### 1. Project top toolbar

- **Left side**: Click the ZenStory logo to **Back to dashboard**, or click the current project title to open the **Project Switcher**. The switcher always includes a search box at the top, lets you click any project to switch, hover a project row to **Rename** or **Delete** it, or click **New Project** at the bottom to expand a name input and create a new long-form novel project.
- **Right side (desktop)**: Shows your daily AI message badge (on Free), **Manage Subscription**, **Export whole work (TXT)** (or **Export whole script (TXT)** in screenplay projects), **More Actions** (**Download current file (TXT)** when a file is open, **Help Docs**, and **Send Feedback**), **Settings**, and your user menu. On mobile, these actions are grouped inside the top-right menu.

### 2. Left sidebar: Files, Skills, and Materials

The left sidebar has three tabs at the top:

- **Files**: Manages your project's **Outlines**, **Characters**, **Worldbuilding**, **Drafts** (or **Scripts** in screenplay projects), and **Materials** folders, with full-text and title search via `Ctrl/Cmd + K`.
- **Skills**: Lists built-in and enabled skills. Click a skill to read its details, or click `+` to attach it to your next AI chat message (up to 3 skills per message).
- **Materials**: Browses reference novels analyzed in your account's Reference Library by category (Characters, Worldbuilding, Special Powers, Storylines, Plotlines), letting you preview entries, **Add to chat**, or **Add to project** (single or batch).

### 3. Center pane: Editor and diff review

The center pane is where you write and review documents:

- **When no file is selected**: Displays the **Start from a single line** empty state with a primary **Let AI write the opening** button, **New Chapter** (or **New Script**) and **New Outline** cards, a collapsible **More options** row (**New Character** and **New Worldbuilding**), and **Import draft files (txt / md)**.
- **When a file is open**: Shows the editable document title at the top, the plain-text body editor in the middle (with pinch-to-zoom from 50% to 250% on touch screens), and the bottom status bar with **Words**, **Paragraphs**, **History**, **Humanize**, save status, and **Save**. During Humanize or a concurrent-edit conflict, the editor switches to a side-by-side diff review split view.

### 4. Right pane: AI assistant

The right pane houses your AI writing partner:

- **Header**: Offers **AI Memory** (view project setting summaries and edit cross-chat **Writing Conventions**) and **New chat**.
- **Message list**: Displays AI replies, clickable file cards with word-count deltas and an **Undo** button, and round completion notes.
- **Input area**: Drag the top border to resize the input panel; above the text box you will see suggestion chips along with any attached quotes, materials, or skill chips. Type `/` to open the skill picker, use the microphone button for voice input, switch between **Fast** and **Quality** modes in the bottom left, and send or **Stop generating** on the right.

### 5. Mobile and portrait tablet layout

On narrow screens, the bottom navigation bar switches between **Files**, **Editor**, and **AI**. Tapping a file in **Files** switches straight to **Editor**, and tapping **Let AI write the opening** in an empty **Editor** switches to **AI** and highlights the starter cards.

## 3. Settings dialog

Click the gear icon in the top right of the dashboard or project workspace to open **Settings**, where the main tabs include:

| Tab | What you can do |
| --- | --- |
| **Profile** | View your username and registered email, or click **Log out** |
| **General** | Switch interface language (**简体中文 / English**), toggle product-improvement analytics for the current browser, choose **Dark Theme** or **Light Theme**, and pick from 6 accent colors |
| **Subscription** | View your current plan and expiration date, click **Redeem Code**, click **Get Pro** or **Renew Pro**, and check **Today's Usage** |
| **API Keys** | Create, copy, or revoke personal API keys (with read/write or read-only permissions and optional expiration days) to connect Claude Code, Codex, or OpenClaw via the ZenStory CLI and writing skill |
