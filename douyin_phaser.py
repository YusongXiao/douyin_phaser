"""
Douyin Media Extractor
Extracts video URLs and image URLs from Douyin pages.
Supports: video pages (/video/), note/image pages (/note/)
"""

import sys
import json
import time
import re
import http.client
from urllib.parse import urlparse
from urllib.request import Request, urlopen

def extract_images_from_dom(page):
    """
    Extract image URLs directly from DOM for note/image pages.
    Returns a list of high-quality, signed image URLs that can be accessed directly.
    """
    # Collect all potential image URLs from both img tags and background-image
    all_urls = set()
    
    # Method 1: img tags
    imgs = page.query_selector_all('img')
    for img in imgs:
        src = img.get_attribute('src')
        if src:
            all_urls.add(src)
    
    # Method 2: background-image styles
    divs = page.query_selector_all('[style*="background-image"]')
    for div in divs:
        style = div.get_attribute('style')
        if style and 'url(' in style:
            match = re.search(r'url\(["\']?([^"\'()]+)["\']?\)', style)
            if match:
                all_urls.add(match.group(1))
    
    # Filter for main content images with valid signatures
    content_images = []
    
    for url in all_urls:
        # Ensure https
        if url.startswith('//'):
            url = 'https:' + url
        
        # Must be from Douyin image CDN
        if 'douyinpic' not in url:
            continue
        
        # Must be content images (not stickers, avatars, emojis, etc.)
        # Content images have biz_tag=aweme_images or biz_tag=pcweb_cover
        if 'biz_tag=aweme_images' in url or 'biz_tag=pcweb_cover' in url:
            # Must have signature for access
            if 'x-expires' in url or 'x-signature' in url:
                # Exclude sticker/comment URLs
                if 'sticker' not in url and 'aweme_comment' not in url:
                    # Only include actual content images (tos-cn-i-0813), not recommendation thumbnails
                    if 'tos-cn-i-0813' in url:
                        content_images.append(url)
    
    # Remove duplicates while preserving order (based on base URL)
    seen_bases = set()
    unique_images = []
    
    for url in content_images:
        # Extract base URL (before query params) for deduplication
        base = url.split('?')[0].split('~')[0]
        if base not in seen_bases:
            seen_bases.add(base)
            unique_images.append(url)
    
    return unique_images


def extract_videos_from_dom(page):
    """
    Extract video source URLs from <video> elements for animated content.
    Note pages with animated GIFs actually use MP4 videos.
    Returns a list of video URLs (one per unique video, preferring v3-web domain).
    """
    video_sources = []
    
    videos = page.query_selector_all('video')
    for video in videos:
        sources = video.query_selector_all('source')
        for source in sources:
            src = source.get_attribute('src')
            if src and 'douyinvod' in src:
                video_sources.append(src)
    
    # Group by video content (URLs ending in same path pattern)
    # and prefer v3-web domain for each unique video
    seen_videos = set()
    unique_videos = []
    
    for url in video_sources:
        # Extract video identifier from URL path
        # Pattern: /video/tos/cn/tos-cn-ve-15/XXXXX/?
        match = re.search(r'/video/tos/cn/tos-cn-ve-15/([^/]+)/', url)
        if match:
            video_id = match.group(1)
            if video_id not in seen_videos:
                seen_videos.add(video_id)
                # Prefer v3-web domain
                if 'v3-web.douyinvod' in url:
                    unique_videos.append(url)
                elif not any('v3-web.douyinvod' in v and video_id in v for v in video_sources):
                    # No v3-web version available, use this one
                    unique_videos.append(url)
    
    return unique_videos


def extract_metadata(detail):
    """
    Extract metadata (title, author, author_id, cover) from aweme_detail.
    Returns a dict with metadata fields.
    """
    metadata = {
        "title": "",
        "author": "",
        "author_id": "",
        "cover": "",
    }

    if not detail:
        return metadata

    metadata["title"] = detail.get("desc", "")

    author_info = detail.get("author", {})
    if author_info:
        metadata["author"] = author_info.get("nickname", "")
        metadata["author_id"] = author_info.get("sec_uid", "")

    # Cover image: try video cover first, then general cover
    video_info = detail.get("video", {})
    if video_info:
        cover_info = video_info.get("cover", {}) or video_info.get("origin_cover", {})
        cover_urls = cover_info.get("url_list", [])
        if cover_urls:
            metadata["cover"] = cover_urls[0]

    if not metadata["cover"]:
        # Fallback: use first image as cover for note pages
        images = detail.get("images", [])
        if images:
            first_img_urls = images[0].get("url_list", [])
            if first_img_urls:
                metadata["cover"] = first_img_urls[0]

    return metadata


def _select_best_url(url_list, preferred_domain):
    """Select the best URL from a list, preferring a specific domain."""
    for u in url_list:
        if preferred_domain in u:
            return u
    return url_list[0] if url_list else None


def extract_note_from_api(page, note_id):
    """
    Extract note/image content by calling the aweme detail API.
    This is more reliable than DOM extraction for note pages with lazy-loaded content.

    Returns:
        dict with 'metadata' and 'items' list, or None if extraction fails.
    """
    api_url = f"https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id={note_id}&device_platform=webapp&aid=6383"

    try:
        response = page.evaluate(f"""
            async () => {{
                try {{
                    const resp = await fetch("{api_url}", {{
                        credentials: 'include'
                    }});
                    return await resp.json();
                }} catch(e) {{
                    return null;
                }}
            }}
        """)

        if not response or "aweme_detail" not in response:
            return None

        detail = response["aweme_detail"]
        if not detail or "images" not in detail:
            return None

        metadata = extract_metadata(detail)
        images = detail["images"]
        items = []

        for img in images:
            # Get the static image URL
            image_url = None
            if "url_list" in img:
                image_url = _select_best_url(img["url_list"], "douyinpic")

            # Check if this image has video (animated GIF as MP4)
            video_url = None
            if "video" in img:
                vid = img["video"]
                play_addr = vid.get("play_addr", {})
                url_list = play_addr.get("url_list", [])
                if url_list:
                    video_url = _select_best_url(url_list, "v3-web.douyinvod")
                    if not video_url:
                        video_url = _select_best_url(url_list, "douyinvod")

            # Build item based on content
            if video_url and image_url:
                items.append({
                    "type": "animated_image",
                    "image_url": image_url,
                    "video_url": video_url,
                })
            elif image_url:
                items.append({
                    "type": "image",
                    "image_url": image_url,
                })

        return {
            "metadata": metadata,
            "items": items,
        }

    except Exception as e:
        print(f"  Error calling detail API: {e}")
        return None


def extract_video_from_api(page, found_data):
    """
    Handler to extract video URL and metadata from API response.
    Modifies found_data dict with extracted URL, cover, and metadata.
    """
    def handle_response(response):
        # Check for the detail API response
        if "aweme/v1/web/aweme/detail" in response.url and response.status == 200:
            try:
                json_data = response.json()
                if "aweme_detail" in json_data:
                    detail = json_data["aweme_detail"]
                    # Only process video pages (has "video" key with bit_rate), not note pages
                    # Skip if we already have the data (handler can fire multiple times)
                    if found_data.get("url"):
                        return
                    if detail and "video" in detail and "bit_rate" in detail.get("video", {}):
                        # Extract metadata
                        found_data["metadata"] = extract_metadata(detail)

                        # Extract video cover URL
                        video_info = detail.get("video", {})
                        cover_info = video_info.get("cover", {})
                        cover_urls = cover_info.get("url_list", [])
                        cover_url = cover_urls[0] if cover_urls else ""

                        # Find best quality


                        bit_rate_list = video_info.get("bit_rate", [])

                        best_url = None
                        best_resolution = 0
                        best_bitrate = 0
                        best_width = 0
                        best_height = 0

                        for br in bit_rate_list:
                            fmt = br.get("format", "")
                            current_br = br.get("bit_rate", 0)

                            br_play_addr = br.get("play_addr", {})
                            br_url_list = br_play_addr.get("url_list", [])

                            width = br_play_addr.get("width", 0)
                            height = br_play_addr.get("height", 0)
                            resolution = width * height

                            # Only select mp4 format (muxed video+audio)
                            if fmt != "mp4" or width == 0:
                                continue

                            # Priority: higher resolution, then higher bitrate
                            if resolution > best_resolution or (
                                resolution == best_resolution and current_br > best_bitrate
                            ):
                                if br_url_list:
                                    best_resolution = resolution
                                    best_bitrate = current_br
                                    best_url = _select_best_url(br_url_list, "douyinvod.com")
                                    best_width = width
                                    best_height = height

                        if best_url:
                            print(f"[Video] Selected: {best_width}x{best_height} mp4 ({best_bitrate//1000}kbps) from {len(bit_rate_list)} options")
                            found_data["url"] = best_url
                            found_data["type"] = "video"
                            found_data["cover_url"] = cover_url
                        else:
                            # Fallback to default play_addr
                            play_addr = video_info.get("play_addr", {})
                            url_list = play_addr.get("url_list", [])
                            if url_list:
                                found_data["url"] = url_list[0]
                                found_data["type"] = "video"
                                found_data["cover_url"] = cover_url

            except Exception as e:
                print(f"  Error parsing API: {e}")

    return handle_response


# ---------------------------------------------------------------------------
# Browser Pool: Keeps a persistent Chromium instance across requests
# ---------------------------------------------------------------------------

class BrowserPool:
    """Maintains a persistent browser instance to avoid cold-start per request."""
    _playwright = None
    _browser = None
    _lock = None

    @classmethod
    def _get_lock(cls):
        """Lazy-init a lock (avoids import-time threading dependency)."""
        if cls._lock is None:
            import threading
            cls._lock = threading.Lock()
        return cls._lock

    @classmethod
    def get_browser(cls):
        """Return (playwright, browser), launching on first call."""
        if cls._browser and cls._browser.is_connected():
            return cls._playwright, cls._browser

        with cls._get_lock():
            # Double check inside lock
            if cls._browser and cls._browser.is_connected():
                return cls._playwright, cls._browser

            from playwright.sync_api import sync_playwright
            cls._playwright = sync_playwright().start()
            cls._browser = cls._playwright.chromium.launch(headless=True)
            print("[BrowserPool] Chromium launched.")
            return cls._playwright, cls._browser

    @classmethod
    def shutdown(cls):
        """Clean up browser and Playwright resources."""
        with cls._get_lock():
            if cls._browser:
                try:
                    cls._browser.close()
                except Exception:
                    pass
                cls._browser = None
            if cls._playwright:
                try:
                    cls._playwright.stop()
                except Exception:
                    pass
                cls._playwright = None
            print("[BrowserPool] Shut down.")


# ---------------------------------------------------------------------------
# Resource blocking: skip images, fonts, CSS, analytics to speed up loading
# ---------------------------------------------------------------------------

_BLOCKED_RESOURCE_TYPES = {"image", "font", "stylesheet", "media", "websocket", "manifest", "texttrack", "eventsource", "ping"}
_BLOCKED_URL_KEYWORDS = ["analytics", "log-sdk", "sentry", "monitor", "beacon", "performance", "frontier/collect"]

def _block_unnecessary(route, request):
    """Abort requests for resources we don't need."""
    if request.resource_type in _BLOCKED_RESOURCE_TYPES:
        route.abort()
    elif any(kw in request.url for kw in _BLOCKED_URL_KEYWORDS):
        route.abort()
    else:
        route.continue_()



# ---------------------------------------------------------------------------
# Performance helpers
# ---------------------------------------------------------------------------

def _resolve_short_url(url):
    """Resolve Douyin short/share URL to final URL via HTTP redirect.

    Uses a single HTTP request to get the 302 Location header and extract
    the content ID, avoiding the slow multi-step browser redirect (~1-2s saved).
    """
    if 'v.douyin.com' not in url and '/share/' not in url:
        return url
    try:
        parsed = urlparse(url)
        conn = http.client.HTTPSConnection(parsed.hostname, timeout=5)
        conn.request("GET", parsed.path, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        })
        resp = conn.getresponse()
        location = resp.getheader("Location") or ""
        conn.close()

        # Extract video/note ID from the redirect Location header
        # e.g. https://www.iesdouyin.com/share/video/7600310201732179264/?...
        vid_match = re.search(r'/video/(\d+)', location)
        note_match = re.search(r'/note/(\d+)', location)
        if vid_match:
            return f"https://www.douyin.com/video/{vid_match.group(1)}"
        elif note_match:
            return f"https://www.douyin.com/note/{note_match.group(1)}"
    except Exception:
        pass
    return url


def _call_detail_api(page, aweme_id):
    """Call the aweme detail API directly from page context via fetch()."""
    api_url = f"https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id={aweme_id}&device_platform=webapp&aid=6383"
    try:
        response = page.evaluate("""
            async (url) => {
                try {
                    const resp = await fetch(url, {credentials: 'include'});
                    return await resp.json();
                } catch(e) {
                    return null;
                }
            }
        """, api_url)
        if response and "aweme_detail" in response:
            return response["aweme_detail"]
    except Exception as e:
        print(f"  Direct API call error: {e}")
    return None


def _parse_video_detail(detail):
    """Parse video information from an aweme_detail dict.

    Returns a result dict matching the API response schema, or None.
    """
    if not detail or "video" not in detail or "bit_rate" not in detail.get("video", {}):
        return None

    metadata = extract_metadata(detail)
    video_info = detail["video"]

    # Cover URL
    cover_info = video_info.get("cover", {})
    cover_urls = cover_info.get("url_list", [])
    cover_url = cover_urls[0] if cover_urls else ""

    # Find best quality mp4
    bit_rate_list = video_info.get("bit_rate", [])
    best_url = None
    best_resolution = 0
    best_bitrate = 0
    best_width = 0
    best_height = 0

    for br in bit_rate_list:
        fmt = br.get("format", "")
        current_br = br.get("bit_rate", 0)
        br_play_addr = br.get("play_addr", {})
        br_url_list = br_play_addr.get("url_list", [])
        width = br_play_addr.get("width", 0)
        height = br_play_addr.get("height", 0)
        resolution = width * height

        if fmt != "mp4" or width == 0:
            continue

        if resolution > best_resolution or (
            resolution == best_resolution and current_br > best_bitrate
        ):
            if br_url_list:
                best_resolution = resolution
                best_bitrate = current_br
                best_url = _select_best_url(br_url_list, "douyinvod.com")
                best_width = width
                best_height = height

    if not best_url:
        play_addr = video_info.get("play_addr", {})
        url_list = play_addr.get("url_list", [])
        if url_list:
            best_url = url_list[0]

    if not best_url:
        return None

    print(f"[Video] Selected: {best_width}x{best_height} mp4 ({best_bitrate // 1000}kbps) from {len(bit_rate_list)} options")

    return {
        "title": metadata.get("title", ""),
        "author": metadata.get("author", ""),
        "author_id": metadata.get("author_id", ""),
        "cover": metadata.get("cover", ""),
        "type": "video",
        "items": [
            {
                "type": "video",
                "video_url": best_url,
                "cover_url": cover_url,
            }
        ],
    }


def get_douyin_media(url):
    """
    Extracts media URLs from a Douyin page.
    Supports both video pages and note/image pages.

    Returns:
        dict matching the API response schema, or None on failure.
    """
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        print("Error: 'playwright' library is required.")
        print("Please install: pip install playwright && playwright install chromium")
        return None

    result = None
    start_ts = time.time()

    # Step 1: Quick-resolve short URL via HTTP (saves ~1-2s of browser redirect)
    resolved_url = _resolve_short_url(url)
    if resolved_url != url:
        print(f"Resolved: {resolved_url}")
    nav_url = resolved_url

    # Step 2: Pre-determine content type and ID from URL
    content_id = None
    content_type = None
    vid_match = re.search(r'/video/(\d+)', resolved_url)
    note_match = re.search(r'/note/(\d+)', resolved_url)
    if vid_match:
        content_id = vid_match.group(1)
        content_type = 'video'
    elif note_match:
        content_id = note_match.group(1)
        content_type = 'note'

    _, browser = BrowserPool.get_browser()
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        viewport={"width": 1280, "height": 720}
    )
    page = context.new_page()
    page.route("**/*", _block_unnecessary)

    try:
        if content_type == 'video':
            # --- Video page: use expect_response for fast API interception ---
            print(f"Navigating to {nav_url}...")
            try:
                with page.expect_response(
                    lambda r: "aweme/v1/web/aweme/detail" in r.url and r.status == 200,
                    timeout=15000
                ) as resp_info:
                    page.goto(nav_url, wait_until="commit", timeout=30000)

                api_resp = resp_info.value
                json_data = api_resp.json()
                print(f"Final URL: {page.url}")

                if "aweme_detail" in json_data:
                    result = _parse_video_detail(json_data["aweme_detail"])
                    if result:
                        print("Successfully extracted video URL.")
            except Exception as e:
                print(f"  Response interception timed out: {e}")

            # Fallback: direct API call if interception failed
            if not result and content_id:
                print("  Trying direct API call...")
                detail = _call_detail_api(page, content_id)
                if detail:
                    result = _parse_video_detail(detail)
                    if result:
                        print("Successfully extracted video URL via direct API.")

            if not result:
                print("Failed to extract video URL.")

        elif content_type == 'note':
            # --- Note/Image page ---
            print(f"Navigating to {nav_url}...")
            page.goto(nav_url, wait_until="domcontentloaded", timeout=30000)
            print(f"Final URL: {page.url}")
            print("\n[Note] Detected image/note page, extracting content...")

            api_result = extract_note_from_api(page, content_id)

            if api_result and api_result.get("items"):
                meta = api_result["metadata"]
                items = api_result["items"]
                result = {
                    "title": meta.get("title", ""),
                    "author": meta.get("author", ""),
                    "author_id": meta.get("author_id", ""),
                    "cover": meta.get("cover", ""),
                    "type": "images",
                    "items": items,
                }
                item_types = [it["type"] for it in items]
                animated_count = item_types.count("animated_image")
                image_count = item_types.count("image")
                print(f"Successfully extracted {animated_count} animated + {image_count} static images via API.")
            else:
                # Fallback to DOM extraction
                print("API extraction failed, falling back to DOM...")
                try:
                    page.wait_for_selector('img[src*="douyinpic"], [style*="background-image"]', timeout=5000)
                except Exception:
                    pass

                dom_images = extract_images_from_dom(page)
                dom_videos = extract_videos_from_dom(page)

                items = []
                if dom_videos:
                    for i, vid_url in enumerate(dom_videos):
                        item = {"type": "animated_image", "video_url": vid_url}
                        if i < len(dom_images):
                            item["image_url"] = dom_images[i]
                        items.append(item)
                    for img_url in dom_images[len(dom_videos):]:
                        items.append({"type": "image", "image_url": img_url})
                else:
                    for img_url in dom_images:
                        items.append({"type": "image", "image_url": img_url})

                if items:
                    result = {
                        "title": "",
                        "author": "",
                        "author_id": "",
                        "cover": "",
                        "type": "images",
                        "items": items,
                    }
                    print(f"Successfully extracted {len(items)} items from DOM.")
                else:
                    print("Failed to extract content.")

        else:
            # --- Unknown type: navigate and determine from final URL ---
            print(f"Navigating to {nav_url}...")
            found_data = {"url": None, "type": None, "cover_url": None, "metadata": None}
            page.on("response", extract_video_from_api(page, found_data))
            page.goto(nav_url, wait_until="domcontentloaded", timeout=30000)
            final_url = page.url
            print(f"Final URL: {final_url}")

            if "/video/" in final_url:
                for _ in range(10):
                    if found_data["url"]:
                        break
                    page.wait_for_timeout(500)

                if found_data["url"]:
                    meta = found_data.get("metadata") or {}
                    result = {
                        "title": meta.get("title", ""),
                        "author": meta.get("author", ""),
                        "author_id": meta.get("author_id", ""),
                        "cover": meta.get("cover", ""),
                        "type": "video",
                        "items": [
                            {
                                "type": "video",
                                "video_url": found_data["url"],
                                "cover_url": found_data.get("cover_url", ""),
                            }
                        ],
                    }
                    print("Successfully extracted video URL.")
                else:
                    vid_m = re.search(r'/video/(\d+)', final_url)
                    if vid_m:
                        detail = _call_detail_api(page, vid_m.group(1))
                        if detail:
                            result = _parse_video_detail(detail)
                            if result:
                                print("Successfully extracted video URL via direct API.")

            elif "/note/" in final_url:
                note_m = re.search(r'/note/(\d+)', final_url)
                if note_m:
                    api_result = extract_note_from_api(page, note_m.group(1))
                    if api_result and api_result.get("items"):
                        meta = api_result["metadata"]
                        result = {
                            "title": meta.get("title", ""),
                            "author": meta.get("author", ""),
                            "author_id": meta.get("author_id", ""),
                            "cover": meta.get("cover", ""),
                            "type": "images",
                            "items": api_result["items"],
                        }
                        print("Successfully extracted content via API.")
            else:
                print(f"Unknown page type: {final_url}")

    except Exception as e:
        print(f"Error: {e}")
    finally:
        context.close()
        elapsed = time.time() - start_ts
        print(f"[Perf] Request completed in {elapsed:.2f}s")

    return result


if __name__ == "__main__":
    if len(sys.argv) > 1:
        target_url = sys.argv[1]
    else:
        target_url = "https://www.douyin.com/video/7596660447961464689"

    print(f"Target: {target_url}")
    print("Extracting...")

    try:
        result = get_douyin_media(target_url)

        if result:
            print("\n" + "=" * 50)
            print(f"Title:  {result.get('title', '')}")
            print(f"Author: {result.get('author', '')}")
            print(f"Type:   {result.get('type', '')}")
            print(f"Cover:  {result.get('cover', '')}")
            print(f"Items ({len(result.get('items', []))}):\n")
            for i, item in enumerate(result.get("items", [])):
                item_type = item["type"]
                if item_type == "video":
                    print(f"  {i+1}. [video]  {item['video_url']}")
                elif item_type == "image":
                    print(f"  {i+1}. [image]  {item['image_url']}")
                elif item_type == "animated_image":
                    print(f"  {i+1}. [animated]")
                    print(f"     image: {item.get('image_url', '')}")
                    print(f"     video: {item.get('video_url', '')}")

            # Also print full JSON for programmatic consumption
            print("\n" + "=" * 50)
            print("JSON output:")
            api_response = {
                "code": 0,
                "message": "success",
                "data": result,
            }
            print(json.dumps(api_response, ensure_ascii=False, indent=2))
        else:
            print("\nFailed to extract media.")
    finally:
        BrowserPool.shutdown()
