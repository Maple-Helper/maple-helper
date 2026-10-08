"""Correct display of mixed Hebrew/English text.

Rules (spec, "Hebrew, English and RTL"):
- Every paragraph gets its own direction from its first strong character, not
  from the UI language.
- Inside an RTL paragraph, every English/number run ("Red Snail", "Lv. 10",
  "+5 STR", "10–20%") is wrapped in a left-to-right embedding (LRE…PDF), so it
  behaves as one closed block: brackets, trailing punctuation and signs land
  where a Hebrew reader expects them.
  (Unicode isolates LRI…PDI would be the modern choice, but Qt's text engine
  does not honor them inside these runs; tests/test_bidi.py measures real glyph positions.)
- A whole English name (a quest's "[Area] Name") goes in one LRI…PDI block
  (ltr_block / ltr_name), also measured in tests/test_bidi.py.
"""
from __future__ import annotations

import html
import re

LRE, PDF, RLM = "‪", "‬", "‏"  # left-to-right embedding, pop, right-to-left mark
LRI, PDI = "⁦", "⁩"            # left-to-right isolate, pop (for a whole English name, see ltr_name)
RLI = "⁧"                  # right-to-left isolate: a Hebrew name inside an English line (name_block)
_ISOLATED = re.compile(f"({LRI}[^{PDI}]*{PDI})")
_ANY_ISOLATE = re.compile(f"[{LRI}{RLI}][^{PDI}]*{PDI}")
RTL_CHARS = "֐-׿؀-ۿיִ-﷿ﹰ-﻿"
_STRONG = re.compile(rf"[A-Za-z{RTL_CHARS}]")
_RTL = re.compile(rf"[{RTL_CHARS}]")

# An LTR run: starts with a Latin letter, a digit, a "[" or a sign followed by a digit;
# may contain spaces and inner punctuation; ends with a letter, digit, %, ) or ].
_RUN = re.compile(
    # start: a letter, a digit, a "[" or '"' before a letter/digit ("[Construction Site B1] ..."), or a
    # sign/$/# right before a digit; but a hyphen glued to a Hebrew letter ("ב-84%", "ל-30") is the Hebrew
    # prefix hyphen, not a minus sign
    rf"(?:(?<![{RTL_CHARS}])[+\-±](?=\d)|[$#](?=\d)|[\[\"](?=[A-Za-z0-9])|\d|[A-Za-z])"
    # body; "1,500" keeps its comma, a@b.com its @, "11:41" its colon; ": " ends the block
    # ("ה-AI: Claude או Codex" is two blocks, not "AI: Claude" read backwards)
    r"(?:(?:[A-Za-z0-9.'’&/+\-–%#×_  ()\[\]\"@<>→]|:(?! )|,(?=\d{3}\b))*"   # (no-break space: i18n.WHOLE_NAMES)
    # "→" too: "Henesys → Ellinia → Perion" is one route, as "->" already was (three runs showed it
    # backwards in a Hebrew line, the review UI-1)
    r"(?:[A-Za-z0-9%)\]\">]|(?<=\d)\+))?"        # "Line 2 <Area 1>" stays one map name; "ACC 40+" keeps its +
)

_OPEN, _CLOSE = "([", ")]"


def _balanced(run: str) -> str:
    """Trim a run so its brackets and quotes are balanced: 'Ellinia (Victoria Road)' and '[Area] Name' stay
    whole, 'Lv. 10)' loses the stray ')', 'Axe Stump (' the dangling '(' and 'Red Snail"' its lone quote."""
    depth, last_ok, quoted = 0, 0, False
    for i, ch in enumerate(run):
        if ch in _OPEN:
            depth += 1
        elif ch in _CLOSE:
            if depth == 0:
                break
            depth -= 1
        elif ch == '"':
            quoted = not quoted
        if depth == 0 and not quoted and (ch.isalnum() or ch in '%)]>"' or (ch == "+" and i and run[i - 1].isdigit())):
            last_ok = i + 1
    return run[:last_ok]


# KB names that the run rules above can't keep whole: "Tree Dungeon, Monkey Forest I" (", " splits a run, see
# "Red Snail, Blue Snail"), "Final Attack: Sword" (": " ends a block). The knowledge base registers its names
# here (set_names); an RTL paragraph shows each one as a single left-to-right block (ltr_block).
_BREAKS = re.compile(r', |: |[\[\]"]')
_NAMES: re.Pattern | None = None


def set_names(names) -> None:
    """The English names (from the knowledge base) to keep as one block in a Hebrew line."""
    global _NAMES
    keep = sorted({n for n in names if n and not _RTL.search(n) and _BREAKS.search(n)}, key=len, reverse=True)
    _NAMES = re.compile(r"(?<![\w\[])(?:" + "|".join(map(re.escape, keep)) + r")(?![\w\]])") if keep else None


def _block_names(text: str) -> str:
    if _NAMES is None or not _BREAKS.search(text):
        return text
    return _NAMES.sub(lambda m: f"{LRI}{m.group(0)}{PDI}", text)


_WORD = re.compile(rf"[A-Za-z{RTL_CHARS}]+")


def direction(text: str) -> str:
    """'rtl' or 'ltr' for one paragraph.

    Starts from the first strong character (Unicode rule P2), but a Hebrew
    sentence that merely opens with an English name ("Red Snail הוא…",
    "(Lv. 10) מפלצת") stays RTL: if at least 40% of its words are Hebrew,
    the paragraph is Hebrew. A name in its own isolate (name_block) doesn't count, as in Unicode's rule.
    """
    rest = _ANY_ISOLATE.sub(" ", text)
    text = rest if _STRONG.search(rest) else text          # (only a name: its own direction)
    m = _STRONG.search(text)
    if not m:
        return "ltr"
    if _RTL.match(m.group(0)):
        return "rtl"
    words = _WORD.findall(text)
    rtl_words = sum(1 for w in words if _RTL.match(w))
    return "rtl" if words and rtl_words / len(words) >= 0.4 else "ltr"


# "מ-10% → 25%", "COT1 -> COT2": the two values and the arrow as one left-to-right block, as sitedata.change_text
# writes them. As two runs in a Hebrew line the arrow pointed at the old value ("25% → 10%", read backwards)
# A side is a value, never a word: "any letters" cut a name at its first space and paired "Blue Snail → Red Snail"
# as "Snail → Red" (the review, UI-1). Names are blocked first (set_names); a whole chain "10 → 20 → 30" is one block
_ARROW_SIDE = (rf"(?:Lv\.[ \u00a0]?)?(?:(?<![{RTL_CHARS}])[+\-]|\$)?\d(?:[\d.,:/×%\-–]*[\d%])?[KMx×%]?"
               r"|COT\d|Launch|MSEA")
_ARROW = re.compile(rf"(?<![A-Za-z0-9.,%])(?:{_ARROW_SIDE})(?:[ \u00a0]?(?:→|->)[ \u00a0]?(?:{_ARROW_SIDE}))+"
                    r"(?![A-Za-z0-9%])")
_ARROW_GAP = re.compile("[ \u00a0]?(→|->)[ \u00a0]?")


def _arrow_block(m: re.Match) -> str:
    return LRI + _ARROW_GAP.sub(lambda a: "\u00a0" + a.group(1) + "\u00a0", m.group(0)) + PDI


def _block_arrows(text: str) -> str:
    return _ARROW.sub(_arrow_block, text) if "→" in text or "->" in text else text


_ROUTE_END = r"[A-Za-z0-9][A-Za-z0-9 .'’&()\-→]*[A-Za-z0-9)]"
_ROUTE_GAP = "[  ]?(?:→|->)[  ]?"


def _join_routes(text: str) -> str:
    """A name block next to an arrow: the route is one block ("A, B → C" read "C → A, B" in a Hebrew line)."""
    text = re.sub(rf"{PDI}({_ROUTE_GAP}){LRI}", lambda m: m.group(1), text)
    text = re.sub(rf"{PDI}({_ROUTE_GAP}{_ROUTE_END})(?![A-Za-z0-9])", lambda m: m.group(1) + PDI, text)
    text = re.sub(rf"(?<![A-Za-z0-9])({_ROUTE_END}{_ROUTE_GAP}){LRI}", lambda m: LRI + m.group(1), text)
    return text


def isolate_ltr_runs(text: str) -> str:
    """Wrap English/number runs in LRE…PDF. Only for RTL paragraphs.
    A name already isolated as one block (ltr_block), or a KB name the runs would split (set_names), is kept
    as one block, and so is an "old → new" pair."""
    if _NAMES is not None:      # names first: an arrow between two names leaves both whole
        text = "".join(part if part.startswith(LRI) else _block_names(part) for part in _ISOLATED.split(text))
        text = _join_routes(text)
    text = "".join(part if part.startswith(LRI) else _block_arrows(part) for part in _ISOLATED.split(text))
    if LRI in text:
        return "".join(part if part.startswith(LRI) else _isolate_runs(part) for part in _ISOLATED.split(text))
    return _isolate_runs(text)


KEEP_TOGETHER = 28      # an English piece up to this long never wraps inside a Hebrew line


def _keep_together(run: str) -> str:
    """A short English piece in a Hebrew line stays on one line: wrapped inside, its two halves landed on two
    lines in mirrored order ("HP/" at one line's end and "MP +10" on the next, seen live). No-break spaces, and
    a word joiner after each "/" (a line may break after a slash)."""
    if len(run) > KEEP_TOGETHER:
        # a long route: each stop stays whole and the line breaks only after an arrow (as one long run it broke
        # inside "(Lv. 35)", review2 UI2-4)
        if "→" in run or "->" in run:
            stops = "".join(p if p in ("→", "->") else _keep_together(p.strip()) for p in re.split(r"(→|->)", run))
            return re.sub(r"\s*(→|->)\s*", lambda m: " " + m.group(1) + " ", stops)
        return run
    return run.replace(" ", " ").replace("/", "/⁠")


def _isolate_runs(text: str) -> str:
    out, pos = [], 0
    for m in _RUN.finditer(text):
        run = _balanced(m.group(0).rstrip(" "))
        start, end = m.start(), m.start() + len(run)
        if not run:
            continue
        before = text[pos:start]
        # a Hebrew prefix with its hyphen ("ל-", "מ-", "ב-") never ends a line: the line broke after "ל-" and the
        # English or the number went to the next line, out of order ("ל-" ... "10,500 mesos", the owner's report)
        if re.search(r"(?:^|[^\u0590-\u05FF])[\u05D1\u05D4\u05D5\u05DB\u05DC\u05DE\u05E9]{1,2}-$", before):
            before += "\u2060"
        out.append(before)
        # the RLM after the block keeps following punctuation (") - ", ", ") in the Hebrew flow,
        # so two English blocks never glue into one left-to-right chunk
        # Qt mirrors a ">" that follows a digit inside a Hebrew line ("Line 2 <Area 1>" shows "<Area 1<"),
        # so map names with an <area> suffix are shown as "Line 2 · Area 1"
        shown = _keep_together(re.sub(r"\s*<([^<>]+)>", r" · \1", run))
        out.append(f"{LRE}{shown}{PDF}{RLM}")
        pos = end
    out.append(text[pos:])
    return "".join(out)


# Markdown an AI answer may still carry (the prompt asks for none): "## Mano", "| Item | Chance |", "`Avoid`",
# "*Tip:*" and "[MeowDB](https://…)" showed as literal marks, and in a Hebrew line the run splitter cut a URL apart
# and reordered its pieces ("(000https://meowdb.com/monster/2220][MeowDB]", the UX audit CHAT-01)
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_RULE = re.compile(r"^\s*([-*_])(?:\s*\1){2,}\s*$")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?\s*$")
_MD_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)")
_URL = re.compile(r"https?://[^\s<>\"'\]\[)(*]+")      # (no "*": a bold link's ** stays outside it)
_ITALIC = re.compile(r"(?<![*\w])\*(?=[^\s*])([^*\n]+?)(?<=[^\s*])\*(?![*\w])")
_LINK_SLOT = 0xE000      # a private-use character holds a link's place while the line is isolated and escaped


def markdown_lines(text: str) -> str:
    """An answer's block markdown as plain lines: a heading becomes a bold line, a pipe table one "a · b" line per
    row (its header bold), and code fences and rules go."""
    lines, out = text.split("\n"), []
    for i, line in enumerate(lines):
        if _FENCE.match(line) or _RULE.match(line) or (_TABLE_SEP.match(line) and "|" in line):
            continue
        m = _HEADING.match(line)
        if m:
            out.append(f"**{m.group(1).strip('* ')}**")
        elif _TABLE_ROW.match(line):
            row = " · ".join(c for c in (c.strip() for c in line.strip().strip("|").split("|")) if c)
            header = i + 1 < len(lines) and bool(_TABLE_SEP.match(lines[i + 1]))     # the row over the |---| line
            out.append(f"**{row.replace('**', '')}**" if header and row else row)
        else:
            out.append(line)
    return "\n".join(out)


def _links(line: str, d: str) -> tuple[str, list[tuple[str, str]]]:
    """[text](url) and bare URLs out of a line: each becomes one private-use character (restored by _restore),
    wrapped as one left-to-right block when its text is English and the paragraph Hebrew, so the run splitter
    never cuts it."""
    found: list[tuple[str, str]] = []

    def slot(shown: str, url: str) -> str:
        found.append((shown, url.replace("​", "")))
        mark = chr(_LINK_SLOT + len(found) - 1)
        rtl_text = bool(_RTL.search(shown))
        if d == "rtl" and not rtl_text:
            return f"{LRI}{mark}{PDI}"
        if d == "ltr" and rtl_text:
            return f"{RLI}{mark}{PDI}"
        return mark

    def bare(m: re.Match) -> str:
        url = m.group(0)
        tail = re.search(r"[.,;:!?]+$", url)          # "see https://meowdb.com." keeps its full stop outside
        if tail:
            url = url[:tail.start()]
        return slot(url, url) + (tail.group(0) if tail else "")

    line = _MD_LINK.sub(lambda m: slot(m.group(1).strip("`*"), m.group(2)), line)
    line = _URL.sub(bare, line)
    return line, found


def _restore(body: str, found: list[tuple[str, str]]) -> str:
    for i, (shown, url) in enumerate(found):
        # a long URL shown as itself still wraps (a break opportunity every 20 characters, never in the link)
        text = html.escape(shown)
        if shown == url and len(text) > 30:
            text = "​".join(text[j:j + 20] for j in range(0, len(text), 20))
        body = body.replace(chr(_LINK_SLOT + i), f'<a href="{html.escape(url, quote=True)}">{text}</a>')
    return body


def paragraph_html(line: str, d: str | None = None, md: bool = False, style: str = "margin:0 0 4px 0;") -> str:
    """One paragraph → HTML with its own dir/alignment; **bold** supported.
    md: an AI answer: links clickable and kept whole, backticks dropped, *italic* (markdown_lines does the blocks)."""
    d = d or direction(line)
    found: list[tuple[str, str]] = []
    if md:
        line, found = _links(line.replace("`", ""), d)
    body = isolate_ltr_runs(line) if d == "rtl" else line
    body = html.escape(body)
    body = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", body)
    if md:
        body = _ITALIC.sub(r"<i>\1</i>", body)
        body = _restore(body, found)
    align = "right" if d == "rtl" else "left"
    return f'<p dir="{d}" align="{align}" style="{style}">{body}</p>'


def paragraph_direction(line: str, message_dir: str) -> str:
    """Inside a Hebrew message, a line is LTR only if it is a real English sentence
    (4+ English words, no Hebrew). "• HP: 371" or "Axe Stump (לבל 17)" stay RTL."""
    if message_dir == "ltr":
        return direction(line)
    if _RTL.search(line):
        return "rtl"
    return "ltr" if len(re.findall(r"[A-Za-z]{2,}", line)) >= 4 and re.search(r"[.?!]\s*$", line) else "rtl"


def message_direction(text: str) -> str:
    """A whole message is Hebrew when a fifth of its words are Hebrew
    (answers are full of English names, so a majority rule would misfire)."""
    words = _WORD.findall(text)
    rtl_words = sum(1 for w in words if _RTL.match(w))
    return "rtl" if words and rtl_words / len(words) >= 0.2 else "ltr"


def to_html(text: str, msg_dir: str | None = None, md: bool = False) -> str:
    """Multi-paragraph message → HTML. Blank lines become small gaps; bullets keep their marker.
    msg_dir: the message's language when the caller knows it (an instant answer is written in the UI's language;
    its list of English map names outvoted its one Hebrew line, and the list went left).
    md: an AI answer: its markdown shown as text, not marks (markdown_lines, paragraph_html)."""
    if md:
        text = markdown_lines(text)
    msg_dir = msg_dir or message_direction(text)
    parts = []
    for line in text.strip().split("\n"):
        line = line.rstrip()
        if not line.strip():
            parts.append('<p style="margin:0; font-size:4px;">&nbsp;</p>')
            continue
        line = re.sub(r"^\s*[-*•]\s+", "• ", line)
        parts.append(paragraph_html(line, paragraph_direction(line, msg_dir), md))
    return "".join(parts)


def plain(text: str, rtl_ui: bool = False) -> str:
    """For single-line labels (plain text). In a Hebrew UI a label is always RTL
    (an RLM mark fixes its direction even when it starts with an English name)."""
    if rtl_ui or direction(text) == "rtl":
        # RLM on both ends: Qt buttons lay text out LTR regardless of the widget's
        # direction; the trailing mark keeps final punctuation ("?") on the left.
        return RLM + isolate_ltr_runs(text) + RLM
    return text


def ltr_block(name: str, rtl_ui: bool) -> str:
    """An English name (no Hebrew in it) as one left-to-right block for a Hebrew sentence. Run by run,
    "[Construction Site B1] Shumi's Lost Coin" came out with its brackets thrown to the other end."""
    if rtl_ui and name and not _RTL.search(name):
        return f"{LRI}{name}{PDI}"
    return name


def name_block(name: str, rtl_ui: bool) -> str:
    """A name (a character's, a quest's) as one block in a sentence of the UI language: an English name in a
    Hebrew sentence (ltr_block), or a Hebrew name in an English one ("אליפז: level 3 → 5" was laid out right to
    left, the whole line reversed)."""
    if rtl_ui:
        return ltr_block(name, True)
    if name and _RTL.search(name):
        return f"{RLI}{name}{PDI}"
    return name


def ltr_name(name: str, rtl_ui: bool) -> str:
    """A label that is only a name: one block when it's English, else plain()."""
    if rtl_ui and name and not _RTL.search(name):
        return RLM + ltr_block(name, rtl_ui) + RLM
    return plain(name, rtl_ui)
