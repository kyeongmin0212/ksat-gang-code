"""Aggregate batch candidate snippets per category, dedupe, distill.

For each of the 10 categories, produce:
  candidates/_agg_{category}.json
containing:
  - top N most-frequent exact sentences
  - numeric hotspots (sentences with digit+% or digit+일/배/원)
  - high-signal snippets (sentences hitting >=2 categories)
  - representative samples grouped by distinct keyword clusters
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

WORK = Path(r"C:/Users/sji48/ksat_gang/dante_analysis")
CAND = WORK / "candidates"
AGG = WORK / "candidates"

CATEGORIES = [
    "ichimoku",
    "moving_average",
    "bollinger",
    "volume",
    "buy_entry",
    "stop_loss",
    "channel_pattern",
    "sejryeok",
    "other_technique",
    "dante_signature",
]

NUMERIC = re.compile(r"(\d+(?:\.\d+)?\s*(?:%|일|배|원|퍼센트|프로|분의\s*\d+))")
IMPORTANT_KEYWORDS = {
    "ichimoku": ["선행스팬", "기준선", "전환선", "구름", "일목", "블루밴드"],
    "moving_average": ["5일", "20일", "60일", "120일", "200일", "224일", "240일",
                       "MA5", "MA20", "MA60", "MA120", "MA200",
                       "골든크로스", "데드크로스", "정배열", "역배열", "수렴", "밀집"],
    "bollinger": ["상단", "하단", "수축", "확장", "중심선", "터치"],
    "volume": ["배", "10배", "5배", "3배", "터짐", "매집", "바닥", "실종", "폭발", "급증"],
    "buy_entry": ["분할매수", "추격매수", "눌림", "지지", "진입", "타이밍", "자리"],
    "stop_loss": ["%", "1차", "2차", "이중", "이탈", "컷"],
    "channel_pattern": ["상승채널", "하락채널", "수평채널", "쐐기", "박스", "수렴", "삼각"],
    "sejryeok": ["세력", "매집", "주포", "손바뀜", "기관", "외국인"],
    "other_technique": ["목표", "1차목표", "2차목표", "피보나치", "엘리엇", "파동", "갭", "전고점", "전저점", "추세선"],
    "dante_signature": ["반드시", "무조건", "절대", "여러분", "꼭", "진짜", "단테", "원칙", "공식"],
}


def normalize(s: str) -> str:
    """Canonical form for dedup — strip spaces+punct."""
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"[\s,\.\!\?~…·:;\"'\(\)\[\]<>/]", "", s)
    return s


def load_all_batches() -> list[dict]:
    files = sorted(CAND.glob("batch_*.json"))
    return files


def aggregate_category(cat: str, batch_files: list[Path]) -> dict:
    # Collect all sentences for this category
    # Also track categories-per-sentence for multi-hit scoring
    all_sents: list[tuple[str, str]] = []  # (sentence, source_file)
    sent_to_cats: defaultdict[str, set[str]] = defaultdict(set)

    for bf in batch_files:
        data = json.loads(bf.read_text(encoding="utf-8"))
        for item in data["by_category"].get(cat, []):
            s = item["sentence"]
            all_sents.append((s, item["source"]))
        # Also populate multi-cat map
        for other_cat, items in data["by_category"].items():
            for item in items:
                sent_to_cats[normalize(item["sentence"])].add(other_cat)

    # Dedup by normalized form, keep first-occurrence source
    seen = {}  # norm -> {sentence, source, count}
    for s, src in all_sents:
        n = normalize(s)
        if not n:
            continue
        if n in seen:
            seen[n]["count"] += 1
        else:
            seen[n] = {"sentence": s, "source": src, "count": 1, "norm": n}

    # Top by frequency
    entries = list(seen.values())
    entries.sort(key=lambda x: -x["count"])

    # Numeric-carrying
    numeric_hits = [e for e in entries if NUMERIC.search(e["sentence"])][:120]

    # Keyword-cluster samples — pick N diverse sentences per important keyword
    kw_samples: dict[str, list[dict]] = {}
    for kw in IMPORTANT_KEYWORDS.get(cat, []):
        samp = [e for e in entries if kw in e["sentence"]][:20]
        if samp:
            kw_samples[kw] = samp

    # Multi-category high-signal (hit by 3+ categories)
    multi = []
    for e in entries[:3000]:
        cats = sent_to_cats.get(e["norm"], set())
        if len(cats) >= 3:
            ee = dict(e)
            ee["hit_cats"] = sorted(cats)
            multi.append(ee)
    multi.sort(key=lambda x: (-len(x["hit_cats"]), -x["count"]))
    multi = multi[:120]

    return {
        "category": cat,
        "unique_sentences": len(entries),
        "top_by_freq": entries[:100],
        "numeric_hits": numeric_hits,
        "keyword_samples": kw_samples,
        "multi_category": multi,
    }


def main() -> None:
    batch_files = load_all_batches()
    print(f"Aggregating across {len(batch_files)} batches")

    overall = {"categories": {}, "totals": {}}
    for cat in CATEGORIES:
        print(f"  {cat} ...", flush=True)
        agg = aggregate_category(cat, batch_files)
        out = AGG / f"_agg_{cat}.json"
        out.write_text(json.dumps(agg, ensure_ascii=False, indent=1), encoding="utf-8")
        overall["totals"][cat] = {
            "unique": agg["unique_sentences"],
            "top_count": len(agg["top_by_freq"]),
            "numeric_count": len(agg["numeric_hits"]),
            "kw_keys": list(agg["keyword_samples"].keys()),
            "multi_count": len(agg["multi_category"]),
        }
        print(
            f"    unique={agg['unique_sentences']}, "
            f"numeric={len(agg['numeric_hits'])}, "
            f"multi={len(agg['multi_category'])}"
        )

    (AGG / "_agg_overview.json").write_text(json.dumps(overall, ensure_ascii=False, indent=1), encoding="utf-8")
    print("DONE")


if __name__ == "__main__":
    main()
