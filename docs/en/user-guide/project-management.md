# Project Management

ZenStory organizes your creative content around projects. Each project is an independent workspace with its own complete file system, AI conversation records, and version history. This document details how to efficiently manage your writing projects.

## Project List

[Screenshot: Project switcher expanded state, showing search box and project list]

The project switcher is located on the left side of the top toolbar. Click the current project name to open the project list. This displays all projects you can access.

### Viewing All Projects

The project list uses a dropdown menu design containing the following information:

- **Project Name** - The project's title
- **Project Description** - Brief project explanation (optional)
- **Current Project Indicator** - The currently selected project shows a checkmark icon

Projects in the list are sorted by most recent access time, with the most recently opened projects at the top.

### Searching Projects

When you have multiple projects, use the search feature to quickly locate them:

1. Click the project switcher to open the dropdown menu
2. Enter keywords from the project name in the search box
3. The list filters in real-time, showing only matching projects

**Search features:**
- Supports fuzzy matching
- Case-insensitive
- Real-time result filtering

---

## Creating Projects

[Screenshot: Create new project input interface]

Creating a new project is simple. You can quickly create one from the project switcher.

### Quick Creation Steps

1. Click the project switcher to open the dropdown menu
2. Click the **"+ Create Project"** button at the bottom
3. Enter the new project's name
4. Click **"Create"** or press **Enter**

After creation, the system automatically switches to the new project, and you can start adding files.

### Project Initialization

Newly created projects automatically include the following default folder structure (English environment):

```
My Novel/
├── Outlines/           # Store story outlines
├── Drafts/             # Store manuscript content
├── Characters/         # Store character profiles
└── Lore/               # Store worldbuilding
```

These folders are automatically generated based on project templates, helping you quickly start writing.

### Project Naming Best Practices

To facilitate management and identification, we recommend following these principles when naming projects:

- **Be specific** - Use the novel name or project code
- **Keep it concise** - Avoid overly long project names
- **Distinguish series** - If you have multiple related projects, use series name + number

**Examples:**
- ✅ Star Odyssey Part One
- ✅ Sci-Fi Short Stories 2024
- ❌ New Project 1
- ❌ Untitled Project

> **Reference:** For detailed first-time project creation workflow, see [Create Your First Project](../getting-started/first-project.md)

---

## Switching Projects

[Screenshot: Project switching operation with mouse hovering over a project]

Switching projects lets you quickly jump between different creative projects, with each project's files and conversation records remaining independent.

### How to Switch Projects

1. Click the project switcher in the top toolbar
2. Find the target project in the project list
3. Click the project name to switch

After switching:
- The left file tree displays the new project's file structure
- The AI assistant loads the new project's conversation history
- The editor shows the new project's last opened file
- The browser address bar updates to the new project's URL

### Quick Navigation

**Direct access via URL:**

Each project has an independent URL address:
```
https://your-domain.com/project/{project-id}
```

You can:
- Add frequently used project URLs to bookmarks
- Share project links with collaborators (if collaboration features are available)
- Use browser forward/back buttons to navigate between projects

### Project Switching Notes

- Switching projects automatically saves the current editor content
- AI conversation records are saved independently in each project
- File tree expand/collapse states are remembered
- Each project has independent version history records

---

## Editing Project Information

[Screenshot: Edit project name interface showing inline edit input box]

You can modify project basic information at any time, including project name and description.

### Modifying Project Name

1. Open the project switcher
2. Hover over the project you want to edit
3. Click the appearing **pencil icon** (edit button)
4. Enter the new project name in the inline input box
5. Press **Enter** to save, or **Esc** to cancel

**Editing tips:**
- The edit icon only appears on mouse hover
- The edit input box auto-focuses and selects the current name
- Supports keyboard shortcut operations
- Clicking outside the input box auto-saves

### Modifying Project Description

Currently, project description needs to be modified through project metadata settings. Future versions will provide direct editing functionality in the project switcher.

### Editing Limitations

- Cannot edit a project name that is currently being edited (need to complete or cancel current edit first)
- Project name cannot be empty
- Recommend using concise and clear names

---

## Deleting Projects

[Screenshot: Delete confirmation dialog showing warning message]

When you no longer need a project, you can delete it. **Please note that deletion is permanent; deleted data cannot be recovered.**

### Deletion Steps

1. Open the project switcher
2. Hover over the project you want to delete
3. Click the appearing **trash icon** (delete button)
4. Click **"Confirm"** in the popup confirmation dialog

**Pre-deletion protection mechanisms:**

The system prevents deletion in the following situations:

- **Last project** - The system requires at least one project to remain; you cannot delete the only current project
- **Confirmation dialog** - Must explicitly confirm to execute deletion

### Effects of Deletion

Deleting a project permanently removes:

- ✗ All project files (outlines, manuscripts, character cards, worldbuilding)
- ✗ All AI conversation records
- ✗ All version history and snapshots
- ✗ Project metadata settings

**Important notes:**
- Deletion is irreversible
- Data cannot be recovered
- Recommend exporting important content before deletion

### Safe Deletion Recommendations

Before deleting a project, we recommend you:

1. **Export important content** - Use the export feature to backup key files
2. **Confirm it's no longer needed** - Carefully check if the project contains important materials
3. **Retain version snapshots** - If possible, first create a complete snapshot of the project

---

## Relationships Between Projects

Understanding the independence between projects helps you better organize creative content.

### Each Project is Completely Independent

ZenStory's projects use a completely isolated design:

**Independent content spaces:**
- Each project has an independent file tree
- Files and folders are not shared between projects
- Character cards and worldbuilding settings are only valid within the current project

**Independent AI contexts:**
- The AI assistant has independent understanding and memory for each project
- AI trained in one project does not affect other projects
- Conversation history is completely isolated

**Independent version history:**
- Each file has independent version records
- Snapshot functionality is only valid within the current project
- Version rollback does not affect other projects

### Files Are Not Shared Across Projects

Currently, ZenStory does not support sharing files across projects. If you need to use the same settings in multiple projects, consider these alternatives:

**Option 1: Copy Content**
1. Open the file in the source project
2. Copy the file content
3. Create a new file in the target project and paste

**Option 2: Template Reuse**
1. Organize the setting structure in the source project
2. Export using the export feature
3. Recreate in the new project referencing the exported content

**Future plans:**
- Materials library feature will support sharing reference materials across projects
- Project template feature will support creating new projects from existing projects

### Suitable Project Scenarios

Based on project independence characteristics, we recommend organizing projects as follows:

**One project = One work:**
- 📚 A full-length novel
- 📚 A collection of short stories
- 📚 A series of stories

**Not recommended:**
- ❌ Mixing multiple unrelated works in one project
- ❌ Splitting one work across multiple projects

### Project Organization Best Practices

**Clear project structure:**
```
Project Name
├── Outlines/           # Story structure
├── Drafts/             # Chapter content
│   ├── Chapter 1
│   ├── Chapter 2
│   └── ...
├── Characters/         # Character profiles
│   ├── Protagonist
│   ├── Supporting Character A
│   └── ...
└── Lore/               # Worldbuilding
    ├── Power System
    ├── Faction Distribution
    └── ...
```

**Leverage AI's context understanding:**
- Place all related content in the same project
- The AI automatically associates characters, worldbuilding, and plot
- Maintain project content integrity and consistency

---

## Tips

1. **Standardize project naming** - Use clear naming conventions for quick identification among multiple projects.

2. **Regularly clean up unneeded projects** - Delete projects you're certain you no longer need to keep your project list tidy.

3. **Regularly export important projects** - Develop a habit of periodically exporting important project content to prevent accidental loss.

4. **Leverage project independence** - Boldly try different creative directions; each project is an independent experimental space.

5. **Use project descriptions** - Add brief descriptions to projects to help you quickly recall project themes and progress.

6. **Avoid over-fragmentation** - A complete work should be placed in one project for the AI to understand the overall context.

---

## Frequently Asked Questions

**Q: How many projects can one account create?**
A: There is currently no limit on the number of projects. You can create as many projects as you need.

**Q: Can projects be synced across different devices?**
A: Yes, project data is stored in the cloud. All projects automatically sync when you log in on different devices.

**Q: Can deleted projects be recovered?**
A: Unfortunately, deletion is permanent. Deleted project data cannot be recovered. Please confirm carefully before deleting.

**Q: Why can't I delete the last project?**
A: The system requires at least one project to remain, ensuring you always have a working project space. If you need to clear content, you can delete files within the project instead of deleting the entire project.

**Q: Can I set a cover or icon for a project?**
A: This feature is not currently supported. Future versions will consider adding this functionality.

**Q: Can projects be merged?**
A: Direct project merging is not currently supported. You can manually merge by copying file content, but note that AI context will not migrate with it.

**Q: Can I share projects with others?**
A: The current version is primarily designed for personal use. Collaboration and sharing features are being planned—stay tuned for future updates.

**Q: Does project AI memory take up storage space?**
A: AI memory is stored on the server side and does not occupy your local storage space. Each project's AI context data is automatically managed by the system.

---

## Related Documentation

- [Create Your First Project](../getting-started/first-project.md) - Beginner's guide
- [File Tree and File Types](./file-tree.md) - Understand file organization structure
- [Chat with AI](./ai-assistant.md) - Master AI assistant usage
- [Version History](./version-history.md) - Manage file modification records
- [Export Feature](./export.md) - Backup and share your work

Happy writing!
