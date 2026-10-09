"""
Messages registry for internationalization.

Stores all API success messages and non-error messages in Chinese and English.
Use {{variable}} placeholders for string interpolation with .format()
"""


# 消息注册表,包含中英文版本
MESSAGES: dict[str, dict[str, str]] = {
    "zh": {
        # ==================== Authentication Messages ====================
        "auth_register_success": "注册成功,请检查邮箱验证码以完成账户激活",
        "auth_login_success": "登录成功",
        "auth_logout_success": "登出成功",
        "auth_token_refresh_success": "令牌刷新成功",
        "auth_email_verified": "邮箱验证成功",
        "auth_password_changed": "密码修改成功",
        "auth_verification_resent": "验证码已重新发送,请检查邮箱",
        "auth_password_reset_requested": "如果这个邮箱注册过 zenstory，验证码已经发出，{minutes} 分钟内有效。",
        "auth_password_reset_success": "密码已重设，请用新密码登录",

        # ==================== Verification Messages ====================
        "verification_resend_cooldown": "验证码发送过于频繁,请在 {{cooldown_seconds}} 秒后重试",
        "verification_send_failed": "验证码发送失败,请稍后重试",
        "verification_email_failed": "邮件发送失败,请检查邮箱地址或稍后重试",
        "verification_error": "发送验证码时发生错误,请稍后重试",
        "verification_too_many_attempts": "验证码错误次数过多,请重新发送验证码",
        "verification_expired": "验证码已过期",
        "verification_not_exist": "验证码已过期或不存在",
        "verification_incorrect": "验证码错误,还剩 {{count}} 次尝试机会",
        "verification_verify_error": "验证时发生错误",
        "verification_send_success": "验证码已发送到您的邮箱",

        # ==================== Project Messages ====================
        "project_create_success": "项目创建成功",
        "project_update_success": "项目更新成功",
        "project_delete_success": "项目删除成功",

        # ==================== File Messages ====================
        "file_create_success": "文件创建成功",
        "file_update_success": "文件更新成功",
        "file_delete_success": "文件删除成功",
        "file_move_success": "文件移动成功",

        # ==================== Version Messages ====================
        "version_create_success": "版本创建成功",
        "version_delete_success": "版本删除成功",
        "version_rollback_success": "版本回滚成功",

        # ==================== Chat Messages ====================
        "chat_create_success": "对话会话创建成功",
        "chat_delete_success": "对话会话删除成功",

        # ==================== Export Messages ====================
        "export_start_success": "导出任务已开始",
        "export_complete": "导出完成",
    },
    "en": {
        # ==================== Authentication Messages ====================
        "auth_register_success": "Registration successful, please check your email for verification code",
        "auth_login_success": "Login successful",
        "auth_logout_success": "Logout successful",
        "auth_token_refresh_success": "Token refresh successful",
        "auth_email_verified": "Email verified successfully",
        "auth_password_changed": "Password changed successfully",
        "auth_verification_resent": "Verification code has been resent, please check your email",
        "auth_password_reset_requested": "If this email is registered with zenstory, we've sent a code. It's valid for {minutes} minutes.",
        "auth_password_reset_success": "Your password has been reset. Log in with your new password.",

        # ==================== Verification Messages ====================
        "verification_resend_cooldown": "Verification code sent too frequently, please retry after {{cooldown_seconds}} seconds",
        "verification_send_failed": "Failed to send verification code, please try again later",
        "verification_email_failed": "Failed to send email, please check your email address or try again later",
        "verification_error": "An error occurred. Please try again later",
        "verification_too_many_attempts": "Too many incorrect attempts, please request a new code",
        "verification_expired": "Verification code has expired",
        "verification_not_exist": "Verification code has expired or does not exist",
        "verification_incorrect": "Incorrect verification code, {{count}} attempts remaining",
        "verification_verify_error": "An error occurred during verification",
        "verification_send_success": "Verification code has been sent to your email",

        # ==================== Project Messages ====================
        "project_create_success": "Project created successfully",
        "project_update_success": "Project updated successfully",
        "project_delete_success": "Project deleted successfully",

        # ==================== File Messages ====================
        "file_create_success": "File created successfully",
        "file_update_success": "File updated successfully",
        "file_delete_success": "File deleted successfully",
        "file_move_success": "File moved successfully",

        # ==================== Version Messages ====================
        "version_create_success": "Version created successfully",
        "version_delete_success": "Version deleted successfully",
        "version_rollback_success": "Version rollback successful",

        # ==================== Chat Messages ====================
        "chat_create_success": "Chat session created successfully",
        "chat_delete_success": "Chat session deleted successfully",

        # ==================== Export Messages ====================
        "export_start_success": "Export task has started",
        "export_complete": "Export completed",
    },
}


def get_message(key: str, lang: str = "zh") -> str:
    """
    根据键和语言获取国际化消息

    Args:
        key: 消息键,如 'auth_login_success'
        lang: 语言代码,默认 'zh',可选 'zh' 或 'en'

    Returns:
        对应的消息文本。如果键或语言不存在,返回键本身

    Examples:
        >>> get_message('auth_login_success', 'zh')
        '登录成功'
        >>> get_message('auth_login_success', 'en')
        'Login successful'
        >>> get_message('verification_resend_cooldown', 'zh').format(cooldown_seconds=60)
        '验证码发送过于频繁,请在 60 秒后重试'
    """
    # 如果语言不支持,使用默认语言
    if lang not in MESSAGES:
        lang = "zh"

    # 获取消息
    message = MESSAGES[lang].get(key, key)

    return message
