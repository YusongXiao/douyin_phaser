"""
Douyin User Works Extractor API Server
Provides HTTP API for extracting all work share-links from a Douyin user profile.

Usage:
    python douyin_user_phaser_api.py
    # Server runs on http://localhost:8001

API:
    GET /?url=<douyin_user_url>
    GET /?url=<douyin_user_url>&max=50
    GET /?url=<douyin_user_url>&cookie=douyin_cookies.json
"""

import asyncio
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import uvicorn

from douyin_phaser import BrowserPool
from douyin_user_phaser import get_all_user_works, load_cookies

app = FastAPI(
    title="Douyin User Works Extractor API",
    description="Extract all work share-links (video / note) from a Douyin user profile",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Persistent cookie cache — loaded once, reused across requests
# ---------------------------------------------------------------------------
_cookie_cache: dict[str, list[dict]] = {}


def _get_cookies(cookie_arg: str | None) -> list[dict]:
    """Load and cache cookies from a cookie argument."""
    if not cookie_arg:
        return []
    if cookie_arg not in _cookie_cache:
        _cookie_cache[cookie_arg] = load_cookies(cookie_arg)
    return _cookie_cache[cookie_arg]


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

@app.on_event("shutdown")
def shutdown_event():
    BrowserPool.shutdown()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _error_response(code: int, message: str):
    return JSONResponse(
        status_code=code,
        content={"code": code, "message": message, "data": None},
    )


def _success_response(data: dict):
    return JSONResponse(
        status_code=200,
        content={"code": 0, "message": "success", "data": data},
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/")
async def root(
    url: str = Query(None, description="Douyin user profile URL"),
    max: int = Query(None, description="Maximum number of works to fetch"),
    cookie: str = Query(None, description="Cookie string, cookie file path, or JSON cookie file (from --login)"),
):
    """
    Root endpoint.
    - ``url`` provided → extract all works from the user profile.
    - No ``url`` → return API info.
    """
    if url:
        if not url.startswith("http"):
            return _error_response(400, "Invalid URL format, must start with http/https")

        if "/user/" not in url:
            return _error_response(400, "URL must be a Douyin user profile URL containing /user/")

        cookies = _get_cookies(cookie)

        try:
            result = await asyncio.to_thread(get_all_user_works, url, max, cookies=cookies)
        except Exception as e:
            return _error_response(500, f"Extraction failed: {str(e)}")

        if not result:
            return _error_response(500, "Extraction failed: no works found")

        return _success_response(result)

    return {
        "name": "Douyin User Works Extractor API",
        "version": "1.0.0",
        "usage": "GET /?url=<douyin_user_url>",
        "params": {
            "url": "Douyin user profile URL (required)",
            "max": "Maximum number of works to fetch (optional)",
            "cookie": "Cookie string or path to cookie file (optional, use --login exported JSON for full auth)",
        },
        "docs": "/docs",
        "tip": "Run `python douyin_user_phaser.py --login` first to export cookies with HttpOnly auth tokens.",
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)
