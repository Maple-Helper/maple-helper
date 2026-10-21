"""Edits assets/live/config.json (the owner's announcements, switches, lowest supported version, text fixes) and
writes assets/live.json again. Merged to main, a change reaches every open app within about 20 minutes (docs/LIVE.md).

    python tools/live_edit.py show
    python tools/live_edit.py announce --id launch-party --he "..." --en "..." [--title-he ... --title-en ...]
           [--link https://... --link-he ... --link-en ...] [--from 2026-10-25] [--until 2026-10-30]
           [--min-app 0.19.0] [--max-app 0.20.0] [--lang he|en] [--warning] [--toast]
    python tools/live_edit.py remove --id launch-party
    python tools/live_edit.py flag mesowatch off        (on / off; "python tools/live_edit.py flags" lists them)
    python tools/live_edit.py min-app 0.19.0            ("" = every version)
    python tools/live_edit.py string err_generic --he "..." [--en "..."]
    python tools/live_edit.py unstring err_generic
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from maplehelper import live  # noqa: E402
from maplehelper.i18n import STRINGS  # noqa: E402

CONFIG = ROOT / "assets" / live.CONFIG


def load() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def save(cfg: dict) -> None:
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print("saved assets/live/config.json")
    import live_manifest
    live_manifest.write()


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show")
    sub.add_parser("flags")
    a = sub.add_parser("announce")
    a.add_argument("--id", required=True)
    for f in ("he", "en", "title-he", "title-en", "link", "link-he", "link-en", "from", "until", "min-app",
              "max-app", "lang"):
        a.add_argument(f"--{f}", default="")
    a.add_argument("--warning", action="store_true")
    a.add_argument("--toast", action="store_true")
    r = sub.add_parser("remove")
    r.add_argument("--id", required=True)
    f = sub.add_parser("flag")
    f.add_argument("name")
    f.add_argument("state", choices=("on", "off"))
    m = sub.add_parser("min-app")
    m.add_argument("version")
    s = sub.add_parser("string")
    s.add_argument("key")
    s.add_argument("--he", default="")
    s.add_argument("--en", default="")
    u = sub.add_parser("unstring")
    u.add_argument("key")
    args = ap.parse_args(argv)
    cfg = load()

    if args.cmd == "show":
        print(json.dumps(cfg, ensure_ascii=False, indent=1))
        return 0
    if args.cmd == "flags":
        for k, v in cfg.get("flags", {}).items():
            print(f"{k:16} {'on' if v is not False else 'OFF'}")
        return 0
    if args.cmd == "announce":
        if not (args.he or args.en):
            sys.exit("an announcement needs --he or --en")
        if args.link and not live.SAFE_LINK.fullmatch(args.link):
            sys.exit("--link must be an https:// address")
        for d in (getattr(args, "from"), args.until):
            if d and live._when(d, False) is None:
                sys.exit(f"{d!r}: write a date as 2026-10-25 or 2026-10-25 18:00")
        item = {"id": args.id}
        for key, val in (("he", args.he), ("en", args.en), ("title_he", args.title_he), ("title_en", args.title_en),
                         ("link", args.link), ("link_text_he", args.link_he), ("link_text_en", args.link_en),
                         ("from", getattr(args, "from")), ("until", args.until), ("min_app", args.min_app),
                         ("max_app", args.max_app), ("lang", args.lang)):
            if val:
                item[key] = val
        if args.warning:
            item["level"] = "warning"
        if args.toast:
            item["toast"] = True
        rows = [x for x in cfg.get("announcements", []) if x.get("id") != args.id]
        cfg["announcements"] = rows + [item]
    elif args.cmd == "remove":
        before = len(cfg.get("announcements", []))
        cfg["announcements"] = [x for x in cfg.get("announcements", []) if x.get("id") != args.id]
        if len(cfg["announcements"]) == before:
            sys.exit(f"no announcement {args.id!r}")
    elif args.cmd == "flag":
        if args.name not in live.FLAGS:
            sys.exit(f"{args.name!r} switches nothing. The switches: {', '.join(live.FLAGS)}")
        cfg.setdefault("flags", {})[args.name] = args.state == "on"
    elif args.cmd == "min-app":
        if args.version and not re.fullmatch(r"\d+\.\d+(\.\d+)?", args.version):
            sys.exit("write a version as 0.19.0")
        cfg["min_app"] = args.version
    elif args.cmd == "string":
        if args.key not in STRINGS:
            sys.exit(f"{args.key!r} is no UI text")
        row = {k: v for k, v in (("he", args.he), ("en", args.en)) if v}
        field = re.compile(r"\{(\w*)\}")
        for lang, text in row.items():
            want = sorted(field.findall(STRINGS[args.key].get(lang) or STRINGS[args.key].get("en", "")))
            if sorted(field.findall(text)) != want:
                sys.exit(f"the {lang} text must keep the same placeholders: {want}")
        if not row:
            sys.exit("give --he and/or --en")
        cfg.setdefault("strings", {})[args.key] = row
    elif args.cmd == "unstring":
        cfg.get("strings", {}).pop(args.key, None)
    save(cfg)
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(main(sys.argv[1:]))
