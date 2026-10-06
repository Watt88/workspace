"""DjVu books, kept native: pages are rendered with `ddjvu`, the hidden text layer, page count and bookmarks are read with
`djvused` (DjVuLibre command-line tools, called as subprocesses).

DjVuLibre is looked for in: $READER3_DJVULIBRE, ./tools/djvulibre, Program Files, PATH. `ddjvu -format=pdf` is NOT used:
it drops the text layer and the bookmarks and inflates the file."""
import io
import math
import os
import re
import shutil
import subprocess
import tempfile
from typing import List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
_found = {}


def find_tool(name: str) -> Optional[str]:
    if name in _found:
        return _found[name]
    exe = name + (".exe" if os.name == "nt" else "")
    cands = [os.environ.get("READER3_DJVULIBRE", ""), os.path.join(HERE, "tools", "djvulibre"),
             r"C:\Program Files\DjVuLibre", r"C:\Program Files (x86)\DjVuLibre"]
    path = next((os.path.join(d, exe) for d in cands if d and os.path.isfile(os.path.join(d, exe))), None) or shutil.which(name)
    _found[name] = path
    return path


def available() -> bool:
    return bool(find_tool("ddjvu") and find_tool("djvused"))


def _run(args: List[str], timeout: int = 180) -> bytes:
    p = subprocess.run(args, capture_output=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(f"{os.path.basename(args[0])} failed: {p.stderr.decode('utf-8', 'replace')[-200:]}")
    return p.stdout


def _sed(path: str, expr: str, timeout: int = 180) -> str:
    return _run([find_tool("djvused"), path, "-e", expr], timeout).decode("utf-8", "replace")


def page_count(path: str) -> int:
    return int(_sed(path, "n").strip())


def page_size(path: str, n: int) -> tuple:
    """(width, height) in pixels of page n (0-based)."""
    m = re.search(r"width=(\d+)\s+height=(\d+)", _sed(path, f"select {n + 1}; size"))
    return (int(m.group(1)), int(m.group(2))) if m else (1200, 1600)


def texts(path: str, n: int) -> List[str]:
    """Hidden text of every page (empty strings for pages without a text layer)."""
    raw = _sed(path, "print-pure-txt", timeout=600)
    parts = raw.split("\f")
    if len(parts) >= n:
        pages = parts[:n]
    else:                                         # the layer is sparse: ask page by page
        pages = []
        for i in range(n):
            pages.append(_sed(path, f"select {i + 1}; print-pure-txt").strip())
    ctrl = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")      # US/GS separators of the zone tree
    return [re.sub(r"[ \t]+", " ", ctrl.sub("", p)).strip() for p in pages]


def _sexp(s: str, i: int = 0):
    """Tiny S-expression reader for djvused outlines: returns (list, next_index)."""
    out = []
    while i < len(s):
        c = s[i]
        if c == "(":
            node, i = _sexp(s, i + 1)
            out.append(node)
        elif c == ")":
            return out, i + 1
        elif c == '"':
            j, buf = i + 1, []
            while j < len(s) and s[j] != '"':
                if s[j] == "\\" and j + 1 < len(s):
                    j += 1
                buf.append(s[j])
                j += 1
            out.append("".join(buf))
            i = j + 1
        elif c.isspace():
            i += 1
        else:                                     # a bare atom such as: bookmarks
            j = i
            while j < len(s) and not s[j].isspace() and s[j] not in '()"':
                j += 1
            out.append(s[i:j])
            i = j
    return out, i


def outline(path: str, n: int) -> List[list]:
    """[[level, title, page_index_0based]] from the bookmarks (empty when the book has none)."""
    try:
        raw = _sed(path, "print-outline")
    except RuntimeError:
        return []
    tree, _ = _sexp(raw)
    res = []

    def walk(items, lvl):
        for it in items:
            if not isinstance(it, list) or not it:
                continue
            title = it[0] if isinstance(it[0], str) else ""
            dest = it[1] if len(it) > 1 and isinstance(it[1], str) else ""
            m = re.fullmatch(r"#(\d+)", dest.strip())
            if title and m and 1 <= int(m.group(1)) <= n:
                res.append([lvl, title.strip(), int(m.group(1)) - 1])
            walk([x for x in it[2:] if isinstance(x, list)], lvl + 1)

    for top in tree:
        if isinstance(top, list) and top and top[0] == "bookmarks":
            walk([x for x in top[1:] if isinstance(x, list)], 1)
    return res


def _render_ppm(path: str, n: int, w: int, h: int) -> bytes:
    fd, tmp = tempfile.mkstemp(suffix=".ppm", prefix="djvu-")
    os.close(fd)
    try:
        _run([find_tool("ddjvu"), "-format=ppm", f"-page={n + 1}", f"-size={w}x{h}", path, tmp])
        with open(tmp, "rb") as f:
            return f.read()
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def render_jpeg(path: str, n: int, width: int, quality: int = 86) -> bytes:
    from PIL import Image
    pw, ph = page_size(path, n)
    w = int(width)
    h = max(1, int(math.ceil(w * ph / pw)))
    img = Image.open(io.BytesIO(_render_ppm(path, n, w, h))).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def render_png_for_ocr(path: str, n: int, long_side: int = 2600) -> bytes:
    from PIL import Image
    pw, ph = page_size(path, n)
    scale = min(1.0, long_side / max(pw, ph))
    w, h = max(1, int(pw * scale)), max(1, int(ph * scale))
    img = Image.open(io.BytesIO(_render_ppm(path, n, w, h))).convert("L")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()
