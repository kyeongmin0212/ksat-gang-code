"""Process all Dante VTT files in batches of 50 with checkpoint/resume."""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from extractor import extract_batch  # noqa

SUBS_DIR = Path(r"C:/Users/sji48/subtitles")
BASE_DIR = Path(r"C:/Users/sji48/ksat_gang")
WORK_DIR = BASE_DIR / "dante_analysis"
CANDIDATES_DIR = WORK_DIR / "candidates"
LOGS_DIR = WORK_DIR / "logs"
PROGRESS_PATH = BASE_DIR / "dante_rules_progress.json"
CHECKPOINT_PATH = WORK_DIR / "checkpoint.json"
LOG_PATH = LOGS_DIR / f"batch_run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

BATCH_SIZE = 50


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def list_vtts() -> list[Path]:
    files = sorted(SUBS_DIR.glob("*.vtt"))
    return files


def load_checkpoint() -> dict:
    if CHECKPOINT_PATH.exists():
        try:
            return json.loads(CHECKPOINT_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"last_batch": -1, "total_batches": 0, "processed_files": 0, "summaries": []}


def save_checkpoint(ck: dict) -> None:
    CHECKPOINT_PATH.write_text(json.dumps(ck, ensure_ascii=False, indent=1), encoding="utf-8")


def save_progress(ck: dict) -> None:
    """Roll up checkpoint counts into dante_rules_progress.json for visibility."""
    progress = {
        "status": "in_progress" if ck["last_batch"] + 1 < ck["total_batches"] else "completed",
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "processed_batches": ck["last_batch"] + 1,
        "total_batches": ck["total_batches"],
        "processed_files": ck["processed_files"],
        "per_category_totals": ck.get("per_category_totals", {}),
        "note": "Keyword-extracted candidate snippets. See candidates/ for batch-level JSON.",
    }
    PROGRESS_PATH.write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    files = list_vtts()
    total = len(files)
    total_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE
    log(f"Discovered {total} VTT files → {total_batches} batches of {BATCH_SIZE}")

    ck = load_checkpoint()
    ck["total_batches"] = total_batches
    ck.setdefault("per_category_totals", {})

    start_batch = ck["last_batch"] + 1
    if start_batch > 0:
        log(f"Resuming from batch {start_batch} / {total_batches}")

    for batch_id in range(start_batch, total_batches):
        slice_start = batch_id * BATCH_SIZE
        slice_end = min(slice_start + BATCH_SIZE, total)
        batch_files = files[slice_start:slice_end]

        t0 = time.time()
        out_path = CANDIDATES_DIR / f"batch_{batch_id:04d}.json"
        try:
            summary = extract_batch(batch_files, batch_id, out_path)
        except Exception as e:
            log(f"  batch {batch_id}: ERROR {e!r}")
            ck["last_batch"] = batch_id - 1
            save_checkpoint(ck)
            return 1

        dt = time.time() - t0
        ck["last_batch"] = batch_id
        ck["processed_files"] = slice_end
        ck["summaries"].append({**summary, "seconds": round(dt, 2)})

        for cat, n in summary["per_cat"].items():
            ck["per_category_totals"][cat] = ck["per_category_totals"].get(cat, 0) + n

        save_checkpoint(ck)
        save_progress(ck)

        log(
            f"  batch {batch_id:04d}/{total_batches - 1}: "
            f"{len(batch_files)} files, {summary['total_hits']} hits, {dt:.1f}s"
        )

    log(f"DONE — {total_batches} batches, {ck['processed_files']} files processed")
    save_progress(ck)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
