#!/usr/bin/env python3
"""
cta - Create Tar Archive
Processes a parent directory of content folders, creating verified tar archives
and CSV manifests for each content subdirectory.

Usage:
    cta /Path/To/Parent/Content/Directory /Path/To/Output/Directory [-Options...]

Options:
    -v, --verbose     Increase output verbosity
    -d, --dry-run     Simulate processing without writing any files
    -h, --help        Show this help message and exit

Dependencies:
    pip install tqdm
"""

import csv
import hashlib
import logging
import argparse
import re
import shutil
import sys
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path

from tqdm import tqdm
from tqdm.contrib.logging import logging_redirect_tqdm


# ---------------------------------------------------------------------------
# tqdm-aware logging handler
# ---------------------------------------------------------------------------

class TqdmLoggingHandler(logging.StreamHandler):
    """Route log records through tqdm.write() so they don't overwrite progress bars."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            tqdm.write(self.format(record), file=sys.stdout)
            self.flush()
        except Exception:
            self.handleError(record)


# ---------------------------------------------------------------------------
# Logger factory
# ---------------------------------------------------------------------------

def setup_logger(
    name: str,
    log_path: Path,
    verbose: bool = False,
) -> "tuple[logging.Logger, logging.FileHandler]":
    """
    Return (logger, file_handler).

    The FileHandler is returned separately so the caller can explicitly
    flush and close it when the logger's scope is finished — this is
    important on network-attached and external volumes (e.g. macOS with
    a drive mounted over USB/Thunderbolt) where the OS page cache may
    not be flushed unless the handle is explicitly closed.
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    # Close and remove any pre-existing handlers before re-configuring.
    for h in logger.handlers[:]:
        try:
            h.flush()
            h.close()
        except Exception:
            pass
    logger.handlers.clear()
    logger.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # File handler – always full DEBUG
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    # Terminal handler – routed through tqdm so bars stay intact
    ch = TqdmLoggingHandler()
    ch.setLevel(logging.DEBUG if verbose else logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    return logger, fh


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def human_size(num_bytes: int) -> str:
    """Convert bytes to a tidy human-readable string (MB / GB)."""
    mb = num_bytes / (1024 ** 2)
    if mb >= 1024:
        return f"{mb / 1024:.2f} GB"
    return f"{mb:.2f} MB"


def get_creation_date(path: Path) -> str:
    """Return the best available timestamp (birth time on macOS, mtime elsewhere)."""
    stat = path.stat()
    ts = getattr(stat, "st_birthtime", None) or stat.st_mtime
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def compute_md5(path: Path, pbar: "tqdm | None" = None, chunk: int = 1 << 20) -> str:
    """
    Return the lowercase hex MD5 digest of *path*.
    If *pbar* is provided it is updated by the number of bytes read per chunk,
    allowing the caller to display file-level byte progress.
    """
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
            if pbar is not None:
                pbar.update(len(block))
    return h.hexdigest()


def dir_size(path: Path) -> int:
    """Return total byte size of all files recursively under *path*."""
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def free_space(path: Path) -> int:
    """Return free bytes available at *path*."""
    return shutil.disk_usage(path).free


# ---------------------------------------------------------------------------
# Directory discovery
# ---------------------------------------------------------------------------

CONTENT_RE = re.compile(r"^[A-Za-z0-9]{11}(_\d+)?$")
DATE_RE    = re.compile(r"^\d{6}$")


def find_content_dirs(parent_dir: Path) -> list[Path]:
    """
    Discover content directories one level beneath parent_dir (or two levels
    if a 6-digit date-code folder sits in between).

    A content directory name must be exactly 11 alphanumeric characters,
    optionally followed by an underscore and one or more digits indicating a
    multi-part item (e.g. fn695bb2830_1, fn695bb2830_2, fn695bb2830_3).

    Accepted layouts:
        parent_dir/YYYYMM/xxxxxxxxxxx/      <- date-code, single-part
        parent_dir/YYYYMM/xxxxxxxxxxx_N/    <- date-code, multi-part
        parent_dir/xxxxxxxxxxx/             <- flat, single-part
        parent_dir/xxxxxxxxxxx_N/           <- flat, multi-part
    """
    content_dirs: list[Path] = []

    for level1 in sorted(parent_dir.iterdir()):
        if not level1.is_dir():
            continue
        if CONTENT_RE.match(level1.name):
            content_dirs.append(level1)
        elif DATE_RE.match(level1.name):
            for level2 in sorted(level1.iterdir()):
                if level2.is_dir() and CONTENT_RE.match(level2.name):
                    content_dirs.append(level2)

    return content_dirs


# ---------------------------------------------------------------------------
# Pre-flight summary
# ---------------------------------------------------------------------------

def print_preflight(content_dirs: list[Path], output_dir: Path) -> None:
    """
    Print a formatted pre-flight table that lists every content directory to be
    processed with its individual size and file count, then shows aggregate
    totals and available disk space. Aborts with a clear message if space is
    insufficient.
    """
    COL_ID    = 13
    COL_PATH  = 52
    COL_SIZE  = 12
    COL_FILES =  7
    divider   = "-" * (COL_ID + COL_PATH + COL_SIZE + COL_FILES + 11)

    print()
    print("  ╔══════════════════════════════════════════════════════════════════╗")
    print("  ║           cta – Create Tar Archive  │  Pre-flight scan           ║")
    print("  ╚══════════════════════════════════════════════════════════════════╝")
    print()
    print("  Scanning content directories …")

    rows: list[tuple[str, Path, int, int]] = []
    total_bytes = 0
    total_files = 0

    with tqdm(
        content_dirs,
        desc="  Scanning",
        unit="dir",
        ncols=80,
        leave=False,
        bar_format="  {l_bar}{bar}| {n_fmt}/{total_fmt} dirs",
    ) as scan_bar:
        for cd in scan_bar:
            scan_bar.set_postfix_str(cd.name)
            files = [f for f in cd.rglob("*") if f.is_file()]
            size  = sum(f.stat().st_size for f in files)
            rows.append((cd.name, cd, size, len(files)))
            total_bytes += size
            total_files += len(files)

    available = free_space(output_dir)

    # ── Table ────────────────────────────────────────────────────────────────
    hdr_id    = "Identifier"
    hdr_path  = "Path"
    hdr_size  = "Size"
    hdr_files = "Files"

    print()
    print(
        f"  {hdr_id:<{COL_ID}}  {hdr_path:<{COL_PATH}}  "
        f"{hdr_size:>{COL_SIZE}}  {hdr_files:>{COL_FILES}}"
    )
    print(f"  {divider}")

    for uid, path, size, nfiles in rows:
        path_str = str(path)
        if len(path_str) > COL_PATH:
            path_str = "\u2026" + path_str[-(COL_PATH - 1):]
        print(
            f"  {uid:<{COL_ID}}  {path_str:<{COL_PATH}}  "
            f"{human_size(size):>{COL_SIZE}}  {nfiles:>{COL_FILES}}"
        )

    print(f"  {divider}")
    print(
        f"  {'TOTAL':<{COL_ID}}  {'':<{COL_PATH}}  "
        f"{human_size(total_bytes):>{COL_SIZE}}  {total_files:>{COL_FILES}}"
    )

    # ── Space summary ─────────────────────────────────────────────────────────
    needed = total_bytes * 2
    print()
    print(f"  Directories to process  :  {len(content_dirs)}")
    print(f"  Total source size       :  {human_size(total_bytes)}")
    print(f"  Estimated space needed  :  {human_size(needed)}  (2x for tarball + verify unpack)")
    print(f"  Available in output dir :  {human_size(available)}")
    print()

    if available < needed:
        print(f"  [ERROR] Insufficient disk space.")
        print(f"          Need {human_size(needed)} but only {human_size(available)} is available.")
        print()
        sys.exit(1)

    print("  [OK] Disk space check passed.")
    print()


# ---------------------------------------------------------------------------
# Core processing steps
# ---------------------------------------------------------------------------

def verify_md5s(
    content_dir: Path,
    log: logging.Logger,
) -> "tuple[bool, list[str]]":
    """
    Locate every .md5 sidecar in *content_dir* and verify each referenced file.
    A tqdm byte-progress bar is shown for each sidecar being processed.

    Behaviour:
      - Entries whose filename begins with '._' are AppleDouble / macOS resource-
        fork stubs created by macOS on non-HFS+ volumes. They are silently
        skipped (DEBUG only) — they are not real content files.
      - If a referenced non-AppleDouble file does not exist on disk, that is
        recorded as a WARNING and processing continues — it is NOT a failure.
      - A checksum mismatch IS a hard failure and sets checksums_ok = False.

    Returns:
        (checksums_ok: bool, warnings: list[str])
        checksums_ok is False only when at least one digest mismatch was found.
        warnings contains one human-readable string per missing file.
    """
    md5_files = list(content_dir.rglob("*.md5"))
    warnings: list[str] = []

    if not md5_files:
        log.warning("No .md5 sidecar files found in %s – skipping MD5 verification.", content_dir.name)
        return True, warnings

    checksums_ok = True

    for md5_file in md5_files:
        log.debug("Reading sidecar: %s", md5_file.name)

        entries: list[tuple[str, Path]] = []
        with open(md5_file, "r", encoding="utf-8", errors="replace") as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(None, 1)
                if len(parts) != 2:
                    log.warning("  Malformed line %d in %s – skipping.", lineno, md5_file.name)
                    continue
                expected_digest = parts[0].lower()
                rel_path = parts[1].lstrip("*").strip()
                target   = md5_file.parent / rel_path

                # ── Skip macOS AppleDouble / resource-fork stubs (._filename) ──
                # These are metadata artefacts written by macOS onto non-HFS+
                # volumes; they are not content files and should not be verified.
                if target.name.startswith("._"):
                    log.debug("  SKIP (AppleDouble stub)  %s", target.name)
                    continue

                entries.append((expected_digest, target))

        # Total bytes across files that actually exist (for the progress bar)
        total_bytes = sum(t.stat().st_size for _, t in entries if t.exists())

        with tqdm(
            total=total_bytes,
            desc=f"  MD5 {md5_file.name}",
            unit="B",
            unit_scale=True,
            unit_divisor=1024,
            ncols=80,
            leave=False,
            bar_format="  {l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]",
        ) as byte_bar:
            for expected_digest, target in entries:
                if not target.exists():
                    msg = (
                        f"[{content_dir.name}] MISSING file referenced in "
                        f"{md5_file.name}: {target.relative_to(content_dir)}"
                    )
                    log.warning("  [WARN] %s", msg)
                    warnings.append(msg)
                    continue
                byte_bar.set_postfix_str(target.name[:30])
                actual = compute_md5(target, pbar=byte_bar)
                if actual == expected_digest:
                    log.debug("  OK  %s", target.name)
                else:
                    log.error(
                        "  FAIL  %s  expected=%s  actual=%s",
                        target.name, expected_digest, actual,
                    )
                    checksums_ok = False

    return checksums_ok, warnings


def remove_ds_store(content_dir: Path, log: logging.Logger, dry_run: bool = False) -> None:
    """Delete all .DS_Store files found recursively inside content_dir."""
    ds_files = list(content_dir.rglob(".DS_Store"))
    if not ds_files:
        log.debug("No .DS_Store files found.")
        return
    for ds in ds_files:
        log.info("  Removing %s", ds)
        if not dry_run:
            ds.unlink()


def write_csv(
    content_dir: Path,
    csv_path: Path,
    log: logging.Logger,
    dry_run: bool = False,
) -> None:
    """
    Write a CSV manifest of all non-.md5 / non-.DS_Store files in content_dir.
    Columns: File Path, File Name, File Size, Date Created.
    """
    EXCLUDE_SUFFIXES = {".md5"}
    EXCLUDE_NAMES    = {".DS_Store"}

    all_files = [
        f for f in sorted(content_dir.rglob("*"))
        if f.is_file()
        and f.suffix.lower() not in EXCLUDE_SUFFIXES
        and f.name not in EXCLUDE_NAMES
    ]

    rows = []
    with tqdm(
        all_files,
        desc="  Building CSV",
        unit="file",
        ncols=80,
        leave=False,
        bar_format="  {l_bar}{bar}| {n_fmt}/{total_fmt} files",
    ) as fbar:
        for f in fbar:
            fbar.set_postfix_str(f.name[:35])
            rows.append({
                "File Path":    str(f.relative_to(content_dir.parent)),
                "File Name":    f.name,
                "File Size":    human_size(f.stat().st_size),
                "Date Created": get_creation_date(f),
            })

    log.info("  CSV manifest: %d entries → %s", len(rows), csv_path.name)
    if not dry_run:
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        with open(csv_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(
                fh,
                fieldnames=["File Path", "File Name", "File Size", "Date Created"],
            )
            writer.writeheader()
            writer.writerows(rows)


def create_tarball(
    content_dir: Path,
    tar_path: Path,
    log: logging.Logger,
    dry_run: bool = False,
) -> bool:
    """
    Create a tar archive of content_dir using Python's tarfile module, streaming
    each file through a tqdm byte-progress bar.

    Equivalent to: tar -cvf <tar_path> -C <content_dir.parent> <uid>
    so that unpacking always produces a top-level folder named with the
    11-character unique identifier, e.g.:
        bb164gn8864/
            OMF2300-3615e-9/
                AVCHD/...

    The _md.csv sidecar is stored in the csv/ output folder, not inside
    content_dir, so it is naturally excluded from the archive.

    Returns True on success.
    """
    if dry_run:
        log.info("  [dry-run] Skipping tarball creation.")
        return True

    all_files = [f for f in sorted(content_dir.rglob("*")) if f.is_file()]
    total_bytes = sum(f.stat().st_size for f in all_files)

    log.info(
        "  Creating tarball: %s  (%d files, %s)",
        tar_path.name, len(all_files), human_size(total_bytes),
    )
    tar_path.parent.mkdir(parents=True, exist_ok=True)

    # Prefix every archive member with the UID folder name so that
    # extracting the tarball always yields  <uid>/... at the top level.
    uid_prefix = Path(content_dir.name)

    try:
        with tarfile.open(tar_path, "w") as tf, tqdm(
            total=total_bytes,
            desc="  Archiving",
            unit="B",
            unit_scale=True,
            unit_divisor=1024,
            ncols=80,
            leave=False,
            bar_format="  {l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]",
        ) as byte_bar:
            for file_path in all_files:
                arcname = uid_prefix / file_path.relative_to(content_dir)
                byte_bar.set_postfix_str(str(arcname)[:35])
                tf.add(file_path, arcname=arcname)
                byte_bar.update(file_path.stat().st_size)

        log.debug("  Tarball written successfully.")
        return True

    except Exception as exc:
        log.error("  Tarball creation failed: %s", exc)
        return False


def verify_tarball(
    tar_path: Path,
    content_dir: Path,
    log: logging.Logger,
    dry_run: bool = False,
) -> bool:
    """
    Unpack the tarball into a temp directory and perform three verification
    checks, producing clear structured log output for each:

    Check A – File-list comparison
        Every file in the source content_dir must appear in the unpacked
        tarball and vice-versa (excluding .DS_Store files, which are removed
        before archiving). Reports any files present only in source or only
        in the archive.

    Check B – Size comparison
        Compares the total byte size of the source content_dir against the
        size of the .tar file on disk. Because tar stores uncompressed data
        plus per-entry headers (~512 B each), the tarball will be slightly
        larger than the source; a tolerance of 0.5 % or 1 MB (whichever is
        greater) is applied. A larger discrepancy is flagged as a warning.

    Check C – MD5 checksum verification
        All .md5 sidecar files embedded in the archive are verified.
        Every passing file is listed at DEBUG level; every failure is listed
        at ERROR level in a clearly delimited block.

    Returns True only if all three checks pass.
    """
    if dry_run:
        log.info("  [dry-run] Skipping tarball verification.")
        return True

    log.info("  Verifying tarball: %s", tar_path.name)
    log.info("  " + "-" * 60)
    tmp_dir = Path(tempfile.mkdtemp(prefix="cta_verify_"))
    all_ok  = True

    try:
        # ── Unpack ───────────────────────────────────────────────────────────
        with tarfile.open(tar_path, "r") as tf:
            members = tf.getmembers()
            with tqdm(
                members,
                desc="  Unpacking",
                unit="file",
                ncols=80,
                leave=False,
                bar_format="  {l_bar}{bar}| {n_fmt}/{total_fmt} files [{elapsed}]",
            ) as fbar:
                for member in fbar:
                    fbar.set_postfix_str(member.name[:35])
                    tf.extract(member, path=tmp_dir, set_attrs=False)

        # The archive was built with a uid/ prefix, so the actual content
        # lives one level down: tmp_dir/<uid>/
        uid           = content_dir.name
        unpacked_root = tmp_dir / uid

        # ── Check A: File-list comparison ────────────────────────────────────
        log.info("  [A] File-list comparison")

        IGNORE = {".DS_Store"}

        def rel_files(base: Path) -> set[str]:
            return {
                str(f.relative_to(base))
                for f in base.rglob("*")
                if f.is_file() and f.name not in IGNORE
            }

        src_files = rel_files(content_dir)
        tar_files = rel_files(unpacked_root)

        only_in_src = sorted(src_files - tar_files)
        only_in_tar = sorted(tar_files - src_files)

        if not only_in_src and not only_in_tar:
            log.info("      [OK] File lists match exactly (%d files).", len(src_files))
        else:
            all_ok = False
            if only_in_src:
                log.error("      [FAIL] %d file(s) in source but NOT in archive:", len(only_in_src))
                for p in only_in_src:
                    log.error("             MISSING FROM TAR: %s", p)
            if only_in_tar:
                log.error("      [FAIL] %d file(s) in archive but NOT in source:", len(only_in_tar))
                for p in only_in_tar:
                    log.error("             EXTRA IN TAR: %s", p)

        # ── Check B: Size comparison ─────────────────────────────────────────
        log.info("  [B] Size comparison")

        src_bytes = sum(
            f.stat().st_size for f in content_dir.rglob("*")
            if f.is_file() and f.name not in IGNORE
        )
        tar_bytes = tar_path.stat().st_size

        # Tolerance: 0.5 % of source size or 1 MiB, whichever is larger.
        tolerance = max(src_bytes * 0.005, 1 * 1024 ** 2)
        # The tarball is always at least as large as the source (headers add
        # a small amount), so we check the absolute difference.
        size_diff     = abs(tar_bytes - src_bytes)
        size_diff_pct = (size_diff / src_bytes * 100) if src_bytes else 0

        log.info(
            "      Source content : %s  (%d bytes)",
            human_size(src_bytes), src_bytes,
        )
        log.info(
            "      Tarball size   : %s  (%d bytes)",
            human_size(tar_bytes), tar_bytes,
        )
        log.info(
            "      Difference     : %s  (%.3f %%)",
            human_size(size_diff), size_diff_pct,
        )

        if size_diff <= tolerance:
            log.info("      [OK] Size difference is within tolerance (≤ 0.5 %% / 1 MB).")
        else:
            log.warning(
                "      [WARN] Size difference %.3f %% exceeds tolerance — "
                "verify manually if unexpected.",
                size_diff_pct,
            )
            # Size mismatch is a warning, not a hard failure — the file-list
            # and MD5 checks are the authoritative integrity tests.

        # ── Check C: MD5 checksum verification ───────────────────────────────
        log.info("  [C] MD5 checksum verification")

        checksums_ok, _warnings = verify_md5s(unpacked_root, log)

        log.info("  " + "=" * 60)
        if checksums_ok and all_ok:
            log.info("  ╔══════════════════════════════════════════════════════════╗")
            log.info("  ║  ✓  ARCHIVE VERIFIED                                     ║")
            log.info("  ╠══════════════════════════════════════════════════════════╣")
            log.info("  ║  Archive  : %-44s ║", tar_path.name)
            log.info("  ║  Files    : %-3d match between source and archive         ║", len(src_files))
            log.info("  ║  Size     : %-10s (source)  →  %-10s (tar)     ║",
                     human_size(src_bytes), human_size(tar_bytes))
            log.info("  ║  MD5s     : All checksums verified against source         ║")
            log.info("  ║  Result   : PASS — archive is a complete, faithful copy   ║")
            log.info("  ╚══════════════════════════════════════════════════════════╝")
        else:
            if not checksums_ok:
                all_ok = False
                log.error("  ╔══════════════════════════════════════════════════════════╗")
                log.error("  ║  ✗  ARCHIVE VERIFICATION FAILED — MD5 MISMATCH(ES)       ║")
                log.error("  ║  Archive : %-45s ║", tar_path.name)
                log.error("  ╚══════════════════════════════════════════════════════════╝")
            if not all_ok:
                log.error("  ╔══════════════════════════════════════════════════════════╗")
                log.error("  ║  ✗  ARCHIVE VERIFICATION FAILED — review errors above    ║")
                log.error("  ║  Archive : %-45s ║", tar_path.name)
                log.error("  ╚══════════════════════════════════════════════════════════╝")

        return all_ok

    except Exception as exc:
        log.error("  Error during tarball verification: %s", exc)
        return False

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        log.debug("  Temporary verification directory removed.")


def write_md5_sidecar(
    target: Path,
    log: logging.Logger,
    dry_run: bool = False,
) -> None:
    """Compute MD5 of *target* with a byte-progress bar and write a .md5 sidecar."""
    md5_path = target.with_suffix(target.suffix + ".md5")
    log.info("  Writing MD5 sidecar: %s", md5_path.name)
    if dry_run:
        return

    file_size = target.stat().st_size
    with tqdm(
        total=file_size,
        desc=f"  Hashing {target.name[:28]}",
        unit="B",
        unit_scale=True,
        unit_divisor=1024,
        ncols=80,
        leave=False,
        bar_format="  {l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]",
    ) as byte_bar:
        digest = compute_md5(target, pbar=byte_bar)

    with open(md5_path, "w", encoding="utf-8") as fh:
        fh.write(f"{digest}  {target.name}\n")


# ---------------------------------------------------------------------------
# Per-content-directory orchestration
# ---------------------------------------------------------------------------

STEPS = [
    "Verify source MD5s",    # 1
    "Write CSV manifest",    # 2
    "Remove .DS_Store",      # 3
    "Create tarball",        # 4
    "Verify tarball",        # 5
    "MD5 sidecar: tarball",  # 6
    "MD5 sidecar: CSV",      # 7
]


def process_content_dir(
    content_dir: Path,
    out_csv_dir: Path,
    out_tar_dir: Path,
    out_log_dir: Path,
    verbose: bool,
    dry_run: bool,
    session_bar: tqdm,
    session_warnings: "dict[str, list[str]]",
) -> bool:
    """
    Run the full 7-step pipeline for a single content directory.
    A per-step tqdm bar tracks which step is active.

    Any warnings collected during MD5 verification (e.g. missing files that are
    not AppleDouble stubs) are appended to session_warnings[uid] so they can be
    included in the final warnings summary log.

    Returns True on complete success (warnings do not cause a False return).
    """
    uid = content_dir.name

    log_path = out_log_dir / f"{uid}.log"
    log, log_fh = setup_logger(uid, log_path, verbose=verbose)

    log.info("=" * 70)
    log.info("Processing: %s", content_dir)
    log.info("=" * 70)

    csv_path = out_csv_dir / f"{uid}_md.csv"
    tar_path = out_tar_dir / f"{uid}_pm.tar"

    with tqdm(
        total=len(STEPS),
        desc=f"  [{uid}]",
        unit="step",
        ncols=80,
        leave=True,
        bar_format="  {l_bar}{bar}| {n_fmt}/{total_fmt} steps  [{elapsed}]  {postfix}",
    ) as step_bar:

        def advance(label: str) -> None:
            step_bar.set_postfix_str(label)
            step_bar.update(1)

        # 1 — Verify source MD5s ─────────────────────────────────────────────
        step_bar.set_postfix_str(STEPS[0])
        log.info("[1/7] %s", STEPS[0])
        checksums_ok, md5_warnings = verify_md5s(content_dir, log)

        # Stash any warnings into the session-level collector
        if md5_warnings:
            session_warnings.setdefault(uid, []).extend(md5_warnings)
            log.warning(
                "  %d missing-file warning(s) recorded for %s (see warnings summary).",
                len(md5_warnings), uid,
            )

        if not checksums_ok:
            log.error("Checksum mismatch(es) found in source files. Aborting.")
            advance(f"{STEPS[0]} FAILED")
            return False
        advance(STEPS[0])

        # 2 — Write CSV manifest ─────────────────────────────────────────────
        step_bar.set_postfix_str(STEPS[1])
        log.info("[2/7] %s", STEPS[1])
        write_csv(content_dir, csv_path, log, dry_run=dry_run)
        advance(STEPS[1])

        # 3 — Remove .DS_Store ───────────────────────────────────────────────
        step_bar.set_postfix_str(STEPS[2])
        log.info("[3/7] %s", STEPS[2])
        remove_ds_store(content_dir, log, dry_run=dry_run)
        advance(STEPS[2])

        # 4 — Create tarball ─────────────────────────────────────────────────
        step_bar.set_postfix_str(STEPS[3])
        log.info("[4/7] %s", STEPS[3])
        if not create_tarball(content_dir, tar_path, log, dry_run=dry_run):
            log.error("Tarball creation failed. Aborting.")
            advance(f"{STEPS[3]} FAILED")
            return False
        advance(STEPS[3])

        # 5 — Verify tarball ─────────────────────────────────────────────────
        step_bar.set_postfix_str(STEPS[4])
        log.info("[5/7] %s", STEPS[4])
        if not verify_tarball(tar_path, content_dir, log, dry_run=dry_run):
            log.error("Tarball verification failed. Aborting.")
            advance(f"{STEPS[4]} FAILED")
            return False
        advance(STEPS[4])

        # 6 — MD5 sidecar for tarball ────────────────────────────────────────
        step_bar.set_postfix_str(STEPS[5])
        log.info("[6/7] %s", STEPS[5])
        write_md5_sidecar(tar_path, log, dry_run=dry_run)
        advance(STEPS[5])

        # 7 — MD5 sidecar for CSV ────────────────────────────────────────────
        step_bar.set_postfix_str(STEPS[6])
        log.info("[7/7] %s", STEPS[6])
        write_md5_sidecar(csv_path, log, dry_run=dry_run)
        advance("done")

    log.info("")
    log.info("  ┌─────────────────────────────────────────────────────────────┐")
    log.info("  │  ✓  ARCHIVE COMPLETE : %-38s │", uid)
    log.info("  │     tar archive created, unpacked, verified, and checksummed │")
    log.info("  └─────────────────────────────────────────────────────────────┘")
    log.info("")

    # Explicitly flush and close the per-directory FileHandler so the log
    # file is fully written to disk before we move on — this is the most
    # reliable way to ensure the file appears on external/network volumes.
    try:
        log_fh.flush()
        log_fh.close()
        log.removeHandler(log_fh)
    except Exception:
        pass

    session_bar.update(1)
    session_bar.set_postfix_str(f"last OK: {uid}")
    return True


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="cta",
        description=(
            "Create Tar Archive – batch-archive content directories "
            "with MD5 verification and tqdm progress bars."
        ),
    )
    parser.add_argument(
        "parent_dir",
        type=Path,
        help="Parent directory containing date-coded or flat content subdirectories.",
    )
    parser.add_argument(
        "output_dir",
        type=Path,
        help="Output directory where archives, CSVs, logs, and sidecars will be written.",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Print DEBUG-level messages to the terminal.",
    )
    parser.add_argument(
        "-d", "--dry-run",
        action="store_true",
        help="Simulate all steps without writing any output files.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    parent_dir: Path = args.parent_dir.resolve()
    output_dir: Path = args.output_dir.resolve()

    if not parent_dir.is_dir():
        tqdm.write(f"ERROR: Parent directory does not exist: {parent_dir}", file=sys.stderr)
        sys.exit(1)

    # ── Create output structure ───────────────────────────────────────────────
    out_csv_dir = output_dir / "csv"
    out_tar_dir = output_dir / "tarball_archives"
    out_log_dir = output_dir / "logs"

    # Logs are always written (they are observational, not "work" output),
    # so the logs directory is created unconditionally — even in dry-run mode.
    out_log_dir.mkdir(parents=True, exist_ok=True)

    if not args.dry_run:
        for d in (out_csv_dir, out_tar_dir):
            d.mkdir(parents=True, exist_ok=True)

    # ── Discover content directories ──────────────────────────────────────────
    content_dirs = find_content_dirs(parent_dir)
    if not content_dirs:
        tqdm.write(f"ERROR: No content directories found under {parent_dir}")
        sys.exit(1)

    # ── Pre-flight table + space check ───────────────────────────────────────
    print_preflight(content_dirs, output_dir)

    # ── Session logger ────────────────────────────────────────────────────────
    session_stamp    = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_log_path = out_log_dir / f"cta_session_{session_stamp}.log"
    session_log, session_fh = setup_logger("cta_session", session_log_path, verbose=args.verbose)

    session_log.info("cta session started")
    session_log.info("  Parent dir  : %s", parent_dir)
    session_log.info("  Output dir  : %s", output_dir)
    session_log.info("  Dry run     : %s", args.dry_run)
    session_log.info("  Verbose     : %s", args.verbose)
    session_log.info("  Directories : %d", len(content_dirs))

    # ── Outer session progress bar ─────────────────────────────────────────
    results:          dict[str, bool]       = {}
    session_warnings: dict[str, list[str]] = {}

    with logging_redirect_tqdm():
        with tqdm(
            total=len(content_dirs),
            desc="Overall progress",
            unit="dir",
            ncols=80,
            position=0,
            leave=True,
            bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} dirs  [{elapsed}<{remaining}]  {postfix}",
        ) as session_bar:
            for cd in content_dirs:
                ok = process_content_dir(
                    content_dir=cd,
                    out_csv_dir=out_csv_dir,
                    out_tar_dir=out_tar_dir,
                    out_log_dir=out_log_dir,
                    verbose=args.verbose,
                    dry_run=args.dry_run,
                    session_bar=session_bar,
                    session_warnings=session_warnings,
                )
                results[cd.name] = ok
                if not ok:
                    session_bar.set_postfix_str(f"FAILED: {cd.name}")

    # ── Write warnings summary log ────────────────────────────────────────────
    total_warning_count = sum(len(v) for v in session_warnings.values())
    if session_warnings:
        warnings_log_path = out_log_dir / f"cta_warnings_{session_stamp}.log"
        with open(warnings_log_path, "w", encoding="utf-8") as wf:
            wf.write("cta – Warnings Summary\n")
            wf.write(f"Session : {session_stamp}\n")
            wf.write(f"Parent  : {parent_dir}\n")
            wf.write(f"Total warnings : {total_warning_count}\n")
            wf.write("=" * 70 + "\n\n")
            for uid, msgs in session_warnings.items():
                wf.write(f"[{uid}]  {len(msgs)} warning(s)\n")
                wf.write("-" * 50 + "\n")
                for msg in msgs:
                    wf.write(f"  {msg}\n")
                wf.write("\n")
        session_log.warning(
            "%d warning(s) across %d director%s — see %s",
            total_warning_count,
            len(session_warnings),
            "y" if len(session_warnings) == 1 else "ies",
            warnings_log_path.name,
        )

    # ── Final summary ─────────────────────────────────────────────────────────
    passed = [uid for uid, ok in results.items() if ok]
    failed = [uid for uid, ok in results.items() if not ok]

    print()
    print("  ╔══════════════════════════════════╗")
    print("  ║         SESSION SUMMARY           ║")
    print("  ╚══════════════════════════════════╝")
    print(f"  Processed  :  {len(results)}")
    print(f"  Succeeded  :  {len(passed)}")
    print(f"  Failed     :  {len(failed)}")
    print(f"  Warnings   :  {total_warning_count}", end="")
    if session_warnings:
        print(f"  (see logs/cta_warnings_{session_stamp}.log)")
    else:
        print()

    if failed:
        print()
        print("  Failed directories:")
        for uid in failed:
            print(f"    [FAIL]  {uid}")
        print()
        session_log.error(
            "Session completed with %d failure(s): %s", len(failed), failed
        )
        sys.exit(1)
    else:
        end_time   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        warn_line  = (
            f"  Warnings   :  {total_warning_count}"
            + (f"  (see logs/cta_warnings_{session_stamp}.log)" if session_warnings else "")
        )
        print()
        session_log.info("")
        session_log.info("  ╔══════════════════════════════════════════════════════════════════╗")
        session_log.info("  ║  ✓  ALL ARCHIVES VERIFIED AND COMPLETE                           ║")
        session_log.info("  ╠══════════════════════════════════════════════════════════════════╣")
        session_log.info("  ║  Every content directory in this batch has been:                 ║")
        session_log.info("  ║    • Checksummed against its source .md5 sidecars                ║")
        session_log.info("  ║    • Packaged into a named, uncompressed tar archive              ║")
        session_log.info("  ║    • Unpacked and re-verified (file list + size + MD5s)           ║")
        session_log.info("  ║    • Signed with a final .md5 checksum for the archive itself     ║")
        session_log.info("  ╠══════════════════════════════════════════════════════════════════╣")
        session_log.info("  ║  Batch     :  %-52s ║", parent_dir.name)
        session_log.info("  ║  Archives  :  %-3d verified                                       ║", len(passed))
        session_log.info("  ║  Failures  :  0                                                   ║")
        if total_warning_count:
            session_log.info("  ║  Warnings  :  %-3d  (see cta_warnings_%s.log)  ║",
                             total_warning_count, session_stamp)
        else:
            session_log.info("  ║  Warnings  :  0                                                   ║")
        session_log.info("  ║  Completed :  %-52s ║", end_time)
        session_log.info("  ╚══════════════════════════════════════════════════════════════════╝")
        session_log.info("")


if __name__ == "__main__":
    main()
