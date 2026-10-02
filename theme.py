"""Colors, fonts, and small drawing helpers shared by the client and server.

The look is a sunny prison yard: warm paper background, sandy tiles, stone
walls, a blue warder and an orange prisoner. Everything is drawn on Tk
canvases so it looks the same on macOS and Windows (native Tk buttons on
macOS ignore background colors).
"""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont


PAPER = "#f6efe2"
CARD = "#fffcf6"
INK = "#2f2a24"
SOFT = "#7c7063"
FAINT = "#b3a693"
LINE = "#e6dac6"

YARD = "#ecdfc6"
YARD_DARK = "#e3d3b5"
STONE = "#9d8c78"
STONE_DARK = "#7d6d5b"
HOLE = "#3a2e27"
HOLE_RIM = "#8a6f58"

WARDER = "#3f6fb5"
WARDER_DARK = "#2c5190"
PRISONER = "#ee8a3a"
PRISONER_DARK = "#c96a1f"
SKIN = "#f4d2b0"

GOOD = "#4f9b6c"
BAD = "#d4553f"
SUN = "#f3c34f"
SUN_SOFT = "#fbe9b7"


class Fonts:
    """Picks a friendly font that exists on this computer."""

    family = "Helvetica"

    @classmethod
    def load(cls, root: tk.Misc) -> None:
        available = set(tkfont.families(root))
        for name in ("Avenir Next", "Nunito", "Segoe UI", "Trebuchet MS", "Helvetica"):
            if name in available:
                cls.family = name
                return

    @classmethod
    def get(cls, size: int, weight: str = "normal") -> tuple:
        return (cls.family, size, weight)


def rounded_rect(canvas: tk.Canvas, x1, y1, x2, y2, radius=12, **kwargs) -> int:
    radius = max(0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    points = [
        x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
        x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
        x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, splinesteps=24, **kwargs)


class PillButton(tk.Canvas):
    """A rounded button that keeps its colors on every platform."""

    def __init__(
        self,
        parent: tk.Misc,
        text: str,
        command=None,
        color: str = INK,
        text_color: str = "white",
        width: int = 200,
        height: int = 42,
        font_size: int = 12,
        bg: str = CARD,
    ) -> None:
        super().__init__(
            parent, width=width, height=height, bg=bg,
            highlightthickness=0, borderwidth=0, cursor="hand2",
        )
        self.command = command
        self.color = color
        self.text_color = text_color
        self.text = text
        self.font_size = font_size
        self.enabled = True
        self.hover = False
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self.bind("<ButtonRelease-1>", self._click)
        self._draw()

    def configure_button(self, text: str | None = None, enabled: bool | None = None,
                         color: str | None = None) -> None:
        if text is not None:
            self.text = text
        if enabled is not None:
            self.enabled = enabled
            self.configure(cursor="hand2" if enabled else "arrow")
        if color is not None:
            self.color = color
        self._draw()

    def _set_hover(self, value: bool) -> None:
        self.hover = value
        self._draw()

    def _click(self, _event) -> None:
        if self.enabled and self.command:
            self.command()

    def _draw(self) -> None:
        self.delete("all")
        w = max(self.winfo_width(), int(self["width"]))
        h = max(self.winfo_height(), int(self["height"]))
        if self.winfo_width() > 1:
            w, h = self.winfo_width(), self.winfo_height()
        fill = self.color if self.enabled else LINE
        fg = self.text_color if self.enabled else FAINT
        if self.enabled:
            # A little "lip" under the button so it feels pressable.
            rounded_rect(self, 1, 4, w - 1, h - 1, radius=h / 2, fill=_shade(fill, 0.78), outline="")
        lift = 1 if (self.hover and self.enabled) else 3
        rounded_rect(self, 1, 1 + (3 - lift), w - 1, h - lift, radius=h / 2, fill=fill, outline="")
        self.create_text(
            w / 2, (h - lift + (3 - lift)) / 2 + 1, text=self.text, fill=fg,
            font=Fonts.get(self.font_size, "bold"),
        )


def _shade(color: str, factor: float) -> str:
    color = color.lstrip("#")
    r, g, b = (int(color[i:i + 2], 16) for i in (0, 2, 4))
    return "#%02x%02x%02x" % (int(r * factor), int(g * factor), int(b * factor))


def card(parent: tk.Misc, **pack) -> tk.Frame:
    frame = tk.Frame(parent, bg=CARD, highlightbackground=LINE, highlightthickness=1)
    if pack:
        frame.pack(**pack)
    return frame


def draw_warder(canvas: tk.Canvas, cx: float, cy: float, size: float, tag: str = "") -> None:
    """Friendly blue guard with a peaked cap and a whistle."""

    s = size
    canvas.create_oval(cx - s * 0.36, cy + s * 0.34, cx + s * 0.36, cy + s * 0.46,
                       fill="#d9c7a6", outline="", tags=tag)
    rounded_rect(canvas, cx - s * 0.3, cy - s * 0.02, cx + s * 0.3, cy + s * 0.42,
                 radius=s * 0.16, fill=WARDER, outline=WARDER_DARK, width=2, tags=tag)
    canvas.create_oval(cx - s * 0.05, cy + s * 0.1, cx + s * 0.05, cy + s * 0.2,
                       fill=SUN, outline="", tags=tag)
    canvas.create_oval(cx - s * 0.22, cy - s * 0.36, cx + s * 0.22, cy + s * 0.06,
                       fill=SKIN, outline="#d8ab85", width=1.5, tags=tag)
    _face(canvas, cx, cy - s * 0.13, s, tag)
    canvas.create_polygon(
        cx - s * 0.25, cy - s * 0.26, cx - s * 0.18, cy - s * 0.44,
        cx + s * 0.18, cy - s * 0.44, cx + s * 0.25, cy - s * 0.26,
        fill=WARDER_DARK, outline="", smooth=False, tags=tag,
    )
    canvas.create_rectangle(cx - s * 0.28, cy - s * 0.28, cx + s * 0.28, cy - s * 0.23,
                            fill=INK, outline="", tags=tag)
    canvas.create_oval(cx - s * 0.04, cy - s * 0.4, cx + s * 0.04, cy - s * 0.32,
                       fill=SUN, outline="", tags=tag)


def draw_prisoner(canvas: tk.Canvas, cx: float, cy: float, size: float, tag: str = "") -> None:
    """Orange jumpsuit, striped beanie, slightly nervous smile."""

    s = size
    canvas.create_oval(cx - s * 0.34, cy + s * 0.34, cx + s * 0.34, cy + s * 0.46,
                       fill="#d9c7a6", outline="", tags=tag)
    rounded_rect(canvas, cx - s * 0.28, cy - s * 0.02, cx + s * 0.28, cy + s * 0.42,
                 radius=s * 0.16, fill=PRISONER, outline=PRISONER_DARK, width=2, tags=tag)
    canvas.create_text(cx, cy + s * 0.2, text="07", fill="white",
                       font=Fonts.get(max(7, int(s * 0.12)), "bold"), tags=tag)
    canvas.create_oval(cx - s * 0.22, cy - s * 0.36, cx + s * 0.22, cy + s * 0.06,
                       fill=SKIN, outline="#d8ab85", width=1.5, tags=tag)
    _face(canvas, cx, cy - s * 0.12, s, tag)
    canvas.create_arc(cx - s * 0.23, cy - s * 0.47, cx + s * 0.23, cy - s * 0.09,
                      start=0, extent=180, fill=INK, outline="", tags=tag)
    for i in range(3):
        y = cy - s * (0.37 - i * 0.05)
        canvas.create_line(cx - s * 0.2 + i * s * 0.02, y, cx + s * 0.2 - i * s * 0.02, y,
                           fill="white", width=max(1, s * 0.02), tags=tag)


def _face(canvas: tk.Canvas, cx: float, cy: float, s: float, tag: str) -> None:
    eye = max(1.5, s * 0.03)
    for dx in (-0.08, 0.08):
        canvas.create_oval(cx + s * dx - eye, cy - eye, cx + s * dx + eye, cy + eye,
                           fill=INK, outline="", tags=tag)
    canvas.create_arc(cx - s * 0.07, cy - s * 0.02, cx + s * 0.07, cy + s * 0.1,
                      start=200, extent=140, style="arc", outline=INK,
                      width=max(1, s * 0.02), tags=tag)
    for dx in (-0.14, 0.14):
        canvas.create_oval(cx + s * dx - s * 0.03, cy + s * 0.03, cx + s * dx + s * 0.03,
                           cy + s * 0.07, fill="#f1b59a", outline="", tags=tag)


def draw_tunnel(canvas: tk.Canvas, cx: float, cy: float, size: float, tag: str = "") -> None:
    """An open manhole with a ladder poking out."""

    s = size
    canvas.create_oval(cx - s * 0.38, cy - s * 0.22, cx + s * 0.38, cy + s * 0.28,
                       fill=HOLE_RIM, outline="", tags=tag)
    canvas.create_oval(cx - s * 0.3, cy - s * 0.15, cx + s * 0.3, cy + s * 0.2,
                       fill=HOLE, outline="", tags=tag)
    for dx in (-0.1, 0.1):
        canvas.create_line(cx + s * dx, cy - s * 0.3, cx + s * dx, cy + s * 0.08,
                           fill="#c9a77a", width=max(2, s * 0.035), tags=tag)
    for dy in (-0.22, -0.12, -0.02):
        canvas.create_line(cx - s * 0.1, cy + s * dy, cx + s * 0.1, cy + s * dy,
                           fill="#c9a77a", width=max(1, s * 0.025), tags=tag)


def draw_wall(canvas: tk.Canvas, x1, y1, x2, y2, tag: str = "", dim: bool = False) -> None:
    """A stack of stone bricks (darker when it sits in the night shadows)."""

    face, mortar = ("#4d4a4f", "#3a383e") if dim else (STONE, STONE_DARK)
    rounded_rect(canvas, x1, y1, x2, y2, radius=10, fill=face, outline=mortar,
                 width=2, tags=tag)
    rows = 3
    h = (y2 - y1) / rows
    for r in range(1, rows):
        y = y1 + h * r
        canvas.create_line(x1 + 4, y, x2 - 4, y, fill=mortar, width=2, tags=tag)
    for r in range(rows):
        offset = 0.5 if r % 2 else 0.25
        for fraction in (offset, offset + 0.5):
            if fraction >= 1:
                continue
            x = x1 + (x2 - x1) * fraction
            canvas.create_line(x, y1 + h * r + 3, x, y1 + h * (r + 1) - 3,
                               fill=mortar, width=2, tags=tag)


# ---------------------------------------------------------------------------
# Night Escape
# ---------------------------------------------------------------------------

NIGHT_BG = "#1d2236"
NIGHT_YARD = "#2c3350"
NIGHT_YARD_ALT = "#30385a"
LANTERN = "#71674a"
LANTERN_HOVER = "#8a7c4f"
NIGHT_CHIP = "#2b3350"
NIGHT_CHIP_TEXT = "#f6e7bf"
ZONE = "#ec6850"


def draw_crate(canvas: tk.Canvas, cx: float, cy: float, size: float, tag: str = "") -> None:
    s = size
    rounded_rect(canvas, cx - s * 0.26, cy - s * 0.22, cx + s * 0.26, cy + s * 0.24, radius=s * 0.06,
                 fill="#b98a55", outline="#7d5a33", width=2, tags=tag)
    for dy in (-0.06, 0.1):
        canvas.create_line(cx - s * 0.26, cy + s * dy, cx + s * 0.26, cy + s * dy,
                           fill="#7d5a33", width=2, tags=tag)
    canvas.create_text(cx, cy + s * 0.02, text="?", fill="#fff4dc",
                       font=Fonts.get(max(8, int(s * 0.26)), "bold"), tags=tag)


def draw_prints(canvas: tk.Canvas, cx: float, cy: float, size: float, tag: str = "",
                color: str = "#e8dcc0") -> None:
    s = size
    for dx, dy, lean in ((-0.11, 0.06, -1), (0.11, -0.12, 1)):
        x, y = cx + s * dx, cy + s * dy
        canvas.create_oval(x - s * 0.07, y - s * 0.11, x + s * 0.07, y + s * 0.11,
                           fill=color, outline="", tags=tag)
        canvas.create_oval(x - s * 0.05 + lean * s * 0.01, y + s * 0.13, x + s * 0.05, y + s * 0.2,
                           fill=color, outline="", tags=tag)


def draw_smoke(canvas: tk.Canvas, cx: float, cy: float, size: float, tag: str = "") -> None:
    s = size
    for dx, dy, r in ((-0.16, 0.04, 0.18), (0.08, -0.08, 0.21), (0.16, 0.12, 0.17), (-0.04, 0.16, 0.15)):
        canvas.create_oval(cx + s * (dx - r), cy + s * (dy - r), cx + s * (dx + r), cy + s * (dy + r),
                           fill="#cfd3dc", outline="", tags=tag)


def draw_searchlight(canvas: tk.Canvas, cx: float, cy: float, size: float, tag: str = "") -> None:
    s = size
    canvas.create_polygon(cx - s * 0.3, cy + s * 0.3, cx, cy + s * 0.04, cx + s * 0.3, cy + s * 0.3,
                          fill="#f7e2a0", outline="", tags=tag)
    canvas.create_polygon(cx - s * 0.08, cy - s * 0.3, cx + s * 0.08, cy - s * 0.3,
                          cx + s * 0.14, cy - s * 0.06, cx - s * 0.14, cy - s * 0.06,
                          fill=WARDER, outline="", tags=tag)
    canvas.create_oval(cx - s * 0.16, cy - s * 0.12, cx + s * 0.16, cy + s * 0.2,
                       fill="#f7d774", outline="", tags=tag)


def draw_key(canvas: tk.Canvas, cx: float, cy: float, size: float, tag: str = "") -> None:
    s = size
    w = max(2, s * 0.07)
    canvas.create_oval(cx - s * 0.28, cy - s * 0.1, cx - s * 0.08, cy + s * 0.1,
                       outline=SUN, width=w, tags=tag)
    canvas.create_line(cx - s * 0.08, cy, cx + s * 0.28, cy, fill=SUN, width=w,
                       capstyle="round", tags=tag)
    for x, h in ((0.18, 0.1), (0.26, 0.08)):
        canvas.create_line(cx + s * x, cy, cx + s * x, cy + s * h, fill=SUN, width=w, tags=tag)


def draw_barrier(canvas: tk.Canvas, cx: float, cy: float, size: float, tag: str = "") -> None:
    s = size
    for dx in (-0.26, 0.26):
        canvas.create_line(cx + s * dx, cy, cx + s * dx, cy + s * 0.28, fill="#7d5a33",
                           width=max(2, s * 0.05), tags=tag)
    canvas.create_rectangle(cx - s * 0.34, cy - s * 0.12, cx + s * 0.34, cy + s * 0.06,
                            fill="#f4efe6", outline="#7d5a33", width=2, tags=tag)
    for x in (-0.22, -0.02, 0.18):
        canvas.create_line(cx + s * x, cy - s * 0.12, cx + s * (x + 0.1), cy + s * 0.06,
                           fill=BAD, width=max(2, s * 0.06), tags=tag)


def draw_lock(canvas: tk.Canvas, cx: float, cy: float, size: float, tag: str = "") -> None:
    s = size
    canvas.create_arc(cx - s * 0.08, cy - s * 0.14, cx + s * 0.08, cy + s * 0.02, start=0, extent=180,
                      style="arc", outline="#7d5a33", width=max(2, s * 0.04), tags=tag)
    canvas.create_rectangle(cx - s * 0.1, cy - s * 0.05, cx + s * 0.1, cy + s * 0.1,
                            fill=SUN, outline="#7d5a33", width=1.5, tags=tag)


def draw_sprint(canvas: tk.Canvas, cx: float, cy: float, size: float, tag: str = "") -> None:
    s = size
    canvas.create_polygon(cx - s * 0.2, cy + s * 0.1, cx + s * 0.02, cy - s * 0.26, cx - s * 0.02, cy - s * 0.04,
                          cx + s * 0.2, cy - s * 0.08, cx - s * 0.06, cy + s * 0.28, cx - s * 0.02, cy + s * 0.06,
                          fill=PRISONER, outline="", tags=tag)


def draw_reticle(canvas: tk.Canvas, cx: float, cy: float, size: float, tag: str = "") -> None:
    """The lock-on target the warder clicks to grab."""

    s = size
    w = max(2, s * 0.05)
    canvas.create_oval(cx - s * 0.32, cy - s * 0.32, cx + s * 0.32, cy + s * 0.32,
                       outline="#ff6b52", width=w, tags=tag)
    canvas.create_oval(cx - s * 0.1, cy - s * 0.1, cx + s * 0.1, cy + s * 0.1,
                       outline="#ff6b52", width=w, tags=tag)
    for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
        canvas.create_line(cx + dx * s * 0.26, cy + dy * s * 0.26, cx + dx * s * 0.46, cy + dy * s * 0.46,
                           fill="#ff6b52", width=w, tags=tag)
