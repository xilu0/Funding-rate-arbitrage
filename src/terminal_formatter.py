import re
from typing import List, Optional, Tuple, Dict, Any

def get_display_width(text: str) -> int:
    """
    Computes the visual terminal column display width for a string,
    accurately accounting for CJK East Asian Wide characters and emojis.
    """
    clean = re.sub(r'(\x1b\[[0-9;]*[a-zA-Z]|\[/?[a-zA-Z0-9_ #]+\])', '', str(text))
    width = 0
    for ch in clean:
        code = ord(ch)
        if (0x1100 <= code <= 0x115F or
            0x2329 <= code <= 0x232A or
            0x2E80 <= code <= 0xA4CF or
            0xAC00 <= code <= 0xD7A3 or
            0xF900 <= code <= 0xFAFF or
            0xFE10 <= code <= 0xFE19 or
            0xFE30 <= code <= 0xFE6F or
            0xFF00 <= code <= 0xFF60 or
            0xFFE0 <= code <= 0xFFE6 or
            0x1F300 <= code <= 0x1F9FF or
            0x20000 <= code <= 0x2FFFD or
            0x30000 <= code <= 0x3FFFD):
            width += 2
        else:
            width += 1
    return width

def clean_tags(text: str) -> str:
    """Strips Rich markdown tags and ANSI escapes."""
    return re.sub(r'(\x1b\[[0-9;]*[a-zA-Z]|\[/?[a-zA-Z0-9_ #]+\])', '', str(text))

def pad_string(text: str, width: int, align: str = "left") -> str:
    """Pads a string to target display width respecting CJK wide characters."""
    dw = get_display_width(text)
    diff = max(0, width - dw)
    if align == "right":
        return " " * diff + text
    elif align == "center":
        left = diff // 2
        right = diff - left
        return " " * left + text + " " * right
    else:
        return text + " " * diff

def format_box(title: str,
               content_lines: List[str],
               min_width: int = 86,
               border_style: str = "single") -> str:
    """Formats a visual rectangular box with clean alignment and borders."""
    clean_lines = [clean_tags(line) for line in content_lines]
    clean_title = clean_tags(title)

    max_content_w = max([get_display_width(l) for l in clean_lines] + [get_display_width(clean_title) + 4, min_width])
    box_w = max_content_w + 4

    top_inner = f" {clean_title} "
    top_diff = max(0, box_w - 2 - get_display_width(top_inner))
    left_top_w = 4
    right_top_w = top_diff - left_top_w

    out = []
    out.append("┌" + "─" * left_top_w + top_inner + "─" * right_top_w + "┐")

    for line in clean_lines:
        if line == "---":
            out.append("├" + "─" * (box_w - 2) + "┤")
        else:
            padded = pad_string(line, box_w - 4, align="left")
            out.append(f"│ {padded} │")

    out.append("└" + "─" * (box_w - 2) + "┘")
    return "\n".join(out)

def format_ascii_table(title: Optional[str],
                       headers: List[str],
                       rows: List[List[str]],
                       aligns: Optional[List[str]] = None) -> str:
    """Formats an aligned ASCII table with exact column widths and Unicode borders."""
    clean_headers = [clean_tags(h) for h in headers]
    clean_rows = [[clean_tags(c) for c in r] for r in rows]

    if not aligns:
        aligns = ["left"] * len(headers)

    col_widths = []
    for col_idx in range(len(headers)):
        h_w = get_display_width(clean_headers[col_idx])
        r_w = max([get_display_width(row[col_idx]) for row in clean_rows], default=0) if clean_rows else 0
        col_widths.append(max(h_w, r_w) + 2)

    total_w = sum(col_widths) + (len(col_widths) - 1) * 3 + 4

    out = []
    if title:
        clean_title = clean_tags(title)
        t_inner = f" {clean_title} "
        t_diff = max(0, total_w - 2 - get_display_width(t_inner))
        left_w = 4
        right_w = max(0, t_diff - left_w)
        out.append("┌" + "─" * left_w + t_inner + "─" * right_w + "┐")
    else:
        top_pieces = ["─" * (w + 2) for w in col_widths]
        out.append("┌─" + "─┬─".join(top_pieces) + "─┐")

    # Header Row
    header_cells = [pad_string(h, w, align=aligns[i]) for i, (h, w) in enumerate(zip(clean_headers, col_widths))]
    out.append("│ " + " │ ".join(header_cells) + " │")

    # Separator
    sep_pieces = ["─" * w for w in col_widths]
    out.append("├─" + "─┼─".join(sep_pieces) + "─┤")

    # Data Rows
    if not clean_rows:
        empty_line = pad_string("无数据", total_w - 4, align="center")
        out.append(f"│ {empty_line} │")
    else:
        for row in clean_rows:
            row_cells = [pad_string(c, w, align=aligns[i]) for i, (c, w) in enumerate(zip(row, col_widths))]
            out.append("│ " + " │ ".join(row_cells) + " │")

    # Bottom Border
    bot_pieces = ["─" * w for w in col_widths]
    out.append("└─" + "─┴─".join(bot_pieces) + "─┘")
    return "\n".join(out)
