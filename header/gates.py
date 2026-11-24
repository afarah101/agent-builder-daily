"""Deterministic gates for the email header. Each gate returns a list of failure reasons; [] means pass."""
import re
import struct

# generate_html.py :root, every hex token. accent-tint is rgba and is not allowed in the header.
BRAND = {"#0f1117", "#1a1d27", "#22263a", "#2d3148", "#f1f5f9", "#94a3b8", "#8592a8", "#00ff88", "#fbbf24", "#f87171"}
TITLE_MAX, TITLE_LINES, MIN_CONTRAST = 60, 2, 4.5
SIZE, MAX_BYTES = (1200, 630), 300 * 1024

_HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
_FUNC = re.compile(r"\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color)\(", re.I)
# CSS Color 4 named colours plus transparent/currentcolor. None is a brand hex, so any of them fails.
NAMED = set("""aliceblue antiquewhite aqua aquamarine azure beige bisque black blanchedalmond blue blueviolet brown
burlywood cadetblue chartreuse chocolate coral cornflowerblue cornsilk crimson cyan darkblue darkcyan darkgoldenrod
darkgray darkgreen darkgrey darkkhaki darkmagenta darkolivegreen darkorange darkorchid darkred darksalmon darkseagreen
darkslateblue darkslategray darkslategrey darkturquoise darkviolet deeppink deepskyblue dimgray dimgrey dodgerblue
firebrick floralwhite forestgreen fuchsia gainsboro ghostwhite gold goldenrod gray green greenyellow grey honeydew
hotpink indianred indigo ivory khaki lavender lavenderblush lawngreen lemonchiffon lightblue lightcoral lightcyan
lightgoldenrodyellow lightgray lightgreen lightgrey lightpink lightsalmon lightseagreen lightskyblue lightslategray
lightslategrey lightsteelblue lightyellow lime limegreen linen magenta maroon mediumaquamarine mediumblue mediumorchid
mediumpurple mediumseagreen mediumslateblue mediumspringgreen mediumturquoise mediumvioletred midnightblue mintcream
mistyrose moccasin navajowhite navy oldlace olive olivedrab orange orangered orchid palegoldenrod palegreen
paleturquoise palevioletred papayawhip peachpuff peru pink plum powderblue purple rebeccapurple red rosybrown royalblue
saddlebrown salmon sandybrown seagreen seashell sienna silver skyblue slateblue slategray slategrey snow springgreen
steelblue tan teal thistle tomato turquoise violet wheat white whitesmoke yellow yellowgreen transparent currentcolor""".split())
_WORD = re.compile(r"(?<![-\w.#])[a-z]+(?![-\w])", re.I)  # a bare word: not part of white-space, .class or --var
_CSS = re.compile(r"<style[^>]*>(.*?)</style>|\bstyle\s*=\s*(\"[^\"]*\"|'[^']*')", re.I | re.S)
_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_PNG_SIG = b"\x89PNG\r\n\x1a\n"


def title_gate(title):
    if not title.strip():
        return ["title is empty"]
    return [f"title is {len(title)} chars (max {TITLE_MAX})"] if len(title) > TITLE_MAX else []


def colour_gate(page_html):
    """Every colour in the final HTML's CSS (<style> blocks and style attributes) is a brand hex. Text content is
    never scanned, so a "#100" or "red" in a headline is not a colour."""
    css = _COMMENT.sub(" ", "\n".join(a or b for a, b in _CSS.findall(page_html)))
    out = [f"non-brand colour {h}" for h in sorted({h.lower() for h in _HEX.findall(css)} - BRAND)]
    out += [f"non-brand colour keyword {w}" for w in sorted({w.lower() for w in _WORD.findall(css)} & NAMED)]
    return out + [f"colour function {f}..) is not a brand hex" for f in sorted(set(_FUNC.findall(css)))]


def _rgb(colour):
    """'#rgb', '#rrggbb' or the browser's computed 'rgb(r, g, b)' / 'rgba(r, g, b, a)' -> (r, g, b).
    shortcut: alpha is ignored; colour_gate already rejects translucent colours in the HTML."""
    c = colour.strip().lower()
    if c.startswith("#"):
        h = c[1:] if len(c) == 7 else "".join(ch * 2 for ch in c[1:4])
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    return tuple(int(float(n)) for n in re.findall(r"[\d.]+", c)[:3])


def _luminance(rgb):
    def lin(v):
        v /= 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(fg, bg):
    """WCAG 2 contrast ratio of two colours."""
    hi, lo = sorted((_luminance(_rgb(fg)), _luminance(_rgb(bg))), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def contrast_gate(measured):
    """measured: [{"slot", "color", "bg"}] from the rendered page (computed styles)."""
    out = []
    for m in measured:
        ratio = contrast(m["color"], m["bg"])
        if ratio < MIN_CONTRAST:
            out.append(f"{m['slot']} contrast {ratio:.2f}:1 (min {MIN_CONTRAST}:1)")
    return out


def lines_gate(measured):
    title = [m for m in measured if m["slot"] == "title"]
    if not title:
        return ["title not found in the render"]
    n = title[0]["lines"]
    if not isinstance(n, (int, float)) or not n >= 1:  # None or NaN when the line height is not a number
        return [f"title line count unreadable ({n!r})"]
    return [f"title wraps to {n} lines (max {TITLE_LINES})"] if n > TITLE_LINES else []


def width_gate(measured):
    """The title fits its box: Chromium's scrollWidth <= clientWidth."""
    title = [m for m in measured if m["slot"] == "title"]
    if not title:
        return []  # lines_gate already reports a missing title
    s, c = title[0].get("scroll"), title[0].get("client")
    if not isinstance(s, int) or not isinstance(c, int):
        return [f"title width unreadable ({s!r}, {c!r})"]
    return [f"title overflows its box ({s}px in {c}px)"] if s > c else []


def png_gate(png):
    if png[:8] != _PNG_SIG or png[12:16] != b"IHDR":
        return ["output is not a PNG"]
    out = []
    size = struct.unpack(">II", png[16:24])
    if size != SIZE:
        out.append(f"PNG is {size[0]}x{size[1]} (need {SIZE[0]}x{SIZE[1]})")
    if len(png) >= MAX_BYTES:
        out.append(f"PNG is {len(png)} bytes (max {MAX_BYTES - 1})")
    return out


def check_html(page_html, title):
    """Gates that run before the render."""
    return title_gate(title) + colour_gate(page_html)


def check_render(measured, png):
    """Gates that run on the render."""
    return lines_gate(measured) + width_gate(measured) + contrast_gate(measured) + png_gate(png)
