<p align="center">
  <img src="https://img.shields.io/badge/SnapDup-1.0.0-blue?style=for-the-badge" alt="version">
  <img src="https://img.shields.io/badge/dependencies-0-brightgreen?style=for-the-badge" alt="zero deps">
  <img src="https://img.shields.io/badge/offline-100%25-orange?style=for-the-badge" alt="offline">
  <img src="https://img.shields.io/badge/license-MIT-green?style=for-the-badge" alt="license">
  <img src="https://img.shields.io/badge/python-%3E%3D3.8-3776ab?style=for-the-badge" alt="python">
</p>

<h1 align="center">🗂️ SnapDup</h1>
<p align="center"><b>Reclaim your disk.</b> Find duplicate files and see exactly what's eating your space — with a beautiful, interactive treemap you can open in any browser.</p>

<p align="center">
  <b>100% offline · Zero dependencies · Cross-platform · Privacy-first</b>
</p>

---

### Why SnapDup?

Every computer slowly fills up with duplicate photos, downloaded installers, copied project folders, and forgotten backups. SnapDup finds the waste and shows it to you — **without uploading a single byte anywhere.**

- 🔍 **Byte-exact duplicate detection** (BLAKE2b full-content hashing)
- 🗺️ **Interactive treemap report** — hover any tile to see the file, size, and path
- 📊 **Storage breakdown by type** (images, video, code, archives…)
- ♻️ **Safe cleanup** — move duplicates to a trash folder, never delete blindly
- 🛡️ **Private by design** — runs entirely on your machine, no network, no accounts
- 🪶 **Zero dependencies** — pure Python standard library; one file, runs anywhere
- 🖥️ **Any OS** — macOS, Linux, Windows. Python 3.8+

> One file. No `pip install`. No internet. No telemetry. Just run it.

---

### Install

**That's it — there is no install.** SnapDup is a single Python file with no dependencies.

```bash
# download and run
python3 snapdup.py ~/Pictures

# or, as a proper package:
pip install snapdup
snapdup ~/Pictures
```

Requirements: Python ≥ 3.8 (already on basically every machine).

---

### Usage

```bash
# Scan a folder and open snapdup_report.html in your browser
python3 snapdup.py ~/Pictures ~/Downloads

# Write the report somewhere specific
python3 snapdup.py /Volumes/Photos -o report.html

# Only consider files >= 1 MB (faster on huge drives)
python3 snapdup.py ~/Movies --min-size 1048576

# Move duplicates to a trash folder instead of leaving them
python3 snapdup.py ~/Downloads --trash ~/.Trash/snapdup_dupes

# Also export a machine-readable JSON summary
python3 snapdup.py . --json summary.json
```

| Option | Description |
|--------|-------------|
| `paths` | One or more files/directories to scan |
| `-o, --output` | HTML report path (default `snapdup_report.html`) |
| `--min-size N` | Ignore files smaller than N bytes |
| `--max-depth N` | Limit directory recursion depth |
| `--trash DIR` | Move duplicates here (safe cleanup) |
| `--json PATH` | Also write a JSON summary |
| `--no-report` | Skip the HTML report |
| `--top N` | Number of top items shown (default 25) |
| `--quiet` | Less output |

---

### What the report looks like

The generated `snapdup_report.html` is **fully self-contained** — no CDN, no font
downloads, no network calls. Open it offline, send it to a friend, archive it.

- **Treemap**: every file/folder is a rectangle sized by its bytes. Color = file type. Red outline = duplicate.
- **Summary cards**: total size, file count, duplicate files, reclaimable space.
- **Storage by type**: where your space actually went.
- **Top duplicate groups**: the biggest wins for cleanup, with full paths.
- **Largest files**: the usual suspects.

Hover any tile to inspect it.

---

### How it works

1. **Walk** the target paths (skips `.git`, `node_modules`, `__pycache__` by default).
2. **Pre-filter** by file size — only files sharing a size can be duplicates.
3. **Hash** candidate contents with BLAKE2b (chunked, constant memory).
4. **Group** identical hashes into duplicate sets; compute reclaimable space.
5. **Render** a squarified treemap (Bruls, Huizing & van Wijk) into self-contained SVG/HTML.

The treemap uses the classic *squarified* algorithm so rectangles stay as
square as possible — easy to read at a glance.

---

### Resource safety

SnapDup is built to be gentle:

- Constant-memory streaming hashing (never loads a file into RAM whole).
- Single-threaded by default — no CPU storms.
- Treemap rendering is capped (leaf budget + min-area) so even a 2 TB drive
  produces a small, fast report.
- Scoped by you: point it at a folder, not your whole disk, unless you want to.

---

### Roadmap

- [ ] Similar-image detection (perceptual hashing, optional `Pillow`)
- [ ] Side-by-side duplicate preview in the report
- [ ] `--schedule` for periodic automatic reports
- [ ] Export to CSV / interactive zoomable treemap (click to drill down)
- [ ] Optional parallel hashing for very large collections

Contributions welcome! See below.

---

### Contributing

1. Fork & clone.
2. `python3 snapdup.py <some-folder>` to test.
3. Open a PR. Keep it dependency-free unless the feature truly needs one.

---

### License

[MIT](LICENSE) — do whatever you want, just keep the notice.
