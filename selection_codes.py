"""抢课接口响应的业务状态分类常量与纯函数。

集中维护四个固定业务状态（“不在设定的选课时间范围内”“选课成功”
“课程容量已满”“不符合选课要求”）的映射规则：结果码优先、消息文本
兜底，未知结果码再使用 message 文本兼容。
"""

import orjson
import requests

SELECTION_RESULT_CODE_STATUSES: dict[str, str] = {
    "XKGL.OPERATE.RESULT_YCGDWRL": "课程容量已满",
    "XKGL.OPERATE.RESULT_YCGZRL": "课程容量已满",
    "XKGL.OPERATE.RESULT_XKSJCTDQRWHCTRWH": "不符合选课要求",
}
SELECTION_MESSAGE_STATUSES: tuple[tuple[str, str], ...] = (
    ("不在设定的选课时间范围内", "不在设定的选课时间范围内"),
    ("选课成功", "选课成功"),
    ("课程容量已满", "课程容量已满"),
    ("不符合选课要求", "不符合选课要求"),
    ("对外容量已满", "课程容量已满"),
    ("总容量已满", "课程容量已满"),
    ("容量已满", "课程容量已满"),
    ("上课时间冲突", "不符合选课要求"),
)


def classify_selection_response(response_text: str) -> str:
    """按结果码和消息文本归一抢课接口的业务状态。

    Args:
        response_text: 抢课接口返回的原始文本。

    Returns:
        四种已知业务状态之一；无法识别时返回“未知响应”。
    """
    normalized = response_text.strip()
    message_text = normalized
    try:
        parsed_response: object = orjson.loads(normalized)
    except orjson.JSONDecodeError:
        parsed_response = None
    if isinstance(parsed_response, dict):
        result_code = parsed_response.get("gjhczztm")
        if isinstance(result_code, str):
            mapped_status = SELECTION_RESULT_CODE_STATUSES.get(result_code.strip())
            if mapped_status is not None:
                return mapped_status
        response_message = parsed_response.get("message")
        if isinstance(response_message, str):
            message_text = response_message.strip()
    for message_fragment, business_status in SELECTION_MESSAGE_STATUSES:
        if message_fragment in message_text:
            return business_status
    return "未知响应"


def is_environment_request_error(error: Exception) -> bool:
    """判断请求异常是否属于网络环境类故障。

    Args:
        error: requests 请求抛出的异常实例。

    Returns:
        TLS 证书、代理或连接层故障返回 True；其他请求错误返回 False。
    """
    return isinstance(error, requests.exceptions.ConnectionError)