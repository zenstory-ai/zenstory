"""
Voice Recognition API endpoints

使用腾讯云 ASR 实现语音识别功能
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import time
from collections.abc import Callable, Coroutine
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field
from services.auth import get_current_active_user

from core.error_codes import ErrorCode
from core.error_handler import APIException
from middleware.rate_limit import require_user_rate_limit
from models import User
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

# SentenceRecognition Data is capped after Base64; use conservative decimal MB.
MAX_BASE64_LENGTH = 3_000_000
MAX_AUDIO_BYTES = MAX_BASE64_LENGTH // 4 * 3
MAX_VOICE_REQUEST_BYTES = MAX_BASE64_LENGTH + 4096
VOICE_RATE_LIMIT_MAX_REQUESTS = 60
VOICE_RATE_LIMIT_WINDOW_SECONDS = 3600
SUPPORTED_AUDIO_FORMATS = ("wav", "pcm", "ogg-opus", "speex", "silk", "mp3", "m4a", "aac", "amr")


class _VoiceRequestRoute(APIRoute):
    """Reject clearly oversized JSON bodies before FastAPI materializes them."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original_handler = super().get_route_handler()

        async def handler(request: Request) -> Response:
            content_length = request.headers.get("content-length")
            if (
                content_length
                and content_length.strip().isdigit()
                and int(content_length) > MAX_VOICE_REQUEST_BYTES
            ):
                raise APIException(
                    error_code=ErrorCode.VOICE_AUDIO_DECODE_FAILED,
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="Voice request body exceeds the supported limit",
                )

            original_receive = request.receive
            received_bytes = 0

            async def limited_receive() -> dict[str, Any]:
                nonlocal received_bytes
                message = await original_receive()
                if message.get("type") == "http.request":
                    received_bytes += len(message.get("body", b""))
                    if received_bytes > MAX_VOICE_REQUEST_BYTES:
                        raise APIException(
                            error_code=ErrorCode.VOICE_AUDIO_DECODE_FAILED,
                            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            detail="Voice request body exceeds the supported limit",
                        )
                return message

            request._receive = limited_receive
            await request.body()
            return await original_handler(request)

        return handler


router = APIRouter(
    prefix="/api/v1/voice",
    tags=["voice"],
    route_class=_VoiceRequestRoute,
)


# Request/Response schemas
class VoiceRecognizeRequest(BaseModel):
    """语音识别请求"""
    audio_data: str = Field(min_length=1)  # Base64 编码的音频数据
    audio_format: Literal["wav", "pcm", "ogg-opus", "speex", "silk", "mp3", "m4a", "aac", "amr"] = "wav"
    sample_rate: Literal[8000, 16000] = 16000
    language: Literal["zh", "en", "zh-CN", "en-US"] = "zh"


class VoiceRecognizeResponse(BaseModel):
    """语音识别响应"""
    text: str
    success: bool
    error: str | None = None
    duration_ms: int | None = None  # 音频时长（毫秒）


def get_tencent_credentials():
    """获取腾讯云凭证"""
    secret_id = os.getenv("TENCENT_SECRET_ID")
    secret_key = os.getenv("TENCENT_SECRET_KEY")

    if not secret_id or not secret_key:
        raise APIException(
            error_code=ErrorCode.VOICE_CREDENTIALS_NOT_CONFIGURED,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

    return secret_id, secret_key


def generate_tencent_signature(
    secret_key: str,
    method: str,
    endpoint: str,
    payload: str,  # 改为直接接收已序列化的JSON字符串
    timestamp: int
) -> tuple[str, str, str, str]:
    """
    生成腾讯云 API v3 签名

    参考文档: https://cloud.tencent.com/document/api/1093/35641

    注意: payload 必须是已序列化的JSON字符串，与实际发送的请求体完全一致
    """
    # 规范请求串
    http_request_method = method.upper()
    canonical_uri = "/"
    canonical_querystring = ""

    # 按字母顺序排序的请求头 (content-type 在 host 之前)
    ct = "application/json; charset=utf-8"
    canonical_headers = f"content-type:{ct}\nhost:{endpoint}\n"
    signed_headers = "content-type;host"

    # 请求体哈希 - 直接使用传入的payload字符串
    hashed_request_payload = hashlib.sha256(payload.encode("utf-8")).hexdigest()

    canonical_request = (
        f"{http_request_method}\n"
        f"{canonical_uri}\n"
        f"{canonical_querystring}\n"
        f"{canonical_headers}\n"
        f"{signed_headers}\n"
        f"{hashed_request_payload}"
    )

    # 待签名字符串
    algorithm = "TC3-HMAC-SHA256"
    date = time.strftime("%Y-%m-%d", time.gmtime(timestamp))
    service = "asr"
    credential_scope = f"{date}/{service}/tc3_request"
    hashed_canonical_request = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()

    string_to_sign = (
        f"{algorithm}\n"
        f"{timestamp}\n"
        f"{credential_scope}\n"
        f"{hashed_canonical_request}"
    )

    # 计算签名
    def sign(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    secret_date = sign(("TC3" + secret_key).encode("utf-8"), date)
    secret_service = sign(secret_date, service)
    secret_signing = sign(secret_service, "tc3_request")
    signature = hmac.new(secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    return signature, credential_scope, signed_headers, algorithm


async def call_tencent_asr(
    audio_data: str,
    audio_format: str,
    sample_rate: int,
    secret_id: str,
    secret_key: str,
    language: str = "zh",
) -> dict:
    """
    调用腾讯云一句话识别 API

    参考文档: https://cloud.tencent.com/document/api/1093/35646
    """
    import httpx

    endpoint = "asr.tencentcloudapi.com"
    action = "SentenceRecognition"
    version = "2019-06-14"
    region = "ap-shanghai"
    timestamp = int(time.time())

    # The frontend normalizes browser recordings; never relabel containers here.
    voice_format = audio_format

    # Engine type (Tencent ASR)
    lang = (language or "").lower()
    if lang.startswith("en"):
        lang = "en"
    elif lang.startswith("zh"):
        lang = "zh"
    else:
        lang = "zh"

    engine_type = f"16k_{lang}" if sample_rate >= 16000 else f"8k_{lang}"

    # 计算音频数据长度
    try:
        # 清理Base64字符串（移除可能的空白字符和换行符）
        clean_audio_data = audio_data
        audio_bytes = base64.b64decode(clean_audio_data, validate=True)
        data_len = len(audio_bytes)
    except Exception as e:
        raise APIException(
            error_code=ErrorCode.VOICE_AUDIO_DECODE_FAILED,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Audio data must be strict Base64"
        ) from e

    # 请求参数
    params = {
        "ProjectId": 0,
        "SubServiceType": 2,  # 一句话识别
        "EngSerViceType": engine_type,
        "SourceType": 1,  # 语音数据来源为语音数据
        "VoiceFormat": voice_format,
        "Data": clean_audio_data,  # 使用清理后的Base64字符串
        "DataLen": data_len,
        "FilterDirty": 0,  # 不过滤脏词
        "FilterModal": 0,  # 不过滤语气词
        "ConvertNumMode": 1,  # 数字转换为阿拉伯数字
    }

    # 关键：先序列化JSON，确保签名计算和请求发送使用完全相同的字符串
    # 腾讯云要求签名计算的payload与实际发送的body必须一字不差
    request_body = json.dumps(params, ensure_ascii=False, separators=(',', ':'))

    # 生成签名 - 传入已序列化的JSON字符串
    signature, credential_scope, signed_headers, algorithm = generate_tencent_signature(
        secret_key, "POST", endpoint, request_body, timestamp
    )

    # 构建 Authorization
    authorization = (
        f"{algorithm} "
        f"Credential={secret_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, "
        f"Signature={signature}"
    )

    # 请求头
    headers = {
        "Authorization": authorization,
        "Content-Type": "application/json; charset=utf-8",
        "Host": endpoint,
        "X-TC-Action": action,
        "X-TC-Version": version,
        "X-TC-Timestamp": str(timestamp),
        "X-TC-Region": region,
    }

    # 发送请求
    # 直接使用已序列化的request_body，确保与签名计算时使用的完全一致
    log_with_context(
        logger,
        logging.INFO,
        "腾讯云ASR请求",
        voice_format=voice_format,
        data_length=data_len,
        engine_type=engine_type,
        request_body_length=len(request_body),
        base64_length=len(clean_audio_data),
    )

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"https://{endpoint}",
            headers=headers,
            content=request_body.encode("utf-8")
        )

        # 记录响应信息
        log_with_context(
            logger,
            logging.INFO,
            "腾讯云ASR响应",
            status_code=response.status_code,
        )

        if response.status_code != 200:
            log_with_context(
                logger,
                logging.ERROR,
                "腾讯云API请求失败",
                status_code=response.status_code,
            )
            raise APIException(
                error_code=ErrorCode.VOICE_API_REQUEST_FAILED,
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"腾讯云 API 请求失败: {response.status_code}"
            )

        return response.json()


@router.post("/recognize", response_model=VoiceRecognizeResponse)
async def recognize_voice(
    request: VoiceRecognizeRequest,
    _current_user: User = Depends(get_current_active_user),
    _rate_limit: int = Depends(
        require_user_rate_limit(
            "voice_recognize",
            VOICE_RATE_LIMIT_MAX_REQUESTS,
            VOICE_RATE_LIMIT_WINDOW_SECONDS,
        )
    ),
):
    """
    一句话语音识别

    将音频数据转换为文字，支持 60 秒以内的短音频。

    - **audio_data**: Base64 编码的音频数据
    - **audio_format**: 音频格式 (wav, pcm, ogg-opus, speex, silk, mp3, m4a, aac, amr)
    - **sample_rate**: 采样率 (8000 或 16000)
    """
    try:
        if len(request.audio_data) > MAX_BASE64_LENGTH:
            raise APIException(
                error_code=ErrorCode.VOICE_AUDIO_DECODE_FAILED,
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Encoded audio data exceeds the 3 MB limit",
            )
        try:
            audio_bytes = base64.b64decode(request.audio_data, validate=True)
        except (ValueError, TypeError) as exc:
            raise APIException(
                error_code=ErrorCode.VOICE_AUDIO_DECODE_FAILED,
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Audio data must be strict Base64",
            ) from exc

        if len(audio_bytes) > MAX_AUDIO_BYTES:
            raise APIException(
                error_code=ErrorCode.VOICE_AUDIO_DECODE_FAILED,
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Audio data exceeds the supported encoded-data limit",
            )

        # 获取凭证
        secret_id, secret_key = get_tencent_credentials()

        # 调用腾讯云 API
        result = await call_tencent_asr(
            audio_data=request.audio_data,
            audio_format=request.audio_format,
            sample_rate=request.sample_rate,
            secret_id=secret_id,
            secret_key=secret_key,
            language=request.language,
        )

        # 解析响应
        if not isinstance(result, dict) or not isinstance(result.get("Response"), dict):
            raise ValueError("Malformed voice provider response")
        response_data = result["Response"]

        # 检查错误
        error = response_data.get("Error")
        if error:
            error_code = error.get("Code", "UnknownError")
            log_with_context(
                logger,
                logging.ERROR,
                "Tencent ASR returned an error",
                provider_error_code=error_code,
            )
            raise APIException(
                error_code=ErrorCode.VOICE_API_REQUEST_FAILED,
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Voice recognition provider rejected the request",
            )

        # 获取识别结果
        recognized_text = response_data.get("Result")
        if not isinstance(recognized_text, str):
            raise ValueError("Missing voice provider result")
        audio_duration = response_data.get("AudioDuration")

        return VoiceRecognizeResponse(
            text=recognized_text,
            success=True,
            duration_ms=int(audio_duration) if audio_duration is not None else None
        )

    except APIException:
        raise
    except Exception as e:
        log_with_context(
            logger,
            logging.ERROR,
            "Unexpected voice recognition provider failure",
            error_type=type(e).__name__,
        )
        raise APIException(
            error_code=ErrorCode.VOICE_API_REQUEST_FAILED,
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Voice recognition provider unavailable",
        ) from e


@router.get("/status")
async def voice_status():
    """
    检查语音识别服务状态
    """
    secret_id = os.getenv("TENCENT_SECRET_ID")
    secret_key = os.getenv("TENCENT_SECRET_KEY")

    return {
        "configured": bool(secret_id and secret_key),
        "provider": "tencent",
        "service": "一句话识别",
        "max_duration_seconds": 60,
        "supported_formats": list(SUPPORTED_AUDIO_FORMATS)
    }
