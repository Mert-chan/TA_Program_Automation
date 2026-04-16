"""
HTML timetable parsing and filling.

NOTE FOR FUTURE DEVELOPERS
--------------------------
This module was written against a specific NTU timetable HTML format (AY2024-25).
The school changed their HTML export format partway through the project, so this
code will likely need to be rewritten for any new template.

Key assumptions that break when the HTML format changes:
  - The timetable is a single <table> with exactly 4 header rows:
      row 0 = days, row 1 = times, row 2 = group codes, row 3 = date range
      row 4+ = one row per week
  - Column offset: col 0 = week number, col 1 = date range, col 2+ = groups
  - "Closed" groups are detected by the word "closed" appearing in any cell of that column
  - Professor acronyms are yellow-highlighted text in the 3rd <p>/<div> block before the table
  - A cell containing only "-" means "no TA assigned here"
  - A cell containing "recess" means skip week
"""

from __future__ import annotations
import os, re
from pathlib import Path
from bs4 import BeautifulSoup, NavigableString, Tag
from src.schedule_utils import WEEKS

invalidCellKeywords = ["recess"]
invalidGroups = ['EPT', 'EPL', 'CLOSED', 'EPLE', 'EPJ']


def fillassignment2Cell(cell, acronyms, replacement_name, soup):
    """
    Write a TA name into an HTML timetable cell.

    Rules:
      - If cell contains "-" or "recess" -> skip (no assignment)
      - If cell is empty -> write TA name
      - If cell has a prof acronym -> replace it with TA name
      - Otherwise -> prepend TA name + <br> before existing content
    """
    raw_text = cell.get_text(strip=True).lower()
    if any(kw in raw_text for kw in invalidCellKeywords):
        return

    original_children = list(cell.contents)
    cell_content = list(cell.contents)
    new_content  = []
    replaced     = False

    # Try acronym-based replacement
    for item in cell_content:
        if isinstance(item, NavigableString):
            text = str(item)
            patched = text
            for ac in acronyms:
                if re.search(rf"\b{ac}\b", text):
                    patched  = re.sub(rf"\b{ac}\b", replacement_name, patched)
                    replaced = True

            if patched != text and patched != replacement_name:
                new_content.append(NavigableString(replacement_name))
                new_content.append(soup.new_tag("br"))
                new_content.append(NavigableString(patched.replace(replacement_name, "").strip()))
            else:
                new_content.append(NavigableString(patched))

        elif isinstance(item, Tag):
            tag_text = item.get_text()
            if any(re.search(rf"\b{ac}\b", tag_text) for ac in acronyms):
                new_content.append(NavigableString(replacement_name))
                new_content.append(soup.new_tag("br"))
                replaced = True
            else:
                new_content.append(item)
        else:
            new_content.append(item)

    if replaced:
        cell.clear()
        for part in new_content:
            cell.append(part)
        return

    full_text = cell.get_text(strip=True)
    clean = full_text.lstrip()
    if clean.startswith("-") or "recess" in full_text.lower():
        return

    if not full_text:
        cell.clear()
        cell.append(NavigableString(replacement_name))
        return

    # Non-empty, no placeholder: prepend TA name + original content
    cell.clear()
    cell.append(NavigableString(replacement_name))
    cell.append(soup.new_tag("br"))
    for ch in original_children:
        cell.append(ch)


def parseTimetable(sem_folder: Path, html_name: str, verbose: bool = False):
    """
    Parse an NTU timetable HTML file and return:
      - soup: the BeautifulSoup object (for later modification)
      - groups: list of active group codes
      - group_info: {group_code: [{"day", "time", "weeks"}]}

    Active = not closed, not in invalidGroups, has at least one non-recess week.
    """
    html_path = os.path.join(sem_folder, str(html_name))
    with open(html_path, encoding="utf-8") as f:
        soup = BeautifulSoup(f, "html.parser")

    table = soup.find("table")
    if not table:
        raise ValueError(f"No <table> found in {html_name}.")

    rows = table.find_all("tr")
    if len(rows) < 4:
        raise ValueError(f"Timetable must have at least 4 header rows in {html_name}.")

    header, data_rows = rows[:4], rows[4:]

    day_labels   = [td.get_text(strip=True) for td in header[0].find_all(["td","th"])][2:]
    time_labels  = [td.get_text(strip=True) for td in header[1].find_all(["td","th"])][1:]
    group_labels = [td.get_text(strip=True) for td in header[2].find_all(["td","th"])][1:]

    def col_has_closed(col_idx: int) -> bool:
        for tr in data_rows:
            cells = tr.find_all(["td","th"])
            if col_idx < len(cells) and "closed" in cells[col_idx].get_text(strip=True).lower():
                return True
        return False

    closed = {group_labels[i] for i in range(len(group_labels)) if col_has_closed(i+2)}
    if verbose and closed:
        print(f"[parse] CLOSED groups removed: {', '.join(sorted(closed))}")

    group_info = {}
    groups = []

    for idx, grp in enumerate(group_labels):
        if grp in closed:
            continue
        if any(ig.lower() in grp.lower() for ig in invalidGroups):
            continue
        if idx >= len(day_labels) or idx >= len(time_labels):
            continue

        col  = idx + 2
        day  = day_labels[idx]
        time = time_labels[idx]

        week_list = []
        for r_idx, tr in enumerate(data_rows):
            if r_idx >= len(WEEKS):
                break

            cells = tr.find_all(["td","th"])
            if col >= len(cells):
                continue

            txt = cells[col].get_text(" ", strip=True)
            low = txt.lower()

            if txt == "-" or "recess" in low:
                continue
            if txt.startswith("-") and "(" in txt:
                continue

            week_list.append(WEEKS[r_idx])

        if week_list:
            groups.append(grp)
            group_info[grp] = [{
                "day": day,
                "time": time,
                "weeks": sorted(set(week_list), key=lambda w: WEEKS.index(w)),
            }]

    if verbose:
        print(f"[parse] {len(groups)} active groups: {', '.join(sorted(groups))}")

    return soup, groups, group_info


def validate_time_periods(soup):
    """
    Check that all time slots in the HTML table exist in RAW_TIMES.
    Raises ValueError if an unknown slot is found — either update RAW_TIMES
    or the HTML template has changed.
    """
    import re
    from src.schedule_utils import RAW_TIMES

    def norm_slot(s: str) -> str:
        s = s.strip().replace(" ", "")
        m = re.fullmatch(r"(\d{1,2}:\d{2})-(\d{1,2}:\d{2})", s)
        if not m:
            return s
        def norm_part(p: str) -> str:
            hh, mm = p.split(":")
            return f"{int(hh):02d}:{mm}"
        return f"{norm_part(m.group(1))}-{norm_part(m.group(2))}"

    table = soup.find("table")
    rows  = table.find_all("tr")
    time_cells = rows[1].find_all(["td", "th"])[1:]
    extracted  = [norm_slot(td.get_text(" ", strip=True)) for td in time_cells]
    raw_clean  = [norm_slot(t) for t in RAW_TIMES]
    unexpected = [t for t in extracted if t not in raw_clean]

    if unexpected:
        raise ValueError(
            f"Unexpected time slots in HTML timetable: {unexpected}\n"
            "These do not match RAW_TIMES in schedule_utils.py.\n"
            "Either fix the HTML or update RAW_TIMES."
        )
    return True


def fill_timetable_with_assignments(soup, semester, assignment_dict, groups, assign):
    """Write TA names into the timetable cells for all assigned groups."""
    table = soup.find("table")
    rows  = table.find_all("tr")
    header, data_rows = rows[:4], rows[4:]
    group_labels = [td.get_text(strip=True) for td in header[2].find_all(["td","th"])][1:]

    group_to_col = {}
    for idx, grp in enumerate(group_labels):
        if grp in groups:
            group_to_col[grp] = idx + 2

    def is_yellow(tag):
        style = (tag.get("style","") or "").lower().replace(" ","")
        cls   = " ".join(tag.get("class", [])).lower()
        return ("background:yellow" in style or "background:#ffff00" in style or
                "yellow" in cls or tag.name == "mark")

    blocks = soup.find_all(["p","div"])
    prof_acros = set()
    if len(blocks) >= 3:
        block = blocks[2]
        for tag in block.find_all(
            lambda x: x.name in ("span","font","b","strong","mark") and is_yellow(x)
        ):
            txt = tag.get_text(strip=True)
            txt = re.sub(r":+$", "", txt)
            if txt:
                prof_acros.add(txt.upper())

    for row in data_rows:
        tds = row.find_all(["td","th"])
        for grp in groups:
            if grp not in assign:
                continue
            sid     = assign[grp]
            ta_name = assignment_dict[semester][sid]["Name"]
            col     = group_to_col[grp]
            if col < len(tds):
                fillassignment2Cell(tds[col], prof_acros, ta_name, soup)


def build_tainfo_table(soup, semester, class_code, assignment_dict,
                       students, groups, group_info, assign):
    """
    Append a summary TA Info table after the main timetable.

    Also validates actual vs expected group/session counts.
    Returns a list of issue strings (empty = all ok).
    """
    table = soup.find("table")
    if not table:
        raise ValueError("No <table> found for TA info.")

    def compress_weeks(weeks):
        if not weeks:
            return "–"
        w = sorted(weeks, key=lambda x: WEEKS.index(x))
        bands = []
        s = p = w[0]
        for x in w[1:]:
            if WEEKS.index(x) == WEEKS.index(p) + 1:
                p = x
            else:
                bands.append((s, p))
                s = p = x
        bands.append((s, p))
        out = []
        for a, b in bands:
            out.append(f"wk{a}" if a == b else f"wk{a}-{b}")
        return ", ".join(out)

    groups_assigned = {sid: [] for sid in students}
    for grp, sid in assign.items():
        if sid in groups_assigned:
            groups_assigned[sid].append(grp)

    stud_slots = {sid: set() for sid in students}
    for grp in groups:
        if grp not in assign:
            continue
        sid  = assign[grp]
        slot = group_info[grp][0]
        stud_slots[sid].add((slot["day"], slot["time"]))

    expected_groups   = {sid: assignment_dict[semester][sid]["Courses"][class_code]["groups"]   for sid in students}
    expected_sessions = {sid: assignment_dict[semester][sid]["Courses"][class_code]["sessions"] for sid in students}

    info_html = [
        '<table border="1" style="margin-top:20px;font-size:90%;">',
        '<caption style="font-weight:600;padding:4px 0;">TA Info</caption>',
        '<tr><th>Name</th><th>ID</th><th>#groups</th><th>#sessions</th>'
        '<th>#TAhrs</th><th>Weeks</th><th>Groups</th><th>Schedule</th></tr>'
    ]

    issues = []

    for sid in students:
        name     = assignment_dict[semester][sid]["Name"]
        ta_hours = assignment_dict[semester][sid]["Courses"][class_code]["TAhours"]
        g_list   = groups_assigned[sid]

        total_sessions = sum(len(group_info[g][0]["weeks"]) for g in g_list)
        weeks_union    = set()
        for g in g_list:
            weeks_union.update(group_info[g][0]["weeks"])

        schedule_txt = ", ".join(f"{d}:{t}" for (d, t) in sorted(stud_slots[sid]))

        info_html.append(
            f"<tr><td>{name}</td><td>{sid}</td><td>{len(g_list)}</td>"
            f"<td>{total_sessions}</td><td>{ta_hours:.1f}</td>"
            f"<td>{compress_weeks(weeks_union)}</td>"
            f"<td>{', '.join(sorted(g_list)) if g_list else '–'}</td>"
            f"<td>{schedule_txt or '–'}</td></tr>"
        )

        if len(g_list) != expected_groups[sid]:
            issues.append(f"{sid}: groups ACT={len(g_list)} != EXP={expected_groups[sid]}")
        if total_sessions != expected_sessions[sid]:
            issues.append(f"{sid}: sessions ACT={total_sessions} != EXP={expected_sessions[sid]}")

    info_html.append("</table>")
    table.insert_after(BeautifulSoup("\n".join(info_html), "html.parser"))

    return issues
