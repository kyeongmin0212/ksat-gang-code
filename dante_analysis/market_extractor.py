"""VTT 자막에서 '시장 판단 / 약세장 / 관망 / 현금 비중 / 지수' 관련 원문 추출."""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from vtt_parser import parse_vtt

SUBS_DIR = Path(r"C:/Users/sji48/subtitles")
OUT_DIR = Path(r"C:/Users/sji48/ksat_gang/logs")

CATEGORIES: dict[str, list[str]] = {
    "시장_판단": [
        r"시장\s*(?:상황|판단|분위기|강도|체력|상태)",
        r"장세\s*(?:판단|분석)?",
        r"시황",
        r"(?:강|약)세장",
        r"상승장", r"하락장",
        r"국면\s*(?:전환|판단)?",
        r"추세\s*전환",
    ],
    "약세_하락장": [
        r"약세\s*(?:장|구간)?",
        r"하락\s*(?:장|국면|추세)",
        r"조정(?:장|기간|구간)?",
        r"베어(?:\s*마켓)?",
        r"폭락",
        r"공포",
        r"코로나\s*때",
        r"리먼",
        r"금융위기",
        r"바닥\s*장",
        r"버블\s*붕괴",
    ],
    "관망_매매중단": [
        r"관망",
        r"쉬어?(?:야|라|세요|는)",
        r"쉬는\s*(?:것|것도|게|자세|용기)",
        r"매매\s*(?:중단|쉬)",
        r"쉬다?가",
        r"기다려(?:야|세요|라)",
        r"안\s*(?:하|들어가|사|매수)",
        r"매매\s*하지\s*마",
        r"주식\s*쉬",
        r"한\s*발\s*물러",
        r"지켜\s*보",
    ],
    "현금_비중": [
        r"현금\s*(?:100|비중|확보|보유|화|유지)",
        r"전량\s*(?:현금|매도|청산)",
        r"현금화",
        r"비중\s*(?:축소|조절|줄|늘)",
        r"포지션\s*(?:축소|정리|비우|줄)",
        r"매도\s*(?:전량|후|타이밍)?",
    ],
    "지수_언급": [
        r"코스피\s*\d*",
        r"코스닥\s*\d*",
        r"KOSPI",
        r"KOSDAQ",
        r"지수\s*(?:가|는|의|도|에|를|선|\s)",
        r"종합\s*지수",
        r"대형\s*주",
        r"시총\s*(?:상위|순)?",
    ],
    "장기_이평_시장": [
        r"200\s*일\s*선",
        r"224\s*일\s*선",
        r"448\s*일\s*선",
        r"장기\s*이평",
        r"장기\s*추세(?:\s*선)?",
        r"연봉\s*선",
        r"월봉\s*선",
        r"부모\s*라인",
    ],
    "체제_국면_경고": [
        r"위험\s*(?:구간|신호|한|하다|시기)",
        r"과열\s*(?:구간|상태|경고)?",
        r"꼭지\s*(?:신호|자리|징후)?",
        r"고점\s*(?:임박|신호|찍)",
        r"폭등\s*후",
        r"주도주\s*(?:바뀜|전환)?",
        r"섹터\s*(?:회전|로테)",
        r"수급\s*이탈",
        r"저가\s*매수\s*기회",
    ],
    "심리_원칙": [
        r"두려(?:움|워)",
        r"탐욕",
        r"멘탈\s*(?:관리|통제|흔들|무너)",
        r"대응\s*(?:매매|전략|해야)",
        r"원칙\s*(?:지키|대로|없으면)",
        r"뇌동\s*매매",
        r"확신\s*(?:없으|가지)",
    ],
}

COMPILED: dict[str, list[re.Pattern]] = {
    cat: [re.compile(p) for p in pats] for cat, pats in CATEGORIES.items()
}

SENTENCE_SPLIT = re.compile(r"(?<=[\.\?\!。])\s+|\n+")


def sentences(text: str) -> list[str]:
    out = []
    for chunk in SENTENCE_SPLIT.split(text):
        s = chunk.strip()
        if len(s) >= 8:
            out.append(s)
    return out


def normalize(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"[\s,\.\!\?~…·:;\"'\(\)\[\]<>/&;]", "", s)
    return s


def extract_from_file(path: Path) -> dict[str, list[tuple[str, str]]]:
    """{category: [(sentence, source_filename), ...]}"""
    text = parse_vtt(path)
    sents = sentences(text)
    hits: dict[str, list[tuple[str, str]]] = {c: [] for c in COMPILED}
    for s in sents:
        for cat, pats in COMPILED.items():
            if any(p.search(s) for p in pats):
                hits[cat].append((s, path.name))
    return hits


def main() -> None:
    files = sorted(SUBS_DIR.glob("*.vtt"))
    print(f"총 {len(files)} 파일 스캔 시작", flush=True)

    agg: dict[str, dict[str, list[str]]] = {c: {} for c in COMPILED}  # cat → {norm: [raw, source]}

    for i, fp in enumerate(files, start=1):
        try:
            hits = extract_from_file(fp)
        except Exception as e:
            continue
        for cat, items in hits.items():
            bucket = agg[cat]
            for raw, src in items:
                n = normalize(raw)
                if not n:
                    continue
                if n not in bucket:
                    bucket[n] = {"raw": raw, "source": src, "count": 1}
                else:
                    bucket[n]["count"] += 1
        if i % 200 == 0:
            print(f"  {i}/{len(files)}", flush=True)

    # 집약 저장
    out_file = OUT_DIR / "market_rules_part_B.txt"
    lines = []
    lines.append("=" * 60)
    lines.append("  [B] 2,261 VTT 자막 — '시장/약세/관망' 관련 원문 추출")
    lines.append("=" * 60)
    for cat, bucket in agg.items():
        entries = sorted(bucket.values(), key=lambda x: -x["count"])
        lines.append("")
        lines.append(f"### {cat}  (unique {len(entries)} / top 40 표시)")
        lines.append("-" * 60)
        for e in entries[:40]:
            lines.append(f'  [x{e["count"]}]  {e["raw"]}')
            lines.append(f'          ─ {e["source"][:80]}')
    out_file.write_text("\n".join(lines), encoding="utf-8")
    print(f"저장: {out_file} (lines={len(lines)})")

    # JSON 버전
    jout = OUT_DIR / "market_rules_part_B.json"
    jdata = {
        cat: sorted(list(bucket.values()), key=lambda x: -x["count"])
        for cat, bucket in agg.items()
    }
    jout.write_text(json.dumps(jdata, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"저장: {jout}")


if __name__ == "__main__":
    main()
