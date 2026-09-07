"""北科大统一认证微信扫码登录的纯 HTTP 实现。

本模块移植自开源项目 isHarryh/USTB-SSO 的 Python 实现
（https://github.com/isHarryh/USTB-SSO，MIT License）中的
``QrAuthProcedure`` 流程，网络层由 httpx 替换为本项目已有的
requests，登录目标固定为北京科技大学本研一体教务管理系统
（https://byyt.ustb.edu.cn）2025 年版预置参数
（entity_id=YW2025006）。登录过程不依赖 Chrome、ChromeDriver 或
Selenium，二维码图片由统一认证服务器直接返回。
"""

import re
import time
from html import unescape
from typing import Callable
from urllib.parse import parse_qs, unquote, urlparse

import requests

# 统一认证与微信二维码服务的固定端点。
_SSO_AUTH_ENTRY = "https://sso.ustb.edu.cn/idp/authCenter/authenticate"
_SSO_QUERY_AUTH_METHODS = "https://sso.ustb.edu.cn/idp/authn/queryAuthMethods"
_SSO_QR_INFO = "https://sso.ustb.edu.cn/idp/authn/getMicroQr"
_SIS_QR_PAGE = "https://sis.ustb.edu.cn/connect/qrpage"
_SIS_QR_IMG = "https://sis.ustb.edu.cn/connect/qrimg"
_SIS_QR_STATE = "https://sis.ustb.edu.cn/connect/state"

# 本研一体教务管理系统（选课接口所在站点）2025 年版登录参数。
BYYT_ENTITY_ID = "YW2025006"
BYYT_REDIRECT_URI = "https://byyt.ustb.edu.cn/oauth/login/code"
BYYT_STATE = "null"

# 单次普通请求超时（秒）。
_REQUEST_TIMEOUT_SECONDS = 10.0
# 二维码状态长轮询单次读取超时（秒），与上游库保持一致。
STATE_POLL_TIMEOUT_SECONDS = 16.0
# 等待扫码确认的总时限（秒）。
QR_WAIT_TIMEOUT_SECONDS = 180.0

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "Chrome/150.0.0.0 Safari/537.36"
)


class SsoAuthError(Exception):
    """统一认证登录流程的异常基类。"""


class SsoApiError(SsoAuthError):
    """统一认证接口返回异常或网络不可达。"""


class SsoBadResponseError(SsoAuthError):
    """统一认证服务器返回了无法解析的内容。"""


class SsoIllegalStateError(SsoAuthError):
    """登录步骤调用顺序不符合认证流程要求。"""


class SsoQrExpiredError(SsoAuthError):
    """微信二维码已过期或被其他设备使用。"""


class SsoQrTimeoutError(SsoAuthError):
    """等待手机扫码确认超过总时限。"""


class SsoLoginCanceledError(SsoAuthError):
    """登录流程被用户或程序主动取消。"""


class UstbSsoQrLogin:
    """执行北科大统一认证微信扫码登录的单次会话。

    实例只保存当前进程内的认证上下文与 HTTP 会话，不落盘任何凭据。
    使用固定端点与预置参数完成 open_auth、生成二维码、等待扫码确认
    与最终换取 Cookie 四个阶段；阶段内部请求失败均抛出
    :class:`SsoAuthError` 子类，中文信息可直接用于界面展示。
    """

    def __init__(
        self,
        entity_id: str = BYYT_ENTITY_ID,
        redirect_uri: str = BYYT_REDIRECT_URI,
        state: str = BYYT_STATE,
        session: requests.Session | None = None,
        qr_wait_timeout: float = QR_WAIT_TIMEOUT_SECONDS,
    ) -> None:
        """初始化一次统一认证登录流程。

        Args:
            entity_id: 登录应用在本研教务统一认证中的实体编号。
            redirect_uri: 认证完成后返回的应用地址。
            state: 登录请求携带的内部状态名。
            session: 可选的外部 requests 会话；为 None 时新建直连会话。
            qr_wait_timeout: 等待扫码确认的总时限（秒）。

        Returns:
            None: 认证上下文保存在当前实例中。
        """
        if session is None:
            session = requests.Session()
            # 校内统一认证站点必须直连，绕过系统/环境代理避免 TLS 失败。
            session.trust_env = False
            session.headers.update({"User-Agent": _USER_AGENT})
        self._session = session
        self._entity_id = entity_id
        self._redirect_uri = redirect_uri
        self._state = state
        self._qr_wait_timeout = qr_wait_timeout
        self._lck: str | None = None
        self._app_id: str | None = None
        self._return_url: str | None = None
        self._random_token: str | None = None
        self._sid: str | None = None

    def open_auth(self) -> None:
        """发起认证并查询可用的认证方式。

        Args:
            None.

        Returns:
            None: 认证上下文 lck 会写入当前实例。

        Raises:
            SsoApiError: 认证入口或认证方式接口不可达、返回异常时抛出。
            SsoBadResponseError: 响应缺少 lck 或无法解析时抛出。
        """
        try:
            response = self._session.get(
                _SSO_AUTH_ENTRY,
                params={
                    "client_id": self._entity_id,
                    "redirect_uri": self._redirect_uri,
                    "login_return": "true",
                    "state": self._state,
                    "response_type": "code",
                },
                allow_redirects=False,
                timeout=_REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as error:
            raise SsoApiError(
                f"无法连接统一认证服务器，请检查网络或代理设置（{error}）"
            ) from error
        if response.status_code // 100 != 3:
            raise SsoApiError(
                f"统一认证入口返回 HTTP {response.status_code}，预期为 3xx 跳转"
            )
        location = response.headers.get("Location", "")
        if not location:
            raise SsoBadResponseError("统一认证入口响应缺少跳转地址")
        query = parse_qs(urlparse(location.replace("/#/", "/")).query)
        self._lck = query.get("lck", [None])[0]
        if not self._lck:
            raise SsoBadResponseError(
                f"统一认证跳转地址缺少 lck 参数：{location[:160]}"
            )
        try:
            methods_response = self._session.post(
                _SSO_QUERY_AUTH_METHODS,
                json={"lck": self._lck, "entityId": self._entity_id},
                timeout=_REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as error:
            raise SsoApiError(
                f"无法查询可用登录方式，请检查网络或代理设置（{error}）"
            ) from error
        if methods_response.status_code != 200:
            raise SsoApiError(
                f"查询可用登录方式返回 HTTP {methods_response.status_code}"
            )
        try:
            payload = methods_response.json()
        except ValueError as error:
            raise SsoBadResponseError("查询可用登录方式的响应不是有效 JSON") from error
        if payload.get("code") != 200:
            raise SsoApiError(
                f"查询可用登录方式失败：{payload.get('message') or payload.get('code')}"
            )

    def prepare_qr_code(self) -> None:
        """获取微信二维码会话并在二维码页面中取得 sid。

        Args:
            None.

        Returns:
            None: 二维码 sid 会写入当前实例。

        Raises:
            SsoIllegalStateError: 尚未调用 open_auth 时抛出。
            SsoApiError: 二维码相关接口不可达或返回异常时抛出。
            SsoBadResponseError: 二维码页面找不到 sid 时抛出。
        """
        if not self._lck:
            raise SsoIllegalStateError("尚未发起统一认证，请先调用 open_auth")
        try:
            qr_info_response = self._session.post(
                _SSO_QR_INFO,
                json={"entityId": self._entity_id, "lck": self._lck},
                timeout=_REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as error:
            raise SsoApiError(
                f"无法获取微信二维码信息，请检查网络或代理设置（{error}）"
            ) from error
        if qr_info_response.status_code != 200:
            raise SsoApiError(
                f"获取微信二维码信息返回 HTTP {qr_info_response.status_code}"
            )
        try:
            qr_info = qr_info_response.json()
        except ValueError as error:
            raise SsoBadResponseError("获取微信二维码信息的响应不是有效 JSON") from error
        if qr_info.get("code") != "200":
            raise SsoApiError(
                f"获取微信二维码信息失败：{qr_info.get('message') or qr_info.get('code')}"
            )
        data = qr_info.get("data") or {}
        self._app_id = data.get("appId")
        self._return_url = data.get("returnUrl")
        self._random_token = data.get("randomToken")
        if not all((self._app_id, self._return_url, self._random_token)):
            raise SsoBadResponseError("微信二维码信息缺少 appId、returnUrl 或 randomToken")
        try:
            qr_page_response = self._session.get(
                _SIS_QR_PAGE,
                params={
                    "appid": self._app_id,
                    "return_url": self._return_url,
                    "rand_token": self._random_token,
                    "embed_flag": "1",
                },
                timeout=_REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as error:
            raise SsoApiError(
                f"无法打开二维码页面，请检查网络或代理设置（{error}）"
            ) from error
        if qr_page_response.status_code != 200:
            raise SsoApiError(
                f"打开二维码页面返回 HTTP {qr_page_response.status_code}"
            )
        match = re.search(r"sid\s?=\s?(\w{32})", qr_page_response.text)
        if not match:
            raise SsoBadResponseError("二维码页面中未找到有效的 sid")
        self._sid = match.group(1)

    def fetch_qr_image(self) -> bytes:
        """下载当前二维码图片并返回图片二进制内容。

        Args:
            None.

        Returns:
            可直接写入文件或交给图片库解析的二维码图片字节。

        Raises:
            SsoIllegalStateError: 尚未调用 prepare_qr_code 时抛出。
            SsoApiError: 二维码图片下载失败时抛出。
        """
        if not self._sid:
            raise SsoIllegalStateError("尚未取得二维码 sid，请先调用 prepare_qr_code")
        try:
            image_response = self._session.get(
                _SIS_QR_IMG,
                params={"sid": self._sid},
                timeout=_REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as error:
            raise SsoApiError(
                f"无法下载二维码图片，请检查网络或代理设置（{error}）"
            ) from error
        if image_response.status_code != 200:
            raise SsoApiError(
                f"下载二维码图片返回 HTTP {image_response.status_code}"
            )
        content = image_response.content
        if not content:
            raise SsoBadResponseError("二维码图片内容为空")
        return content

    def wait_for_pass_code(
        self, should_stop: Callable[[], bool] | None = None
    ) -> str:
        """长轮询二维码状态直到扫码确认、失效或超过总时限。

        Args:
            should_stop: 可选回调；返回 True 时立即取消等待并抛出取消异常。

        Returns:
            扫码确认后服务器返回的授权口令字符串。

        Raises:
            SsoLoginCanceledError: should_stop 返回 True 时抛出。
            SsoQrExpiredError: 二维码过期或已被使用时抛出。
            SsoQrTimeoutError: 超过总等待时限时抛出。
            SsoApiError: 二维码状态接口返回异常时抛出。
            SsoIllegalStateError: 尚未取得 sid 时抛出。
            SsoBadResponseError: 状态响应无法解析时抛出。
        """
        if not self._sid:
            raise SsoIllegalStateError("尚未取得二维码 sid，请先调用 prepare_qr_code")
        started_at = time.monotonic()
        while True:
            if should_stop is not None and should_stop():
                raise SsoLoginCanceledError("登录已取消")
            if time.monotonic() - started_at >= self._qr_wait_timeout:
                raise SsoQrTimeoutError(
                    f"等待扫码确认超过 {self._qr_wait_timeout:g} 秒，请重新生成二维码"
                )
            try:
                state_response = self._session.get(
                    _SIS_QR_STATE,
                    params={"sid": self._sid},
                    timeout=STATE_POLL_TIMEOUT_SECONDS,
                )
            except requests.RequestException:
                # 长轮询单次读取超时或连接中断时短暂间隔后继续等待。
                time.sleep(1.0)
                continue
            if state_response.status_code != 200:
                raise SsoApiError(
                    f"二维码状态查询返回 HTTP {state_response.status_code}"
                )
            try:
                payload = state_response.json()
            except ValueError as error:
                raise SsoBadResponseError("二维码状态响应不是有效 JSON") from error
            code = payload.get("code")
            if code == 1:
                pass_code = payload.get("data")
                if not pass_code:
                    raise SsoBadResponseError("扫码确认响应缺少授权口令")
                return str(pass_code)
            if code in (3, 202):
                raise SsoQrExpiredError("二维码已过期，请重新生成二维码")
            if code in (101, 102):
                message = payload.get("message") or ""
                raise SsoApiError(f"二维码状态无效（{code}）：{message}".strip())
            # code == 4（等待超时）或未知状态码：继续轮询。

    def complete_auth(self, pass_code: str) -> dict[str, str]:
        """使用扫码确认得到的授权口令完成认证并换取 Cookie。

        Args:
            pass_code: wait_for_pass_code 返回的授权口令。

        Returns:
            认证完成后会话内全部 Cookie 的“名称 → 值”映射。

        Raises:
            SsoIllegalStateError: 二维码准备不完整时抛出。
            SsoApiError: 完成认证的接口不可达或跳转方式不支持时抛出。
            SsoBadResponseError: 认证跳转响应无法解析时抛出。
        """
        if not all((self._app_id, self._return_url, self._random_token)):
            raise SsoIllegalStateError("二维码信息不完整，请重新开始登录流程")
        params: dict[str, str] = {
            "appid": self._app_id,
            "auth_code": pass_code,
            "rand_token": self._random_token,
        }
        for key, values in parse_qs(urlparse(self._return_url).query).items():
            if values:
                params.setdefault(key, values[0])
        try:
            auth_response = self._session.get(
                self._return_url,
                params=params,
                allow_redirects=True,
                timeout=_REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as error:
            raise SsoApiError(
                f"完成统一认证时无法连接服务器，请检查网络或代理设置（{error}）"
            ) from error
        text = auth_response.text or ""
        action_match = re.search(r'var actionType\s*=\s*"([^"]+)"', text)
        location_match = re.search(r'var locationValue\s*=\s*"([^"]+)"', text)
        if not (action_match and location_match):
            raise SsoBadResponseError("认证跳转页面中未解析出登录终点地址")
        action_type = unescape(unquote(action_match.group(1)))
        location_value = unescape(unquote(location_match.group(1)))
        if action_type.upper() != "GET":
            raise SsoApiError(f"不支持的统一认证跳转方式：{action_type}")
        try:
            self._session.get(
                location_value,
                allow_redirects=True,
                timeout=_REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as error:
            raise SsoApiError(
                f"进入选课系统时无法连接服务器，请检查网络或代理设置（{error}）"
            ) from error
        return {cookie.name: cookie.value for cookie in self._session.cookies}
