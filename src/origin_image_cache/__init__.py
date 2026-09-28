"""Serve image bytes for an origin URL from local indexes, or fetch and store a miss."""

from __future__ import annotations

__version__ = "0.1.0"

from origin_image_cache.cache import CachedImage, ImageCache

__all__ = ["CachedImage", "ImageCache", "__version__"]
