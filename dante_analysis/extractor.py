"""Extract trading-methodology snippets from Dante VTT subtitles.

Reads a batch of VTT files, scans each sentence for category keywords, and
saves the matching sentences (with surrounding context) into a per-batch
JSON candidate file. The orchestrator ``batch_processor.py`` drives this.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List

from vtt_parser import parse_vtt

# ---------------------------------------------------------------------------
# Category keyword dictionary — Korean patterns drawn from trading vocabulary.
# Each category has a list of regex patterns. A sentence matches a category
# if ANY pattern matches. One sentence can match multiple categories.
# ---------------------------------------------------------------------------

CATEGORY_PATTERNS: Dict[str, List[str]] = {
    # 1) 일목균형표 / 구름대 / 전환선 / 기준선 / 선행스팬
    "ichimoku": [
        r"일목(?:균형표|구름|균형)",
        r"구름(?:대|층|띠)?",
        r"전환선",
        r"기준선",
        r"선행\s*스팬",
        r"후행\s*스팬",
        r"구름\s*(?:돌파|이탈|위|아래|상단|하단|위에|아래에)",
        r"블루\s*밴드",
    ],
    # 2) 이평선
    "moving_average": [
        r"이평선?",
        r"이동평균",
        r"\d+\s*일\s*선",
        r"\d+\s*일\s*이평",
        r"\d+\s*일\s*이동평균",
        r"5일선|20일선|60일선|120일선|200일선|224일선|240일선",
        r"MA\s*\d+",
        r"골든\s*크로스",
        r"데드\s*크로스",
        r"정배열",
        r"역배열",
        r"이평\s*(?:수렴|밀집|배열|정배열|역배열)",
    ],
    # 3) 볼린저밴드
    "bollinger": [
        r"볼린저\s*밴드",
        r"볼밴",
        r"밴드\s*(?:상단|하단|수축|확장|중심|중앙)",
        r"밴드\s*(?:터치|이탈|돌파)",
    ],
    # 4) 거래량
    "volume": [
        r"거래량",
        r"매집",
        r"세력\s*(?:매집|진입|개입|유입)",
        r"물량\s*(?:출회|집중|터짐|소화)",
        r"대(?:량|형)\s*거래",
        r"거래량\s*(?:터짐|폭발|급증|증가|감소|바닥|수렴|실종|말라)",
        r"\d+\s*(?:배|배수)",
    ],
    # 5) 매수 조건 / 진입 타이밍
    "buy_entry": [
        r"매수(?:\s*타이밍|\s*조건|\s*자리|\s*시점|가능|영역|신호|진입)?",
        r"진입(?:\s*타이밍|\s*자리|\s*시점|\s*조건|점|가능)?",
        r"들어(?:가|갈)",
        r"담(?:는|을|기|아)",
        r"불(?:타|타기)",
        r"분할\s*매수",
        r"추격\s*매수",
        r"저점\s*매수",
        r"눌림(?:\s*매수|목|목매수|목자리)?",
        r"지지(?:선|받|확인|라인)",
    ],
    # 6) 손절 / 리스크
    "stop_loss": [
        r"손절",
        r"로스컷",
        r"컷(?:팅|오프)?",
        r"손실\s*(?:제한|한정|최소화)",
        r"이탈\s*시",
        r"1차\s*손절|2차\s*손절|이중\s*손절",
        r"\-\s*\d+\s*%|\-\s*\d+\.\d+\s*%",
        r"(?:손절|컷)\s*(?:라인|선|가|자리)",
    ],
    # 7) 채널 / 패턴
    "channel_pattern": [
        r"채널",
        r"추세(?:선|대|)?",
        r"쐐기",
        r"박스(?:권|대)?",
        r"삼각\s*수렴",
        r"수렴(?:형|대|점|끝)?",
        r"상승\s*채널|하락\s*채널|수평\s*채널|우상향|우하향",
        r"지지\s*저항",
        r"저항(?:선|대)?",
    ],
    # 8) 세력 판단
    "sejryeok": [
        r"세력",
        r"주포|작전\s*세력",
        r"손(?:바뀜|절매|절 물량)",
        r"기관(?:\s*매수|\s*매도|\s*진입)?",
        r"외국인(?:\s*매수|\s*매도)?",
        r"거래원",
        r"수급",
        r"매집\s*(?:완료|흔적|구간)",
    ],
    # 9) 기타 기법 (목표가, 분할, 지지저항)
    "other_technique": [
        r"목표\s*가",
        r"목표\s*(?:가격|수익률)",
        r"1차\s*목표|2차\s*목표|3차\s*목표",
        r"분할\s*(?:매수|매도|익절)",
        r"익절",
        r"피보나치",
        r"엘리엇",
        r"파동",
        r"갭(?:\s*상승|\s*하락|\s*메우|채우)?",
        r"추세\s*전환",
        r"전\s*고점|전\s*저점|신\s*고가|신\s*저가",
        r"저항\s*돌파|지지\s*이탈",
    ],
    # 10) 단테 특유 어투/개념
    "dante_signature": [
        r"여러분",
        r"제가\s*말씀",
        r"진짜\s*(?:중요|핵심)",
        r"반드시|무조건|절대",
        r"꼭\s*기억",
        r"단테\s*(?:기법|매매|식|원칙|공식)",
        r"나의\s*매매\s*(?:원칙|공식|기준)",
        r"내\s*매매\s*(?:원칙|공식|기준)",
        r"멘탈|심리|뇌동매매|대응",
        r"안\s*깨(?:지|질|져)",
        r"끼\s*얹(?:기|어)",
        r"끼\s*얹는",
    ],
}

# Precompile
COMPILED: Dict[str, List[re.Pattern]] = {
    cat: [re.compile(p) for p in pats] for cat, pats in CATEGORY_PATTERNS.items()
}


# Sentence splitter for Korean — split on full stops, question marks, newlines
SENTENCE_SPLIT = re.compile(r'(?<=[\.\?\!。])\s+|\n+')


def sentences(text: str) -> List[str]:
    out = []
    for chunk in SENTENCE_SPLIT.split(text):
        s = chunk.strip()
        if len(s) >= 6:
            out.append(s)
    return out


def extract_from_file(path: Path) -> Dict[str, List[Dict]]:
    """Return {category: [{sentence, prev, next}, ...]} for one file."""
    text = parse_vtt(path)
    sents = sentences(text)

    hits: Dict[str, List[Dict]] = {cat: [] for cat in COMPILED}

    for i, s in enumerate(sents):
        for cat, pats in COMPILED.items():
            if any(p.search(s) for p in pats):
                hits[cat].append({
                    "sentence": s,
                    "prev": sents[i - 1] if i > 0 else "",
                    "next": sents[i + 1] if i + 1 < len(sents) else "",
                    "idx": i,
                })
                # Don't break — one sentence may fit multiple categories

    return hits


def extract_batch(files: List[Path], batch_id: int, out_path: Path) -> Dict:
    """Process a batch of files, write combined results to ``out_path``.

    Returns a small summary dict used by the orchestrator log.
    """
    batch_result = {
        "batch_id": batch_id,
        "files": [],
        "by_category": {cat: [] for cat in COMPILED},
    }

    for fp in files:
        try:
            hits = extract_from_file(fp)
        except Exception as e:
            batch_result["files"].append({"name": fp.name, "error": str(e)})
            continue

        file_summary = {"name": fp.name, "counts": {c: len(v) for c, v in hits.items()}}
        batch_result["files"].append(file_summary)

        for cat, items in hits.items():
            for it in items:
                it2 = dict(it)
                it2["source"] = fp.name
                batch_result["by_category"][cat].append(it2)

    out_path.write_text(json.dumps(batch_result, ensure_ascii=False, indent=1), encoding='utf-8')
    summary = {
        "batch_id": batch_id,
        "file_count": len(files),
        "total_hits": sum(len(v) for v in batch_result["by_category"].values()),
        "per_cat": {c: len(v) for c, v in batch_result["by_category"].items()},
    }
    return summary
