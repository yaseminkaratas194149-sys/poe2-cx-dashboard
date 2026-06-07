"""Currency-icon cache for the cx UI.

Currencies carry an `icon_url` (64×64 PNG on web.poecdn.com). This cache:
  - disk-caches the original PNG once per currency (`_icon_cache/<api>.png`),
  - resizes in memory per requested display size (kept by (api, size)),
  - hands out Tk PhotoImages created on the MAIN thread and held in a dict so
    Tk doesn't garbage-collect them out from under the Treeview.

`get_pil()` is thread-safe (network + PIL only — no Tk), so callers fetch in a
background pool and create the PhotoImage on the main thread via `get_photo()`.
"""

import io
import threading
import urllib.request
from pathlib import Path

from PIL import Image, ImageTk

try:
    from cx.config import USER_AGENT
except Exception:
    USER_AGENT = "poe2cx/0.1"

_CACHE_DIR = Path(__file__).resolve().parent / "_icon_cache"


def _safe(api_id: str) -> str:
    return "".join(c if (c.isalnum() or c in "-_") else "_" for c in api_id)


class IconCache:
    def __init__(self):
        self._orig = {}            # api_id -> PIL.Image (original, RGBA)
        self._resized = {}         # (api_id, size) -> PIL.Image
        self._photos = {}          # (api_id, size) -> ImageTk.PhotoImage
        self._lock = threading.Lock()
        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    # -- background thread: network + PIL only -----------------------------

    def _original(self, api_id, url):
        with self._lock:
            if api_id in self._orig:
                return self._orig[api_id]
        path = _CACHE_DIR / f"{_safe(api_id)}.png"
        img = None
        if path.exists():
            try:
                img = Image.open(path).convert("RGBA")
            except Exception:
                img = None
        if img is None and url:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                data = urllib.request.urlopen(req, timeout=12).read()
                img = Image.open(io.BytesIO(data)).convert("RGBA")
                try:
                    img.save(path)
                except Exception:
                    pass
            except Exception:
                img = None
        if img is not None:
            with self._lock:
                self._orig[api_id] = img
        return img

    def get_pil(self, api_id, url, size):
        """Return a `size`×`size` RGBA PIL.Image (disk/network), or None.
        Safe to call off the main thread."""
        key = (api_id, size)
        with self._lock:
            if key in self._resized:
                return self._resized[key]
        orig = self._original(api_id, url)
        if orig is None:
            return None
        img = orig if orig.size == (size, size) else orig.resize(
            (size, size), Image.LANCZOS)
        with self._lock:
            self._resized[key] = img
        return img

    # -- main thread: Tk image creation ------------------------------------

    def get_photo(self, api_id, size, pil, master):
        """Return a cached PhotoImage for (api_id, size), creating it from
        `pil` if needed. MUST be called on the Tk main thread."""
        key = (api_id, size)
        ph = self._photos.get(key)
        if ph is None:
            ph = ImageTk.PhotoImage(pil, master=master)
            self._photos[key] = ph
        return ph
