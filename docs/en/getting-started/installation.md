# Account Registration and Login

Welcome to ZenStory Novel Writing Workbench! This document will guide you through registering and logging into your account to begin your AI-assisted creative journey.

---

## Registration Methods

### Email Registration (Recommended)

Email registration is the most common method and requires verifying your email address to ensure account security.

[Screenshot: Registration form showing username, email, password, confirm password fields and invite code input]

#### Registration Steps

1. **Access the Registration Page**
   - Visit the ZenStory official website homepage
   - Click the "Register" button in the top right corner
   - Or directly access the `/register` page

2. **Fill in Registration Information**

   - **Username**: At least 3 characters, this will be your unique identifier on ZenStory
   - **Email Address**: Use your regular email for receiving verification codes and password recovery
   - **Password**: At least 6 characters, recommended to include letters and numbers
   - **Confirm Password**: Re-enter your password to ensure accuracy

3. **Enter Invite Code** (Optional but Recommended)

   - If you have an invite code, find the "Invite Code" input field at the bottom of the registration form
   - Format: `XXXX-XXXX` (e.g., `ABCD-1234`)
   - Registering with an invite code and completing email verification grants you **100 points**
   - The invite code is validated in real-time, showing a green checkmark when valid

4. **Submit Registration**

   - Click the "Register" button
   - The system will send a 6-digit verification code to your email
   - The page will automatically redirect to the email verification page

[Screenshot: Email verification page showing verification code input and resend button]

5. **Verify Email**

   - Open your email and find the verification email from ZenStory
   - Copy the 6-digit verification code from the email
   - Enter the verification code on the verification page
   - Click the "Verify" button

6. **Complete Registration**

   - After successful verification, you'll be automatically logged in
   - Redirect to the Dashboard homepage
   - Start creating your first project

> **Tip**: If you don't receive the verification email, check your spam folder or click the "Resend Verification Code" button (60-second cooldown).

---

### Google Account Login

A quick login method that doesn't require filling out forms or email verification.

[Screenshot: Login/Registration page highlighting the "Sign in with Google" button]

#### Usage Steps

1. Find the "Sign in with Google" button on the login or registration page
2. Click the button to redirect to the Google authorization page
3. Select your Google account
4. Authorize ZenStory to access your basic information (email, name, avatar)
5. Registration/login will complete automatically and redirect to the workbench

#### Advantages

- **No password to remember**: Simply use your Google account to log in
- **No email verification required**: Google has already verified your email, so you can skip this step
- **Auto-fill information**: The system automatically uses your Google name and avatar
- **More secure and convenient**: Enjoy Google's security protection and two-factor authentication

> **Note**: First-time Google login will automatically create an account, and the system will generate a username based on your Google information. If the username is already taken, a numeric suffix will be automatically added (e.g., `zhangsan2`).

---

### Invite Code System

ZenStory uses an invitation-based registration system to encourage healthy community growth.

[Screenshot: Invite code input field showing green checkmark validation status for valid code]

#### Obtaining an Invite Code

Invite codes can be obtained through:

- **Registered users**: Ask your friends or colleagues for one
- **Official channels**: Apply through the official community or Discord channel
- **Event giveaways**: Participate in official creative events to receive codes

#### Invitation Reward Mechanism

After registering with an invite code, both parties receive rewards:

| Role | Reward | When |
|------|--------|------|
| **Inviter** | 100 points | After invitee completes email verification |
| **Invitee** | 100 points | After completing email verification |

#### Invite Code Usage Rules

- **Format**: `XXXX-XXXX` (8-character alphanumeric combination)
- **Usage limit**: Each invite code can be used up to 3 times
- **Validity period**: Some invite codes may have expiration dates
- **Real-time validation**: The system validates invite codes in real-time when entered
- **Anti-abuse mechanism**: The system detects abnormal registration behavior (such as same IP, device fingerprint) to ensure fair use

#### Auto-fill via Link

If you receive an invitation link containing an invite code parameter, the system will auto-fill it:

```
https://zenstory.ai/register?invite=ABCD-1234
```

After visiting this link, the invite code input field will automatically populate with `ABCD-1234`.

---

## Login and Logout

### Login Process

[Screenshot: Login page showing username/email input, password input, and login button]

#### Login Steps

1. Visit the `/login` page or click the "Login" button on the homepage
2. Enter your username or email address (either works)
3. Enter your password
4. Click the "Login" button

#### Post-Login Behavior

After successful login, the system intelligently redirects based on your project status:

- **Has projects**: Redirect to the most recently used project
- **No projects**: Redirect to Dashboard homepage, guided to create first project
- **SSO redirect**: If logging in from an external application, you'll automatically return to the original app with authentication tokens

### Remember Login State

ZenStory uses JWT tokens to manage login state:

- **Access token**: Short validity period (e.g., 2 hours), used for API requests
- **Refresh token**: Longer validity period (e.g., 7 days), used to automatically refresh access tokens
- **Auto-refresh**: When the access token expires, the system automatically uses the refresh token to obtain a new one
- **No repeated logins**: As long as the refresh token is valid, no need to log in again

> **Security Tip**: Don't check your browser's "Remember password" feature on public computers. ZenStory's tokens are stored in browser local storage and won't be cleared when you exit the browser.

### Secure Logout

[Screenshot: User menu dropdown highlighting the "Logout" option]

#### Logout Steps

1. Click your avatar or username in the top right corner of the page
2. Click the "Logout" button in the dropdown menu
3. Clear locally stored authentication tokens
4. Redirect to the login page

#### Automatic Logout Situations

You will be automatically logged out in these situations:

- Refresh token expired (more than 7 days of inactivity)
- Server returns 401 unauthorized error
- Token tampering detected
- User changed password on another device

---

## Password Recovery

If you forget your password, you can reset it through your registered email.

[Screenshot: Forgot password page showing email input field]

### Reset Steps

1. **Access Password Recovery Page**
   - Click the "Forgot Password?" link on the login page
   - Or directly access the `/forgot-password` page

2. **Enter Email Address**
   - Fill in the email address you used during registration
   - Click the "Send Reset Link" button

3. **Check Your Email**
   - Open your email and find the password reset email from ZenStory
   - The email contains a reset link valid for **1 hour**

4. **Reset Password**
   - Click the reset link in the email
   - Enter a new password on the opened page (at least 6 characters)
   - Re-enter to confirm the new password
   - Click the "Confirm Reset" button

5. **Log In Again**
   - After successful reset, log in with your new password
   - All login sessions on other devices will be invalidated and require re-login

> **Note**: The reset link can only be used once. If the link expires, you'll need to request a new reset email.

---

## Account Settings

After logging in, you can modify your personal information in the settings page.

[Screenshot: Settings page showing nickname, avatar, password modification, and theme switching options]

### Change Nickname

1. Go to the "Settings" page
2. Find the "Nickname" input field in the "Personal Information" section
3. Modify your nickname (username cannot be changed)
4. Click the "Save" button

### Change Password

1. Go to the "Settings" page
2. Click "Change Password" in the "Security Settings" section
3. Enter your current password
4. Enter your new password (at least 6 characters)
5. Confirm your new password
6. Click "Confirm Change"
7. After successful change, all devices must re-login with the new password

### Theme Switching

ZenStory supports light/dark themes:

1. Go to the "Settings" page
2. Find the "Theme" option in the "Appearance Settings" section
3. Choose:
   - **Light Mode**: White background, suitable for daytime use
   - **Dark Mode**: Dark background, suitable for nighttime use, easier on the eyes
   - **Follow System**: Automatically switches based on system settings

4. Changes take effect immediately, no page refresh needed

### Language Switching

ZenStory supports multiple interface languages:

1. Go to the "Settings" page
2. Select interface language in the "Language Settings" section
3. Currently supported:
   - **Chinese (Simplified)**: Default language
   - **English**: English interface

4. The page will automatically refresh after switching to apply the new language

---

## Frequently Asked Questions

### Didn't receive verification email?

**Possible causes and solutions**:

1. **Email in spam folder**: Check your spam/junk folder
2. **Incorrect email address**: Confirm the email you entered is correct
3. **Email delay**: Wait 2-3 minutes, some email providers may have delays
4. **Too many requests**: If you clicked "Resend" multiple times, there's a 60-second cooldown
5. **Email blocked**: Try registering with a different email (e.g., Gmail, Outlook, or other commonly used email services)

### Seeing "Email already registered"?

**Solutions**:

- This email is already registered, please log in directly
- If you forgot your password, use the "Forgot Password" feature to reset it
- If you want to use a new email, you'll need to register with a different email address

### Invite code invalid?

**Possible causes**:

1. **Invite code expired**: Some invite codes have expiration dates
2. **Invite code reached limit**: Each invite code can be used up to 3 times
3. **Invite code disabled**: The inviter may have manually disabled the code
4. **Format error**: Ensure the invite code format is `XXXX-XXXX`

**Solution**: Contact the inviter for a new invite code, or apply for one in the community.

### Google login failed?

**Possible causes and solutions**:

1. **Browser blocked popup**: Allow browser popups, or manually redirect to the Google authorization page
2. **Network issue**: Check your network connection and ensure you can access Google services
3. **Google service error**: Try again later, or use email registration instead
4. **Insufficient account permissions**: Ensure you authorize ZenStory to access basic information (email, name)

### Logged out immediately after login?

**Possible causes**:

1. **Token expired**: Refresh token has expired (more than 7 days of inactivity), need to log in again
2. **Password changed**: Password was changed on another device, all devices need to re-login
3. **Token validation failed**: Clear browser cache and local storage, then log in again
4. **Server error**: Try again later, or contact customer support

---

## Security Recommendations

To protect your account security, please follow these recommendations:

1. **Use a Strong Password**
   - At least 8 characters
   - Include uppercase and lowercase letters, numbers, and special characters
   - Avoid using easily guessable information like birthdays or phone numbers

2. **Change Password Regularly**
   - Change your password every 3-6 months
   - Don't use the same password across multiple websites

3. **Be Cautious on Public Devices**
   - Don't check "Remember password" on public computers
   - Log out promptly after use
   - Clear browser cache and cookies

4. **Protect Your Invite Code**
   - Don't share invite codes in public places
   - Regularly check invite code usage
   - Disable invite codes immediately if you notice unusual activity

5. **Enable Two-Factor Authentication** (Coming Soon)
   - Bind a phone number or authenticator app
   - Require dynamic verification code during login

---

## Getting Help

If you encounter account-related issues, you can get help through:

- **Online Documentation**: Visit [docs.zenstory.ai](https://docs.zenstory.ai) for complete documentation
- **Help Center**: Click the "Help" button in the app to view FAQs
- **Community Forum**: Ask questions at [community.zenstory.ai](https://community.zenstory.ai)
- **Customer Support Email**: Send an email to support@zenstory.ai
- **Issue Feedback**: Submit a ticket by clicking "Feedback" in the settings page

---

## Next Steps

After completing registration and login, you can:

- **[Create Your First Project](./first-project.md)** - Start your creative journey
- **[Understand the Interface](../user-guide/interface-overview.md)** - Familiarize yourself with ZenStory's three-panel layout
- **[Chat with AI](../user-guide/ai-assistant.md)** - Learn how to efficiently use the AI assistant
- **[Manage Files](../user-guide/file-tree.md)** - Master file organization and version control

Happy writing!
