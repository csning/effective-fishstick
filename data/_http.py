"""HTTP 请求层 — 使用 curl-cffi 模拟浏览器 TLS 指纹，绕过东方财富反爬。

curl-cffi 通过 impersonate 参数伪造 Chrome 131 的 JA3/JA4 指纹，
让东方财富 API 认为请求来自真实浏览器。
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
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Cache-Control": "no-cache",
}

_orig_get = _std_requests.get
_orig_post = _std_requests.post
_orig_session_request = _std_requests.Session.request
_patched = False

# Lazy-init curl-cffi session with Chrome 131 impersonation
_curl_session = None


def _get_session():
    global _curl_session
    if _curl_session is None:
        _curl_session = _curl_requests.Session(impersonate="chrome131")
        _curl_session.headers.update(_EM_HEADERS)
    return _curl_session


def enable():
    """用 curl-cffi 接管全局 requests，模拟浏览器 TLS 指纹。"""
    global _patched
    if _patched:
        return

    def _get(url, **kwargs):
        h = dict(kwargs.get("headers") or {})
        for k, v in _EM_HEADERS.items():
            h.setdefault(k, v)
        kwargs["headers"] = h
        kwargs.pop("verify", None)  # curl-cffi 不支持 verify=False, 用自定义 CA
        try:
            return _get_session().get(url, **kwargs)
        except Exception:
            return _orig_get(url, **kwargs)

    def _post(url, **kwargs):
        h = dict(kwargs.get("headers") or {})
        for k, v in _EM_HEADERS.items():
            h.setdefault(k, v)
        kwargs.pop("verify", None)
        try:
            return _get_session().post(url, **kwargs)
        except Exception:
            return _orig_post(url, **kwargs)

    _std_requests.get = _get
    _std_requests.post = _post
    _patched = True


def disable():
    """恢复原始 requests 方法。"""
    global _patched
    if not _patched:
        return
    _std_requests.get = _orig_get
    _std_requests.post = _orig_post
    _patched = False
