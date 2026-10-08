"""
Error codes definition for internationalization.

All error codes follow the format: ERR_{MODULE}_{SPECIFIC_ERROR}
Module codes:
- 1xxx: Common errors
- 2xxx: Authentication & authorization errors
- 3xxx: Project errors
- 4xxx: File errors
- 5xxx: Version errors
- 6xxx: Snapshot errors
- 7xxx: Export errors
- 8xxx: Chat errors
- 9xxx: Voice errors
- 10xxx: Subscription errors
- 11xxx: Quota errors
- 12xxx: Redemption errors
- 13xxx: Points errors
- 14xxx: Agent stream errors
"""


class ErrorCode:
    """Error code constants."""

    # ==================== Common Errors (1000-1999) ====================

    INTERNAL_SERVER_ERROR = "ERR_INTERNAL_SERVER_ERROR"
    VALIDATION_ERROR = "ERR_VALIDATION_ERROR"
    DATABASE_ERROR = "ERR_DATABASE_ERROR"
    SERVICE_UNAVAILABLE = "ERR_SERVICE_UNAVAILABLE"
    BAD_REQUEST = "ERR_BAD_REQUEST"
    NOT_FOUND = "ERR_NOT_FOUND"
    RESOURCE_CONFLICT = "ERR_RESOURCE_CONFLICT"

    # ==================== Authentication & Authorization Errors (2000-2999) ====================

    AUTH_UNAUTHORIZED = "ERR_AUTH_UNAUTHORIZED"
    AUTH_INVALID_CREDENTIALS = "ERR_AUTH_INVALID_CREDENTIALS"
    AUTH_TOKEN_EXPIRED = "ERR_AUTH_TOKEN_EXPIRED"
    AUTH_TOKEN_INVALID = "ERR_AUTH_TOKEN_INVALID"
    AUTH_INACTIVE_USER = "ERR_AUTH_INACTIVE_USER"
    AUTH_SCOPE_DENIED = "ERR_AUTH_SCOPE_DENIED"
    AUTH_USERNAME_EXISTS = "ERR_AUTH_USERNAME_EXISTS"
    AUTH_EMAIL_EXISTS = "ERR_AUTH_EMAIL_EXISTS"
    AUTH_EMAIL_NOT_VERIFIED = "ERR_AUTH_EMAIL_NOT_VERIFIED"
    AUTH_EMAIL_ALREADY_VERIFIED = "ERR_AUTH_EMAIL_ALREADY_VERIFIED"
    AUTH_INVALID_VERIFICATION_CODE = "ERR_AUTH_INVALID_VERIFICATION_CODE"
    AUTH_INVITE_CODE_REQUIRED = "ERR_AUTH_INVITE_CODE_REQUIRED"
    AUTH_REGISTRATION_FAILED = "ERR_AUTH_REGISTRATION_FAILED"
    AUTH_RESEND_FAILED = "ERR_AUTH_RESEND_FAILED"
    AUTH_RATE_LIMIT_EXCEEDED = "ERR_AUTH_RATE_LIMIT_EXCEEDED"
    NOT_AUTHORIZED = "ERR_NOT_AUTHORIZED"
    NOT_AUTHORIZED_TO_EXPORT = "ERR_NOT_AUTHORIZED_TO_EXPORT"
    NOT_AUTHORIZED_TO_ACCESS_SNAPSHOT = "ERR_NOT_AUTHORIZED_TO_ACCESS_SNAPSHOT"
    NOT_AUTHORIZED_TO_ACCESS_FIRST_SNAPSHOT = "ERR_NOT_AUTHORIZED_TO_ACCESS_FIRST_SNAPSHOT"
    NOT_AUTHORIZED_TO_ACCESS_SECOND_SNAPSHOT = "ERR_NOT_AUTHORIZED_TO_ACCESS_SECOND_SNAPSHOT"

    # ==================== Project Errors (3000-3999) ====================

    PROJECT_NOT_FOUND = "ERR_PROJECT_NOT_FOUND"
    PROJECT_ALREADY_EXISTS = "ERR_PROJECT_ALREADY_EXISTS"
    PROJECT_SOFT_DELETED = "ERR_PROJECT_SOFT_DELETED"
    PROJECT_EXPORT_NO_DRAFTS = "ERR_PROJECT_EXPORT_NO_DRAFTS"

    # ==================== File Errors (4000-4999) ====================

    FILE_NOT_FOUND = "ERR_FILE_NOT_FOUND"
    FILE_TYPE_INVALID = "ERR_FILE_TYPE_INVALID"
    FILE_TOO_LARGE = "ERR_FILE_TOO_LARGE"
    FILE_CONTENT_TOO_LONG = "ERR_FILE_CONTENT_TOO_LONG"
    FILE_ENCODING_UNSUPPORTED = "ERR_FILE_ENCODING_UNSUPPORTED"
    VECTOR_SEARCH_UNAVAILABLE = "ERR_VECTOR_SEARCH_UNAVAILABLE"

    # ==================== Material Decomposition Errors ====================
    # Upload pre-checks (HTTP errors) and ingestion-job failure codes. Jobs store
    # only these codes in IngestionJob.error_message; raw exceptions stay in logs.

    MATERIAL_NO_CHAPTERS = "ERR_MATERIAL_NO_CHAPTERS"
    MATERIAL_TOO_MANY_CHAPTERS = "ERR_MATERIAL_TOO_MANY_CHAPTERS"
    MATERIAL_FILE_UNREADABLE = "ERR_MATERIAL_FILE_UNREADABLE"
    MATERIAL_DISPATCH_FAILED = "ERR_MATERIAL_DISPATCH_FAILED"
    MATERIAL_DISPATCH_TIMEOUT = "ERR_MATERIAL_DISPATCH_TIMEOUT"
    MATERIAL_PROCESSING_TIMEOUT = "ERR_MATERIAL_PROCESSING_TIMEOUT"
    MATERIAL_LLM_UNAVAILABLE = "ERR_MATERIAL_LLM_UNAVAILABLE"
    MATERIAL_EXTRACTION_FAILED = "ERR_MATERIAL_EXTRACTION_FAILED"
    MATERIAL_PARTIALLY_COMPLETED = "ERR_MATERIAL_PARTIALLY_COMPLETED"
    MATERIAL_DECOMPOSE_FAILED = "ERR_MATERIAL_DECOMPOSE_FAILED"

    # ==================== Skill Package Errors ====================

    SKILL_PACKAGE_INVALID = "ERR_SKILL_PACKAGE_INVALID"
    SKILL_PACKAGE_TOO_LARGE = "ERR_SKILL_PACKAGE_TOO_LARGE"

    # ==================== Version Errors (5000-5999) ====================

    VERSION_NOT_FOUND = "ERR_VERSION_NOT_FOUND"
    VERSION_NO_VERSIONS_FOUND = "ERR_VERSION_NO_VERSIONS_FOUND"
    VERSION_DELETE_FAILED = "ERR_VERSION_DELETE_FAILED"
    VERSION_RESTORE_FAILED = "ERR_VERSION_RESTORE_FAILED"

    # ==================== Snapshot Errors (6000-6999) ====================

    SNAPSHOT_NOT_FOUND = "ERR_SNAPSHOT_NOT_FOUND"
    SNAPSHOT_ONE_OR_BOTH_NOT_FOUND = "ERR_SNAPSHOT_ONE_OR_BOTH_NOT_FOUND"
    SNAPSHOT_FIRST_NOT_FOUND = "ERR_SNAPSHOT_FIRST_NOT_FOUND"
    SNAPSHOT_SECOND_NOT_FOUND = "ERR_SNAPSHOT_SECOND_NOT_FOUND"
    SNAPSHOT_DIFF_FAILED = "ERR_SNAPSHOT_DIFF_FAILED"
    SNAPSHOT_CREATE_FAILED = "ERR_SNAPSHOT_CREATE_FAILED"

    # ==================== Export Errors (7000-7999) ====================

    EXPORT_NO_DRAFTS = "ERR_EXPORT_NO_DRAFTS"

    # ==================== Chat Errors (8000-8999) ====================

    CHAT_SESSION_NOT_FOUND = "ERR_CHAT_SESSION_NOT_FOUND"
    CHAT_MESSAGE_NOT_FOUND = "ERR_CHAT_MESSAGE_NOT_FOUND"

    # ==================== Voice Errors (9000-9999) ====================

    VOICE_CREDENTIALS_NOT_CONFIGURED = "ERR_VOICE_CREDENTIALS_NOT_CONFIGURED"
    VOICE_AUDIO_DECODE_FAILED = "ERR_VOICE_AUDIO_DECODE_FAILED"
    VOICE_API_REQUEST_FAILED = "ERR_VOICE_API_REQUEST_FAILED"

    # ==================== Inspiration Errors (10000-10999) ====================

    INSPIRATION_NOT_FOUND = "ERR_INSPIRATION_NOT_FOUND"
    INSPIRATION_COPY_FAILED = "ERR_INSPIRATION_COPY_FAILED"

    # ==================== Referral Errors (11000-11999) ====================

    REFERRAL_NOT_FOUND = "ERR_REFERRAL_NOT_FOUND"
    REFERRAL_ALREADY_EXISTS = "ERR_REFERRAL_ALREADY_EXISTS"
    REFERRAL_MAX_CODES_REACHED = "ERR_REFERRAL_MAX_CODES_REACHED"
    REFERRAL_CODE_INVALID = "ERR_REFERRAL_CODE_INVALID"
    REFERRAL_CODE_EXPIRED = "ERR_REFERRAL_CODE_EXPIRED"
    REFERRAL_CODE_USED_UP = "ERR_REFERRAL_CODE_USED_UP"

    # ==================== Subscription Errors (10xxx) ====================

    SUBSCRIPTION_NOT_FOUND = "ERR_SUBSCRIPTION_NOT_FOUND"
    SUBSCRIPTION_EXPIRED = "ERR_SUBSCRIPTION_EXPIRED"
    FEATURE_NOT_INCLUDED = "ERR_FEATURE_NOT_INCLUDED"
    PAYMENT_SYNC_FAILED = "ERR_PAYMENT_SYNC_FAILED"

    # ==================== Quota Errors (11xxx) ====================

    QUOTA_EXCEEDED = "ERR_QUOTA_EXCEEDED"
    QUOTA_AI_CONVERSATIONS_EXCEEDED = "ERR_QUOTA_AI_CONVERSATIONS_EXCEEDED"
    QUOTA_AI_DAILY_COST_EXCEEDED = "ERR_QUOTA_AI_DAILY_COST_EXCEEDED"
    AI_COST_BUDGET_UNAVAILABLE = "ERR_AI_COST_BUDGET_UNAVAILABLE"
    QUOTA_PROJECTS_EXCEEDED = "ERR_QUOTA_PROJECTS_EXCEEDED"
    QUOTA_FILE_VERSIONS_EXCEEDED = "ERR_QUOTA_FILE_VERSIONS_EXCEEDED"
    QUOTA_EXPORT_FORMAT_RESTRICTED = "ERR_QUOTA_EXPORT_FORMAT_RESTRICTED"

    # ==================== Redemption Errors (12xxx) ====================

    REDEMPTION_CODE_INVALID = "ERR_REDEMPTION_CODE_INVALID"
    REDEMPTION_CODE_EXPIRED = "ERR_REDEMPTION_CODE_EXPIRED"
    REDEMPTION_CODE_USED = "ERR_REDEMPTION_CODE_USED"
    REDEMPTION_CODE_DISABLED = "ERR_REDEMPTION_CODE_DISABLED"
    REDEMPTION_CODE_CHECKSUM_FAILED = "ERR_REDEMPTION_CODE_CHECKSUM_FAILED"
    REDEMPTION_CODE_DOWNGRADE = "ERR_REDEMPTION_CODE_DOWNGRADE"
    REDEMPTION_CODE_ALREADY_REDEEMED_BY_YOU = "ERR_REDEMPTION_CODE_ALREADY_REDEEMED_BY_YOU"
    REDEMPTION_RATE_LIMIT_EXCEEDED = "ERR_REDEMPTION_RATE_LIMIT_EXCEEDED"

    # ==================== Points Errors (13xxx) ====================

    POINTS_INSUFFICIENT_BALANCE = "ERR_POINTS_INSUFFICIENT_BALANCE"
    POINTS_ALREADY_CHECKED_IN = "ERR_POINTS_ALREADY_CHECKED_IN"
    POINTS_INVALID_AMOUNT = "ERR_POINTS_INVALID_AMOUNT"
    POINTS_REDEMPTION_MINIMUM_NOT_MET = "ERR_POINTS_REDEMPTION_MINIMUM_NOT_MET"

    # ==================== Agent Stream Errors (14xxx) ====================

    SESSION_BUSY = "ERR_SESSION_BUSY"
    AGENT_UPSTREAM_RATE_LIMITED = "ERR_AGENT_UPSTREAM_RATE_LIMITED"
    AGENT_UPSTREAM_UNAVAILABLE = "ERR_AGENT_UPSTREAM_UNAVAILABLE"
    AGENT_CONTEXT_TOO_LONG = "ERR_AGENT_CONTEXT_TOO_LONG"
    AGENT_RUN_FAILED = "ERR_AGENT_RUN_FAILED"
    AGENT_TOOL_FAILURE_LIMIT = "ERR_AGENT_TOOL_FAILURE_LIMIT"
    AGENT_RUN_TIMEOUT = "ERR_AGENT_RUN_TIMEOUT"
    AGENT_MODEL_CALL_LIMIT = "ERR_AGENT_MODEL_CALL_LIMIT"
    AGENT_FILE_SAVE_FAILED = "ERR_AGENT_FILE_SAVE_FAILED"


# Error messages mapping (for server-side reference, if needed)
# Note: These messages are primarily used by frontend i18n
ERROR_MESSAGES = {
    "zh": {
        "ERR_INTERNAL_SERVER_ERROR": "服务器内部错误，请稍后重试",
        "ERR_VALIDATION_ERROR": "请求参数验证失败",
        "ERR_DATABASE_ERROR": "数据库操作失败",
        "ERR_RESOURCE_CONFLICT": "操作与现有数据冲突，请检查后重试",
        "ERR_SERVICE_UNAVAILABLE": "服务暂时不可用",
        "ERR_BAD_REQUEST": "请求格式错误",
        "ERR_NOT_FOUND": "资源不存在",

        "ERR_AUTH_UNAUTHORIZED": "未授权访问",
        "ERR_AUTH_INVALID_CREDENTIALS": "用户名或密码错误",
        "ERR_AUTH_TOKEN_EXPIRED": "登录已过期，请重新登录",
        "ERR_AUTH_TOKEN_INVALID": "无效的登录凭证",
        "ERR_AUTH_INACTIVE_USER": "账户已被禁用",
        "ERR_AUTH_USERNAME_EXISTS": "用户名已被注册",
        "ERR_AUTH_EMAIL_EXISTS": "邮箱已被注册",
        "ERR_AUTH_EMAIL_NOT_VERIFIED": "邮箱未验证，请先验证邮箱",
        "ERR_AUTH_EMAIL_ALREADY_VERIFIED": "邮箱已验证",
        "ERR_AUTH_INVALID_VERIFICATION_CODE": "验证码无效或已过期",
        "ERR_AUTH_INVITE_CODE_REQUIRED": "请输入邀请码",
        "ERR_AUTH_REGISTRATION_FAILED": "注册失败，请稍后重试",
        "ERR_AUTH_RESEND_FAILED": "验证码发送失败，请稍后重试",
        "ERR_AUTH_RATE_LIMIT_EXCEEDED": "请求过于频繁，请稍后重试",
        "ERR_NOT_AUTHORIZED": "您没有权限执行此操作",
        "ERR_NOT_AUTHORIZED_TO_EXPORT": "您没有权限导出此项目",
        "ERR_NOT_AUTHORIZED_TO_ACCESS_SNAPSHOT": "您没有权限访问此快照",
        "ERR_NOT_AUTHORIZED_TO_ACCESS_FIRST_SNAPSHOT": "您没有权限访问第一个快照",
        "ERR_NOT_AUTHORIZED_TO_ACCESS_SECOND_SNAPSHOT": "您没有权限访问第二个快照",

        "ERR_PROJECT_NOT_FOUND": "项目不存在",
        "ERR_PROJECT_ALREADY_EXISTS": "项目已存在",
        "ERR_PROJECT_SOFT_DELETED": "项目已删除",
        "ERR_PROJECT_EXPORT_NO_DRAFTS": "项目中没有草稿可导出",

        "ERR_FILE_NOT_FOUND": "文件不存在",
        "ERR_FILE_TYPE_INVALID": "文件类型无效",
        "ERR_FILE_TOO_LARGE": "文件过大",
        "ERR_FILE_CONTENT_TOO_LONG": "文件内容过长",
        "ERR_FILE_ENCODING_UNSUPPORTED": "无法识别文件编码，请转存为 UTF-8 或 GBK 后上传",
        "ERR_MATERIAL_NO_CHAPTERS": "没有识别到章节标题",
        "ERR_MATERIAL_TOO_MANY_CHAPTERS": "章节数量超过单次拆解上限",
        "ERR_MATERIAL_FILE_UNREADABLE": "原始文件无法读取",
        "ERR_MATERIAL_DISPATCH_FAILED": "拆解任务启动失败",
        "ERR_MATERIAL_DISPATCH_TIMEOUT": "拆解任务排队超时",
        "ERR_MATERIAL_PROCESSING_TIMEOUT": "拆解任务处理超时",
        "ERR_MATERIAL_LLM_UNAVAILABLE": "AI 服务暂时不可用",
        "ERR_MATERIAL_EXTRACTION_FAILED": "章节内容拆解失败",
        "ERR_MATERIAL_PARTIALLY_COMPLETED": "部分内容拆解失败",
        "ERR_MATERIAL_DECOMPOSE_FAILED": "拆解失败",
        "ERR_VECTOR_SEARCH_UNAVAILABLE": "向量搜索服务不可用",
        "ERR_SKILL_PACKAGE_INVALID": "技能包格式无效",
        "ERR_SKILL_PACKAGE_TOO_LARGE": "技能包或资源文件超出大小限制",

        "ERR_VERSION_NOT_FOUND": "版本不存在",
        "ERR_VERSION_NO_VERSIONS_FOUND": "未找到文件的版本",
        "ERR_VERSION_DELETE_FAILED": "删除版本失败",
        "ERR_VERSION_RESTORE_FAILED": "恢复版本失败",

        "ERR_SNAPSHOT_NOT_FOUND": "快照不存在",
        "ERR_SNAPSHOT_ONE_OR_BOTH_NOT_FOUND": "未找到快照",
        "ERR_SNAPSHOT_FIRST_NOT_FOUND": "未找到第一个快照",
        "ERR_SNAPSHOT_SECOND_NOT_FOUND": "未找到第二个快照",
        "ERR_SNAPSHOT_DIFF_FAILED": "比较快照失败",
        "ERR_SNAPSHOT_CREATE_FAILED": "创建快照失败",

        "ERR_EXPORT_NO_DRAFTS": "此项目中没有草稿",

        "ERR_CHAT_SESSION_NOT_FOUND": "对话会话不存在",
        "ERR_CHAT_MESSAGE_NOT_FOUND": "消息不存在",

        "ERR_VOICE_CREDENTIALS_NOT_CONFIGURED": "腾讯云 API 凭证未配置",
        "ERR_VOICE_AUDIO_DECODE_FAILED": "音频数据解码失败",
        "ERR_VOICE_API_REQUEST_FAILED": "语音识别 API 请求失败",

        "ERR_INSPIRATION_NOT_FOUND": "灵感不存在",
        "ERR_INSPIRATION_COPY_FAILED": "复制灵感失败",
        "ERR_REFERRAL_NOT_FOUND": "邀请记录不存在",
        "ERR_REFERRAL_ALREADY_EXISTS": "用户已有邀请记录",
        "ERR_REFERRAL_MAX_CODES_REACHED": "已达到邀请码数量上限",
        "ERR_REFERRAL_CODE_INVALID": "邀请码无效",
        "ERR_REFERRAL_CODE_EXPIRED": "邀请码已过期",
        "ERR_REFERRAL_CODE_USED_UP": "邀请码已用完",

        "ERR_SUBSCRIPTION_NOT_FOUND": "订阅不存在",
        "ERR_SUBSCRIPTION_EXPIRED": "订阅已过期",
        "ERR_FEATURE_NOT_INCLUDED": "当前套餐暂不包含该功能",
        "ERR_PAYMENT_SYNC_FAILED": "暂时无法向支付平台查询订单，请稍后刷新",

        "ERR_QUOTA_EXCEEDED": "配额已用尽",
        "ERR_QUOTA_AI_CONVERSATIONS_EXCEEDED": "AI 对话次数已达上限",
        "ERR_QUOTA_AI_DAILY_COST_EXCEEDED": "今日 AI 额度已用完，北京时间次日零点恢复，升级 Pro 可继续创作",
        "ERR_AI_COST_BUDGET_UNAVAILABLE": "暂时无法确认 AI 可用额度，请稍后重试",
        "ERR_QUOTA_PROJECTS_EXCEEDED": "项目数量已达上限",
        "ERR_QUOTA_FILE_VERSIONS_EXCEEDED": "文件版本数量已达上限",
        "ERR_QUOTA_EXPORT_FORMAT_RESTRICTED": "当前订阅不支持此导出格式",

        "ERR_REDEMPTION_CODE_INVALID": "兑换码无效",
        "ERR_REDEMPTION_CODE_EXPIRED": "兑换码已过期",
        "ERR_REDEMPTION_CODE_USED": "兑换码已被使用",
        "ERR_REDEMPTION_CODE_DISABLED": "兑换码已被禁用",
        "ERR_REDEMPTION_CODE_CHECKSUM_FAILED": "兑换码校验失败",
        "ERR_REDEMPTION_CODE_DOWNGRADE": "当前付费套餐仍在有效期内，不能兑换更低档位的兑换码",
        "ERR_REDEMPTION_CODE_ALREADY_REDEEMED_BY_YOU": "你已经兑换过这个码了，权益已在账户里",
        "ERR_REDEMPTION_RATE_LIMIT_EXCEEDED": "兑换请求过于频繁，请稍后重试",

        "ERR_POINTS_INSUFFICIENT_BALANCE": "积分余额不足",
        "ERR_POINTS_ALREADY_CHECKED_IN": "今日已签到",
        "ERR_POINTS_INVALID_AMOUNT": "积分数量无效",
        "ERR_POINTS_REDEMPTION_MINIMUM_NOT_MET": "兑换最低需要 7 天",

        "ERR_SESSION_BUSY": "上一轮回复仍在生成或收尾中，请稍候片刻再发送，或开启新对话",
        "ERR_AGENT_UPSTREAM_RATE_LIMITED": "AI 服务当前繁忙，请稍后重试",
        "ERR_AGENT_UPSTREAM_UNAVAILABLE": "AI 服务暂时不可用或响应超时，请稍后重试",
        "ERR_AGENT_CONTEXT_TOO_LONG": "本轮对话内容过长，请精简消息或开启新对话后重试",
        "ERR_AGENT_RUN_FAILED": "生成回复时发生错误，请重试",
        "ERR_AGENT_TOOL_FAILURE_LIMIT": "AI 反复调用工具失败，已停止本轮生成",
        "ERR_AGENT_RUN_TIMEOUT": "本轮生成时间过长，已自动停止",
        "ERR_AGENT_MODEL_CALL_LIMIT": "本轮生成的模型调用次数已达上限，已自动停止",
        "ERR_AGENT_FILE_SAVE_FAILED": "保存生成的文件内容失败，请重试",
    },
    "en": {
        "ERR_INTERNAL_SERVER_ERROR": "Internal server error, please try again later",
        "ERR_VALIDATION_ERROR": "Request validation failed",
        "ERR_DATABASE_ERROR": "Database operation failed",
        "ERR_RESOURCE_CONFLICT": "The request conflicts with existing data",
        "ERR_SERVICE_UNAVAILABLE": "Service temporarily unavailable",
        "ERR_BAD_REQUEST": "Invalid request format",
        "ERR_NOT_FOUND": "Resource not found",

        "ERR_AUTH_UNAUTHORIZED": "Unauthorized access",
        "ERR_AUTH_INVALID_CREDENTIALS": "Invalid username or password",
        "ERR_AUTH_TOKEN_EXPIRED": "Session expired, please login again",
        "ERR_AUTH_TOKEN_INVALID": "Invalid authentication token",
        "ERR_AUTH_INACTIVE_USER": "Account has been disabled",
        "ERR_AUTH_USERNAME_EXISTS": "Username already registered",
        "ERR_AUTH_EMAIL_EXISTS": "Email already registered",
        "ERR_AUTH_EMAIL_NOT_VERIFIED": "Email not verified, please verify your email first",
        "ERR_AUTH_EMAIL_ALREADY_VERIFIED": "Email already verified",
        "ERR_AUTH_INVALID_VERIFICATION_CODE": "Invalid or expired verification code",
        "ERR_AUTH_INVITE_CODE_REQUIRED": "Invite code is required",
        "ERR_AUTH_REGISTRATION_FAILED": "Registration failed, please try again later",
        "ERR_AUTH_RESEND_FAILED": "Failed to send verification code, please try again later",
        "ERR_AUTH_RATE_LIMIT_EXCEEDED": "Too many requests, please try again later",
        "ERR_NOT_AUTHORIZED": "You are not authorized to perform this action",
        "ERR_NOT_AUTHORIZED_TO_EXPORT": "You are not authorized to export this project",
        "ERR_NOT_AUTHORIZED_TO_ACCESS_SNAPSHOT": "You are not authorized to access this snapshot",
        "ERR_NOT_AUTHORIZED_TO_ACCESS_FIRST_SNAPSHOT": "You are not authorized to access the first snapshot",
        "ERR_NOT_AUTHORIZED_TO_ACCESS_SECOND_SNAPSHOT": "You are not authorized to access the second snapshot",

        "ERR_PROJECT_NOT_FOUND": "Project not found",
        "ERR_PROJECT_ALREADY_EXISTS": "Project already exists",
        "ERR_PROJECT_SOFT_DELETED": "Project has been deleted",
        "ERR_PROJECT_EXPORT_NO_DRAFTS": "No drafts available to export",

        "ERR_FILE_NOT_FOUND": "File not found",
        "ERR_FILE_TYPE_INVALID": "Invalid file type",
        "ERR_FILE_TOO_LARGE": "File is too large",
        "ERR_FILE_CONTENT_TOO_LONG": "File content is too long",
        "ERR_FILE_ENCODING_UNSUPPORTED": "Unrecognized text encoding; save the file as UTF-8 or GBK",
        "ERR_MATERIAL_NO_CHAPTERS": "No chapter headings were recognized",
        "ERR_MATERIAL_TOO_MANY_CHAPTERS": "Too many chapters for one decomposition",
        "ERR_MATERIAL_FILE_UNREADABLE": "The original file could not be read",
        "ERR_MATERIAL_DISPATCH_FAILED": "The decomposition job failed to start",
        "ERR_MATERIAL_DISPATCH_TIMEOUT": "The decomposition job timed out in the queue",
        "ERR_MATERIAL_PROCESSING_TIMEOUT": "The decomposition job timed out",
        "ERR_MATERIAL_LLM_UNAVAILABLE": "The AI service is temporarily unavailable",
        "ERR_MATERIAL_EXTRACTION_FAILED": "Chapter decomposition failed",
        "ERR_MATERIAL_PARTIALLY_COMPLETED": "Some content could not be decomposed",
        "ERR_MATERIAL_DECOMPOSE_FAILED": "Decomposition failed",
        "ERR_VECTOR_SEARCH_UNAVAILABLE": "Vector search service unavailable",
        "ERR_SKILL_PACKAGE_INVALID": "Invalid skill package",
        "ERR_SKILL_PACKAGE_TOO_LARGE": "Skill package or resource exceeds size limits",

        "ERR_VERSION_NOT_FOUND": "Version not found",
        "ERR_VERSION_NO_VERSIONS_FOUND": "No versions found for this file",
        "ERR_VERSION_DELETE_FAILED": "Failed to delete version",
        "ERR_VERSION_RESTORE_FAILED": "Failed to restore version",

        "ERR_SNAPSHOT_NOT_FOUND": "Snapshot not found",
        "ERR_SNAPSHOT_ONE_OR_BOTH_NOT_FOUND": "One or both snapshots not found",
        "ERR_SNAPSHOT_FIRST_NOT_FOUND": "First snapshot not found",
        "ERR_SNAPSHOT_SECOND_NOT_FOUND": "Second snapshot not found",
        "ERR_SNAPSHOT_DIFF_FAILED": "Failed to compare snapshots",
        "ERR_SNAPSHOT_CREATE_FAILED": "Failed to create snapshot",

        "ERR_EXPORT_NO_DRAFTS": "No drafts found in this project",

        "ERR_CHAT_SESSION_NOT_FOUND": "Chat session not found",
        "ERR_CHAT_MESSAGE_NOT_FOUND": "Message not found",

        "ERR_VOICE_CREDENTIALS_NOT_CONFIGURED": "Tencent Cloud API credentials not configured",
        "ERR_VOICE_AUDIO_DECODE_FAILED": "Failed to decode audio data",
        "ERR_VOICE_API_REQUEST_FAILED": "Voice recognition API request failed",

        "ERR_INSPIRATION_NOT_FOUND": "Inspiration not found",
        "ERR_INSPIRATION_COPY_FAILED": "Failed to copy inspiration",
        "ERR_REFERRAL_NOT_FOUND": "Referral record not found",
        "ERR_REFERRAL_ALREADY_EXISTS": "User already has a referral record",
        "ERR_REFERRAL_MAX_CODES_REACHED": "Maximum number of invite codes reached",
        "ERR_REFERRAL_CODE_INVALID": "Invalid invite code",
        "ERR_REFERRAL_CODE_EXPIRED": "Invite code has expired",
        "ERR_REFERRAL_CODE_USED_UP": "Invite code has reached usage limit",

        "ERR_SUBSCRIPTION_NOT_FOUND": "Subscription not found",
        "ERR_SUBSCRIPTION_EXPIRED": "Subscription has expired",
        "ERR_FEATURE_NOT_INCLUDED": "This feature is not included in the current plan",
        "ERR_PAYMENT_SYNC_FAILED": "Could not check the order with the payment provider. Refresh later.",

        "ERR_QUOTA_EXCEEDED": "Quota exceeded",
        "ERR_QUOTA_AI_CONVERSATIONS_EXCEEDED": "AI conversation limit reached",
        "ERR_QUOTA_AI_DAILY_COST_EXCEEDED": "Today’s AI allowance is used up. It resets the next day at midnight Beijing time. Upgrade to Pro to continue writing",
        "ERR_AI_COST_BUDGET_UNAVAILABLE": "We cannot confirm your AI allowance right now. Please try again later",
        "ERR_QUOTA_PROJECTS_EXCEEDED": "Project limit reached",
        "ERR_QUOTA_FILE_VERSIONS_EXCEEDED": "File version limit reached",
        "ERR_QUOTA_EXPORT_FORMAT_RESTRICTED": "Export format not available in your subscription plan",

        "ERR_REDEMPTION_CODE_INVALID": "Invalid redemption code",
        "ERR_REDEMPTION_CODE_EXPIRED": "Redemption code has expired",
        "ERR_REDEMPTION_CODE_USED": "Redemption code has already been used",
        "ERR_REDEMPTION_CODE_DISABLED": "Redemption code has been disabled",
        "ERR_REDEMPTION_CODE_CHECKSUM_FAILED": "Redemption code verification failed",
        "ERR_REDEMPTION_CODE_DOWNGRADE": "Your paid plan is still active; a lower-tier code cannot be redeemed",
        "ERR_REDEMPTION_CODE_ALREADY_REDEEMED_BY_YOU": "You've already redeemed this code; it's on your account.",
        "ERR_REDEMPTION_RATE_LIMIT_EXCEEDED": "Too many redemption attempts, please try again later",

        "ERR_POINTS_INSUFFICIENT_BALANCE": "Insufficient points balance",
        "ERR_POINTS_ALREADY_CHECKED_IN": "Already checked in today",
        "ERR_POINTS_INVALID_AMOUNT": "Invalid points amount",
        "ERR_POINTS_REDEMPTION_MINIMUM_NOT_MET": "Minimum redemption is 7 days",

        "ERR_SESSION_BUSY": "The previous reply is still being generated or finalized. Wait a moment, or start a new chat",
        "ERR_AGENT_UPSTREAM_RATE_LIMITED": "The AI service is busy right now. Please try again shortly",
        "ERR_AGENT_UPSTREAM_UNAVAILABLE": "The AI service is unavailable or timed out. Please try again shortly",
        "ERR_AGENT_CONTEXT_TOO_LONG": "This conversation is too long. Shorten your message or start a new chat",
        "ERR_AGENT_RUN_FAILED": "Something went wrong while generating the reply. Please try again",
        "ERR_AGENT_TOOL_FAILURE_LIMIT": "The AI kept failing to use its tools, so this reply was stopped",
        "ERR_AGENT_RUN_TIMEOUT": "This reply took too long and was stopped automatically",
        "ERR_AGENT_MODEL_CALL_LIMIT": "This reply reached its model-call limit and was stopped automatically",
        "ERR_AGENT_FILE_SAVE_FAILED": "Failed to save the generated file content. Please try again",
    },
}


def get_error_message(error_code: str, lang: str = "zh") -> str:
    """
    Get error message by error code and language.

    Args:
        error_code: The error code constant
        lang: Language code (zh or en)

    Returns:
        Translated error message or the error code itself if not found
    """
    return ERROR_MESSAGES.get(lang, ERROR_MESSAGES.get("zh", {})).get(error_code, error_code)
