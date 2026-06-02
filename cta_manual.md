# cta — Create Tar Archive
### User Manual & Reference
**Stanford Media Preservation Lab** · v0.1 · February 2026

---

## Contents

1. [Overview](#1-overview)
2. [Requirements](#2-requirements)
3. [Installation](#3-installation)
4. [Usage](#4-usage)
5. [Options](#5-options)
6. [Source Directory Structure](#6-source-directory-structure)
7. [Output Structure](#7-output-structure)
8. [Pipeline — What Happens Step by Step](#8-pipeline--what-happens-step-by-step)
9. [Verification in Depth](#9-verification-in-depth)
10. [Progress Display](#10-progress-display)
11. [Warnings vs. Failures](#11-warnings-vs-failures)
12. [Final Session Summary](#12-final-session-summary)
13. [Log Files](#13-log-files)
14. [Exit Codes](#14-exit-codes)
15. [Notes](#15-notes)

---

## 1. Overview

`cta` is a command-line tool for batch-archiving digital content directories into verified, auditable tar archives. It is designed for preservation and transfer workflows where provenance, file integrity, and chain-of-custody documentation are essential.

Given a parent directory containing one or more content folders identified by unique 11-character alphanumeric identifiers, the tool produces a verified `.tar` archive for each one, alongside a CSV file manifest, MD5 sidecar files, and a complete audit log. Multi-part items — where a single identifier is split across several directories suffixed `_1`, `_2`, `_3`, etc. — are handled identically to single-part items.

---

## 2. Requirements

- **macOS or Linux** (tested on macOS)
- **Python 3.10 or later**
- **tqdm** — the only third-party dependency

```bash
pip install tqdm
```

All other dependencies (`hashlib`, `tarfile`, `csv`, `logging`, `shutil`, `tempfile`) are part of the Python standard library.

---

## 3. Installation

Copy the script to a location on your `PATH` and make it executable:

```bash
cp cta.py /usr/local/bin/cta
chmod +x /usr/local/bin/cta
```

---

## 4. Usage

```
cta <SOURCE_BATCH_DIR>  <OUTPUT_DIR>  [OPTIONS]
```

**Standard run:**
```bash
cta /Volumes/camera_read/MOL_2026/Batch_1/260218  /Volumes/archive_write/260218_pm-output
```

**Dry run — simulate without writing any archive files:**
```bash
cta /Volumes/camera_read/MOL_2026/Batch_1/260218  /Volumes/archive_write/260218_pm-output  --dry-run
```

**Verbose output:**
```bash
cta /Volumes/camera_read/MOL_2026/Batch_1/260218  /Volumes/archive_write/260218_pm-output  --verbose
```

---

## 5. Options

| Option | Description |
|--------|-------------|
| `--dry-run`, `-d` | Simulate the full pipeline without writing any archive, CSV, or MD5 files. All steps are logged as normal. Useful for verifying source structure and expected output before committing. |
| `--verbose`, `-v` | Print DEBUG-level messages to the terminal. Full debug detail is always written to log files regardless of this flag. |
| `--help`, `-h` | Print usage information and exit. |

---

## 6. Source Directory Structure

`cta` recognises two layout styles.

**Date-coded layout (most common):**
```
260218/                         ← 6-digit date code
├── ff989gt5757/                ← 11-character alphanumeric content directory
├── fm724wx5272/
├── fn695bb2830_1/              ← multi-part item, part 1
├── fn695bb2830_2/              ← multi-part item, part 2
├── fn695bb2830_3/              ← multi-part item, part 3
├── fr844bj6956/
└── ft112tn4957/
```

**Flat layout:**
```
parent_directory/
├── ff989gt5757/
├── fn695bb2830_1/
└── fn695bb2830_2/
```

Content directory names must be exactly **11 alphanumeric characters**, optionally followed by an underscore and one or more digits (e.g. `_1`, `_2`, `_12`). Any directory that does not match this pattern is silently ignored. Date-code folders must be exactly 6 digits.

Each part of a multi-part item is archived, verified, and documented completely independently, as if it were its own item.

---

## 7. Output Structure

```
output_directory/
├── tarball_archives/
│   ├── ff989gt5757_pm.tar          ← uncompressed archive
│   ├── ff989gt5757_pm.tar.md5      ← MD5 checksum of the archive
│   ├── fn695bb2830_1_pm.tar
│   ├── fn695bb2830_1_pm.tar.md5
│   ├── fn695bb2830_2_pm.tar
│   ├── fn695bb2830_2_pm.tar.md5
│   ├── fn695bb2830_3_pm.tar
│   └── fn695bb2830_3_pm.tar.md5
├── csv/
│   ├── ff989gt5757_md.csv          ← file manifest (path, name, size, date)
│   ├── ff989gt5757_md.csv.md5      ← MD5 checksum of the manifest
│   ├── fn695bb2830_1_md.csv
│   ├── fn695bb2830_1_md.csv.md5
│   └── ...
└── logs/
    ├── cta_session_<timestamp>.log  ← session-level log
    ├── ff989gt5757.log              ← full per-directory log
    ├── fn695bb2830_1.log
    ├── fn695bb2830_2.log
    ├── fn695bb2830_3.log
    └── cta_warnings_<timestamp>.log ← warnings summary (if applicable)
```

### Archive structure on extraction

Each `.tar` file extracts to a top-level folder named with the full directory name (including any part suffix), so the identifier is always visible without referencing the filename:

```
fn695bb2830_2/
└── OMF2300-3615e-9/
    ├── AVCHD/
    │   └── BDMV/
    │       ├── STREAM/
    │       ├── CLIPINF/
    │       └── PLAYLIST/
    └── M4ROOT/
```

---

## 8. Pipeline — What Happens Step by Step

For each content directory, `cta` runs a seven-step pipeline. A tqdm progress bar tracks each step in the terminal, with byte-level throughput bars for all I/O-intensive operations.

---

### Step 1 — Verify source MD5 checksums

Reads all `.md5` sidecar files in the source content directory and verifies that each referenced file matches its recorded checksum.

- **macOS AppleDouble stubs** (`._filename`) are automatically detected and skipped. These are metadata artefacts written by macOS to non-HFS+ volumes (such as camera cards formatted as exFAT) and contain no content.
- **Files referenced in a sidecar but not found on disk** are recorded as warnings. Processing continues — see [Warnings vs. Failures](#11-warnings-vs-failures).
- **Checksum mismatches** are hard failures and halt processing of that directory immediately.

---

### Step 2 — Write CSV file manifest

Produces a `<uid>_md.csv` file listing every content file with four columns:

| Column | Description |
|--------|-------------|
| File Path | Path relative to the content directory's parent |
| File Name | Filename only |
| File Size | Human-readable size (e.g. 2.34 GB) |
| Date Created | Best available timestamp (birth time on macOS, modification time on Linux) |

`.md5` sidecar files and `.DS_Store` files are excluded from the manifest.

---

### Step 3 — Remove .DS_Store files

Finds and deletes any `.DS_Store` files recursively within the source content directory before archiving. These are macOS-generated system artefacts with no archival value.

---

### Step 4 — Create tar archive

Packages the entire content directory into an uncompressed `.tar` file at `tarball_archives/<uid>_pm.tar`. The archive is built with a tqdm byte-progress bar showing throughput and estimated time.

Each file is stored under the directory name as a prefix, so extracting the archive always produces a named top-level folder. The `_md.csv` manifest is stored separately in `csv/` and is not included in the archive.

---

### Step 5 — Verify tarball

The archive is unpacked into a temporary directory and subjected to three independent checks. See [Verification in Depth](#9-verification-in-depth) for full detail.

After verification, the temporary directory is deleted immediately.

---

### Step 6 — MD5 sidecar for the archive

Computes an MD5 digest of the final `.tar` file and writes it as `<uid>_pm.tar.md5` alongside the archive. This allows downstream users to verify the integrity of the archive file itself before opening it.

---

### Step 7 — MD5 sidecar for the CSV manifest

Computes an MD5 digest of the `_md.csv` manifest and writes it as `<uid>_md.csv.md5` alongside the manifest.

---

At the end of all seven steps, the per-directory log records a completion banner:

```
┌─────────────────────────────────────────────────────────────┐
│  ✓  ARCHIVE COMPLETE : fn695bb2830_2                        │
│     tar archive created, unpacked, verified, and checksummed │
└─────────────────────────────────────────────────────────────┘
```

---

## 9. Verification in Depth

Step 5 unpacks the archive into a temporary directory and runs three independent checks before deleting it.

---

### A — File-list comparison

The set of files extracted from the archive is compared against the source content directory. `.DS_Store` files are excluded from both sides. Any discrepancy is explicitly reported:

- Files present in the source but absent from the archive are listed as `MISSING FROM TAR`
- Files present in the archive but absent from the source are listed as `EXTRA IN TAR`

If the lists match exactly, the log records the total file count and an `[OK]` verdict.

---

### B — Size comparison

The total byte size of the source content is compared against the size of the `.tar` file on disk. Because tar archives are uncompressed, these figures should be very close — the difference accounts only for per-entry tar header overhead (~512 bytes per file).

A discrepancy beyond **0.5% of the source size or 1 MB** (whichever is larger) is flagged as a warning for manual review. Size mismatch is a warning rather than a hard failure — the file-list and MD5 checks are the authoritative integrity tests.

The log records both sizes and the absolute and percentage difference:

```
[B] Size comparison
    Source content : 14.78 GB  (15872041832 bytes)
    Tarball size   : 14.78 GB  (15872094720 bytes)
    Difference     : 51.84 KB  (0.000 %)
    [OK] Size difference is within tolerance (≤ 0.5% / 1 MB).
```

---

### C — MD5 checksum verification

All `.md5` sidecar files inside the unpacked archive are re-verified against their companion files. Every result is written to the log. A clear `[PASS]` or `[FAIL]` verdict is issued for each sidecar and for the archive as a whole.

On a clean pass, the log records:

```
╔══════════════════════════════════════════════════════════╗
║  ✓  ARCHIVE VERIFIED                                     ║
╠══════════════════════════════════════════════════════════╣
║  Archive  : fn695bb2830_2_pm.tar                        ║
║  Files    : 72  match between source and archive        ║
║  Size     : 14.78 GB  (source)  →  14.78 GB  (tar)     ║
║  MD5s     : All checksums verified against source       ║
║  Result   : PASS — archive is a complete, faithful copy ║
╚══════════════════════════════════════════════════════════╝
```

---

## 10. Progress Display

`cta` uses tqdm to display progress at multiple levels simultaneously.

| Bar | Scope | Unit |
|-----|-------|------|
| Overall progress | Whole session | Content directories processed |
| Per-step bar | Each content directory | Pipeline steps (1–7) |
| MD5 verify | Each `.md5` sidecar | Bytes + transfer rate |
| Building CSV | Manifest generation | Files |
| Archiving | Tarball creation | Bytes + transfer rate + ETA |
| Unpacking | Tarball extraction | Files |
| Hashing sidecar | Final MD5 sidecars | Bytes + transfer rate |

---

## 11. Warnings vs. Failures

`cta` distinguishes between anomalies that should be noted but do not indicate a data integrity problem, and conditions that do.

| Condition | Level | Behaviour |
|-----------|-------|-----------|
| macOS AppleDouble stub (`._filename`) referenced in a `.md5` sidecar | Skipped (DEBUG) | Silently ignored. These are macOS metadata artefacts, not content files. |
| File referenced in a `.md5` sidecar not found on disk | WARNING | Logged and collected in the warnings summary. Processing continues. |
| Size difference between source and archive exceeds tolerance | WARNING | Logged. The file-list and MD5 checks are the authoritative tests. |
| File present in source but absent from the extracted archive | FAILURE | Hard failure. Processing of this directory is halted. |
| MD5 checksum mismatch | FAILURE | Hard failure. Processing of this directory is halted. |
| Tarball creation error | FAILURE | Hard failure. Processing of this directory is halted. |

At the end of a session containing warnings, a separate `cta_warnings_<timestamp>.log` file is written listing every warning grouped by content directory. The session summary always shows a total warnings count.

---

## 12. Final Session Summary

At the end of every run, the session log and terminal display a structured summary. On a clean pass:

```
╔══════════════════════════════════════════════════════════════════╗
║  ✓  ALL ARCHIVES VERIFIED AND COMPLETE                           ║
╠══════════════════════════════════════════════════════════════════╣
║  Every content directory in this batch has been:                 ║
║    • Checksummed against its source .md5 sidecars                ║
║    • Packaged into a named, uncompressed tar archive             ║
║    • Unpacked and re-verified (file list + size + MD5s)          ║
║    • Signed with a final .md5 checksum for the archive itself    ║
╠══════════════════════════════════════════════════════════════════╣
║  Batch     :  260218                                             ║
║  Archives  :  7   verified                                       ║
║  Failures  :  0                                                  ║
║  Warnings  :  71  (see cta_warnings_20260218_083154.log)         ║
║  Completed :  2026-02-18 08:38:24                                ║
╚══════════════════════════════════════════════════════════════════╝
```

---

## 13. Log Files

Every run produces the following log files in the `logs/` subdirectory of the output directory.

| File | Contents |
|------|----------|
| `cta_session_<timestamp>.log` | Session-level log covering startup, pre-flight checks, per-directory completion banners, warnings count, and the final session summary. |
| `<uid>.log` | Per-directory log containing the full step-by-step detail for one content directory: all checksum results, file sizes, timing, and the archive verification verdict. |
| `cta_warnings_<timestamp>.log` | Written only when warnings were raised. Lists every missing-file warning grouped by content directory. Absent if no warnings occurred. |

Per-directory log files are explicitly flushed and closed after each directory completes, ensuring they are fully written to disk on external or network-attached volumes before the next directory begins processing.

All log entries follow the format:
```
YYYY-MM-DD HH:MM:SS  LEVEL     Message
```

---

## 14. Exit Codes

| Code | Meaning |
|------|---------|
| `0` | Success — all content directories processed, archived, and verified. |
| `1` | Failure — one or more directories failed (checksum mismatch, missing files, tarball error, or insufficient disk space). |

---

## 15. Notes

### AppleDouble resource-fork stubs

When macOS copies files to a non-HFS+ volume such as a Sony camera card formatted as exFAT, it writes a companion `._filename` file alongside each real file to store extended attributes. The `.md5` sidecar files generated on camera cards include entries for these stubs. `cta` automatically skips any sidecar entry whose filename begins with `._` — they produce no warnings and do not affect the pass/fail outcome.

### Multi-part items

Where a single catalogued item is spread across multiple directories (e.g. `fn695bb2830_1`, `fn695bb2830_2`, `fn695bb2830_3`), each part is treated as an independent unit throughout the pipeline — its own archive, its own manifest, its own log, and its own verification result. There is no dependency between parts; a failure in part 2 does not affect the archiving of part 3.

### Nested camera card package structure

Sony camera cards use a nested directory structure (AVCHD, M4ROOT, SONY) that may present as a package on macOS. `cta` treats all directories as ordinary folders and archives them recursively without modification.

### .DS_Store files

`.DS_Store` files are deleted from the source content directory before archiving (Step 3) and are excluded from the CSV manifest. They are not present in the tar archive.

### Disk space pre-flight check

Before any work begins, `cta` verifies that the output volume has at least **twice the total source content size** available — sufficient for the tar archive plus the temporary extraction directory used during verification. If space is insufficient, the session aborts before any files are written.

### Dry-run mode

When processing a new camera model or card format for the first time, run `cta` with the `-d` flag. This validates the source structure, runs the pre-flight space check, and logs the complete pipeline without writing any output files.

### External and network volumes

Log files are explicitly flushed and closed to disk after each directory completes. This is necessary because Python's logging module does not guarantee that file handles are flushed until process exit — on external or network-attached volumes, this can result in log files appearing empty or absent. The explicit lifecycle management in `cta` prevents this.
