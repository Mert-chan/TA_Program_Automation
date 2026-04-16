"""
Orchestration utilities: Excel loading, course/student resolution,
HTML file discovery, and the main per-course and per-semester pipelines.
"""

from __future__ import annotations
import os, re
import pandas as pd
from pathlib import Path
from typing import Dict, Tuple, List, Any
from collections import defaultdict
import datetime as dt
from src.schedule_utils import WEEKS
from src.algorithm import assign_groups, AssignmentError
from src.html_utils import (
    parseTimetable, validate_time_periods,
    fill_timetable_with_assignments,
    build_tainfo_table
)

# HTML table tags that indicate a file contains a lab/tutorial/project/design timetable.
# Only files with these tags in their name are processed.
includeTags = ["lab", "tut", "prj", "des"]

COURSE_CODE_PATTERN = re.compile(r"^[A-Z]{1,4}\d{4}[A-Z]?$")

# Strings that, if found in a course name, mean the row should be skipped.
# Extend this list if new non-course rows appear in future Excel files.
excludedCoursewords = [
    "extra request", "invigilation", "admin", "adjustment", "completed",
    "withdrawn", "balance", "booked", "portfolio", "showcase", "3mt",
    "dip-", "iem-dip", "ay", "survey", "accreditation", "support",
    "quiz supervision", "quiz evaluation", "project", "request",
    "offset", "email", "hours", "duty", "automation"
]


def is_valid_course_name(raw: str) -> bool:
    if not raw:
        return False
    low = raw.lower()
    if any(bad in low for bad in excludedCoursewords):
        return False
    code = raw.strip().upper()
    return COURSE_CODE_PATTERN.fullmatch(code) is not None


def safe_int(val):
    if pd.isna(val) or str(val).strip() in ['', '-', '--']:
        return 0
    try:
        return int(val)
    except (ValueError, TypeError):
        return 0


def safe_float(val):
    if pd.isna(val) or str(val).strip() in ['', '-', '--']:
        return 0
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0


def extract_sem_data(row, course_key: str, group_key: str, lab_key: str, hour_key: str):
    course = row.get(course_key)
    if pd.notna(course) and str(course).strip():
        return course.strip(), {
            'groups': safe_int(row.get(group_key, 0)),
            'sessions': safe_float(row.get(lab_key, 0)),
            'TAhours': safe_float(row.get(hour_key, 0)),
        }
    return None, None


def orderedDict(df: pd.DataFrame) -> Dict[str, Dict[str, Any]]:
    """
    Parse the Assignment sheet into a nested dict:
      Sem -> StudentID -> {
         'Name', 'Sup ID', 'TotalTAhrs', 'TotalODAhrs',
         'Courses': { COURSE -> {'groups', 'sessions', 'TAhours'} }
      }

    NOTE: Column names are hardcoded to match the AY2024-25 Excel template.
    If the school changes their Excel format, update the column name mappings
    in the `for sem, keys in {...}` block below.
    """
    def _norm_name(x): return str(x).strip().upper()
    assignmentDict = defaultdict(lambda: defaultdict(dict))

    for _, row in df.iterrows():
        studentID = _norm_name(row.get('NTUID'))
        if not studentID:
            continue

        name      = str(row.get('Name', '')).strip()
        sup_id    = str(row.get('Sup ID', '')).strip()
        ta_hours  = safe_float(row.get('Scholarship TA', 0))
        oda_hours = safe_float(row.get('Scholarship ODA', 0))

        for sem, keys in {
            'Sem 1': ('Sem 1 Course Assigned', '# of group', 'Sem 1 # of sessions', 'Sem 1 #of TA hrs'),
            'Sem 2': ('Sem 2 Course Assigned', '# of group2', 'Sem 2 # of sessions', 'Sem 2 #of TA hrs'),
        }.items():
            course, course_data = extract_sem_data(row, *keys)
            if not course:
                continue
            if any(bad in course.lower() for bad in excludedCoursewords):
                continue
            if not is_valid_course_name(course):
                continue
            course = _norm_name(course)
            if course.endswith("T"):
                continue

            if studentID not in assignmentDict[sem]:
                assignmentDict[sem][studentID] = {
                    'Name': name,
                    'Sup ID': sup_id,
                    'TotalTAhrs': ta_hours,
                    'TotalODAhrs': oda_hours,
                    'Courses': {}
                }

            courses = assignmentDict[sem][studentID]['Courses']
            if course in courses:
                cur = courses[course]
                cur['groups']   = max(cur.get('groups', 0), course_data.get('groups', 0))
                cur['sessions'] = max(cur.get('sessions', 0.0), course_data.get('sessions', 0.0))
                cur['TAhours']  = course_data.get('TAhours', cur.get('TAhours', 0.0))
            else:
                courses[course] = dict(course_data)

    return {sem: dict(stu_map) for sem, stu_map in assignmentDict.items()}


def load_workbook(path: Path, verbose: bool = False) -> pd.DataFrame:
    """Load the Excel workbook and return the 'Assignment' sheet as a DataFrame."""
    try:
        all_sheets = pd.read_excel(path, sheet_name=None, header=1)
        if verbose:
            print(f"Loaded: {path.name} | Sheets: {list(all_sheets.keys())}")

        if 'Assignment' not in all_sheets:
            raise KeyError("Missing required sheet: 'Assignment'")

        return all_sheets['Assignment']

    except FileNotFoundError:
        raise RuntimeError(f"File not found: {path}")
    except KeyError as e:
        raise RuntimeError(f"Missing sheet or column: {e}")
    except Exception as e:
        raise RuntimeError(f"Failed to load '{path.name}': {e}")


def _extract_4digits(code: str):
    """Return the last 4-digit sequence found in a string (e.g. '2103' from 'EE2103L')."""
    m = re.findall(r"\d{4}", str(code))
    return m[-1] if m else None


def resolve_course_code(nested_dict, semester, course_code, verbose: bool = True):
    """
    Map a short code like 'E2103' to the canonical key stored in the dict (e.g. 'EE2103').
    Falls back to the input unchanged if no match is found.
    """
    code = str(course_code).strip()
    want_tutorial  = code.upper().endswith("T")
    target_digits  = _extract_4digits(code)

    if not target_digits:
        return code

    sem_data   = nested_dict.get(semester, {})
    candidates = set()

    for sid, info in sem_data.items():
        for course in info.get("Courses", {}):
            cname   = str(course).strip()
            is_tut  = cname.upper().endswith("T")
            if want_tutorial != is_tut:
                continue
            if _extract_4digits(cname) == target_digits:
                candidates.add(cname)

    if not candidates:
        return code

    canonical = sorted(candidates)[0]
    if verbose and canonical != code:
        print(f"[INFO] Resolving '{code}' -> '{canonical}' for {semester}")
    return canonical


def getStudents_forCourse(nested_dict, semester, course_code, verbose: bool = True) -> List[str]:
    canonical = resolve_course_code(nested_dict, semester, course_code, False)
    students  = []
    sem_data  = nested_dict.get(semester, {})

    for sid, info in sem_data.items():
        for course, cinfo in info.get('Courses', {}).items():
            if cinfo.get('groups', 0) <= 0:
                continue
            if str(course).lower() == canonical.lower():
                students.append(sid)

    students = list(set(students))
    if not students:
        raise ValueError(f"No students found for '{course_code}' (canonical '{canonical}') in {semester}.")

    return students


def getHTMLfileName(semFolder: Path, courseCode: str) -> str:
    """
    Find the HTML timetable file for a given course code in the semester folder.

    Matching strategy:
      1. Look for files containing the course code string (case-insensitive)
      2. Fall back to 4-digit matching (e.g. '2103' matches 'E2103L(LAB) - FT.html')
      3. Raise if ambiguous or not found
    """
    semFolder  = Path(semFolder)
    if not semFolder.is_dir():
        raise FileNotFoundError(f"Folder not found: {semFolder}")

    code       = str(courseCode).strip().lower()
    code_digits = _extract_4digits(code)

    files      = [f for f in os.listdir(semFolder) if not f.startswith("~")]
    tagged     = [f for f in files if any(tag in f.lower() for tag in includeTags)]

    def is_tut(f): return "tut" in f.lower()

    tut_files     = [f for f in tagged if is_tut(f)]
    non_tut_files = [f for f in tagged if not is_tut(f)]

    hits = [f for f in non_tut_files if code in f.lower()]

    if not hits and code_digits:
        hits = [f for f in non_tut_files if _extract_4digits(f) == code_digits]
        if hits:
            print(f"[INFO] '{courseCode}' (digits {code_digits}) -> {hits}")

    if hits:
        if len(hits) > 1:
            raise RuntimeError(f"Ambiguous timetable for '{courseCode}': {hits}")
        tut_hits = [f for f in tut_files if code in f.lower()]
        if not tut_hits and code_digits:
            tut_hits = [f for f in tut_files if _extract_4digits(f) == code_digits]
        if tut_hits:
            print(f"[INFO] Found tutorial version {tut_hits}, using non-tutorial {hits[0]}")
        return hits[0]

    tut_hits = [f for f in tut_files if code in f.lower()]
    if not tut_hits and code_digits:
        tut_hits = [f for f in tut_files if _extract_4digits(f) == code_digits]
    if tut_hits:
        raise NotImplementedError(
            f"'{courseCode}' matches only tutorial HTML: {tut_hits}. Tutorial handling not implemented."
        )

    raise FileNotFoundError(f"No timetable HTML for '{courseCode}' in {semFolder}")


from src.schedule_utils import createTA_schedule_log, readTA_log


def generateTA_timetable(
    class_code      : str,
    sem_folder      : Path,
    assignment_dict : Dict,
    semester        : str = "Sem 2",
    out_dir         : Path | None = None,
    verbose         : bool = False,
) -> Path:
    """
    Full pipeline for one course:
      1. Locate its HTML timetable
      2. Parse groups and their time slots
      3. Run the assignment algorithm
      4. Fill the HTML with TA names
      5. Append a TA Info summary table
      6. Save output (suffix _filled or _UNVERIFIED if mismatches found)
    """
    class_code = resolve_course_code(assignment_dict, semester, class_code, verbose)

    html_name = getHTMLfileName(sem_folder, class_code)
    html_path = Path(sem_folder) / html_name
    if verbose: print(f"[timetable] Using file: {html_path}")

    students = getStudents_forCourse(assignment_dict, semester, class_code, False)
    quota = {
        sid: assignment_dict[semester][sid]["Courses"][class_code]["groups"]
        for sid in students
    }

    log_path  = createTA_schedule_log(sem_folder, semester, verbose)
    run_label = f"{class_code}_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}"

    soup, groups, group_info = parseTimetable(sem_folder, html_name, verbose)
    validate_time_periods(soup)

    try:
        assign, stud_load = assign_groups(
            group_info, students, quota, log_path, run_label, verbose
        )
    except AssignmentError as e:
        print(e.args[0])
        assign    = e.assign
        stud_load = e.stud_load

    fill_timetable_with_assignments(
        soup=soup,
        semester=semester,
        assignment_dict=assignment_dict,
        groups=groups,
        assign=assign,
    )

    issues = build_tainfo_table(
        soup=soup,
        semester=semester,
        class_code=class_code,
        assignment_dict=assignment_dict,
        students=students,
        groups=groups,
        group_info=group_info,
        assign=assign,
    )

    out_dir  = Path(out_dir or sem_folder)
    out_name = f"{class_code}_UNVERIFIED.html" if issues else f"{class_code}_filled.html"
    out_path = out_dir / out_name
    out_path.write_text(str(soup), encoding="utf-8")

    if verbose:
        print(f"[SAVE] -> {out_path}")
        if issues:
            print("[FAIL] -> Mismatch in expected groups/sessions:")
            for x in issues:
                print(" -", x)

    return out_path


def build_course_order(sem_folder: Path, verbose: bool = True) -> List[dict]:
    """
    Scan the semester folder and return courses sorted by difficulty (number of groups).
    Used by process_sem to decide processing order.
    """
    sem_folder = Path(sem_folder)
    files  = [f for f in os.listdir(sem_folder) if f.endswith(".html") and not f.startswith("~")]
    tagged = [f for f in files if any(tag in f.lower() for tag in includeTags)]

    by_digits: dict[str, dict] = {}
    for fname in tagged:
        digits = _extract_4digits(fname)
        if not digits:
            continue
        entry = by_digits.setdefault(digits, {"digits": digits, "codes": set(), "files": []})
        entry["files"].append(sem_folder / fname)
        stem = Path(fname).stem
        m = re.search(r"[A-Za-z]{1,4}\s*\d{4}", stem)
        if m:
            entry["codes"].add(m.group(0).replace(" ", "").upper())

    for digits, entry in by_digits.items():
        rep_file = entry["files"][0]
        soup, groups, group_info = parseTimetable(sem_folder, rep_file.name, verbose=False)
        n_groups = len(groups)
        entry["n_groups"]   = n_groups
        entry["difficulty"] = n_groups
        entry["code"] = sorted(entry["codes"]).pop() if entry["codes"] else digits

    ordered = sorted(by_digits.values(), key=lambda x: x["difficulty"])

    if verbose:
        print("[INFO] Course order by difficulty (n_groups):")
        for e in ordered:
            print(f"  {e['code']} (digits {e['digits']}): {e['n_groups']} groups, files={len(e['files'])}")
    return ordered


def process_sem(
    sem_folder      : Path,
    semester        : str,
    assignment_dict : dict,
    reset_log       : bool = True,
    verbose         : bool = True,
    out_dir         : Path = None,
):
    """
    Process all courses in a semester folder in order of difficulty.
    Courses are processed hardest-first so constrained groups get priority.
    """
    ordered = build_course_order(sem_folder, verbose=False)

    if reset_log:
        log_path = createTA_schedule_log(sem_folder, semester, verbose=False)
        if Path(log_path).exists():
            Path(log_path).unlink()
        createTA_schedule_log(sem_folder, semester, verbose=False)
        if verbose: print(f"[LOG] Fresh TA schedule log created for {semester}")

    for entry in ordered:
        raw_code = entry["code"]
        digits   = entry["digits"]

        try:
            canonical = resolve_course_code(assignment_dict, semester, raw_code, False)
            print(f"\n[RUN] Processing Course {canonical} in {semester}")
            generateTA_timetable(
                class_code      = canonical,
                sem_folder      = sem_folder,
                assignment_dict = assignment_dict,
                semester        = semester,
                out_dir         = out_dir,
                verbose         = True,
            )
        except Exception as e:
            print(f"[FAIL] {raw_code} (digits {digits}): {e}")
