# Frequently Asked Questions (FAQ)

This document collects the most common questions from zenstory users to help you quickly find answers.

---

## Account

### Q: What should I do if I forget my password?

**A:** Click "Forgot Password" on the login page, enter your registered email address, and the system will send a reset link to your inbox. Click the link in the email to set a new password. The reset link is valid for 24 hours.

If you don't receive the email in your inbox, please check your spam folder.

---

### Q: What login methods are supported?

**A:** zenstory supports the following login methods:

- **Email + Password**: Log in using the email and password you set during registration
- **Google Account**: One-click login via Google OAuth, no additional password setup required

Both methods provide full functionality. Choose whichever method you prefer.

---

### Q: Can I change my registered email address?

**A:** Yes. After logging in, go to the "Personal Settings" page and click the edit button next to your email address to modify it.

**Note**: After changing your email, you'll need to verify the new email address by entering a verification code sent to it. Please ensure the new email address is correct and can receive emails properly.

---

### Q: What is an invitation code?

**A:** An invitation code is zenstory's user referral system. You can invite friends to register for zenstory by sharing your invitation code:

- **Referrer Reward**: Earn 100 points for each friend who successfully registers
- **Invitee Reward**: Earn 100 points after registering with an invitation code and completing email verification

Each user can create up to 3 invitation codes, and each code can be used up to 3 times. Invitation codes follow the format `XXXX-XXXX` (e.g., `A1B2-C3D4`).

---

## Projects and Files

### Q: How many files can a project contain?

**A:** There is no hard limit. You can create any number of files in a project, including outlines, drafts, character cards, lore entries, and more.

We recommend organizing files by type using folders (e.g., "Outlines," "Characters," "Settings") for easier management and better AI context understanding.

---

### Q: Are files automatically saved?

**A:** Yes. zenstory uses a real-time auto-save mechanism:

- **Draft Editor**: Every keystroke is automatically saved without manual intervention
- **Version History**: Each save creates a version snapshot that you can revisit at any time

You can view the complete version history on the file details page, and compare or restore to any version.

---

### Q: Can I export all my content?

**A:** Yes. Click the "Export" button on the project page, and the system will merge all draft content from that project in chapter order and export it as a `.txt` file.

The exported file contains complete content in UTF-8 encoding, compatible with Windows Notepad.

---

### Q: Can deleted files be recovered?

**A:** File deletion uses a soft delete mechanism. Deleted files are moved to the trash and retained for 30 days. During this period, you can recover them from "Deleted Files."

After 30 days, files are permanently deleted and cannot be recovered. Please confirm carefully before deleting files.

---

## AI Assistant

### Q: Can I modify AI-generated content?

**A:** Absolutely. AI-generated content is inserted directly into the editor, where you can:

- Edit the text directly in the editor
- Select text and ask AI to refine it further
- Use the "Rewrite" function to have AI regenerate the content

AI is your writing assistant; you have complete control over the final content.

---

### Q: Does AI know what I've written before?

**A:** Yes. zenstory's AI assistant has intelligent context understanding capabilities:

- **Intra-project File Association**: AI automatically reads other files in the same project (such as outlines, character cards, settings) to understand the overall story background
- **Conversation Memory**: Within the same conversation, AI remembers previous exchanges
- **Smart References**: You can reference any file's content, and AI will create based on the referenced material

**Tip**: For better AI understanding, we recommend saving character profiles, world-building, and other settings as separate files within the project.

---

### Q: How can I help AI better understand my needs?

**A:** Here are tips to improve AI understanding accuracy:

1. **Be Specific**: Avoid vague instructions. Instead of "Help me write something," say "Write a scene where the protagonist wakes up in urban ruins and discovers there's no one around"
2. **Reference Relevant Files**: In conversations, use @ to reference character cards and setting files so AI accurately understands character personalities and world-building
3. **Break Down Complex Tasks**: Split complex tasks into steps, such as "First list 5 plot options" → "Select option 2 and expand on it"
4. **Provide Examples**: If you need a specific style, paste a reference passage and tell AI "Write in this style"

---

### Q: What should I do if AI response is slow?

**A:** AI response speed is affected by the following factors:

- **Network Connection**: Check if your network is stable
- **Content Length**: If you've referenced many files or have long context, processing time will increase
- **Server Load**: Response may be slower during peak hours (e.g., weekday evenings)

**Recommendations**:
- Keep referenced files per conversation under 5
- If there's no response for over 30 seconds, try refreshing the page
- For long content, generate in segments rather than requesting large amounts at once

---

## Skills

### Q: What's the difference between built-in skills and custom skills?

**A:**

| Feature | Built-in Skills | Custom Skills |
|---------|-----------------|---------------|
| **Source** | System presets | User-created |
| **Editing** | Not editable | Can be modified anytime |
| **Trigger** | Fixed keywords | Custom trigger words |
| **Use Cases** | Outline generation, chapter summaries, character analysis | Specific writing styles, fixed templates, personalized needs |

Custom skills are ideal for users with established creative workflows. For example, you can create a "Xianxia Battle Scene" skill with preset description patterns and styles, so AI generates content following your template every time it's triggered.

---

### Q: Who can see shared skills?

**A:** Skill sharing uses a review system:

1. **Submit for Review**: After sharing a skill, it enters the pending review queue
2. **Admin Review**: Only approved skills enter the public skill library
3. **Public Visibility**: After approval, all users can search and add your skill in the "Skill Market"

Please ensure shared skills are original and don't violate community guidelines. Reviews typically complete within 1-3 business days.

---

## Material Library

### Q: Will uploaded novels be visible to others?

**A:** No. Novels uploaded to your material library are completely private:

- Only you can view and manage them
- AI-extracted character data, world-building, golden fingers, and other data are also visible only to you
- Other users cannot access your material library

The material library feature is solely for assisting your creative work and will not be published or shared with third parties in any form.

---

### Q: Is AI extraction accurate?

**A:** AI extraction accuracy depends on novel quality and type:

- **Well-structured novels**: Core outputs such as characters, world-building, and main plot extraction are generally accurate (about 85%+)
- **Complex multi-threaded narratives**: Some details may require manual verification
- **Special types**: Works like stream-of-consciousness or non-linear narratives may not extract well

**Recommendations**:
- After extraction completes, review character cards and world-building settings to check and correct errors
- Golden fingers, plot lines, and optional relationship-analysis outputs need special attention—AI may miss or misinterpret them
- Extraction results serve as references but should be combined with manual adjustments

---

## Inspirations and Plans

### Q: What’s the difference between Inspirations and Materials?

**A:** They serve different purposes:

- **Inspirations**: reusable idea templates (genre hooks, conflicts, character dynamics) to start faster
- **Materials**: structured references extracted from uploaded novels (characters, world-building, storylines)

A practical flow is: pick direction in Inspirations, then deepen details with Materials.

---

### Q: Where can I check my plan benefits and quotas?

**A:** After logging in, open **Plans & Benefits** in the dashboard sidebar. There you can review:

- current plan status
- key capabilities and quota usage
- plan differences
- upgrade and redeem-code entry points

If you're evaluating before purchase, check the public **Pricing** page first.

---

## Other

### Q: Can I use zenstory offline?

**A:** No. zenstory is a pure cloud application. All features (including AI assistant, file storage, and material library) require an internet connection.

**Reasons**:
- AI generation relies on cloud-based large language models
- Real-time file synchronization requires server support
- Cross-device access requires cloud storage

If you need to write without internet access, we recommend exporting content to local documents while online, then editing offline and uploading later.

---

### Q: Is my data secure?

**A:** zenstory employs multiple security measures to protect your data:

- **Encrypted Storage**: All files are stored with AES-256 encryption
- **Transit Encryption**: Data transmission uses HTTPS/TLS encryption
- **Access Control**: Strict authentication ensures only you can access your content
- **Regular Backups**: Database is automatically backed up daily to prevent data loss
- **Privacy Protection**: Your creative content is not used to train AI models and is not shared with third parties

We deeply understand the importance of your creative content to you. Data security is our top priority.

---

### Q: Are there word limits?

**A:**

- **Single File**: Recommended maximum of 100,000 words (editor may slow down beyond this)
- **Single AI Generation**: Typically 500-2,000 words (dynamically adjusted based on complexity)
- **Material Library Upload**: Maximum 100MB per file (approximately 50 million words)

**Recommendations**:
- Split long novels into multiple draft files by chapter
- Keep auxiliary files like outlines and settings under 10,000 words
- If single AI generation is too long, make multiple requests

---

## Still Can't Find Your Answer?

If your question isn't listed above, you can get help through:

- **Documentation**: Check the [User Guide](../user-guide/interface-overview.md) for detailed features
- **Feedback**: Click the "Help" button in the bottom-right corner of the app to submit feedback
- **Community**: Join the user community to exchange experiences with other creators

Our team continuously updates this FAQ to add the most common user questions to the documentation.
