"""HTTP 请求头猴子补丁 — 绕过东方财富反爬。

同时 patch requests.get / requests.post / requests.Session.request。
"""

import requests

_EM_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Cache-Control": "max-age=0",
    "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"macOS"',
}

_orig_get = requests.get
_orig_post = requests.post
_orig_session_request = requests.Session.request
_patched = False


def enable():
    """全局注入东方财富反爬请求头。"""
    global _patched
    if _patched:
        return

    def _get(url, **kwargs):
        h = dict(kwargs.pop("headers", None) or {})
        for k, v in _EM_HEADERS.items():
            h.setdefault(k, v)
        kwargs["headers"] = h
        return _orig_get(url, **kwargs)

    def _post(url, **kwargs):
        h = dict(kwargs.pop("headers", None) or {})
        for k, v in _EM_HEADERS.items():
            h.setdefault(k, v)
        kwargs["headers"] = h
        return _orig_post(url, **kwargs)

    def _session_request(self, method, url, **kwargs):
        h = dict(kwargs.pop("headers", None) or {})
        for k, v in _EM_HEADERS.items():
            h.setdefault(k, v)
        kwargs["headers"] = h
        return _orig_session_request(self, method, url, **kwargs)

    requests.get = _get
    requests.post = _post
    requests.Session.request = _session_request
    _patched = True


def disable():
    """恢复原始 requests 方法。"""
    global _patched
    if not _patched:
        return
    requests.get = _orig_get
    requests.post = _orig_post
    requests.Session.request = _orig_session_request
    _patched = False
