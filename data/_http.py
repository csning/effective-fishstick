"""HTTP 请求层 — 使用 curl-cffi 模拟 Chrome TLS 指纹，绕过东方财富反爬。

curl-cffi 通过 impersonate 参数伪造 Chrome 131 的 JA3/JA4 指纹。
直接接管 requests.get/post/Session.request，不经过 standard requests。
"""

import requests as _std_requests
from curl_cffi import requests as _curl_requests

_EM_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/",
    "Accept": "text/html,application/json,*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Connection": "keep-alive",
}

_orig_get = _std_requests.get
_orig_post = _std_requests.post
_orig_session_request = _std_requests.Session.request
_patched = False

_curl_session = None


def _get_session():
    global _curl_session
    if _curl_session is None:
        _curl_session = _curl_requests.Session(impersonate="chrome131")
    return _curl_session


def enable():
    """用 curl-cffi (Chrome TLS 指纹) 接管全局 requests。"""
    global _patched
    if _patched:
        return

    def _get(url, **kwargs):
        params = kwargs.pop("params", None)
        headers = dict(kwargs.pop("headers", None) or {})
        timeout = kwargs.pop("timeout", 30)
        verify = kwargs.pop("verify", None)
        for k, v in _EM_HEADERS.items():
            headers.setdefault(k, v)
        return _get_session().get(
            url, params=params, headers=headers, timeout=timeout
        )

    def _post(url, **kwargs):
        headers = dict(kwargs.pop("headers", None) or {})
        timeout = kwargs.pop("timeout", 30)
        for k, v in _EM_HEADERS.items():
            headers.setdefault(k, v)
        return _get_session().post(
            url, headers=headers, timeout=timeout, **kwargs
        )

    _std_requests.get = _get
    _std_requests.post = _post
    _patched = True


def disable():
    global _patched
    if not _patched:
        return
    _std_requests.get = _orig_get
    _std_requests.post = _orig_post
    _patched = False
