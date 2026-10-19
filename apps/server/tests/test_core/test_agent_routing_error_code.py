from core.error_codes import ErrorCode, get_error_message


def test_agent_routing_failure_has_fixed_bilingual_copy():
    assert ErrorCode.AGENT_ROUTING_FAILED == "ERR_AGENT_ROUTING_FAILED"
    assert get_error_message(ErrorCode.AGENT_ROUTING_FAILED, "zh") == (
        "这次没能处理你的要求，作品没有改动。可以稍后重试。"
    )
    assert get_error_message(ErrorCode.AGENT_ROUTING_FAILED, "en") == (
        "We couldn't process your request this time, and your work wasn't changed. "
        "You can try again later."
    )
