"""
app.py  

Streamlit GUI for TA Program Automation

Usage:
    streamlit run app.py

Expected project structure:
    TA_Program_Automation/
      app.py
      src/
        algorithm.py
        schedule_utils.py
        html_utils.py
        utils.py
"""

from __future__ import annotations

import io
import re
import sys
import shutil
from pathlib import Path
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime
from typing import Dict, Any, List

import streamlit as st
import pandas as pd  # noqa: F401

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.utils import (
    load_workbook,
    orderedDict,
    generateTA_timetable,
    process_sem,
    build_course_order,
)
from src.schedule_utils import createTA_schedule_log

COURSE_PATTERN = re.compile(r"^[A-Z]{1,4}\d{4}[A-Z]?$")


def get_courses_for_sem(assignment_dict: Dict[str, Any], semester: str) -> List[str]:
    sem_data = assignment_dict.get(semester, {})
    courses = set()
    for _, info in sem_data.items():
        for c in info.get("Courses", {}).keys():
            code = str(c).strip().upper()
            if COURSE_PATTERN.fullmatch(code):
                courses.add(code)
    return sorted(courses)


if "initialized" not in st.session_state:
    st.session_state.initialized        = True
    st.session_state.excel_path         = ""
    st.session_state.sem_folder         = ""
    st.session_state.out_root           = str(PROJECT_ROOT / "output_html")
    st.session_state.semester           = "Sem 1"
    st.session_state.mode               = "single"
    st.session_state.assignment_dict    = None
    st.session_state.courses            = []
    st.session_state.selected_courses   = []
    st.session_state.log_text           = ""
    st.session_state.last_run_status    = ""
    st.session_state.last_run_out_dir   = ""


def append_log(text: str) -> None:
    st.session_state.log_text = (st.session_state.log_text or "") + text


st.title("TA Program Automation")

with st.expander("Paths & Configuration", expanded=True):
    st.text_input("Assignment Excel file path", key="excel_path",
                  placeholder="e.g. C:/path/to/Assignment.xlsx")
    st.text_input("Semester HTML folder", key="sem_folder",
                  placeholder="e.g. C:/path/to/Sem1_HTML")
    st.text_input("Output root folder", key="out_root")

    c1, c2, c3 = st.columns([1, 1, 1])

    with c1:
        st.selectbox("Semester", options=["Sem 1", "Sem 2"], key="semester")

    with c2:
        st.radio("Mode",
                 options=["single", "batch"],
                 format_func=lambda m: "Single course" if m == "single" else "Full semester (batch)",
                 key="mode", horizontal=False)

    with c3:
        if st.button("Reset TA schedule log"):
            sem_folder = st.session_state.sem_folder.strip()
            semester   = st.session_state.semester
            sem_path   = Path(sem_folder)
            if not sem_folder or not sem_path.is_dir():
                st.error("Provide a valid Semester HTML folder first.")
            else:
                try:
                    log_path = Path(createTA_schedule_log(sem_path, semester, verbose=False))
                    if log_path.exists():
                        log_path.unlink()
                    createTA_schedule_log(sem_path, semester, verbose=False)
                    append_log(f"[LOG] TA schedule log reset for {semester} at: {log_path}\n")
                    st.success(f"TA schedule log reset for {semester}.")
                except Exception as e:
                    append_log(f"[ERROR] Failed to reset log: {e}\n")
                    st.error(f"Failed to reset log:\n{e}")

    if st.button("Load assignment data from Excel"):
        excel_path = st.session_state.excel_path.strip()
        if not excel_path:
            st.error("Provide an assignment Excel file path.")
        else:
            p = Path(excel_path)
            if not p.exists():
                st.error(f"File does not exist:\n{p}")
            else:
                try:
                    df = load_workbook(p, verbose=False)
                    assignment_dict = orderedDict(df)
                    st.session_state.assignment_dict = assignment_dict
                    courses = get_courses_for_sem(assignment_dict, st.session_state.semester)
                    st.session_state.courses = courses
                    append_log(f"[INFO] Loaded assignment data from: {p}\n")
                    st.success(f"Loaded. Found {len(courses)} courses for {st.session_state.semester}.")
                except Exception as e:
                    st.session_state.assignment_dict = None
                    append_log(f"[ERROR] {e}\n")
                    st.error(f"Failed to load:\n{e}")


left_col, right_col = st.columns([1.5, 2])

with left_col:
    mode            = st.session_state.mode
    assignment_dict = st.session_state.assignment_dict

    if mode == "single":
        st.markdown("### Single-course selection")
        if assignment_dict is None:
            st.info("Load assignment data to populate the course list.")
        else:
            courses = get_courses_for_sem(assignment_dict, st.session_state.semester)
            st.session_state.courses = courses
            if not courses:
                st.error("No canonical courses found for this semester.")
            else:
                selected = st.multiselect("Courses:", options=courses,
                                          default=st.session_state.selected_courses)
                st.session_state.selected_courses = selected
                st.markdown(f"**Selected:** {', '.join(selected) if selected else '(none)'}")
    else:
        st.markdown("### Batch preview (from HTML timetables)")
        sem_folder = st.session_state.sem_folder.strip()
        sem_path   = Path(sem_folder)
        if not sem_folder:
            st.info("Provide a Semester HTML folder to see the batch preview.")
        elif not sem_path.is_dir():
            st.error(f"Not a valid folder:\n{sem_path}")
        else:
            try:
                ordered = build_course_order(sem_path, verbose=False)
                if not ordered:
                    preview = f"No valid timetable HTMLs found in:\n{sem_path}\n"
                else:
                    lines: List[str] = [
                        f"Detected {len(ordered)} courses in:\n{sem_path}\n",
                        "Order by difficulty (n_groups):\n",
                    ]
                    for e in ordered:
                        lines.append(f"  • {e.get('code', e.get('digits', '?'))} — {e.get('n_groups', 0)} groups")
                    preview = "\n".join(lines)
            except Exception as e:
                preview = f"Failed to read HTML timetables:\n{e}\n"
            st.text_area("Batch preview:", value=preview, height=250)

with right_col:
    st.markdown("### Run TA Automation")

    if st.button("Run TA Automation", type="primary"):
        excel_path = st.session_state.excel_path.strip()
        sem_folder = st.session_state.sem_folder.strip()
        out_root   = st.session_state.out_root.strip()
        semester   = st.session_state.semester
        mode       = st.session_state.mode

        if not excel_path or not Path(excel_path).exists():
            st.error("Provide a valid assignment Excel file path.")
        elif not sem_folder or not Path(sem_folder).is_dir():
            st.error("Provide a valid Semester HTML folder.")
        elif not out_root:
            st.error("Provide an output root folder.")
        else:
            out_root_path = Path(out_root)
            out_root_path.mkdir(parents=True, exist_ok=True)

            if st.session_state.assignment_dict is None:
                try:
                    df = load_workbook(Path(excel_path), verbose=False)
                    st.session_state.assignment_dict = orderedDict(df)
                    append_log(f"[INFO] Loaded assignment data from: {excel_path}\n")
                except Exception as e:
                    append_log(f"[ERROR] {e}\n")
                    st.error(f"Failed to load assignment data:\n{e}")
                    st.stop()

            assignment_dict     = st.session_state.assignment_dict
            selected_courses    : List[str] = []

            if mode == "single":
                selected_courses = st.session_state.selected_courses
                if not selected_courses:
                    st.error("Select at least one course.")
                    st.stop()

            run_ts     = datetime.now().strftime("%Y%m%d_%H%M%S")
            mode_tag   = "batch" if mode == "batch" else "single"
            course_tag = ""
            if mode == "single" and selected_courses:
                course_tag = "_" + selected_courses[0].replace(" ", "")

            run_id      = f"{semester.replace(' ', '')}_{mode_tag}{course_tag}_{run_ts}"
            run_out_dir = out_root_path / f"TA_Automation_Output_{run_id}"
            run_out_dir.mkdir(parents=True, exist_ok=True)

            st.session_state.log_text        = ""
            st.session_state.last_run_status = f"Running... (run id: {run_id})"
            st.session_state.last_run_out_dir = str(run_out_dir)

            log_buffer      = io.StringIO()
            sem_folder_path = Path(sem_folder)

            import traceback

            try:
                with redirect_stdout(log_buffer), redirect_stderr(log_buffer):
                    print(f"=== TA Automation Run {run_id} ===")
                    print(f"Semester: {semester} | Mode: {mode}")
                    if mode == "single":
                        print(f"Courses: {', '.join(selected_courses)}")
                    print()

                    if mode == "batch":
                        process_sem(
                            sem_folder=sem_folder_path,
                            semester=semester,
                            assignment_dict=assignment_dict,
                            reset_log=True,
                            verbose=True,
                            out_dir=run_out_dir,
                        )
                    else:
                        for code in selected_courses:
                            generateTA_timetable(
                                class_code=code,
                                sem_folder=sem_folder_path,
                                assignment_dict=assignment_dict,
                                semester=semester,
                                out_dir=run_out_dir,
                                verbose=True,
                            )

            except Exception as e:
                tb = "".join(traceback.format_exc())
                log_buffer.write(f"\n[EXCEPTION]\n{tb}")
                st.session_state.log_text    = log_buffer.getvalue()
                st.session_state.last_run_status = "Run failed."
                st.error(f"Execution failed:\n{e}")
                st.stop()

            full_log = log_buffer.getvalue()

            # Copy TA schedule log into run folder
            ta_log_src = sem_folder_path / f"{semester}_TA_schedule_log.csv"
            ta_log_dst = None
            if ta_log_src.exists():
                ta_log_dst = run_out_dir / f"TA_schedule_log_{run_id}.csv"
                shutil.copyfile(ta_log_src, ta_log_dst)

            # Classify output files
            files         = sorted([p for p in run_out_dir.rglob("*") if p.is_file()])
            ok_html       : List[str] = []
            unverified_html: List[str] = []
            other_files   : List[str] = []

            for p in files:
                rel   = str(p.relative_to(run_out_dir))
                lower = rel.lower()
                if lower.endswith(".html"):
                    (unverified_html if "unverified" in lower else ok_html).append(rel)
                else:
                    other_files.append(rel)

            # Collect FAIL lines
            lines        = full_log.splitlines()
            fail_entries : List[str] = []
            last_course  : str | None = None

            for ln in lines:
                stripped = ln.strip()
                if stripped.startswith("[RUN] Processing Course "):
                    m = re.search(r"Processing Course\s+(\S+)\s+in\s+", stripped)
                    if m:
                        last_course = m.group(1)
                    continue
                if not stripped.startswith("[FAIL"):
                    continue
                generic = "Number of groups or sessions assigned for these students is not matching"
                if generic in stripped:
                    prefix = f"[FAIL] {last_course}: " if last_course else "[FAIL] "
                    fail_entries.append(
                        prefix + "groups/sessions mismatch for some students. Check Excel."
                    )
                else:
                    fail_entries.append(stripped)

            # Build summary block
            summary = ["", "[INFO] Run summary:", f"  Output folder: {run_out_dir}",
                       "  Correct HTML files:"]
            summary += [f"    {n}" for n in sorted(ok_html)] or ["    (none)"]
            summary += ["  HTML with issues (UNVERIFIED):"]
            summary += [f"    {n}" for n in sorted(unverified_html)] or ["    (none)"]
            if other_files:
                summary += ["  Other files:"] + [f"    {n}" for n in sorted(other_files)]
            if fail_entries:
                summary += ["  Failures:"] + [f"    {m}" for m in fail_entries]

            full_log += "\n" + "\n".join(summary) + "\n"
            st.session_state.log_text = full_log

            (run_out_dir / f"TA_run_{run_id}.txt").write_text(full_log, encoding="utf-8")

            if ta_log_src.exists():
                append_log(f"\n[INFO] TA schedule log copied to: {ta_log_dst}\n")
            else:
                append_log(f"\n[WARN] TA schedule log not found at: {ta_log_src}\n")

            st.session_state.last_run_status  = f"Run completed. Output folder:\n{run_out_dir}"
            st.session_state.last_run_out_dir = str(run_out_dir)
            st.success(f"Run completed.\nOutput folder:\n{run_out_dir}")


st.markdown("### Execution log")
if st.session_state.last_run_status:
    st.write(f"**Status:** {st.session_state.last_run_status}")
st.text_area("Log (read-only):", value=st.session_state.log_text, height=350)
