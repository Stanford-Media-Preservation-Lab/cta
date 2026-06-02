# cta — Create Tar Archive

A command-line tool for batch-archiving digital content directories into verified, auditable tar archives. Built for the Stanford Media Preservation Lab.

## What it does

Given a parent directory containing one or more content folders — identified by unique 11-character alphanumeric identifiers, optionally with part suffixes (`_1`, `_2`, etc.) — `cta` runs each through a seven-step pipeline:

1. Verifies source MD5 sidecar checksums
2. Writes a CSV file manifest
3. Removes `.DS_Store` files
4. Creates an uncompressed tar archive (extracts to a named top-level folder)
5. Unpacks the archive and runs three verification checks: file-list comparison, size comparison, and MD5 re-verification
6. Writes an MD5 sidecar for the archive
7. Writes an MD5 sidecar for the manifest

Every step is logged. Every check has an explicit pass/fail verdict.

## Installation

**Recommended — via pipx (isolated environment, globally available command):**

```bash
pipx install git+https://github.com/your-org/cta.git
```

**Alternative — via pip:**

```bash
pip install git+https://github.com/your-org/cta.git
```

Once installed, `cta` is available as a command on your `PATH`.

## Requirements

- Python 3.10 or later
- [tqdm](https://github.com/tqdm/tqdm) (installed automatically)

## Usage

```bash
cta <SOURCE_BATCH_DIR>  <OUTPUT_DIR>  [OPTIONS]
```

**Standard run:**
```bash
cta /Volumes/camera_read/MOL_2026/Batch_1/260218  /Volumes/archive_write/260218_pm-output
```

**Dry run — simulate without writing any files:**
```bash
cta /Volumes/camera_read/MOL_2026/Batch_1/260218  /Volumes/archive_write/260218_pm-output  --dry-run
```

**Verbose output:**
```bash
cta /Volumes/camera_read/MOL_2026/Batch_1/260218  /Volumes/archive_write/260218_pm-output  --verbose
```

### Options

| Option | Description |
|--------|-------------|
| `--dry-run`, `-d` | Simulate the full pipeline without writing any archive, CSV, or MD5 files. |
| `--verbose`, `-v` | Print DEBUG-level messages to the terminal. |
| `--help`, `-h` | Print usage and exit. |

## Source directory structure

`cta` recognises two layouts:

```
260218/                    ← 6-digit date code (optional)
├── ff989gt5757/           ← 11-character content directory
├── fn695bb2830_1/         ← multi-part item, part 1
├── fn695bb2830_2/         ← multi-part item, part 2
└── fn695bb2830_3/         ← multi-part item, part 3
```

or flat (no date-code folder):

```
parent_directory/
├── ff989gt5757/
└── fn695bb2830_1/
```

## Output structure

```
output_directory/
├── tarball_archives/
│   ├── ff989gt5757_pm.tar
│   ├── ff989gt5757_pm.tar.md5
│   └── ...
├── csv/
│   ├── ff989gt5757_md.csv
│   ├── ff989gt5757_md.csv.md5
│   └── ...
└── logs/
    ├── cta_session_<timestamp>.log
    ├── ff989gt5757.log
    └── cta_warnings_<timestamp>.log   ← only if warnings occurred
```

## Upgrading

```bash
pipx upgrade cta-smpl
```

## Uninstalling

```bash
pipx uninstall cta-smpl
```

## Documentation

See [`cta_manual.md`](cta_manual.md) for the full user manual.

## License

MIT — see [LICENSE](LICENSE).
