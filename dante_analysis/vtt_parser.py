"""Parse YouTube VTT captions into deduplicated plain text."""
import re
from pathlib import Path

TIMESTAMP_RE = re.compile(r'^\d{2}:\d{2}:\d{2}\.\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}\.\d{3}.*$')
INLINE_TS_RE = re.compile(r'<\d{2}:\d{2}:\d{2}\.\d{3}>')
TAG_RE = re.compile(r'</?c[^>]*>')


def parse_vtt(path: Path) -> str:
    """Return deduplicated spoken text from a VTT file.

    YouTube auto-captions emit rolling pairs of lines (previous + current).
    We collect the current cue line per timestamp block, strip inline
    timing tags, then dedupe consecutive identical lines.
    """
    try:
        text = path.read_text(encoding='utf-8', errors='ignore')
    except Exception:
        return ''

    lines = text.splitlines()
    cues = []
    i = 0
    while i < len(lines):
        if TIMESTAMP_RE.match(lines[i]):
            # Collect cue body (next non-empty lines until blank or next timestamp)
            i += 1
            body = []
            while i < len(lines) and lines[i].strip() and not TIMESTAMP_RE.match(lines[i]):
                body.append(lines[i])
                i += 1
            if body:
                # Prefer the LAST body line (has the newly-added words with inline timestamps)
                raw = body[-1]
                clean = INLINE_TS_RE.sub('', raw)
                clean = TAG_RE.sub('', clean).strip()
                if clean:
                    cues.append(clean)
        else:
            i += 1

    # Dedup consecutive identical / containing lines
    dedup = []
    for c in cues:
        if dedup and (c == dedup[-1] or c in dedup[-1]):
            continue
        if dedup and dedup[-1] in c:
            # new line extends previous, replace
            dedup[-1] = c
            continue
        dedup.append(c)

    return '\n'.join(dedup)


if __name__ == '__main__':
    import sys
    p = Path(sys.argv[1])
    print(parse_vtt(p))
