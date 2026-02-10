"""
Douyin Media Extractor API Server
Provides HTTP API for extracting media URLs from Douyin pages.

Usage:
    python douyin_api.py
    # Server runs on http://localhost:8000

API:
    GET /?url=<douyin_url>
"""

import asyncio
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import uvicorn

from douyin_phaser import get_douyin_media

app = FastAPI(
    title="Douyin Media Extractor API",
    description="Extract video and image URLs from Douyin pages",
    version="1.0.0",
)

# Enable CORS for all origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _error_response(code: int, message: str):
    """Build a standard error JSON response."""
    return JSONResponse(
        status_code=code,
        content={"code": code, "message": message, "data": None},
    )


def _success_response(data: dict):
    """Build a standard success JSON response."""
    return JSONResponse(
        status_code=200,
        content={"code": 0, "message": "success", "data": data},
    )


@app.get("/")
async def root(url: str = Query(None, description="Douyin share URL or page URL")):
    """
    Root endpoint.
    - If `url` parameter is provided: Extracts media from the URL.
    - If no parameter: Returns API info.
    """
    if url:
        # Validate URL format
        if not url.startswith("http"):
            return _error_response(400, "Invalid URL format, must start with http/https")

        try:
            # Run the synchronous Playwright extraction in a thread to avoid blocking
            result = await asyncio.to_thread(get_douyin_media, url)
        except Exception as e:
            return _error_response(500, f"Extraction failed: {str(e)}")

        if not result:
            return _error_response(500, "Extraction failed: no media found")

        return _success_response(result)

    # If no URL provided, return info
    return {
        "name": "Douyin Media Extractor API",
        "version": "1.0.0",
        "usage": "GET /?url=<douyin_url>",
        "docs": "/docs",
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
