"""Check agent emails against the BLUF format (House rule H11).

Usage: python evals/email_check.py FILE [FILE ...]
Exit code 0 only when every file passes.

Rules checked:
  R1  Text above the DETAIL line is 180 words or fewer (URLs and mailto links are not counted).
  R2  Flesch reading ease of the text above DETAIL is 60 or more.
  R3  3 items or fewer.
  R4  Each item has Found, Your link, Do, Test prompt and Source lines.
  R5  Your link names a project from docs/PROJECTS.md.
  R6  Do starts with ACT NOW, LATER or DROP and gives a why.
  R7  Source holds a link.
  R8  Each item has a YES/NO mailto pair.
  R9  Each Test prompt Tn has a copy box below DETAIL: 600 chars or fewer, branch-only / read-only / spike,
      and it ends with one PASS/FAIL check.
"""
import re
import sys
from pathlib import Path

import textstat  # pin textstat==0.7.4 (0.7.5+ downloads cmudict at run time)

ROOT = Path(__file__).resolve().parent.parent
PROJECTS_MD = ROOT / "docs" / "PROJECTS.md"
MAX_WORDS = 180
MIN_FLESCH = 60.0
MAX_ITEMS = 3
MAX_PROMPT = 600
FIELDS = ("Found:", "Your link:", "Do:", "Test prompt:", "Source:")

URL = re.compile(r"(?:https?://|mailto:)\S+")
MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
ITEM_HEAD = re.compile(r"^(\d)\.\s+\S", re.M)
FENCE = re.compile(r"^(T\d)\b.*?\n```[a-z]*\n(.*?)\n```", re.M | re.S)


def project_names():
    names = re.findall(r"^## (.+?) — `", PROJECTS_MD.read_text(encoding="utf-8"), re.M)
    return [n.strip() for n in names]


def body_of(text):
    """Drop the Subject/Date header block above the first '---' line."""
    parts = re.split(r"^---\s*$", text, maxsplit=1, flags=re.M)
    return parts[1] if len(parts) == 2 else text


def plain(text):
    text = MD_LINK.sub(r"\1", text)
    text = URL.sub("", text)
    text = re.sub(r"[`*_|]", " ", text)
    return text


def split_items(top):
    heads = list(ITEM_HEAD.finditer(top))
    items = []
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(top)
        items.append((m.group(1), top[m.start():end]))
    return items


def check(path, projects):
    errors = []
    body = body_of(Path(path).read_text(encoding="utf-8"))
    m = re.search(r"^DETAIL\s*$", body, re.M)
    if not m:
        return ["no DETAIL line"], {}
    top, detail = body[: m.start()], body[m.end():]
    readable = plain(top)
    words = len(re.findall(r"[A-Za-z0-9][\w'’.%£$/-]*", readable))
    flesch = textstat.flesch_reading_ease(readable)
    if words > MAX_WORDS:
        errors.append(f"R1 {words} words above DETAIL (max {MAX_WORDS})")
    if flesch < MIN_FLESCH:
        errors.append(f"R2 Flesch {flesch:.1f} (min {MIN_FLESCH})")

    items = split_items(top)
    if len(items) > MAX_ITEMS:
        errors.append(f"R3 {len(items)} items (max {MAX_ITEMS})")
    boxes = {k: v for k, v in FENCE.findall(detail)}
    for num, block in items:
        tag = f"item {num}"
        for f in FIELDS:
            if not re.search(rf"^{re.escape(f)}", block, re.M):
                errors.append(f"R4 {tag} has no '{f}' line")
        link = re.search(r"^Your link:(.*)$", block, re.M)
        if link and not any(p.lower() in link.group(1).lower() for p in projects):
            errors.append(f"R5 {tag} Your link names no project from PROJECTS.md")
        do = re.search(r"^Do:\s*(ACT NOW|LATER|DROP)\b\s*[—:-]\s*(\S.*)$", block, re.M)
        if not do:
            errors.append(f"R6 {tag} Do is not 'ACT NOW|LATER|DROP — why'")
        src = re.search(r"^Source:(.*)$", block, re.M)
        if src and not URL.search(src.group(1)):
            errors.append(f"R7 {tag} Source has no link")
        if not (re.search(r"mailto:\S*YES", block) and re.search(r"mailto:\S*NO", block)):
            errors.append(f"R8 {tag} has no YES/NO mailto pair")
        tp = re.search(r"^Test prompt:\s*(T\d)", block, re.M)
        if not tp:
            errors.append(f"R9 {tag} Test prompt does not name a copy box (T1, T2, T3)")
            continue
        box = boxes.get(tp.group(1))
        if box is None:
            errors.append(f"R9 {tag} copy box {tp.group(1)} not found below DETAIL")
            continue
        if len(box) > MAX_PROMPT:
            errors.append(f"R9 {tag} {tp.group(1)} is {len(box)} chars (max {MAX_PROMPT})")
        if not re.search(r"\b(read-only|spike|branch)\b", box, re.I):
            errors.append(f"R9 {tag} {tp.group(1)} is not branch-only, read-only or spike")
        last = box.strip().splitlines()[-1]
        if not re.search(r"\bPASS\b.*\bFAIL\b|\bPASS if\b", last):
            errors.append(f"R9 {tag} {tp.group(1)} does not end with a PASS/FAIL check")
    return errors, {"words": words, "flesch": round(flesch, 1), "items": len(items)}


def main(paths):
    projects = project_names()
    failed = 0
    for p in paths:
        errors, stats = check(p, projects)
        status = "PASS" if not errors else "FAIL"
        failed += bool(errors)
        s = " ".join(f"{k}={v}" for k, v in stats.items())
        print(f"{status} {Path(p).name} {s}")
        for e in errors:
            print(f"     - {e}")
    print(f"\n{len(paths) - failed}/{len(paths)} pass")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
