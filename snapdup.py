#!/usr/bin/env python3
"""
SnapDup — Reclaim your disk.

A 100% offline, zero-dependency duplicate-file finder and interactive disk
visualizer. Finds byte-identical duplicates, shows what is eating your space,
and renders a self-contained, interactive HTML treemap report — no internet,
no accounts, no telemetry. Pure Python standard library.

Usage:
    python3 snapdup.py PATH [PATH ...] [-o report.html]
    python3 snapdup.py ~/Pictures --trash ~/.Trash/dupes
"""

import argparse
import datetime as _dt
import hashlib
import html
import json
import math
import os
import platform
import sys
import time
from collections import defaultdict

__version__ = "1.0.0"

# --------------------------------------------------------------------------
# Categories & colors
# --------------------------------------------------------------------------
CATEGORIES = {
    "image": ["jpg", "jpeg", "png", "gif", "bmp", "webp", "svg", "tif", "tiff",
              "heic", "heif", "ico", "raw", "cr2", "nef", "arw", "avif"],
    "video": ["mp4", "mkv", "mov", "avi", "webm", "flv", "wmv", "m4v", "mpg",
              "mpeg", "ts", "3gp"],
    "audio": ["mp3", "wav", "flac", "aac", "ogg", "m4a", "wma", "opus", "wma"],
    "archive": ["zip", "rar", "7z", "tar", "gz", "tgz", "bz2", "xz", "dmg",
                "iso", "cab", "zst"],
    "doc": ["pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "md",
            "rtf", "odt", "csv", "epub", "pages", "key", "numbers", "tex"],
    "code": ["py", "js", "ts", "java", "c", "cpp", "h", "hpp", "go", "rs",
             "rb", "php", "swift", "kt", "sh", "json", "yml", "yaml", "xml",
             "html", "css", "sql", "ipynb", "lua", "pl", "r"],
    "data": ["db", "sqlite", "sqlite3", "parquet", "feather", "pkl", "npy",
             "npz", "h5", "hdf5"],
}
CAT_COLOR = {
    "image": "#3b82f6",
    "video": "#8b5cf6",
    "audio": "#10b981",
    "archive": "#ef4444",
    "doc": "#f59e0b",
    "code": "#14b8a6",
    "data": "#ec4899",
    "other": "#64748b",
}


def categorize(path):
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    for cat, exts in CATEGORIES.items():
        if ext in exts:
            return cat
    return "other"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def fmt_bytes(n):
    if n is None:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    f = float(n)
    for u in units:
        if f < 1024 or u == "PB":
            return f"{f:.1f} {u}" if u != "B" else f"{int(f)} {u}"
        f /= 1024


def fmt_count(n):
    return f"{n:,}"


def hash_file(path, chunk=1 << 20):
    """Full-content BLAKE2b hash (byte-exact duplicate detection)."""
    h = hashlib.blake2b(digest_size=32)
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.digest()


def safe_walk(root):
    """Yield (dirpath, dirnames, filenames) skipping unreadable entries."""
    try:
        yield from os.walk(root, onerror=lambda e: None, followlinks=False)
    except Exception:
        return


# --------------------------------------------------------------------------
# Treemap math (squarified)
# --------------------------------------------------------------------------
def _worst(row, side):
    s = sum(row)
    mx, mn = max(row), min(row)
    if s == 0 or mn == 0:
        return float("inf")
    return max((side * side * mx) / (s * s), (s * s) / (side * side * mn))


def _squarify(values, nodes, rect, out):
    if not values:
        return
    x, y, w, h = rect
    if len(values) == 1:
        out.append((nodes[0], x, y, w, h))
        return
    side = min(w, h)
    prefix_v, prefix_n = [values[0]], [nodes[0]]
    i = 1
    while i < len(values):
        cand = prefix_v + [values[i]]
        if _worst(cand, side) <= _worst(prefix_v, side):
            prefix_v, prefix_n = cand, prefix_n + [nodes[i]]
            i += 1
        else:
            break
    if w >= h:
        col_w = sum(prefix_v) / h if h else 0
        cy = y
        for v, n in zip(prefix_v, prefix_n):
            rh = (v / col_w) if col_w else 0
            out.append((n, x, cy, col_w, rh))
            cy += rh
        rem = (x + col_w, y, w - col_w, h)
    else:
        row_h = sum(prefix_v) / w if w else 0
        cx = x
        for v, n in zip(prefix_v, prefix_n):
            rw = (v / row_h) if row_h else 0
            out.append((n, cx, y, rw, row_h))
            cx += rw
        rem = (x, y + row_h, w, h - row_h)
    _squarify(values[i:], nodes[i:], rem, out)


def squarify(items, rect):
    """items: list of (node, area). Returns list of (node, x, y, w, h)."""
    out = []
    if not items:
        return out
    total = sum(a for _, a in items)
    W, H = rect[2], rect[3]
    if total <= 0 or W <= 0 or H <= 0:
        return out
    scale = (W * H) / total
    vals = [a * scale for _, a in items]
    nds = [n for n, _ in items]
    _squarify(vals, nds, rect, out)
    return out


# --------------------------------------------------------------------------
# Tree building
# --------------------------------------------------------------------------
def build_tree(paths):
    """Return a virtual root node with nested children."""
    root = {"name": "<root>", "size": 0, "path": None, "cat": None,
            "children": {}, "is_file": False, "is_dup": False}

    def add_file(abspath, size):
        parts = abspath.split(os.sep)
        cur = root
        for p in parts[:-1]:
            if p not in cur["children"]:
                cur["children"][p] = {"name": p, "size": 0, "path": None,
                                      "cat": None, "children": {},
                                      "is_file": False, "is_dup": False}
            cur = cur["children"][p]
        name = parts[-1]
        cur["children"][name] = {
            "name": name, "size": size, "path": abspath,
            "cat": categorize(abspath), "children": {},
            "is_file": True, "is_dup": False,
        }

    for ap in paths:
        add_file(ap, 0)  # sizes filled after scan; placeholder not used
    return root


def scan(paths, min_size=0, max_depth=None, ignore=None, exclude=None, quiet=False):
    """Walk paths, collect file records. Returns (files, errors)."""
    files = []
    errors = []
    seen = set()
    for base in paths:
        base = os.path.abspath(base)
        if not os.path.exists(base):
            errors.append(f"not found: {base}")
            continue
        depth0 = base.rstrip(os.sep).count(os.sep)
        if os.path.isfile(base):
            _collect(base, files, min_size, errors, seen, exclude)
            continue
        for dirpath, dirnames, filenames in safe_walk(base):
            if ignore:
                dirnames[:] = [d for d in dirnames if d not in ignore]
            if max_depth is not None:
                d = dirpath.rstrip(os.sep).count(os.sep) - depth0
                if d >= max_depth:
                    dirnames[:] = []
            for fn in filenames:
                fp = os.path.join(dirpath, fn)
                if ignore and fn in ignore:
                    continue
                _collect(fp, files, min_size, errors, seen, exclude)
    return files, errors


def _collect(fp, files, min_size, errors, seen, exclude):
    fp = os.path.abspath(fp)
    if fp in seen:
        return
    if exclude and fp in exclude:
        return
    seen.add(fp)
    try:
        st = os.lstat(fp)
        if not os.path.isfile(fp) or os.path.islink(fp):
            return
        size = st.st_size
        if size < min_size:
            return
        files.append({
            "path": fp, "size": size, "mtime": st.st_mtime,
            "cat": categorize(fp),
        })
    except OSError as e:
        errors.append(f"{fp}: {e.strerror}")


def find_duplicates(files):
    # Group file *paths* by size. We map size -> [paths] (strings only) because
    # storing the mutable file dicts directly as dict values has been observed to
    # corrupt dict iteration on some CPython builds; paths are immutable.
    by_size = {}
    for f in files:
        by_size.setdefault(f["size"], []).append(f["path"])
    candidate_paths = [p for sz, lst in by_size.items()
                       if len(lst) > 1 and sz > 0 for p in lst]

    # Hash each candidate path (streaming BLAKE2b) and group by digest.
    by_hash = {}
    for p in candidate_paths:
        try:
            digest = hash_file(p)
        except OSError:
            continue
        by_hash.setdefault(digest, []).append(p)

    groups_paths = [lst for lst in by_hash.values() if len(lst) > 1]

    # Map paths back to the original file dicts for downstream rendering.
    fmap = {f["path"]: f for f in files}
    groups = [[fmap[p] for p in g] for g in groups_paths]

    dup_paths = set()
    for g in groups:
        for f in g:
            dup_paths.add(f["path"])
    return groups, dup_paths


def build_render_tree(files):
    root = {"name": "<root>", "size": 0, "path": None, "cat": "other",
            "children": {}, "is_file": False, "is_dup": False}
    for f in files:
        parts = f["path"].split(os.sep)
        cur = root
        for p in parts[:-1]:
            nxt = cur["children"].get(p)
            if nxt is None:
                nxt = {"name": p, "size": 0, "path": None, "cat": "other",
                       "children": {}, "is_file": False, "is_dup": False}
                cur["children"][p] = nxt
            cur = nxt
        name = parts[-1]
        cur["children"][name] = {
            "name": name, "size": f["size"], "path": f["path"],
            "cat": f["cat"], "children": {}, "is_file": True,
            "is_dup": f.get("is_dup", False),
        }
    # compute directory sizes bottom-up
    def size_up(node):
        if node["is_file"]:
            return node["size"]
        s = 0
        for c in node["children"].values():
            s += size_up(c)
        node["size"] = s
        return s
    size_up(root)
    return root


# --------------------------------------------------------------------------
# Render treemap with leaf budget
# --------------------------------------------------------------------------
def render_rects(root, width=1200, height=760, max_depth=7, min_area=600,
                 budget=600):
    rects = []
    budget_left = [budget]

    def recurse(node, rect, depth):
        if budget_left[0] <= 0:
            return
        if node["is_file"] or depth >= max_depth or not node["children"]:
            emit(node, rect)
            return
        area = rect[2] * rect[3]
        if area < min_area:
            emit(node, rect)
            return
        kids = sorted(node["children"].values(),
                      key=lambda c: c["size"], reverse=True)
        items = [(c, max(c["size"], 1)) for c in kids]
        sub = squarify(items, rect)
        for child, x, y, w, h in sub:
            if budget_left[0] <= 0:
                break
            recurse(child, (x, y, w, h), depth + 1)

    def emit(node, rect):
        if budget_left[0] <= 0:
            return
        budget_left[0] -= 1
        x, y, w, h = rect
        if w < 0.5 or h < 0.5:
            return
        rects.append({
            "name": node["name"], "path": node["path"],
            "cat": node["cat"], "size": node["size"],
            "is_file": node["is_file"], "is_dup": node.get("is_dup", False),
            "x": x, "y": y, "w": w, "h": h,
        })

    recurse(root, (0, 0, width, height), 0)
    return rects


# --------------------------------------------------------------------------
# HTML report
# --------------------------------------------------------------------------
def build_report(meta, rects, groups, top_files, cat_breakdown):
    css = """
    :root{--bg:#0f172a;--panel:#1e293b;--text:#e2e8f0;--muted:#94a3b8;
    --accent:#38bdf8;--dup:#f43f5e;}
    *{box-sizing:border-box;}
    body{margin:0;background:var(--bg);color:var(--text);
    font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;}
    header{padding:28px 32px 12px;}
    h1{margin:0;font-size:26px;letter-spacing:.5px;}
    .sub{color:var(--muted);margin-top:6px;font-size:14px;}
    .cards{display:flex;flex-wrap:wrap;gap:14px;padding:18px 32px;}
    .card{background:var(--panel);border:1px solid #334155;border-radius:14px;
    padding:16px 20px;min-width:160px;flex:1;}
    .card .k{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:1px;}
    .card .v{font-size:24px;font-weight:700;margin-top:6px;}
    .card.dup .v{color:var(--dup);}
    .layout{display:flex;gap:18px;padding:0 32px 24px;flex-wrap:wrap;}
    .tm-wrap{background:var(--panel);border:1px solid #334155;border-radius:14px;
    padding:12px;flex:1 1 640px;}
    .side{flex:1 1 300px;display:flex;flex-direction:column;gap:18px;}
    .box{background:var(--panel);border:1px solid #334155;border-radius:14px;padding:16px;}
    .box h2{margin:0 0 12px;font-size:16px;}
    svg{width:100%;height:auto;display:block;border-radius:8px;}
    rect{cursor:pointer;transition:opacity .12s;}
    rect:hover{opacity:.82;}
    .legend{display:flex;flex-wrap:wrap;gap:10px;margin-top:10px;font-size:12px;}
    .legend span{display:inline-flex;align-items:center;gap:6px;color:var(--muted);}
    .dot{width:12px;height:12px;border-radius:3px;display:inline-block;}
    .bar{display:flex;align-items:center;gap:8px;margin:8px 0;font-size:13px;}
    .bar .lbl{width:84px;color:var(--muted);}
    .bar .track{flex:1;background:#0f172a;border-radius:6px;height:14px;overflow:hidden;}
    .bar .fill{height:100%;}
    .bar .val{width:90px;text-align:right;color:var(--text);}
    table{width:100%;border-collapse:collapse;font-size:13px;}
    th,td{text-align:left;padding:7px 8px;border-bottom:1px solid #334155;}
    th{color:var(--muted);font-weight:600;}
    td.size{text-align:right;color:var(--accent);}
    .dup-row td:first-child{color:var(--dup);}
    #info{position:fixed;right:24px;bottom:24px;background:#020617ee;border:1px solid #334155;
    border-radius:12px;padding:14px 16px;max-width:340px;font-size:13px;display:none;
    box-shadow:0 10px 30px #0008;z-index:9;}
    #info .p{color:var(--muted);word-break:break-all;margin-top:6px;font-size:11px;}
    footer{padding:14px 32px 40px;color:var(--muted);font-size:12px;}
    code{background:#0f172a;padding:2px 6px;border-radius:5px;color:var(--accent);}
    """
    svg_w, svg_h = 1200, 760
    rect_svg = []
    for r in rects:
        color = CAT_COLOR.get(r["cat"], "#64748b")
        stroke = "#f43f5e" if r["is_dup"] else "#0f172a"
        sw = 2 if r["is_dup"] else 0.5
        x, y, w, h = r["x"], r["y"], r["w"], r["h"]
        name = html.escape(r["name"] or "")
        path = html.escape(r["path"] or "")
        size = fmt_bytes(r["size"])
        label = ""
        if w > 54 and h > 18:
            fs = max(9, min(13, int(w / max(len(name), 1) * 1.6)))
            label = (f'<text x="{x+6:.1f}" y="{y+16:.1f}" fill="#0b1220" '
                     f'font-size="{fs}" font-weight="600" '
                     f'style="pointer-events:none">{name[:int(w/fs*1.4)]}</text>')
        rect_svg.append(
            f'<rect data-name="{name}" data-path="{path}" data-size="{size}" '
            f'data-cat="{r["cat"]}" x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" '
            f'height="{h:.2f}" fill="{color}" stroke="{stroke}" '
            f'stroke-width="{sw}">{label}</rect>')
    svg_inner = "".join(rect_svg)
    legend = "".join(
        f'<span><i class="dot" style="background:{c}"></i>{k}</span>'
        for k, c in CAT_COLOR.items())

    max_cat = max((v for v, _ in cat_breakdown.values()), default=1) or 1
    bars = []
    for cat, (size, count) in sorted(cat_breakdown.items(),
                                     key=lambda kv: kv[1][0], reverse=True):
        pct = (size / max_cat) * 100
        bars.append(
            f'<div class="bar"><div class="lbl">{cat}</div>'
            f'<div class="track"><div class="fill" style="width:{pct:.1f}%;'
            f'background:{CAT_COLOR.get(cat,"#64748b")}"></div></div>'
            f'<div class="val">{fmt_bytes(size)} · {count}</div></div>')

    dup_rows = []
    for i, g in enumerate(groups[:40]):
        sz = g[0]["size"]
        wasted = sz * (len(g) - 1)
        paths = "<br>".join(html.escape(f["path"]) for f in g[:6])
        more = f"<br>… +{len(g)-6} more" if len(g) > 6 else ""
        dup_rows.append(
            f'<tr class="dup-row"><td>#{i+1}</td><td class="size">'
            f'{fmt_bytes(sz)}</td><td>×{len(g)}</td>'
            f'<td class="size">{fmt_bytes(wasted)}</td></tr>'
            f'<tr><td colspan="4" style="border:0;color:var(--muted);'
            f'font-size:11px">{paths}{more}</td></tr>')

    file_rows = []
    for f in top_files[:25]:
        file_rows.append(
            f'<tr><td>{html.escape(os.path.basename(f["path"]))}</td>'
            f'<td style="color:var(--muted)">{f["cat"]}</td>'
            f'<td class="size">{fmt_bytes(f["size"])}</td></tr>')

    js = """
    const info=document.getElementById('info');
    document.querySelectorAll('rect').forEach(r=>{
      r.addEventListener('mousemove',e=>{
        info.style.display='block';
        info.style.left=(e.clientX+14)+'px';
        info.style.top=(e.clientY+14)+'px';
        info.innerHTML='<b>'+(r.dataset.name||'')+'</b><br>'+
          (r.dataset.cat||'')+' · '+r.dataset.size+
          '<div class="p">'+(r.dataset.path||'')+'</div>';
      });
      r.addEventListener('mouseleave',()=>info.style.display='none');
    });
    """
    html_doc = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SnapDup Report</title><style>{css}</style></head>
<body>
<header><h1>🗂️ SnapDup — Disk Report</h1>
<div class="sub">Generated {meta['date']} · scanned {fmt_count(meta['files'])} files · {meta['roots']}</div></header>
<div class="cards">
  <div class="card"><div class="k">Total size</div><div class="v">{fmt_bytes(meta['total'])}</div></div>
  <div class="card"><div class="k">Files</div><div class="v">{fmt_count(meta['files'])}</div></div>
  <div class="card dup"><div class="k">Duplicate files</div><div class="v">{fmt_count(meta['dup_files'])}</div></div>
  <div class="card dup"><div class="k">Reclaimable</div><div class="v">{fmt_bytes(meta['wasted'])}</div></div>
  <div class="card"><div class="k">Duplicate groups</div><div class="v">{fmt_count(meta['groups'])}</div></div>
</div>
<div class="layout">
  <div class="tm-wrap">
    <svg viewBox="0 0 {svg_w} {svg_h}" preserveAspectRatio="xMidYMid meet">{svg_inner}</svg>
    <div class="legend">{legend}
      <span><i class="dot" style="background:#f43f5e"></i>duplicate</span></div>
  </div>
  <div class="side">
    <div class="box"><h2>Storage by type</h2>{''.join(bars)}</div>
    <div class="box"><h2>Top duplicate groups</h2>
      <table><tr><th>#</th><th>Each</th><th>Copies</th><th>Wasted</th></tr>
      {''.join(dup_rows) or '<tr><td colspan=4>No duplicates found 🎉</td></tr>'}</table></div>
    <div class="box"><h2>Largest files</h2>
      <table><tr><th>Name</th><th>Type</th><th>Size</th></tr>
      {''.join(file_rows)}</table></div>
  </div>
</div>
<div id="info"></div>
<footer>SnapDup {__version__} · 100% offline · zero dependencies · hover tiles for details ·
run <code>python3 snapdup.py PATH</code> to make your own</footer>
<script>{js}</script>
</body></html>"""
    return html_doc


# --------------------------------------------------------------------------
# Terminal UI (ANSI, no deps)
# --------------------------------------------------------------------------
C = {
    "reset": "\033[0m", "bold": "\033[1m", "dim": "\033[2m",
    "red": "\033[31m", "green": "\033[32m", "yellow": "\033[33m",
    "blue": "\033[34m", "magenta": "\033[35m", "cyan": "\033[36m",
}


def t(code, s):
    return f"{C[code]}{s}{C['reset']}"


def term_summary(meta, errors, top_files, groups):
    line = "=" * 60
    print()
    print(t("bold", line))
    print(t("bold", "  🗂️  SnapDup — scan complete"))
    print(t("dim", line))
    print(f"  Scanned        : {t('cyan', fmt_count(meta['files']))} files")
    print(f"  Total size     : {t('bold', fmt_bytes(meta['total']))}")
    print(f"  Duplicate files: {t('red', fmt_count(meta['dup_files']))}")
    print(f"  Duplicate groups: {fmt_count(meta['groups'])}")
    print(f"  Reclaimable    : {t('green', fmt_bytes(meta['wasted']))}")
    if errors:
        print(t("yellow", f"  Warnings       : {len(errors)} (skipped)"))
    print(t("dim", line))
    if top_files:
        print(t("bold", "  Largest files:"))
        for f in top_files[:5]:
            print(f"   {t('cyan', fmt_bytes(f['size'])):>10}  {os.path.basename(f['path'])}")
    if groups:
        print(t("bold", "  Top duplicate groups (wasted space):"))
        for i, g in enumerate(groups[:5]):
            wasted = g[0]["size"] * (len(g) - 1)
            print(f"   #{i+1} {t('red', fmt_bytes(wasted)):>10}  "
                  f"{os.path.basename(g[0]['path'])} ×{len(g)}")
    print(t("dim", line))
    print(f"  Report: {t('bold', meta['report'])}")
    print(t("dim", "  Open it in any browser — fully offline, no data leaves your machine."))
    print(t("bold", line))
    print()


# --------------------------------------------------------------------------
# Trash / cleanup (safe)
# --------------------------------------------------------------------------
def move_to_trash(paths, trash_dir):
    os.makedirs(trash_dir, exist_ok=True)
    moved = 0
    for p in paths:
        try:
            dest = os.path.join(trash_dir, os.path.basename(p))
            if os.path.exists(dest):
                dest = dest + f".{os.getpid()}.{moved}"
            os.rename(p, dest)
            moved += 1
        except OSError as e:
            print(t("red", f"  ! could not move {p}: {e}"))
    return moved


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="snapdup",
        description="Find duplicate files and visualize disk usage — 100% offline.",
    )
    ap.add_argument("paths", nargs="+", help="directories or files to scan")
    ap.add_argument("-o", "--output", default="snapdup_report.html",
                    help="HTML report path (default: snapdup_report.html)")
    ap.add_argument("--min-size", type=int, default=0,
                    help="ignore files smaller than N bytes")
    ap.add_argument("--max-depth", type=int, default=None,
                    help="limit directory recursion depth")
    ap.add_argument("--no-report", action="store_true",
                    help="skip HTML report")
    ap.add_argument("--json", default=None,
                    help="also write a JSON summary to this path")
    ap.add_argument("--trash", default=None,
                    help="move duplicates here instead of leaving them")
    ap.add_argument("--top", type=int, default=25,
                    help="number of top items to show")
    ap.add_argument("--quiet", action="store_true", help="less output")
    ap.add_argument("--version", action="version",
                    version=f"snapdup {__version__}")
    args = ap.parse_args(argv)

    if not args.quiet:
        print(t("bold", "🗂️  SnapDup") + t("dim", f" v{__version__} — scanning…"))

    t0 = time.time()
    exclude = {os.path.abspath(args.output)}
    if args.json:
        exclude.add(os.path.abspath(args.json))
    files, errors = scan(args.paths, args.min_size, args.max_depth,
                         ignore={".git", "__pycache__", "node_modules"},
                         exclude=exclude, quiet=args.quiet)

    if not files:
        print(t("yellow", "No scannable files found."))
        return 1

    groups, dup_paths = find_duplicates(files)
    for f in files:
        f["is_dup"] = f["path"] in dup_paths

    total = sum(f["size"] for f in files)
    dup_files_count = len(dup_paths)
    wasted = sum(g[0]["size"] * (len(g) - 1) for g in groups)

    cat_breakdown = defaultdict(lambda: [0, 0])
    for f in files:
        cat_breakdown[f["cat"]][0] += f["size"]
        cat_breakdown[f["cat"]][1] += 1

    top_files = sorted(files, key=lambda f: f["size"], reverse=True)[:args.top]
    groups_sorted = sorted(groups, key=lambda g: g[0]["size"] * (len(g) - 1),
                           reverse=True)

    meta = {
        "date": _dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "roots": ", ".join(os.path.abspath(p) for p in args.paths),
        "files": len(files), "total": total,
        "dup_files": dup_files_count, "groups": len(groups),
        "wasted": wasted, "report": os.path.abspath(args.output),
        "seconds": round(time.time() - t0, 1),
    }

    if not args.no_report:
        render_root = build_render_tree(files)
        rects = render_rects(render_root)
        report = build_report(meta, rects, groups_sorted, top_files,
                              dict(cat_breakdown))
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(report)
        if not args.quiet:
            print(t("green", f"  ✓ report written ({fmt_bytes(len(report))})"))

    if args.trash and dup_paths:
        moved = move_to_trash(sorted(dup_paths), args.trash)
        if not args.quiet:
            print(t("green", f"  ✓ moved {moved} duplicates to {args.trash}"))

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({
                "meta": {k: v for k, v in meta.items()},
                "errors": errors[:50],
                "top_files": [{"path": f["path"], "size": f["size"],
                              "cat": f["cat"]} for f in top_files],
                "groups": [{"size": g[0]["size"], "copies": len(g),
                            "paths": [f["path"] for f in g]}
                           for g in groups_sorted[:200]],
            }, fh, indent=2)

    if not args.quiet:
        term_summary(meta, errors, top_files, groups_sorted)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print(t("yellow", "\nInterrupted."))
        sys.exit(130)
