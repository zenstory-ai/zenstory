# Common Issues Troubleshooting

When you encounter problems while using ZenStory, follow this guide to troubleshoot step by step. If the issue persists, please contact customer support for assistance.

---

## Login Issues

### Unable to Log In

If you're experiencing login failures, please check the following steps:

1. **Check Email and Password**
   - Confirm the email address matches the one used during registration
   - Check if password case sensitivity is correct
   - Clear any potential leading or trailing spaces in the input fields

   [Screenshot: Close-up of login page input fields, highlighting email and password fields]

2. **Try Password Reset**
   - Click the "Forgot Password" link on the login page
   - Enter your registered email and check your inbox (including spam folder)
   - The reset link is valid for 24 hours

3. **Clear Browser Cache**
   - Chrome: Settings → Privacy and Security → Clear browsing data → Select "Cached images and files"
   - Safari: Preferences → Privacy → Manage Website Data → Remove All Data
   - Refresh the page after clearing and try again

4. **Try a Different Browser**
   - We recommend using modern browsers like Chrome, Safari, or Edge
   - Ensure your browser is updated to the latest version
   - Avoid using outdated browsers like IE

### Google Login Failed

1. **Confirm Google Account is Working**
   - Visit google.com in a separate tab to confirm you can log in normally
   - Check if your Google account has two-factor authentication enabled

2. **Check Network Connection**
   - Confirm you can access Google services normally
   - If using a VPN, try switching nodes or temporarily disabling it

3. **Try Email/Password Login**
   - If Google login continues to fail, you can use the email and password from registration
   - Both login methods are fully equivalent

### Auto Logout After Login

1. **Check Browser Cookie Settings**
   - Ensure your browser allows websites to use cookies
   - Chrome: Settings → Privacy and Security → Third-party cookies → Allow third-party cookies

2. **Clear Local Storage**
   - Open browser developer tools (F12)
   - Application → Local Storage → Right-click to clear
   - Refresh the page and log in again

3. **Check Token Expiration**
   - Login status automatically expires after 7 days
   - Re-login is required after expiration - this is a normal security mechanism

---

## AI-Related Issues

### AI Response is Slow

AI content generation takes time, but excessively long response times may be caused by:

1. **Check Network Connection**
   - Confirm network stability by testing other websites
   - If using a VPN, try switching nodes
   - Mobile network users should switch to Wi-Fi

2. **Be Patient with Longer Content**
   - Generating about 500 words typically takes 5-15 seconds
   - Processing time increases if multiple files are referenced as context
   - We recommend referencing no more than 5 files at once

   [Screenshot: Loading animation in AI chat dialog, highlighting "Generating..." prompt]

3. **Refresh Page and Retry**
   - If there's no response after 30 seconds, refresh the page
   - AI conversation history is automatically saved, you can continue after refresh
   - Simply resend the same question

### AI Not Generating as Expected

AI understanding issues can usually be improved by:

1. **Check AI Memory Settings**
   - Confirm relevant character cards and setting files are referenced in the current session
   - Use the @ symbol in the chat box to explicitly reference files
   - AI prioritizes information from referenced files

2. **Provide More Specific Descriptions**
   - Avoid vague instructions like "help me write something"
   - Instead, be specific: "Write a scene where the protagonist walks alone on a street at night in the rain, recalling childhood memories"
   - Include elements like characters, locations, emotions, and actions

3. **Add Relevant Context**
   - Briefly explain the current plot background in the conversation
   - For example: "This is Chapter 3, the protagonist just discovered a friend's secret"
   - AI will adjust the style and direction of generated content based on context

4. **Try a Different Approach**
   - If "describe a battle scene" doesn't work well
   - Change to "describe a duel between two swordsmen in martial arts novel style, focusing on moves and actions"
   - Specify style, perspective, and key elements

### AI Conversation Interrupted

If conversation suddenly stops or shows an error, follow these steps:

1. **Refresh Page**
   - Press F5 or click the browser refresh button
   - Conversation history is preserved after refresh
   - Resend the question to continue

2. **Check Network Connection**
   - AI chat uses streaming (SSE) and requires a stable network
   - Network fluctuations may cause connection interruptions
   - Retry after confirming network stability

3. **Create New Session**
   - If the same session repeatedly interrupts, click "New Conversation" button
   - New sessions avoid interference from historical data
   - Remember to re-reference needed files

### AI Generated Content Formatting is Messy

1. **Check if Prompt is Clear**
   - Explicitly specify output format: "Please list in bullet points"
   - Example: "Output in table format: [Name] | [Age] | [Occupation]"

2. **Generate in Segments**
   - Don't request generating very long content all at once
   - Split into multiple conversations, generating one part each time
   - For example: "First write the opening 200 words" → "Continue with the middle part"

---

## Editor Issues

### Content Not Saved

ZenStory uses an auto-save mechanism. If you notice content loss:

1. **Check Network Connection**
   - Auto-save requires network connection
   - Check the save status icon in the top right corner of the editor
   - Cloud icon indicates syncing, green checkmark indicates saved

   [Screenshot: Editor top right corner save status icon, highlighting different icon meanings]

2. **Check Save Status**
   - Editor bottom shows "Last saved at XX:XX"
   - If not updated for a long time, there may be a network issue
   - Manually press Ctrl+S (Mac: Cmd+S) to trigger save

3. **Check Version History**
   - Click the "History" tab in the right sidebar
   - Version history records snapshots of each auto-save
   - You can rollback to any historical version

### Editor Failed to Load

1. **Refresh Page**
   - Press F5 to refresh, wait for editor to reload
   - This usually resolves temporary loading issues

2. **Clear Browser Cache**
   - Corrupted cache data may cause loading failures
   - See "Login Issues → Clear Browser Cache" for clearing methods

3. **Check Browser Compatibility**
   - Supported browsers: Chrome 90+, Safari 14+, Edge 90+
   - IE browser is not supported
   - Please upgrade older browsers to the latest version

### Editor Lagging

1. **Performance Issues from Large Files**
   - Single files should not exceed 100,000 characters
   - If file is too large, consider splitting by chapters into multiple files

2. **Close Unnecessary Tabs**
   - Too many browser tabs open will occupy memory
   - Close other unneeded tabs to free up resources

3. **Check Device Memory**
   - Open Task Manager to check memory usage
   - If memory is insufficient, close other applications

---

## File Issues

### Unable to Create File

1. **Check Project Status**
   - Confirm you're in the correct project
   - Project name is displayed in the top navigation bar

2. **Refresh Page**
   - File tree data may not have synced in time
   - Refresh the page and try again

3. **Try Other File Types**
   - If one file type fails to create
   - Try creating other file types to test if it's a general issue

### File Tree Display Abnormal

1. **Refresh Page**
   - File tree data comes from the server, refresh to reload
   - Shortcut: F5 or Ctrl+R (Mac: Cmd+R)

2. **Check Network Connection**
   - Unstable network may cause incomplete file tree loading
   - Confirm network is normal then refresh page

3. **Clear Local Cache**
   - Developer Tools (F12) → Application → Local Storage
   - Clear then refresh page

### Unable to Delete File

1. **Confirm Delete Operation**
   - Clicking delete button shows a confirmation dialog
   - You need to click "Confirm" to actually delete

2. **Check if File is Referenced**
   - If file is being referenced in AI conversation
   - End conversation or refresh page then retry

---

## Export Issues

### Export Failed

1. **Check Network Connection**
   - The exported TXT is downloaded to your device; a dropped connection interrupts it
   - Try again once your connection is stable

2. **Save First**
   - Export only includes saved manuscript and script files
   - Wait until the editor no longer shows "Unsaved" or "Saving...", then export

3. **Refresh and Retry**
   - Refresh the page and export again
   - Disable download-related browser extensions and retry

### Exported File Has Garbled Text

1. **Open with Correct Encoding**
   - Exported TXT files use UTF-8 encoding
   - Windows Notepad: Select UTF-8 encoding when opening
   - Or use editors like VS Code, Notepad++

2. **Avoid Encoding Issues During Conversion**
   - If you convert formats with third-party tools, ensure source file encoding is UTF-8
   - Before submitting, preview once in a local editor

---

## Performance Issues

### Page Lagging

1. **Close Unnecessary Tabs**
   - Too many browser tabs occupies significant memory
   - Closing other tabs can significantly improve performance

2. **Clear Browser Cache**
   - Too much cached data may affect performance
   - Regularly clean browser cache and cookies

3. **Check Device Memory**
   - Open Task Manager (Windows) or Activity Monitor (Mac)
   - If memory usage exceeds 80%, close other applications

4. **Check CPU Usage**
   - Other programs using significant CPU affects browser performance
   - Close CPU-intensive programs (like video rendering, large games, etc.)

### Slow First Load

1. **First Load Downloads Resources**
   - ZenStory's first load requires downloading JS, CSS, and other static resources
   - Subsequent visits use browser cache and will be much faster

2. **Network Speed Impact**
   - Using 4G/5G or high-speed Wi-Fi can speed up loading
   - Avoid first visits during network peak hours (e.g., 8-10 PM)

---

## Still Not Resolved?

If you still can't solve the problem following the above steps, please contact customer support for help:

### Contact Customer Support

- **In-App Feedback**: Click the "Help" button in the bottom right corner to submit a problem description
- **Email Support**: support@zenstory.ai
- **Working Hours**: Weekdays 9:00-18:00, response within 24 hours

### Providing the Following Information Can Speed Up Resolution

1. **Problem Screenshots**
   - Include complete error messages or abnormal interface
   - Mark the location where the problem occurred

2. **Operation Steps**
   - Describe detailed operation steps for customer service to reproduce the issue
   - For example: "After login → Click project A → Create new file → Enter title → Click create button → Error"

3. **Environment and Device Information**
   - Browser type and version (e.g., Chrome 120)
   - Operating system (e.g., Windows 11 / macOS 14)
   - Network environment (e.g., Telecom 100M broadband / 4G network)

4. **Problem Occurrence Time**
   - Providing approximate time helps technical staff check logs
   - For example: "Around 8 PM on January 15, 2024"

Our technical team will locate the problem as soon as possible and provide you with a solution.

---

**Related Documentation**

- [Quick Start](../getting-started/quick-start.md) - Learn the basics of using ZenStory
- [FAQ](../reference/faq.md) - View common questions about features
- [User Guide](../user-guide/interface-overview.md) - Detailed feature usage tutorials
