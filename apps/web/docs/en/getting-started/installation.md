# Account Registration and Login

Learn how registration, verification, invitations, login and account-recovery entrypoints work in the ZenStory workbench, including behavior that depends on deployment settings.

Registration, login and account entrypoints for the ZenStory workbench are on [app.zenstory.ai](https://app.zenstory.ai). Organization information and this guide live on zenstory.ai; use the workbench links below rather than looking for registration on the organization homepage.

> **Source review: 2026-09-12.** This guide describes the fixed source version linked below; it is **not a live-account acceptance test**. No account was created, email delivery verified or real Google authorization completed. Deployment flags, invitation rules and verification countdowns depend on the current page and server response.

## Email Registration

1. Open the workbench [registration page](https://app.zenstory.ai/register).
2. Enter a username, email address, password and password confirmation. The current web form requires at least 3 characters for the username, at least 6 for the password, and matching passwords.
3. Follow the form's invitation requirement. The active registration policy determines whether a code is required, and the page reads that policy again before submission. Neither “optional” nor “invite-only” is a universal deployment rule.
4. Read and accept the terms of service and privacy policy, then submit.
5. After a successful registration request, the page moves to email verification. Enter the code from the email; successful verification saves the login state and opens the Dashboard.

A successful registration response does not establish email delivery. If the email is missing, check the address and spam folder, then use the page's resend option after its cooldown. Follow the displayed expiry and resend countdowns; this guide does not guarantee a fixed interval or delivery time.

## Invitation Links and Rewards

An invitation link can prefill a code. This example demonstrates the URL format only; `ABCD-1234` is **not a valid code issued to you**:

```text
https://app.zenstory.ai/register?invite=ABCD-1234
```

The page also accepts `?code=` and normalizes the input format. A complete code looks like `XXXX-XXXX`; completing it can trigger a validation lookup. The server checks existence, enabled state, expiry and remaining uses.

- Usage limits, expiry and rewards depend on the current rules and code record. Source defaults are not fixed promises about the live deployment.
- Invitation rewards are processed after email verification and can be restricted by anti-abuse checks. Successful registration or verification does not guarantee a reward.
- For an invalid code, check the copied value and page error; request a usable code from its provider if needed. Do not treat the example as registration eligibility.

## Google Login (Deployment-Dependent)

If a Google button is displayed on the login or registration page, follow its authorization flow. Showing the button and configuring backend OAuth are separate requirements; this guide does not verify the live configuration.

New Google users still follow the active invitation policy. The reviewed source uses returned Google information to create a new account, marks its email verified and saves a picture when available. This is not a guarantee of account creation, authorization availability or security. A documented Google flow is not proof that every self-hosted deployment enables it.

## Login and Session State

Open the [login page](https://app.zenstory.ai/login) and enter your username or email address and password.

When there is no pending destination to resume, ordinary password login attempts to open the current user's locally saved project. If that record is not usable, it selects from the most recently updated or created projects. With no projects, or a failed project-list request, it falls back to the Dashboard. Login does not always open the “last-used project.”

The current web app saves login tokens and user state in browser local storage. An ordinary API request receiving an unauthorized response can attempt a refresh and retry; a rejected refresh or another unauthorized response requires login again. Token lifetimes are deployment settings; automatic refresh does not guarantee that login will never be required again.

The user menu offers logout. The current frontend clears local login state; that is not immediate revocation of every device's session, and different entrypoints need not navigate to the same destination. Log out after using a shared device rather than treating a closed tab as logout.

## Forgotten Password

Follow the login page to the [forgot-password page](https://app.zenstory.ai/forgot-password). A feature flag controls this entrypoint: when enabled, it displays a support-email contact link; otherwise it returns to login. Use the contact actually shown on the page. This guide makes no response-time commitment.

**The reviewed version has no web self-service reset-link flow or password-change form in Settings.** A backend change-password endpoint is not a web button; follow the current page instructions.

Keep non-sensitive error messages when troubleshooting. Do not publish passwords, verification or invitation codes, or login tokens in public reports. This guide does not claim two-factor authentication, immediate cross-device logout or a service guarantee for a particular support channel.

## Reviewed Source

These links pin the same source version so implementation boundaries can be checked. They do not prove that later versions or production settings are unchanged.

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
