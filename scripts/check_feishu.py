#!/usr/bin/env python3
"""Feishu Bot connectivity diagnostic script.

Comprehensive check of Feishu integration configurations.

Usage:
    python scripts/check_feishu.py
    python scripts/check_feishu.py --port 8000
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from config import get_settings
from notify.feishu import FeishuClient


def check_basic_config() -> dict:
    """Check basic configuration."""
    ok = True
    results = {}
    settings = get_settings()

    checks = [
        ("app_id", settings.notify.feishu_app_id, "notify.feishu_app_id"),
        ("app_secret", settings.notify.feishu_app_secret, "notify.feishu_app_secret"),
        ("webhook", settings.notify.feishu_webhook, "notify.feishu_webhook"),
    ]

    for name, value, path in checks:
        configured = bool(value)
        results[name] = {
            "status": "OK" if configured else "MISSING",
            "configured": configured,
            "path": path,
            "value_hint": f"{value[:8]}..." if configured and len(str(value)) > 8 else "(empty)",
        }
        if not configured and name != "webhook":
            ok = False

    results["overall"] = ok
    return results


async def check_api_connectivity() -> dict:
    """Check Feishu API connectivity and credential validity."""
    settings = get_settings()
    results = {}

    if not settings.notify.feishu_app_id or not settings.notify.feishu_app_secret:
        results["status"] = "SKIP"
        results["error"] = "app_id or app_secret not configured"
        return results

    feishu = FeishuClient(
        app_id=settings.notify.feishu_app_id,
        app_secret=settings.notify.feishu_app_secret,
    )

    try:
        t0 = time.monotonic()
        token = await feishu._ensure_token()
        elapsed = time.monotonic() - t0
        results["status"] = "OK"
        results["latency_ms"] = round(elapsed * 1000)
        results["token_prefix"] = token[:12] + "..."
        results["message"] = "tenant_access_token obtained successfully"
    except Exception as e:
        results["status"] = "FAIL"
        results["error"] = str(e)
        results["message"] = "Failed to obtain tenant_access_token"
    finally:
        await feishu.close()

    return results


async def check_server_running(port: int) -> dict:
    """Check if the local server is running."""
    results = {"port": port}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"http://127.0.0.1:{port}/health")
            results["status"] = "OK" if resp.status_code == 200 else "WARN"
            results["http_code"] = resp.status_code
            if resp.status_code == 200:
                results["response"] = resp.json()
    except httpx.ConnectError:
        results["status"] = "DOWN"
        results["message"] = f"Cannot connect to 127.0.0.1:{port}, server may not be running"
    except Exception as e:
        results["status"] = "WARN"
        results["error"] = str(e)

    return results


async def check_webhook_reachability(port: int) -> dict:
    """Check if the webhook endpoint responds correctly."""
    results = {"port": port}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"http://127.0.0.1:{port}/feishu/webhook",
                json={"type": "url_verification", "challenge": "test_challenge_12345"},
            )
            results["status"] = "OK" if resp.status_code == 200 else "FAIL"
            results["http_code"] = resp.status_code
            if resp.status_code == 200:
                data = resp.json()
                results["challenge_ok"] = data.get("challenge") == "test_challenge_12345"
    except httpx.ConnectError:
        results["status"] = "DOWN"
        results["message"] = f"Cannot reach webhook endpoint, server may not be running on port {port}"
    except Exception as e:
        results["status"] = "WARN"
        results["error"] = str(e)

    return results


def print_report(basic, api, server, webhook, port):
    """Print formatted diagnostic report."""
    print()
    print("=" * 60)
    print("  Effective Fishstick - Feishu Bot Diagnostic Report")
    print("=" * 60)

    print("\n[1] Basic Config")
    for name, info in basic.items():
        if name == "overall":
            continue
        icon = "[OK]" if info["configured"] else "[!!]"
        print(f"   {icon} {info['path']:30s} -> {info['value_hint']}")

    print("\n[2] Feishu API Connectivity")
    if api.get("status") == "OK":
        print(f"   [OK] tenant_access_token obtained (latency: {api.get('latency_ms', '?')}ms)")
        print(f"        token: {api.get('token_prefix', '?')}")
    elif api.get("status") == "SKIP":
        print(f"   [--] Skipped: {api.get('error', '')}")
    else:
        print(f"   [!!] {api.get('error', api.get('message', ''))}")

    print(f"\n[3] Local Server Status (port {port})")
    if server.get("status") == "OK":
        print(f"   [OK] Server running - HTTP {server.get('http_code', '?')}")
    else:
        print(f"   [!!] {server.get('message', server.get('error', ''))}")

    print("\n[4] Webhook Endpoint")
    if webhook.get("status") == "OK":
        challenge_ok = webhook.get("challenge_ok", False)
        print(f"   [OK] Webhook endpoint responds")
        print(f"        URL verification: {'OK' if challenge_ok else 'FAIL'}")
    else:
        print(f"   [!!] {webhook.get('message', webhook.get('error', ''))}")

    print("\n" + "=" * 60)
    print("  Summary & Next Steps")
    print("=" * 60)

    issues = []

    if not basic.get("overall"):
        if not basic.get("app_id", {}).get("configured"):
            issues.append("Missing feishu_app_id -> add to config/settings.local.yaml")
        if not basic.get("app_secret", {}).get("configured"):
            issues.append("Missing feishu_app_secret -> add to config/settings.local.yaml")

    if api.get("status") not in ("OK", "SKIP"):
        issues.append(f"Feishu API unreachable: {api.get('error', 'unknown')}")

    if server.get("status") != "OK":
        issues.append(f"Server not running -> python main.py serve")

    if not issues:
        if not basic.get("webhook", {}).get("configured"):
            print("\n   Note: feishu_webhook not configured (optional, only affects webhook send mode).")
        print()
        print("   All local checks passed!")
        print()
        print("   On the Feishu Developer Console, verify:")
        print(f"   1. Event Subscription URL: http://<VPS_PUBLIC_IP>:{port}/feishu/webhook")
        print("   2. Permissions: im:message, im:message:read_as_bot")
        print("   3. App is published (or test version with test users added)")
        print("   4. Bot is added to the target chat")
        print(f"   5. VPS firewall allows port {port}")
        print()
        print(f"   Start server: python main.py serve")
        print(f"   Diagnostic endpoint: http://127.0.0.1:{port}/feishu/health")
        return

    for i, issue in enumerate(issues, 1):
        print(f"\n   {i}. {issue}")

    if server.get("status") == "OK":
        print(f"\n   Server is running. Check: http://127.0.0.1:{port}/feishu/health")

    print()


async def main():
    parser = argparse.ArgumentParser(description="Feishu Bot configuration diagnostic")
    parser.add_argument("--port", type=int, default=8000, help="Server port (default 8000)")
    args = parser.parse_args()

    print("Running Feishu Bot diagnostics...")

    basic = check_basic_config()
    api = await check_api_connectivity()
    server = await check_server_running(args.port)
    webhook = await check_webhook_reachability(args.port)

    print_report(basic, api, server, webhook, args.port)


if __name__ == "__main__":
    asyncio.run(main())
