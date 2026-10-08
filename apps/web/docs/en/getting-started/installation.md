# Account Registration and Login

This page covers sign-up, email verification, invitations, login and password recovery in ZenStory Workbench. Sign-up and login happen at [app.zenstory.ai](https://app.zenstory.ai), not on the organization site zenstory.ai.

## Email Registration

1. Open the workbench [registration page](https://app.zenstory.ai/register).
2. Enter a username, email address, password and password confirmation. The username needs at least 3 characters, the password at least 6, and both passwords must match.
3. If the page shows an invite code field, follow its hint; a field marked "optional" can stay empty.
4. Read and accept the terms of service and privacy policy, then submit.
5. The page then moves to email verification. Enter the code from the email; once it is verified you land in the workbench.

If the email doesn't arrive, check the address and your spam folder, then resend once the countdown on the page ends.

## Invitation Links and Rewards

An invitation link fills in the code for you. The format looks like this (`ABCD-1234` is only an example):

```text
https://app.zenstory.ai/register?invite=ABCD-1234
```

The page also accepts `?code=`. A complete code looks like `XXXX-XXXX`; once it is filled in, the page checks that it exists, hasn't expired and still has uses left.

- When a friend signs up with your code and verifies their email, you both get points, which you can redeem for Pro.
- Suspicious sign-ups may not receive the reward.
- If a code is invalid, check that you copied all of it, or ask its owner for a new one.

## Google Login

If the login or registration page shows a Google button, click it and follow the authorization steps. New accounts created with Google follow the same invite code rules, and their email counts as verified. Self-hosted deployments need Google OAuth configured first.

## Login and Session State

Open the [login page](https://app.zenstory.ai/login) and enter your username or email address and password.

After login you return to the project you last used on this device; without that record you get the most recently updated project, and with no projects yet you land in the workbench.

Login state is kept in browser storage and refreshes automatically when it expires; if the refresh fails, log in again.

The user menu has a logout option. Logging out clears the login state on this device only; on a shared device, log out when you finish, since closing the tab is not enough.

## Forgotten Password

Follow the login page to the [forgot-password page](https://app.zenstory.ai/forgot-password) and use the contact it shows; on the hosted app, write to support@zenstory.ai from your registered email.

**There is currently no web self-service reset-link flow, and no password-change form in Settings.**

When reporting a login problem, never share passwords, verification codes, invite codes or login tokens.

## Reviewed Source

These links point at the source version this page was checked against.

- [Registration link and form](https://github.com/zenstory-ai/zenstory/blob/76b8ab84ce73ca309083f0c1a90f3c9d7ce8d72b/apps/web/src/pages/Register.tsx#L91-L237)
- [Registration policy](https://github.com/zenstory-ai/zenstory/blob/76b8ab84ce73ca309083f0c1a90f3c9d7ce8d72b/apps/server/api/auth.py#L115-L172)
- [Verification completion and resend](https://github.com/zenstory-ai/zenstory/blob/76b8ab84ce73ca309083f0c1a90f3c9d7ce8d72b/apps/web/src/pages/VerifyEmail.tsx#L153-L207)
- [Invite-code validation](https://github.com/zenstory-ai/zenstory/blob/76b8ab84ce73ca309083f0c1a90f3c9d7ce8d72b/apps/server/api/referral.py#L160-L209)
- [Reward configuration defaults](https://github.com/zenstory-ai/zenstory/blob/76b8ab84ce73ca309083f0c1a90f3c9d7ce8d72b/apps/server/services/features/referral_service.py#L31-L38)
- [Invitation reward anti-abuse checks](https://github.com/zenstory-ai/zenstory/blob/76b8ab84ce73ca309083f0c1a90f3c9d7ce8d72b/apps/server/services/features/referral_service.py#L385-L426)
- [Authentication feature flags](https://github.com/zenstory-ai/zenstory/blob/76b8ab84ce73ca309083f0c1a90f3c9d7ce8d72b/apps/web/src/config/auth.ts#L47-L68)
- [New Google users and invitation policy](https://github.com/zenstory-ai/zenstory/blob/76b8ab84ce73ca309083f0c1a90f3c9d7ce8d72b/apps/server/api/oauth.py#L492-L591)
- [Navigation after password login](https://github.com/zenstory-ai/zenstory/blob/76b8ab84ce73ca309083f0c1a90f3c9d7ce8d72b/apps/web/src/pages/Login.tsx#L160-L247)
- [Token storage and local logout](https://github.com/zenstory-ai/zenstory/blob/76b8ab84ce73ca309083f0c1a90f3c9d7ce8d72b/apps/web/src/contexts/AuthContext.tsx#L248-L282)
- [API refresh and failure handling](https://github.com/zenstory-ai/zenstory/blob/76b8ab84ce73ca309083f0c1a90f3c9d7ce8d72b/apps/web/src/lib/apiClient.ts#L124-L179)
- [Forgot-password page](https://github.com/zenstory-ai/zenstory/blob/76b8ab84ce73ca309083f0c1a90f3c9d7ce8d72b/apps/web/src/pages/ForgotPassword.tsx#L8-L65)

## Next Steps

- [Create your first project](./first-project.md)
- [Explore the interface](../user-guide/interface-overview.md)
- [Work with the AI assistant](../user-guide/ai-assistant.md)
- [Manage files](../user-guide/file-tree.md)
