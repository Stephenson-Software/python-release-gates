"""Turn the captured gate output in src/ into HTML pages that media.json screenshots.

    python3 docs/screenshots/render.py        # writes docs/screenshots/build/*.html

src/*.txt is a terminal transcript, exactly as the commands printed it (lines starting `$ `
are the commands). src/*.summary.md is what gates/summarize.py printed for that run's
result.json, the Markdown the workflows write to the run summary. Standard library only.
"""

import html
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC, OUT = HERE / "src", HERE / "build"

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>{title}</title><style>
body{{margin:0;padding:24px;background:#eef1f5;font:14px/1.5 -apple-system,"Segoe UI",Helvetica,Arial,sans-serif}}
.shot{{display:inline-flex;flex-direction:column;gap:20px;max-width:1180px}}
.term{{background:#0d1117;color:#c9d1d9;border-radius:10px;box-shadow:0 6px 20px rgba(0,0,0,.25);overflow:hidden}}
.bar{{background:#21262d;padding:8px 12px;display:flex;gap:7px;align-items:center;color:#8b949e;font-size:12px}}
.dot{{width:11px;height:11px;border-radius:50%;background:#484f58}}
.bar span.t{{margin-left:10px}}
pre{{margin:0;padding:14px 18px 16px;font:13px/1.55 "DejaVu Sans Mono",Menlo,Consolas,monospace;white-space:pre-wrap;word-break:break-all}}
.cmd{{color:#79c0ff}} .pass{{color:#3fb950;font-weight:bold}} .fail{{color:#f85149;font-weight:bold}} .dim{{color:#8b949e}}
.sum{{background:#fff;border:1px solid #d0d7de;border-radius:8px;padding:6px 20px 16px;color:#1f2328}}
.sum h2{{font-size:20px;border-bottom:1px solid #d8dee4;padding-bottom:6px}}
.sum code{{background:#eff1f3;padding:2px 5px;border-radius:5px;font:12px "DejaVu Sans Mono",monospace}}
.sum table{{border-collapse:collapse}} .sum th,.sum td{{border:1px solid #d0d7de;padding:5px 12px;text-align:left}}
.sum th{{background:#f6f8fa}}
</style></head><body><div class="shot">{body}</div></body></html>"""


def terminal(path: Path) -> str:
    lines, continued = [], False
    for raw in path.read_text().splitlines():
        line = html.escape(raw)
        if raw.startswith("$ ") or continued:
            continued = raw.endswith("\\")
            line = f'<span class="cmd">{line}</span>'
        elif raw.startswith("PASS "):
            line = re.sub(r"^PASS (\S+:)", r'<span class="pass">PASS</span> <b>\1</b>', line)
        elif raw.startswith("FAIL "):
            line = re.sub(r"^FAIL (\S+:)", r'<span class="fail">FAIL</span> <b>\1</b>', line)
        else:
            line = f'<span class="dim">{line}</span>'
        lines.append(line)
    bar = '<div class="bar"><i class="dot"></i><i class="dot"></i><i class="dot"></i>' \
          f'<span class="t">{html.escape(path.stem)}</span></div>'
    return f'<div class="term">{bar}<pre>{chr(10).join(lines)}</pre></div>'


def inline(text: str) -> str:
    return re.sub(r"`([^`]*)`", r"<code>\1</code>", html.escape(text))


def summary(path: Path) -> str:
    """The small Markdown subset summarize.py writes: a heading, a line, one table."""
    out, rows = [], []
    for raw in path.read_text().splitlines():
        if raw.startswith("## "):
            out.append(f"<h2>{inline(raw[3:])}</h2>")
        elif raw.startswith("|"):
            cells = [c.strip().replace("\\|", "|") for c in raw.strip("|").split(" | ")]
            if not set("".join(cells)) <= set("-| "):
                rows.append(cells)
        elif raw.strip():
            out.append(f"<p>{inline(raw)}</p>")
    if rows:
        head = "".join(f"<th>{inline(c)}</th>" for c in rows[0])
        body = "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in rows[1:])
        out.append(f"<table><tr>{head}</tr>{body}</table>")
    return f'<div class="sum">{"".join(out)}</div>'


def page(name: str, parts: list) -> None:
    OUT.mkdir(exist_ok=True)
    (OUT / f"{name}.html").write_text(PAGE.format(title=name, body="".join(parts)))


page("terminal-pass-fail", [terminal(SRC / "tests-gate-pass.txt"), terminal(SRC / "tests-gate-fail.txt")])
page("run-summary", [summary(SRC / "tests-gate-pass.summary.md"), summary(SRC / "tests-gate-fail.summary.md")])
for txt in sorted(SRC.glob("save-compat-*.txt")):
    page(txt.stem, [terminal(txt)])
