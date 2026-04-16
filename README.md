# TA Program Automation

Automates weekly teaching program construction for TA assignments at NTU EEE department.

Given an Excel file listing which TAs are assigned to which courses and how many groups they cover, the tool assigns each group to a specific TA and fills the corresponding HTML timetable files making sure there are no clashes between different student schedules.

**The TA selection process (deciding which TAs get which courses) is out of scope.** That was the next step to be automized but project is abonded due to standartization issues.

---

## What it does

1. Reads assignment data from an Excel workbook (`Assignment` sheet)
2. For each course, parses the HTML timetable file to extract group slots and active weeks
3. Runs a constraint-based assignment algorithm to match TAs to groups
4. Writes TA names into the HTML timetable cells and appends a summary table
5. Saves a cumulative schedule log so cross-course clash detection works across a full semester

---

## Algorithm

The core assignment logic (`src/algorithm.py`) is reusable regardless of input format.

**Hard constraints** (never violated):

- A TA cannot be assigned to a group that clashes with any previously assigned slot
- A TA cannot exceed their quota for a course

**Soft penalties** (minimized):

- Back-to-back sessions on the same day
- Multiple sessions on the same day
- Overloading a single week

Groups are processed hardest-first (fewest available TAs first). After greedy assignment, a pairwise swap pass improves the solution without violating hard constraints.

All courses in a semester are processed sequentially against the same schedule log, so clashes are prevented globally across courses, not just within one.

---

## Why the HTML parsing needs rewriting

`src/html_utils.py` and the HTML-related parts of `src/utils.py` were written against a specific NTU timetable HTML format (AY2024-25 Sem 1). The school changed their HTML export format, which is what stopped this project.

A future developer will need to:

- Rewrite `parseTimetable()` to match the new HTML structure
- Rewrite `fillassignment2Cell()` and `fill_timetable_with_assignments()` for the new cell layout
- Update `RAW_TIMES` in `src/schedule_utils.py` if any new time slots are added
- Update the Excel column name mappings in `orderedDict()` in `src/utils.py` if the workbook format changes

The algorithm itself (`src/algorithm.py`) and the schedule tensor representation (`src/schedule_utils.py`) do not depend on the HTML format and can be kept as-is.

---

## Setup

Requires Python 3.11+.

**Quick start (Windows):** double-click `run_app.bat`, it installs dependencies on first run and launches the GUI.

**Manual:**

```
pip install -r requirements.txt
streamlit run app.py
```

---

## Data privacy

This repository contains no school data. Input files must be provided locally by the user.
