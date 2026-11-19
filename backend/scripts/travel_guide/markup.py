"""Small HTML pieces the guide is written with.

Everything taken from the setup goes through ``esc``; the pieces return HTML.
"""

import html
from collections.abc import Iterable, Sequence

TICKED = "☑"
UNTICKED = "☐"


def esc(text: object) -> str:
    return html.escape(str(text), quote=True)


def c(text: object) -> str:
    """A name or a value typed exactly."""
    return f'<span class="c">{esc(text)}</span>'


def f(text: object) -> str:
    """A formula."""
    return f'<span class="f">{esc(text)}</span>'


def maybe_f(text: object | None, empty: str = "empty") -> str:
    return (
        f(text) if text not in (None, "") else f'<span class="mut">{esc(empty)}</span>'
    )


def mut(text: object) -> str:
    return f'<span class="mut">{esc(text)}</span>'


def tick(on: bool) -> str:
    return TICKED if on else UNTICKED


def table(
    head: Sequence[str],
    rows: Iterable[Sequence[str]],
    *,
    numbered: bool = True,
    widths: Sequence[str] | None = None,
    css: str = "",
) -> str:
    """A table of HTML cells; the first column, when ``numbered``, is #."""
    heads = (["#"] if numbered else []) + list(head)
    cols = ""
    if widths:
        # Laid out by these widths, not by the longest formula in a column.
        css += " fixed"
        cols = "<colgroup>" + "".join(
            f'<col style="width:{w}">' if w else "<col>"
            for w in (["20pt"] if numbered else []) + list(widths)
        )
        cols += "</colgroup>"
    body = []
    for n, row in enumerate(rows, 1):
        cells = [f'<td class="n">{n}</td>'] if numbered else []
        cells += [f"<td>{cell}</td>" for cell in row]
        body.append("<tr>" + "".join(cells) + "</tr>")
    thead = "".join(f"<th>{esc(h)}</th>" for h in heads)
    return (
        f'<table class="{css}">{cols}<thead><tr>{thead}</tr></thead>'
        f"<tbody>{''.join(body)}</tbody></table>"
    )


def kv(rows: Iterable[tuple[str, str]]) -> str:
    """A table of settings: a label, then its HTML value."""
    body = "".join(f"<tr><th>{esc(k)}</th><td>{v}</td></tr>" for k, v in rows)
    return f'<table class="kv"><tbody>{body}</tbody></table>'


def reference(number: int, title: str, where: str, body: str) -> str:
    """A Part B table: its R number, title and where it is set in the editor."""
    return (
        f'<section id="r{number}"><div class="r"><span class="badge">R{number}</span>'
        f'<h2>{esc(title)}</h2><span class="where">{esc(where)}</span></div>'
        f"{body}</section>"
    )


def sub(title: str) -> str:
    return f'<h3 class="sub">{title}</h3>'
