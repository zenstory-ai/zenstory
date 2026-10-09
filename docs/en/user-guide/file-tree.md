# File Tree & File Types

ZenStory uses a unified file system to manage your creative content. All files are organized in a tree structure, helping you clearly manage the various components of your novel.

## File Tree Structure

[Screenshot: Complete file tree view, showing the folder hierarchy in the left sidebar]

The file tree is located on the left side of the interface, using a classic hierarchical tree structure:

- **Folder**: Used to organize and categorize files, supports nesting
- **File**: Specific creative content, displays different icons based on type
- **Expand/Collapse**: Click on a folder to expand or collapse its contents
- **File Count**: The number of files contained is displayed on the right side of each folder

The file tree automatically identifies file types based on folder names. For example, files created in the "Characters" folder will automatically be set as character card type.

## Four Core File Types

ZenStory supports four core file types, each with a specific purpose and display icon.

### Outline

[Screenshot: Outline file example, showing hierarchical chapter planning]

**Purpose**: Plan story structure, chapter arrangements, plot direction

**Features**:
- Hierarchical structure, supports multi-level nesting
- Ideal for recording main storylines, episode outlines, plot points
- AI can generate corresponding draft content based on outlines

**Typical Usage**:
- Create an "Outlines" folder to store the overall story architecture
- Create separate outline files for each chapter or plot point
- Select the outline in AI chat to have AI generate the main text based on it

**Icon**: Document icon (FileText)

---

### Draft

[Screenshot: Draft file example, showing rich text editor and word count]

**Purpose**: Write actual content, the core of final output

**Features**:
- Rich text editor, supports formatting
- Real-time word count
- Auto-save and version history
- AI can continue, rewrite, or expand content

**Typical Usage**:
- Create a "Drafts" folder to organize drafts by chapter
- After selecting a draft file, request continuation or modification in AI chat
- Use version history to track changes

**Icon**: Book icon (BookOpen)

---

### Character Card

[Screenshot: Character card example, showing character name, personality, appearance, etc.]

**Purpose**: Record detailed character settings and profiles

**Information Included**:
- Name, age, gender, identity
- Personality traits, relationships
- Physical description, attire
- Background story, growth experience
- Special abilities, signature features

**Typical Usage**:
- Create a "Characters" folder, establish profiles for each main character
- AI automatically references character settings when generating content
- Maintain consistency in character personalities

**Icon**: User group icon (Users)

---

### Lore

[Screenshot: Lore file example, showing power systems, faction distributions, etc.]

**Purpose**: Build story background, world settings, rule systems

**Information Included**:
- World background (era, location, environment)
- Power systems (magic, martial arts, technology, etc.)
- Faction distributions (nations, organizations, sects)
- Historical events, important settings
- Social rules, cultural customs

**Typical Usage**:
- Create a "Lore" folder to categorize and store various world-building settings
- Common categories: power systems, factions, history, geography, etc.
- AI maintains logical consistency in content based on lore settings

**Icon**: Sparkles icon

## File Operations

### Create New File

1. **Hover over a folder**, click the **+** button that appears
2. Enter the file name
3. Press **Enter** to confirm creation, or press **Esc** to cancel

File type is automatically identified based on the folder:
- "Outlines" folder -> Outline type
- "Drafts" folder -> Draft type
- "Characters" folder -> Character card type
- "Lore" folder -> Lore type

### Rename File

Currently, renaming files requires deleting and recreating them. Direct rename functionality will be supported in future versions.

### Delete File

1. **Hover over a file**, click the **trash icon** that appears
2. Confirm the deletion

**Note**: Deletion is irreversible, please proceed with caution.

### Move File

Drag-and-drop file moving is not currently supported. This feature will be provided in future versions.

## Folder Management

### Create Folder

1. In the project root directory, click the **+** button to create a new folder
2. Enter the folder name (recommended to use type names like "Outlines", "Characters", "Lore", etc.)

### Recommended Folder Structure

```
Project Name
├── Outlines/        # Store story outlines
│   ├── Overall Structure
│   └── Episode Outlines
├── Drafts/          # Store draft content
│   ├── Chapter 1
│   ├── Chapter 2
│   └── ...
├── Characters/      # Store character settings
│   ├── Protagonist
│   ├── Supporting Character A
│   └── ...
└── Lore/            # Store world-building
    ├── Power System
    ├── Faction Distribution
    └── ...
```

### Expand/Collapse Folders

- Click the folder name to expand or collapse
- When expanded, you can see all files and subfolders within the folder
- When collapsed, only the folder name and file count are displayed

## Keyboard Shortcuts

### File Search

**Shortcut**: `Ctrl + K` (Windows/Linux) or `Cmd + K` (Mac)

Quickly search all files in the current project:

1. Press the shortcut to open the search box
2. Enter file name keywords (supports fuzzy matching)
3. Use **Up/Down arrow keys** to navigate through results
4. Press **Enter** to open the selected file
5. Press **Esc** to close the search

**Search Features**:
- Supports fuzzy matching, case-insensitive
- Sorted by relevance (exact match > prefix match > contains match)
- Displays the full path of files
- Supports filtering by file type

### Other Shortcuts

- **Enter**: Confirm creation in the new file input box
- **Esc**: Cancel current operation (new file, search, etc.)
- **Delete**: Delete selected file (requires confirmation)

## Tips

1. **Use folders effectively for categorization**: A clear folder structure helps AI better understand the project structure and automatically reference relevant settings during conversations.

2. **Keep character cards and lore complete**: AI references these settings when generating content. Complete settings help AI maintain content consistency.

3. **Use outlines to drive creation**: First create detailed outlines, then have AI generate main text based on the outlines for more expected results.

4. **Check version history regularly**: Draft files support version history, allowing you to revert to previous versions at any time.

5. **Use search to quickly locate**: When the project has many files, using `Ctrl/Cmd + K` for quick search is more efficient than manual browsing.

## FAQ

**Q: Can file types be changed?**
A: Currently, file types cannot be directly changed after creation. It is recommended to delete and recreate the file with the correct type.

**Q: Is there a limit on the number of folders?**
A: There is no hard limit, but it is recommended to keep the structure clear and avoid excessively deep nesting levels.

**Q: How does AI know which settings to use?**
A: AI automatically analyzes the conversation context and selected files, intelligently referencing relevant character cards, lore, and other settings. You can also explicitly specify which files to reference in the conversation.

**Q: Can deleted files be recovered?**
A: Currently, deletion is permanent and cannot be recovered. Future versions will provide a recycle bin feature.
