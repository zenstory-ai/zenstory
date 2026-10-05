"""
Email service using Resend API for sending verification codes.
"""
import asyncio
import os

import resend

from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

# Initialize Resend with API key
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
RESEND_FROM_EMAIL = os.getenv("RESEND_FROM_EMAIL", "noreply@zenstory.ai")

if RESEND_API_KEY:
    resend.api_key = RESEND_API_KEY


def _resend_timeout_seconds() -> int:
    raw = os.getenv("RESEND_TIMEOUT_SECONDS", "").strip()
    try:
        value = int(raw) if raw else 8
    except ValueError:
        value = 8
    # Keep within 5-10s: long enough for Resend's API, short enough that a
    # stuck call fails registration fast instead of holding a worker thread.
    return min(10, max(5, value))


RESEND_TIMEOUT_SECONDS = _resend_timeout_seconds()

# The SDK's default requests client waits 30s. Replace it with the same
# client and a short timeout when this SDK version exposes the hook.
if hasattr(resend, "RequestsClient") and hasattr(resend, "default_http_client"):
    resend.default_http_client = resend.RequestsClient(timeout=RESEND_TIMEOUT_SECONDS)


async def send_verification_email(
    email: str,
    code: str,
    expiry_minutes: int = 5,
    language: str = "zh"
) -> bool:
    """
    Send a verification code email to the user.

    Args:
        email: Recipient email address
        code: 6-digit verification code
        expiry_minutes: Expiry time in minutes (default: 5)
        language: Email language ('zh' or 'en', default: 'zh')

    Returns:
        bool: True if successful, False otherwise
    """
    try:
        if not RESEND_API_KEY:
            log_with_context(
                logger,
                30,  # WARNING
                "Resend API key not configured",
            )
            return False

        # Language-specific content
        if language == "en":
            email_content = {
                "title": "Verify Your Email",
                "greeting": "Hi there,",
                "instruction": "Thanks for signing up for zenstory. Please enter the verification code below to complete your registration:",
                "warning": "This code will expire in {expiry} minutes. If you didn't request this code, you can safely ignore this email.",
                "support": "Need help? Please visit zenstory.ai.",
                "footer": "© 2026 zenstory · zenstory.ai",
                "unsubscribe": "You're receiving this email because you signed up for zenstory.",
            }
        else:  # Chinese (default)
            email_content = {
                "title": "验证您的邮箱",
                "greeting": "您好，",
                "instruction": "感谢您注册 zenstory。请输入下方的验证码完成注册流程：",
                "warning": "此验证码将在 {expiry} 分钟后过期。如果您没有请求此验证码，请忽略此邮件。",
                "support": "需要帮助？请访问 zenstory.ai。",
                "footer": "© 2026 zenstory · zenstory.ai",
                "unsubscribe": "您收到此邮件是因为您注册了 zenstory 账号。",
            }

        # Create HTML email content (minimal, clean design inspired by Linear/Notion)
        html_content = f"""
        <!DOCTYPE html>
        <html lang="{'en' if language == 'en' else 'zh-CN'}">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>{email_content['title']}</title>
        </head>
        <body style="margin:0;padding:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif;background-color:#ffffff;color:#1a1a1a;line-height:1.6;">
            <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color:#ffffff;">
                <tr>
                    <td style="padding:48px 24px;">
                        <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width:480px;margin:0 auto;">
                            <!-- Logo -->
                            <tr>
                                <td style="padding-bottom:40px;">
                                    <span style="font-size:20px;font-weight:600;color:#1a1a1a;letter-spacing:-0.3px;">zenstory</span>
                                </td>
                            </tr>

                            <!-- Greeting -->
                            <tr>
                                <td style="padding-bottom:24px;">
                                    <p style="margin:0;font-size:15px;color:#1a1a1a;">{email_content['greeting']}</p>
                                </td>
                            </tr>

                            <!-- Instruction -->
                            <tr>
                                <td style="padding-bottom:32px;">
                                    <p style="margin:0;font-size:15px;color:#666666;line-height:1.7;">{email_content['instruction']}</p>
                                </td>
                            </tr>

                            <!-- Verification Code -->
                            <tr>
                                <td style="padding-bottom:32px;">
                                    <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%">
                                        <tr>
                                            <td style="background-color:#f7f7f8;border-radius:12px;padding:28px 24px;text-align:center;">
                                                <span style="font-family:'SF Mono',Monaco,Consolas,monospace;font-size:32px;font-weight:600;letter-spacing:8px;color:#1a1a1a;">{code}</span>
                                            </td>
                                        </tr>
                                    </table>
                                </td>
                            </tr>

                            <!-- Warning -->
                            <tr>
                                <td style="padding-bottom:40px;">
                                    <p style="margin:0;font-size:14px;color:#999999;line-height:1.6;">{email_content['warning'].format(expiry=expiry_minutes)}</p>
                                </td>
                            </tr>

                            <!-- Divider -->
                            <tr>
                                <td style="padding-bottom:24px;">
                                    <div style="height:1px;background-color:#eeeeee;"></div>
                                </td>
                            </tr>

                            <!-- Footer -->
                            <tr>
                                <td>
                                    <p style="margin:0 0 8px 0;font-size:13px;color:#999999;">{email_content['footer']}</p>
                                    <p style="margin:0;font-size:12px;color:#cccccc;">{email_content['unsubscribe']}</p>
                                </td>
                            </tr>
                        </table>
                    </td>
                </tr>
            </table>
        </body>
        </html>
        """

        # Send email using Resend
        params = {
            "from": f"zenstory <{RESEND_FROM_EMAIL}>",
            "to": [email],
            "subject": email_content['title'],
            "html": html_content,
        }

        # The SDK call is blocking HTTP; run it in a worker thread.
        r = await asyncio.to_thread(resend.Emails.send, params)  # type: ignore[arg-type]

        if r.get("id"):
            log_with_context(
                logger,
                20,  # INFO
                "Verification email sent successfully",
                email=email,
                language=language,
            )
            return True
        else:
            log_with_context(
                logger,
                40,  # ERROR
                "Failed to send verification email",
                email=email,
                language=language,
                error=str(r),  # type: ignore[attr-defined]
            )
            return False

    except Exception as e:
        log_with_context(
            logger,
            40,  # ERROR
            "Error sending verification email",
            email=email,
            error=str(e),
            error_type=type(e).__name__,
        )
        return False
