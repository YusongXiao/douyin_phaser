"""
Douyin Media Extractor
Extracts video URLs and image URLs from Douyin pages.
Supports: video pages (/video/), note/image pages (/note/)
"""

import sys
import json
import time
import re
from urllib.parse import urlparse

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
                        if bit_rate_list:
                            print(f"\n[Video] Found {len(bit_rate_list)} quality options.")

                        best_url = None
                        best_resolution = 0
                        best_bitrate = 0
                        best_width = 0
                        best_height = 0

                        for br in bit_rate_list:
                            fmt = br.get("format", "")
                            is_h265 = br.get("is_h265", 0)
                            current_br = br.get("bit_rate", 0)
                            gear = br.get("gear_name", "unknown")

                            br_play_addr = br.get("play_addr", {})
                            br_url_list = br_play_addr.get("url_list", [])

                            width = br_play_addr.get("width", 0)
                            height = br_play_addr.get("height", 0)
                            resolution = width * height

                            codec = "H265" if is_h265 else "H264"
                            print(f" - {gear} ({width}x{height}) | {fmt} | {codec} | Bitrate: {current_br}")

                            # Only select mp4 format (muxed video+audio)
                            if fmt != "mp4":
                                continue

                            if width == 0:
                                continue

                            # Priority: higher resolution, then higher bitrate
                            is_better = False
                            if resolution > best_resolution:
                                is_better = True
                            elif resolution == best_resolution and current_br > best_bitrate:
                                is_better = True

                            if is_better and br_url_list:
                                best_resolution = resolution
                                best_bitrate = current_br
                                selected = _select_best_url(br_url_list, "douyinvod.com")
                                best_url = selected
                                best_width = width
                                best_height = height

                        if best_url:
                            print(f"\nSelected: {best_width}x{best_height} | Bitrate: {best_bitrate} | Format: mp4")
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


def get_douyin_media(url):
    """
    Extracts media URLs from a Douyin page.
    Supports both video pages and note/image pages.

    Returns:
        dict matching the API response schema:
        {
            "title": str,
            "author": str,
            "author_id": str,
            "cover": str,
            "type": "video" | "images",
            "items": [
                {"type": "video", "video_url": str, "cover_url": str},
                {"type": "image", "image_url": str},
                {"type": "animated_image", "image_url": str, "video_url": str}
            ]
        }
        or None on failure.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Error: 'playwright' library is required.")
        print("Please install: pip install playwright && playwright install chromium")
        return None

    result = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 720}
        )
        page = context.new_page()

        found_data = {"url": None, "type": None, "cover_url": None, "metadata": None}

        # Attach API response handler for video extraction
        page.on("response", extract_video_from_api(page, found_data))

        try:
            print(f"Navigating to {url}...")
            page.goto(url, wait_until="domcontentloaded", timeout=30000)

            # Wait and check for video extraction
            time.sleep(3)

            # Determine content type from final URL
            final_url = page.url
            print(f"Final URL: {final_url}")

            if "/video/" in final_url:
                # Video page - wait for API interception
                max_retries = 15
                for _ in range(max_retries):
                    if found_data["url"]:
                        break
                    page.mouse.wheel(0, 100)
                    time.sleep(0.5)

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
                    print("Failed to extract video URL via API.")

            elif "/note/" in final_url:
                # Note/Image page - extract via API for complete content
                print("\n[Note] Detected image/note page, extracting content...")

                # Extract note ID from URL
                note_match = re.search(r'/note/(\d+)', final_url)
                if note_match:
                    note_id = note_match.group(1)

                    # Try API extraction first (more reliable for lazy-loaded content)
                    api_result = extract_note_from_api(page, note_id)

                    if api_result and api_result.get("items"):
                        meta = api_result["metadata"]
                        items = api_result["items"]
                        # Determine top-level type
                        content_type = "images"
                        result = {
                            "title": meta.get("title", ""),
                            "author": meta.get("author", ""),
                            "author_id": meta.get("author_id", ""),
                            "cover": meta.get("cover", ""),
                            "type": content_type,
                            "items": items,
                        }
                        item_types = [it["type"] for it in items]
                        animated_count = item_types.count("animated_image")
                        image_count = item_types.count("image")
                        print(f"Successfully extracted {animated_count} animated + {image_count} static images via API.")
                    else:
                        # Fallback to DOM extraction
                        print("API extraction failed, falling back to DOM...")
                        time.sleep(2)
                        dom_images = extract_images_from_dom(page)
                        dom_videos = extract_videos_from_dom(page)

                        items = []
                        if dom_videos:
                            # Pair videos with images if possible
                            for i, vid_url in enumerate(dom_videos):
                                item = {
                                    "type": "animated_image",
                                    "video_url": vid_url,
                                }
                                if i < len(dom_images):
                                    item["image_url"] = dom_images[i]
                                items.append(item)
                            # Remaining images (if more images than videos)
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
                    print("Could not extract note ID from URL.")

            else:
                print(f"Unknown page type: {final_url}")

        except Exception as e:
            print(f"Error: {e}")
        finally:
            browser.close()

    return result


if __name__ == "__main__":
    if len(sys.argv) > 1:
        target_url = sys.argv[1]
    else:
        target_url = "https://www.douyin.com/video/7596660447961464689"

    print(f"Target: {target_url}")
    print("Extracting...")

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
