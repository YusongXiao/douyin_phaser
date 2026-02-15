"""
Douyin User Works Extractor
Extracts all work share-links (video / note) from a Douyin user profile page.

Usage:
    python douyin_user_phaser.py <user_url>
    python douyin_user_phaser.py <user_url> --max 50
    python douyin_user_phaser.py <user_url> --json

Examples:
    python douyin_user_phaser.py "https://www.douyin.com/user/MS4wLjABAAAA..."
"""

import sys
import json
import time
import re
import os
import argparse
from datetime import datetime

from douyin_phaser import BrowserPool, HAS_STEALTH, _stealth


# ---------------------------------------------------------------------------
# Cookie helpers
# ---------------------------------------------------------------------------

def parse_cookie_string(cookie_str):
    """Parse a cookie header string into a list of Playwright cookie dicts.

    Accepts the format copied from browser DevTools (document.cookie or
    the Cookie header value):
        name1=value1; name2=value2; ...

    Returns:
        list[dict]  – each dict has keys: name, value, domain, path
    """
    cookies = []
    if not cookie_str or not cookie_str.strip():
        return cookies
    for part in cookie_str.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, _, value = part.partition("=")
        cookies.append({
            "name": name.strip(),
            "value": value.strip(),
            "domain": ".douyin.com",
            "path": "/",
        })
    return cookies


def load_cookies(cookie_arg):
    """Load cookies from a CLI argument.

    The argument can be:
      1. A JSON file (.json or file starts with '[') – full cookie dicts
         including HttpOnly cookies exported by --login.
      2. A text file (.txt or existing file) – reads the first non-empty line
         as a cookie header string.
      3. A raw cookie string directly.

    Returns:
        list[dict] – Playwright cookie dicts, or empty list.
    """
    if not cookie_arg:
        return []

    # Check if it's a file path
    if os.path.isfile(cookie_arg):
        print(f"  Loading cookies from file: {cookie_arg}")
        with open(cookie_arg, "r", encoding="utf-8") as f:
            content = f.read().strip()
        if not content:
            return []
        # Try JSON first (exported by --login)
        if content.startswith("["):
            try:
                cookies = json.loads(content)
                # Ensure required fields
                for c in cookies:
                    c.setdefault("path", "/")
                    c.setdefault("domain", ".douyin.com")
                return cookies
            except json.JSONDecodeError:
                pass
        # Otherwise first non-empty, non-comment line as cookie string
        for line in content.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                return parse_cookie_string(line)
        return []

    # Otherwise treat as raw cookie string
    return parse_cookie_string(cookie_arg)


# ---------------------------------------------------------------------------
# Interactive login (saves HttpOnly cookies)
# ---------------------------------------------------------------------------

def interactive_login(save_path="douyin_cookies.json"):
    """Open a visible browser window for QR-code login.

    After the user logs in, ALL cookies (including HttpOnly ones like
    sessionid, ttwid, odin_tt, etc.) are saved to *save_path* as JSON.
    These can later be loaded with ``--cookie douyin_cookies.json``.

    Returns:
        list[dict] – the saved cookies, or empty list on failure.
    """
    from playwright.sync_api import sync_playwright

    print("\n" + "=" * 60)
    print("  Interactive Login")
    print("  A browser window will open.  Please scan the QR code")
    print("  with the Douyin app to log in.")
    print("  After you see your profile / homepage, press Enter here.")
    print("=" * 60 + "\n")

    pw = sync_playwright().start()
    browser = pw.chromium.launch(
        headless=False,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-infobars",
        ],
    )
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        viewport={"width": 1280, "height": 800},
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
    )
    page = context.new_page()
    if HAS_STEALTH:
        _stealth.apply_stealth_sync(page)

    page.goto("https://www.douyin.com/", wait_until="domcontentloaded", timeout=30000)

    # Wait for user to finish logging in
    input(">>> Press Enter after you have logged in in the browser... ")

    # Grab ALL cookies (including HttpOnly)
    cookies = context.cookies()
    context.close()
    browser.close()
    pw.stop()

    if not cookies:
        print("  No cookies captured – login may have failed.")
        return []

    # Check for critical auth cookies
    names = {c["name"] for c in cookies}
    critical = ["sessionid", "sessionid_ss", "ttwid", "odin_tt", "sid_tt", "uid_tt"]
    found = [c for c in critical if c in names]
    missing = [c for c in critical if c not in names]
    print(f"  Captured {len(cookies)} cookies")
    if found:
        print(f"  Auth cookies present: {', '.join(found)}")
    if missing:
        print(f"  Auth cookies MISSING: {', '.join(missing)}  (login may have failed)")

    # Save to file
    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(cookies, f, ensure_ascii=False, indent=2)
    print(f"  Cookies saved to: {save_path}")
    print(f"  Reuse with:  --cookie {save_path}")
    return cookies


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def extract_sec_user_id(url):
    """Extract sec_user_id from a Douyin user profile URL.

    Supports:
        https://www.douyin.com/user/SEC_USER_ID
        https://www.douyin.com/user/SEC_USER_ID?from_tab_name=main&vid=...
    """
    match = re.search(r'/user/([^/?&#]+)', url)
    return match.group(1) if match else None


# ---------------------------------------------------------------------------
# Internal API helpers
# ---------------------------------------------------------------------------

def _fetch_posts_api(page, sec_user_id, max_cursor=0, count=20):
    """Call the user post-list API via fetch() inside the page context.

    Returns the parsed JSON dict, or None on failure.
    """
    api_url = (
        f"https://www.douyin.com/aweme/v1/web/aweme/post/"
        f"?sec_user_id={sec_user_id}"
        f"&count={count}"
        f"&max_cursor={max_cursor}"
        f"&device_platform=webapp"
        f"&aid=6383"
    )
    try:
        return page.evaluate("""
            async (url) => {
                try {
                    const resp = await fetch(url, {credentials: 'include'});
                    return await resp.json();
                } catch(e) {
                    return null;
                }
            }
        """, api_url)
    except Exception as e:
        print(f"  fetch error: {e}")
        return None


def _fetch_user_profile(page, sec_user_id):
    """Call the user profile API to get nickname / avatar etc."""
    api_url = (
        f"https://www.douyin.com/aweme/v1/web/user/profile/other/"
        f"?sec_user_id={sec_user_id}"
        f"&device_platform=webapp"
        f"&aid=6383"
    )
    try:
        return page.evaluate("""
            async (url) => {
                try {
                    const resp = await fetch(url, {credentials: 'include'});
                    return await resp.json();
                } catch(e) {
                    return null;
                }
            }
        """, api_url)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Item / metadata parsers
# ---------------------------------------------------------------------------

def _parse_aweme_item(item):
    """Convert a single aweme_list item into a lightweight work dict."""
    aweme_id = item.get("aweme_id", "")
    desc = item.get("desc", "")
    has_images = bool(item.get("images"))
    content_type = "note" if has_images else "video"
    share_url = (
        f"https://www.douyin.com/note/{aweme_id}"
        if has_images
        else f"https://www.douyin.com/video/{aweme_id}"
    )

    work = {
        "aweme_id": aweme_id,
        "type": content_type,
        "desc": desc,
        "share_url": share_url,
    }

    if item.get("is_top"):
        work["is_top"] = True

    create_time = item.get("create_time")
    if create_time:
        work["create_time"] = create_time

    return work


def _extract_user_info_from_item(item):
    """Extract user info from the author field of an aweme item."""
    author = item.get("author", {})
    if not author:
        return {}
    avatar_urls = (author.get("avatar_thumb") or {}).get("url_list", [])
    return {
        "nickname": author.get("nickname", ""),
        "sec_uid": author.get("sec_uid", ""),
        "uid": author.get("uid", ""),
        "avatar": avatar_urls[0] if avatar_urls else "",
    }


def _extract_user_info_from_profile(profile_data):
    """Extract user info from the profile API response."""
    if not profile_data:
        return {}
    user = profile_data.get("user", {})
    if not user:
        return {}
    avatar_urls = (user.get("avatar_thumb") or {}).get("url_list", [])
    return {
        "nickname": user.get("nickname", ""),
        "sec_uid": user.get("sec_uid", ""),
        "uid": user.get("uid", ""),
        "avatar": avatar_urls[0] if avatar_urls else "",
        "signature": user.get("signature", ""),
        "aweme_count": user.get("aweme_count", None),
    }


def _format_ts(ts):
    """Convert Unix timestamp to readable string."""
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Strategy 1 — Direct API pagination (fast path)
# ---------------------------------------------------------------------------

def _try_api_approach(page, sec_user_id, max_works=None):
    """Paginate the post-list API via fetch(). Returns result dict or None."""
    all_works = []
    user_info = {}
    max_cursor = 0
    page_num = 0

    while True:
        page_num += 1
        print(f"  [API] Fetching page {page_num} (cursor: {max_cursor})...")

        data = _fetch_posts_api(page, sec_user_id, max_cursor)

        if not data:
            if page_num == 1:
                return None
            break

        # Check status_code if present
        if data.get("status_code") not in (0, None):
            print(f"  API error: status_code={data.get('status_code')}")
            if page_num == 1:
                return None
            break

        aweme_list = data.get("aweme_list")
        if aweme_list is None:
            if page_num == 1:
                return None
            break

        if not aweme_list:
            break

        has_more = data.get("has_more", 0)
        max_cursor = data.get("max_cursor", 0)

        for item in aweme_list:
            all_works.append(_parse_aweme_item(item))
            if not user_info:
                user_info = _extract_user_info_from_item(item)

        print(f"    Got {len(aweme_list)} items (total: {len(all_works)})")

        if max_works and len(all_works) >= max_works:
            all_works = all_works[:max_works]
            break

        if not has_more:
            break

        time.sleep(0.5)

    if not all_works:
        return None

    return {
        "user": user_info,
        "works_count": len(all_works),
        "works": all_works,
    }


# ---------------------------------------------------------------------------
# Strategy 2 — Scroll the page & intercept responses (primary)
# ---------------------------------------------------------------------------

# Light resource blocker: only blocks analytics/tracking, NOT images/media
# Images must load so that IntersectionObserver triggers lazy-loading of posts.
_LIGHT_BLOCKED_KEYWORDS = [
    "analytics", "log-sdk", "sentry", "monitor", "beacon",
    "performance", "frontier/collect",
    "hot/search", "notification",
    "user/settings", "risklevel", "online_feedback",
    "solution/resource", "turn/offline",
    "seo/inner/link",
]

def _light_block(route, request):
    """Block only analytics/tracking; allow images and other content."""
    if request.resource_type in {"font", "websocket", "manifest", "texttrack", "eventsource", "ping"}:
        route.abort()
    elif any(kw in request.url for kw in _LIGHT_BLOCKED_KEYWORDS):
        route.abort()
    else:
        route.continue_()


def _create_user_page_context(cookies=None):
    """Create a browser context + page tailored for user page scrolling.

    Unlike BrowserPool.new_context_page(), this does NOT block images/media
    so that IntersectionObserver-based lazy loading works properly.

    Args:
        cookies: list[dict] – Playwright cookie dicts to inject.
    """
    _, browser = BrowserPool.get_browser()
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        viewport={"width": 1280, "height": 800},
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
    )
    if cookies:
        context.add_cookies(cookies)
    page = context.new_page()
    if HAS_STEALTH:
        _stealth.apply_stealth_sync(page)
    page.route("**/*", _light_block)
    return context, page


def _dismiss_popups(page):
    """Dismiss login/notification popups that may block scrolling."""
    selectors = [
        '[class*="close"][class*="modal"]',
        '[class*="close"][class*="login"]',
        '[class*="dy-account-close"]',
        '.douyin-login .dy-account-close',
        '[class*="modal"] [class*="close"]',
        '[class*="dialog"] [class*="close"]',
        'div[class*="mask"] ~ div [class*="close"]',
    ]
    for sel in selectors:
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click()
                page.wait_for_timeout(300)
        except Exception:
            pass


def _try_xhr_pagination(sec_user_id, user_page_url, max_works=None, expected_count=None, cookies=None):
    """Navigate to user page, then paginate via XHR calls.

    The browser's own JS interceptors on ``XMLHttpRequest.prototype.open``
    and ``fetch`` automatically inject ``a_bogus``, ``msToken`` and
    ``verifyFp`` parameters.  So we just need to fire plain XHR/fetch
    requests from within the page context and let the interceptors sign
    them for us.

    This approach does **not** rely on scrolling / IntersectionObserver,
    so it works even when a login wall blocks scroll-based pagination.

    **Important**: We do NOT use the SSR-triggered initial API response
    because intercepting it is racy (sometimes captures 18 items, sometimes
    just 1).  Instead we wait for the page to load (so that the signing JS
    is initialised), then drive *all* pagination ourselves starting from
    ``max_cursor=0``.
    """
    context, page = _create_user_page_context(cookies=cookies)
    try:
        all_works = []
        seen_ids = set()
        user_info = {}

        print(f"  [XHR] Navigating to {user_page_url}...")
        page.goto(user_page_url, wait_until="domcontentloaded", timeout=30000)

        # Wait for the signing JS (securitySDK / secsdk) to initialise.
        # We know the page is ready when the first SSR API call has fired.
        try:
            page.wait_for_response(
                lambda r: "aweme/v1/web/aweme/post" in r.url and r.status == 200,
                timeout=10000,
            )
        except Exception:
            pass  # timeout is fine — signing JS may already be ready
        page.wait_for_timeout(1000)  # small extra buffer

        # ── Paginate using our own XHR calls (cursor=0 → end) ────────
        max_cursor = 0
        has_more = True
        page_num = 0
        empty_pages = 0
        MAX_EMPTY = 3  # give up after 3 consecutive empty/error responses

        while has_more and empty_pages < MAX_EMPTY:
            if max_works and len(all_works) >= max_works:
                break

            page_num += 1
            api_url = (
                f"https://www.douyin.com/aweme/v1/web/aweme/post/"
                f"?device_platform=webapp&aid=6383&channel=channel_pc_web"
                f"&sec_user_id={sec_user_id}&count=18&max_cursor={max_cursor}"
                f"&locate_query=false&show_live_replay_strategy=1"
                f"&need_time_list=1&time_list_query=0&whale_cut_token="
                f"&cut_version=1&publish_video_strategy_type=2"
                f"&from_user_page=1"
            )

            print(f"  [XHR] Fetching page {page_num} (cursor: {max_cursor})...")

            # Use XHR – the browser's interceptor adds a_bogus / msToken
            result = page.evaluate("""
                (url) => {
                    return new Promise((resolve) => {
                        const xhr = new XMLHttpRequest();
                        xhr.open('GET', url, true);
                        xhr.withCredentials = true;
                        xhr.onload = function() {
                            try {
                                resolve(JSON.parse(xhr.responseText));
                            } catch(e) {
                                resolve({_error: 'json_parse', _status: xhr.status, _text: xhr.responseText.substring(0, 300)});
                            }
                        };
                        xhr.onerror = function() {
                            resolve({_error: 'network', _status: xhr.status});
                        };
                        xhr.ontimeout = function() {
                            resolve({_error: 'timeout'});
                        };
                        xhr.timeout = 15000;
                        xhr.send();
                    });
                }
            """, api_url)

            if not result or result.get("_error"):
                print(f"    XHR error: {result}")
                empty_pages += 1
                time.sleep(1)
                continue

            aweme_list = result.get("aweme_list") or []
            has_more = bool(result.get("has_more"))
            new_cursor = result.get("max_cursor", 0)

            batch = 0
            for item in aweme_list:
                aweme_id = item.get("aweme_id")
                if aweme_id and aweme_id not in seen_ids:
                    seen_ids.add(aweme_id)
                    all_works.append(_parse_aweme_item(item))
                    batch += 1
                    if not user_info:
                        info = _extract_user_info_from_item(item)
                        if info:
                            user_info.update(info)

            print(f"    Got {len(aweme_list)} items (+{batch} new), total: {len(all_works)}, has_more={has_more}")

            if batch == 0:
                empty_pages += 1
            else:
                empty_pages = 0

            if new_cursor:
                max_cursor = new_cursor

            time.sleep(0.5 + (0.3 * (page_num % 3)))  # slight jitter

        if max_works and len(all_works) > max_works:
            all_works = all_works[:max_works]

        if not all_works:
            return None

        return {
            "user": user_info,
            "works_count": len(all_works),
            "works": all_works,
        }
    finally:
        context.close()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def get_all_user_works(url, max_works=None, cookies=None):
    """
    Extract all work share-links from a Douyin user profile.

    Args:
        url:       Douyin user profile URL
        max_works: Maximum number of works to retrieve (None = all)
        cookies:   list[dict] – Playwright cookie dicts for authenticated access

    Returns:
        dict  { user: {...}, works_count: int, works: [...] }
        or None on failure.
    """
    sec_user_id = extract_sec_user_id(url)
    if not sec_user_id:
        print("Error: Cannot extract sec_user_id from URL")
        print("Expected format: https://www.douyin.com/user/SEC_USER_ID")
        return None

    user_page_url = f"https://www.douyin.com/user/{sec_user_id}"
    print(f"User sec_uid: {sec_user_id}")
    print(f"Page URL:     {user_page_url}")

    start_ts = time.time()
    result = None

    try:
        # ── Step 1: warm up browser (cookies / session) ──────────────
        print("\nWarming up browser session...")
        BrowserPool.ensure_warmed(user_page_url)
        warm_page = BrowserPool.get_warm_page()

        # ── Step 2: try user profile API for richer info ─────────────
        profile_data = _fetch_user_profile(warm_page, sec_user_id)
        profile_info = _extract_user_info_from_profile(profile_data)
        expected_count = None
        if profile_info.get("nickname"):
            print(f"User:         {profile_info['nickname']}")
        if profile_info.get("aweme_count") is not None:
            expected_count = profile_info["aweme_count"]
            print(f"Total works:  {expected_count} (reported by API)")

        # ── Step 3: XHR-based pagination (primary — browser auto-signs) ──
        if cookies:
            print(f"\nUsing {len(cookies)} cookies for authenticated access")
        print("Fetching works via XHR pagination (browser auto-signs)...")
        result = _try_xhr_pagination(sec_user_id, user_page_url, max_works, expected_count, cookies=cookies)

        if result and result.get("works"):
            if profile_info:
                result["user"] = {**profile_info, **{k: v for k, v in result["user"].items() if v}}
            elapsed = time.time() - start_ts
            print(f"\n[Perf] Completed in {elapsed:.2f}s (XHR)")
            return result

        # ── Step 4: fallback to direct API pagination ────────────────
        print("\nXHR approach returned no data, falling back to direct API...")
        result = _try_api_approach(warm_page, sec_user_id, max_works)

        if result and result.get("works"):
            if profile_info:
                result["user"] = {**profile_info, **{k: v for k, v in result["user"].items() if v}}
            elapsed = time.time() - start_ts
            print(f"\n[Perf] Completed in {elapsed:.2f}s (API)")
            return result

        print("\nFailed to extract any works.")
        return None

    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
        return None

    finally:
        elapsed = time.time() - start_ts
        print(f"[Perf] Total time: {elapsed:.2f}s")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Extract all work share-links from a Douyin user profile",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Step 1: login once to get full cookies (including HttpOnly)
  python douyin_user_phaser.py --login

  # Step 2: use saved cookies for full pagination
  python douyin_user_phaser.py "https://www.douyin.com/user/MS4wLjABAAAA..." --cookie douyin_cookies.json
  python douyin_user_phaser.py "https://www.douyin.com/user/MS4wLjABAAAA..." --cookie douyin_cookies.json --max 50
  python douyin_user_phaser.py "https://www.douyin.com/user/MS4wLjABAAAA..." --cookie douyin_cookies.json --json
        """,
    )
    parser.add_argument("url", nargs="?", default=None, help="Douyin user profile URL")
    parser.add_argument(
        "--max", type=int, default=None, dest="max_works",
        help="Maximum number of works to fetch (default: all)",
    )
    parser.add_argument(
        "--cookie", type=str, default=None, dest="cookie",
        help='Cookie string, cookie file (.txt), or JSON cookie file '
             '(exported by --login). '
             'Format: "name1=value1; name2=value2; ..." or path to file.',
    )
    parser.add_argument(
        "--login", action="store_true", dest="do_login",
        help="Open a browser window for QR-code login, save cookies "
             "(including HttpOnly) to douyin_cookies.json, then exit. "
             "Reuse with: --cookie douyin_cookies.json",
    )
    parser.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output only JSON (suppress human-readable summary)",
    )

    args = parser.parse_args()

    # ── Interactive login mode ──────────────────────────────────
    if args.do_login:
        try:
            interactive_login()
        finally:
            BrowserPool.shutdown()
        sys.exit(0)
    if not args.url:
        parser.error("url is required (unless using --login)")
    # Load cookies
    cookies = load_cookies(args.cookie)
    if cookies:
        print(f"Loaded {len(cookies)} cookies")
        # Check for critical auth cookies
        cookie_names = {c.get("name", "") for c in cookies}
        critical = ["sessionid", "sessionid_ss", "ttwid", "odin_tt", "sid_tt"]
        missing = [c for c in critical if c not in cookie_names]
        if missing:
            print(f"  Warning: missing auth cookies: {', '.join(missing)}")
            print(f"  Hint: use --login to get full cookies (including HttpOnly)")

    try:
        result = get_all_user_works(args.url, args.max_works, cookies=cookies)
    except KeyboardInterrupt:
        print("\nAborted by user.")
        sys.exit(130)
    finally:
        BrowserPool.shutdown()

    if not result:
        print("\nNo works found.")
        sys.exit(1)

    # ── Output ────────────────────────────────────────────────────────
    if args.json_output:
        output = {"code": 0, "message": "success", "data": result}
        print(json.dumps(output, ensure_ascii=False, indent=2))
        sys.exit(0)

    # Human-readable summary
    user = result.get("user", {})
    works = result.get("works", [])

    print("\n" + "=" * 64)
    if user.get("nickname"):
        print(f"  User:       {user['nickname']}")
    if user.get("sec_uid"):
        print(f"  sec_uid:    {user['sec_uid']}")
    if user.get("signature"):
        sig = user["signature"].replace("\n", " ")
        print(f"  Signature:  {sig[:60]}{'...' if len(sig) > 60 else ''}")
    print(f"  Fetched:    {len(works)} works")
    print("=" * 64)

    video_count = sum(1 for w in works if w["type"] == "video")
    note_count = sum(1 for w in works if w["type"] == "note")
    print(f"  Videos: {video_count}  |  Notes (图文): {note_count}")
    print("-" * 64)

    for i, work in enumerate(works):
        type_tag = "[video]" if work["type"] == "video" else "[note] "
        pinned = " 📌" if work.get("is_top") else ""
        desc = work.get("desc", "")
        desc_preview = (desc[:50] + "...") if len(desc) > 50 else desc

        ts_str = ""
        if work.get("create_time"):
            ts_str = f"  ({_format_ts(work['create_time'])})"

        print(f"\n  {i+1:>4}. {type_tag}{pinned}{ts_str}")
        if desc_preview:
            print(f"        {desc_preview}")
        print(f"        {work['share_url']}")

    print("\n" + "=" * 64)

    # Also dump JSON
    print("\nJSON output:")
    output = {"code": 0, "message": "success", "data": result}
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
