"""统一认证纯 HTTP 微信扫码登录模块的单元测试。

测试使用可编程会话替身模拟统一认证各阶段接口，不发起真实网络请求。
"""

from typing import Any, Callable

import pytest
import requests

from ustb_sso_client import (
    BYYT_ENTITY_ID,
    BYYT_REDIRECT_URI,
    BYYT_STATE,
    SsoApiError,
    SsoBadResponseError,
    SsoIllegalStateError,
    SsoLoginCanceledError,
    SsoQrExpiredError,
    SsoQrTimeoutError,
    UstbSsoQrLogin,
)

_AUTH_ENTRY = "https://sso.ustb.edu.cn/idp/authCenter/authenticate"
_AUTH_METHODS = "https://sso.ustb.edu.cn/idp/authn/queryAuthMethods"
_QR_INFO = "https://sso.ustb.edu.cn/idp/authn/getMicroQr"
_QR_PAGE = "https://sis.ustb.edu.cn/connect/qrpage"
_QR_IMG = "https://sis.ustb.edu.cn/connect/qrimg"
_QR_STATE = "https://sis.ustb.edu.cn/connect/state"


class FakeResponse:
    """模拟一次 HTTP 响应。"""

    def __init__(
        self,
        status_code: int = 200,
        text: str = "",
        content: bytes = b"",
        headers: dict[str, str] | None = None,
        json_data: Any = None,
    ) -> None:
        """初始化模拟响应内容。

        Args:
            status_code: 模拟的 HTTP 状态码。
            text: 模拟的响应文本。
            content: 模拟的响应二进制内容。
            headers: 模拟的响应头。
            json_data: json() 方法返回的内容。

        Returns:
            None: 响应内容保存在实例中。
        """
        self.status_code = status_code
        self.text = text
        self.content = content
        self.headers = headers or {}
        self._json_data = json_data

    def json(self) -> Any:
        """返回预置的 JSON 内容。

        Args:
            None.

        Returns:
            预置的 JSON 可序列化内容。
        """
        return self._json_data


class StubSession:
    """记录请求并委托给路由函数返回响应的会话替身。"""

    def __init__(
        self, router: Callable[[str, str], FakeResponse] | None = None
    ) -> None:
        """初始化空请求记录与路由函数。

        Args:
            router: 按请求方法与地址返回模拟响应的函数。

        Returns:
            None: 请求记录保存在实例中。
        """
        self.calls: list[tuple[str, str]] = []
        self.cookies = requests.cookies.RequestsCookieJar()
        self._router = router or (
            lambda method, url: FakeResponse(status_code=404)
        )

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        """记录并路由一次 GET 请求。

        Args:
            url: 请求地址。
            **kwargs: 被忽略的请求参数。

        Returns:
            路由函数返回的模拟响应。
        """
        self.calls.append(("GET", url))
        return self._router("GET", url)

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        """记录并路由一次 POST 请求。

        Args:
            url: 请求地址。
            **kwargs: 被忽略的请求参数。

        Returns:
            路由函数返回的模拟响应。
        """
        self.calls.append(("POST", url))
        return self._router("POST", url)


def _redirect_headers(location: str) -> dict[str, str]:
    """构造带 Location 跳转头的模拟响应头。

    Args:
        location: 模拟的跳转地址。

    Returns:
        只包含 Location 键的响应头字典。
    """
    return {"Location": location}


def _base_router() -> Callable[[str, str], FakeResponse]:
    """构造能完成一次成功 open_auth 的默认路由。

    Args:
        None.

    Returns:
        返回按地址分发模拟响应的路由函数。
    """
    location = (
        "https://sso.ustb.edu.cn/ac/#/index?lck=context_oauth2_"
        "0123456789abcdef0123456789abcdef&entityId=YW2025006"
    )

    def router(method: str, url: str) -> FakeResponse:
        """按请求地址返回预置响应。

        Args:
            method: 被忽略的请求方法。
            url: 请求地址。

        Returns:
            与地址匹配的模拟响应。
        """
        if url.startswith(_AUTH_ENTRY):
            return FakeResponse(
                status_code=302,
                text="",
                headers=_redirect_headers(location),
            )
        if url.startswith(_AUTH_METHODS):
            return FakeResponse(
                status_code=200,
                json_data={"code": 200, "message": "ok", "data": []},
            )
        return FakeResponse(status_code=404)

    return router


def _open_flow(
    session: StubSession | None = None,
) -> UstbSsoQrLogin:
    """创建已完成 open_auth 的登录流程实例。

    Args:
        session: 可选的会话替身。

    Returns:
        已取得认证上下文的登录流程实例。
    """
    flow = UstbSsoQrLogin(session=session or StubSession(_base_router()))
    flow.open_auth()
    return flow


def test_open_auth_extracts_lck_and_queries_auth_methods() -> None:
    """验证 open_auth 从跳转地址提取 lck 并查询可用认证方式。

    Args:
        None.

    Returns:
        None: 通过断言验证认证上下文已建立。
    """
    session = StubSession(_base_router())
    flow = UstbSsoQrLogin(session=session)

    flow.open_auth()

    assert flow._lck is not None
    assert flow._lck.startswith("context_oauth2_")
    post_calls = [url for method, url in session.calls if method == "POST"]
    assert any(url.startswith(_AUTH_METHODS) for url in post_calls)


def test_open_auth_requires_3xx_redirect() -> None:
    """验证认证入口未返回 3xx 时抛出中文接口错误。

    Args:
        None.

    Returns:
        None: 通过断言验证异常类型与提示。
    """

    def router(method: str, url: str) -> FakeResponse:
        """返回非 3xx 的认证入口响应。

        Args:
            method: 被忽略的请求方法。
            url: 请求地址。

        Returns:
            200 状态码的模拟响应。
        """
        return FakeResponse(status_code=200)

    flow = UstbSsoQrLogin(session=StubSession(router))

    with pytest.raises(SsoApiError) as error_info:
        flow.open_auth()

    assert "HTTP 200" in str(error_info.value)


def test_open_auth_reports_missing_lck() -> None:
    """验证跳转地址缺少 lck 时抛出解析错误。

    Args:
        None.

    Returns:
        None: 通过断言验证异常类型与提示。
    """

    def router(method: str, url: str) -> FakeResponse:
        """返回不含 lck 的跳转地址。

        Args:
            method: 被忽略的请求方法。
            url: 请求地址。

        Returns:
            指向无参数首页的 302 模拟响应。
        """
        return FakeResponse(
            status_code=302,
            headers=_redirect_headers("https://sso.ustb.edu.cn/ac/#/index"),
        )

    flow = UstbSsoQrLogin(session=StubSession(router))

    with pytest.raises(SsoBadResponseError) as error_info:
        flow.open_auth()

    assert "lck" in str(error_info.value)


def test_open_auth_reports_query_methods_failure() -> None:
    """验证认证方式查询业务失败时抛出中文接口错误。

    Args:
        None.

    Returns:
        None: 通过断言验证异常信息包含业务失败原因。
    """

    def router(method: str, url: str) -> FakeResponse:
        """返回认证方式查询失败响应。

        Args:
            method: 被忽略的请求方法。
            url: 请求地址。

        Returns:
            业务码非 200 的模拟响应。
        """
        if url.startswith(_AUTH_ENTRY):
            return FakeResponse(
                status_code=302,
                headers=_redirect_headers(
                    "https://sso.ustb.edu.cn/ac/#/index?lck=context_x"
                ),
            )
        return FakeResponse(
            status_code=200, json_data={"code": 500, "message": "服务繁忙"}
        )

    flow = UstbSsoQrLogin(session=StubSession(router))

    with pytest.raises(SsoApiError) as error_info:
        flow.open_auth()

    assert "服务繁忙" in str(error_info.value)


def test_open_auth_wraps_network_errors_with_hint() -> None:
    """验证网络异常被包装为带网络提示的接口错误。

    Args:
        None.

    Returns:
        None: 通过断言验证异常信息包含原始原因。
    """

    def router(method: str, url: str) -> FakeResponse:
        """模拟网络不可达。

        Args:
            method: 被忽略的请求方法。
            url: 被忽略的请求地址。

        Returns:
            不返回值，直接抛出网络异常。
        """
        raise requests.ConnectionError("[[NETWORK_DOWN]]")

    flow = UstbSsoQrLogin(session=StubSession(router))

    with pytest.raises(SsoApiError) as error_info:
        flow.open_auth()

    assert "网络" in str(error_info.value)
    assert "[[NETWORK_DOWN]]" in str(error_info.value)


def test_prepare_qr_code_extracts_sid() -> None:
    """验证 prepare_qr_code 取得 appId 并从二维码页面提取 sid。

    Args:
        None.

    Returns:
        None: 通过断言验证 sid 已写入流程实例。
    """
    sid = "a" * 32

    def router(method: str, url: str) -> FakeResponse:
        """按阶段返回二维码准备所需响应。

        Args:
            method: 被忽略的请求方法。
            url: 请求地址。

        Returns:
            与地址匹配的模拟响应。
        """
        if url.startswith(_AUTH_ENTRY):
            return FakeResponse(
                status_code=302,
                headers=_redirect_headers(
                    "https://sso.ustb.edu.cn/ac/#/index?lck=context_oauth2_test"
                ),
            )
        if url.startswith(_AUTH_METHODS):
            return FakeResponse(
                status_code=200,
                json_data={"code": 200, "message": "ok", "data": []},
            )
        if url.startswith(_QR_INFO):
            return FakeResponse(
                status_code=200,
                json_data={
                    "code": "200",
                    "data": {
                        "appId": "[[APP_ID]]",
                        "returnUrl": "https://sso.ustb.edu.cn/idp/authCenter/authenticateByLck",
                        "randomToken": "[[RANDOM_TOKEN]]",
                    },
                },
            )
        if url.startswith(_QR_PAGE):
            return FakeResponse(
                status_code=200, text=f"<script>var sid = {sid};</script>"
            )
        return FakeResponse(status_code=404)

    session = StubSession(router)
    flow = UstbSsoQrLogin(session=session)
    flow.open_auth()

    flow.prepare_qr_code()

    assert flow._sid == sid
    assert flow._app_id == "[[APP_ID]]"


def test_prepare_qr_code_requires_open_auth() -> None:
    """验证未 open_auth 时 prepare_qr_code 抛出状态错误。

    Args:
        None.

    Returns:
        None: 通过断言验证异常类型。
    """
    flow = UstbSsoQrLogin(session=StubSession(_base_router()))

    with pytest.raises(SsoIllegalStateError):
        flow.prepare_qr_code()


def test_fetch_qr_image_returns_content() -> None:
    """验证 fetch_qr_image 返回二维码图片字节。

    Args:
        None.

    Returns:
        None: 通过断言验证图片内容原样返回。
    """
    flow = _open_flow()
    flow._sid = "b" * 32

    def router(method: str, url: str) -> FakeResponse:
        """返回模拟二维码图片。

        Args:
            method: 被忽略的请求方法。
            url: 请求地址。

        Returns:
            带 PNG 内容的模拟响应。
        """
        if url.startswith(_QR_IMG):
            return FakeResponse(status_code=200, content=b"[[PNG_DATA]]")
        return FakeResponse(status_code=404)

    flow._session._router = router  # type: ignore[attr-defined]

    assert flow.fetch_qr_image() == b"[[PNG_DATA]]"


def test_fetch_qr_image_reports_download_failure() -> None:
    """验证二维码图片下载失败时抛出中文接口错误。

    Args:
        None.

    Returns:
        None: 通过断言验证异常信息包含 HTTP 状态码。
    """
    flow = _open_flow()
    flow._sid = "c" * 32

    def router(method: str, url: str) -> FakeResponse:
        """返回失败的图片下载响应。

        Args:
            method: 被忽略的请求方法。
            url: 请求地址。

        Returns:
            500 状态码的模拟响应。
        """
        return FakeResponse(status_code=500)

    flow._session._router = router  # type: ignore[attr-defined]

    with pytest.raises(SsoApiError) as error_info:
        flow.fetch_qr_image()

    assert "HTTP 500" in str(error_info.value)


def test_wait_for_pass_code_returns_code_on_confirmation() -> None:
    """验证扫码确认后 wait_for_pass_code 返回授权口令。

    Args:
        None.

    Returns:
        None: 通过断言验证口令内容。
    """
    flow = _open_flow()
    flow._sid = "d" * 32

    def router(method: str, url: str) -> FakeResponse:
        """返回确认成功的状态响应。

        Args:
            method: 被忽略的请求方法。
            url: 请求地址。

        Returns:
            携带授权口令的模拟响应。
        """
        return FakeResponse(
            status_code=200, json_data={"code": 1, "data": "[[PASS_CODE]]"}
        )

    flow._session._router = router  # type: ignore[attr-defined]

    assert flow.wait_for_pass_code() == "[[PASS_CODE]]"


@pytest.mark.parametrize("expired_code", [3, 202])
def test_wait_for_pass_code_reports_expired_qr(expired_code: int) -> None:
    """验证二维码过期状态抛出过期异常。

    Args:
        expired_code: 模拟的过期状态码。

    Returns:
        None: 通过断言验证异常类型与提示。
    """
    flow = _open_flow()
    flow._sid = "e" * 32

    def router(method: str, url: str) -> FakeResponse:
        """返回过期状态响应。

        Args:
            method: 被忽略的请求方法。
            url: 被忽略的请求地址。

        Returns:
            携带过期状态码的模拟响应。
        """
        return FakeResponse(
            status_code=200, json_data={"code": expired_code, "data": None}
        )

    flow._session._router = router  # type: ignore[attr-defined]

    with pytest.raises(SsoQrExpiredError) as error_info:
        flow.wait_for_pass_code()

    assert "二维码已过期" in str(error_info.value)


def test_wait_for_pass_code_reports_invalid_state() -> None:
    """验证二维码状态无效时抛出中文接口错误。

    Args:
        None.

    Returns:
        None: 通过断言验证异常信息包含状态码。
    """
    flow = _open_flow()
    flow._sid = "f" * 32

    def router(method: str, url: str) -> FakeResponse:
        """返回无效状态响应。

        Args:
            method: 被忽略的请求方法。
            url: 被忽略的请求地址。

        Returns:
            携带无效状态码的模拟响应。
        """
        return FakeResponse(
            status_code=200, json_data={"code": 101, "message": "凭证无效"}
        )

    flow._session._router = router  # type: ignore[attr-defined]

    with pytest.raises(SsoApiError) as error_info:
        flow.wait_for_pass_code()

    assert "101" in str(error_info.value)


def test_wait_for_pass_code_supports_cancellation() -> None:
    """验证 should_stop 返回 True 时抛出取消异常。

    Args:
        None.

    Returns:
        None: 通过断言验证不会发起网络请求。
    """
    flow = _open_flow()
    flow._sid = "g" * 32

    with pytest.raises(SsoLoginCanceledError):
        flow.wait_for_pass_code(should_stop=lambda: True)


def test_wait_for_pass_code_times_out_after_limit() -> None:
    """验证超过总时限后抛出超时异常。

    Args:
        None.

    Returns:
        None: 通过断言验证超时提示包含时限。
    """
    flow = _open_flow()
    flow._sid = "h" * 32
    flow._qr_wait_timeout = 0.05

    def router(method: str, url: str) -> FakeResponse:
        """返回持续等待的超时状态。

        Args:
            method: 被忽略的请求方法。
            url: 被忽略的请求地址。

        Returns:
            携带等待超时状态码的模拟响应。
        """
        return FakeResponse(
            status_code=200, json_data={"code": 4, "data": None}
        )

    flow._session._router = router  # type: ignore[attr-defined]

    with pytest.raises(SsoQrTimeoutError) as error_info:
        flow.wait_for_pass_code()

    assert "重新生成二维码" in str(error_info.value)


def test_complete_auth_follows_redirect_and_returns_cookies() -> None:
    """验证 complete_auth 按跳转页解析终点并返回会话 Cookie。

    Args:
        None.

    Returns:
        None: 通过断言验证 Cookie 映射包含登录会话凭据。
    """
    flow = _open_flow()
    flow._app_id = "[[APP_ID]]"
    flow._return_url = (
        "https://sso.ustb.edu.cn/idp/authCenter/authenticateByLck?"
        "thirdPartyAuthCode=microQr"
    )
    flow._random_token = "[[RANDOM_TOKEN]]"
    flow._session.cookies.set(
        "SESSION", "[[SESSION_VALUE]]", domain="byyt.ustb.edu.cn", path="/"
    )
    flow._session.cookies.set(
        "INCO", "[[INCO_VALUE]]", domain="byyt.ustb.edu.cn", path="/"
    )
    landing = "https://byyt.ustb.edu.cn/authentication/main"

    def router(method: str, url: str) -> FakeResponse:
        """返回认证跳转与登录终点页面。

        Args:
            method: 被忽略的请求方法。
            url: 请求地址。

        Returns:
            含跳转脚本或普通页面的模拟响应。
        """
        if url.startswith(flow._return_url.split("?")[0]):  # type: ignore[union-attr]
            return FakeResponse(
                status_code=200,
                text=(
                    'var actionType = "GET";\n'
                    f'var locationValue = "{landing}";'
                ),
            )
        return FakeResponse(status_code=200, text="选课系统主页")

    flow._session._router = router  # type: ignore[attr-defined]

    cookies = flow.complete_auth("[[PASS_CODE]]")

    assert cookies["SESSION"] == "[[SESSION_VALUE]]"
    assert cookies["INCO"] == "[[INCO_VALUE]]"


def test_complete_auth_requires_qr_preparation() -> None:
    """验证二维码信息不完整时 complete_auth 抛出状态错误。

    Args:
        None.

    Returns:
        None: 通过断言验证异常类型。
    """
    flow = _open_flow()

    with pytest.raises(SsoIllegalStateError):
        flow.complete_auth("[[PASS_CODE]]")


def test_complete_auth_reports_unparsable_landing_page() -> None:
    """验证跳转页缺少登录终点变量时抛出解析错误。

    Args:
        None.

    Returns:
        None: 通过断言验证异常信息包含提示。
    """
    flow = _open_flow()
    flow._app_id = "[[APP_ID]]"
    flow._return_url = "https://sso.ustb.edu.cn/idp/authCenter/authenticateByLck"
    flow._random_token = "[[RANDOM_TOKEN]]"

    def router(method: str, url: str) -> FakeResponse:
        """返回不含跳转变量的页面。

        Args:
            method: 被忽略的请求方法。
            url: 被忽略的请求地址。

        Returns:
            普通 HTML 文本的模拟响应。
        """
        return FakeResponse(status_code=200, text="<html>未知页面</html>")

    flow._session._router = router  # type: ignore[attr-defined]

    with pytest.raises(SsoBadResponseError) as error_info:
        flow.complete_auth("[[PASS_CODE]]")

    assert "登录终点" in str(error_info.value)


def test_byyt_prefab_parameters_target_oauth_login_code() -> None:
    """验证本研一体教务管理系统预置参数指向选课登录地址。

    Args:
        None.

    Returns:
        None: 通过断言验证登录参数正确。
    """
    assert BYYT_ENTITY_ID == "YW2025006"
    assert BYYT_REDIRECT_URI == "https://byyt.ustb.edu.cn/oauth/login/code"
    assert BYYT_STATE == "null"
