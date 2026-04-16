"""
Core assignment and optimization logic for TA scheduling.

Assigns each candidate group (from group_info) to exactly one TA,
respecting hard constraints and minimizing soft penalties.

Inputs:
    - group_info[group] = list of slots: {"day", "time", "weeks"}
    - slot2tensor(slot)  -> 3D (day x atom x week) tensor for a group slot
    - readTA_log(TA)     -> TA's current global schedule tensor
    - quota[TA]          = required number of groups TA must cover for this course

STEP 1
    Build a flat list of (group_id, slot) entries.
    Sort so harder groups come first:
        - groups where few TAs are available
        - groups with longer time ranges

STEP 2  (greedy)
FOR EACH GROUP:
    1) Compute T_group = slot2tensor(slot)
    2) For each candidate TA:
        a) CAPACITY CHECK (hard)  — skip if TA already reached quota
        b) CLASH CHECK (hard)     — skip if T_group & T_global has any overlap
        c) Compute penalty score:
             - Avoid back-to-back sessions        (BB_WEIGHT)
             - Avoid multiple sessions same day   (SAME_DAY_WEIGHT)
             - Avoid overloading a single week    (WEEK_LOAD_WEIGHT)
             - Prefer TAs with more remaining quota (LOAD_WEIGHT)
    3) Assign group to TA with minimum penalty
    4) Update TA's global tensor: T_new = T_global | T_group

STEP 3  (pairwise swap optimization)
    Try all TA pairs; accept any swap that reduces total penalty without
    introducing hard constraint violations.

STEP 4  (finalize)
    Write each TA's course schedule and updated global schedule to the log.

Notes:
    - All courses in a semester are processed in sequence against the same log,
      so cross-course clash prevention is cumulative.
    - Hard rules MUST be respected; soft rules minimize fairness issues.
    - If no valid TA exists for a group, AssignmentError is raised with a
      partial result so the caller can inspect or recover.
"""

from typing import Dict, List, Tuple
import numpy as np
from pathlib import Path
from src.schedule_utils import *

# Penalty weights
LOAD_WEIGHT      = 0.5   # General fairness across TAs
BB_WEIGHT        = 50    # Avoid back-to-back sessions
SAME_DAY_WEIGHT  = 20    # Avoid multiple sessions per day
WEEK_LOAD_WEIGHT = 5     # Avoid overloading a single week


def _count_back_to_back(T_global: np.ndarray, T_group: np.ndarray) -> int:
    hits = 0
    inds = np.argwhere(T_group > 0)
    for d, a, w in inds:
        if a > 0 and T_global[d, a-1, w]: hits += 1
        if a < T_global.shape[1]-1 and T_global[d, a+1, w]: hits += 1
    return hits


def _candidate_count_for_group(grp, students, quota, ta_global, group_tensors):
    T_group = group_tensors[grp]
    cnt = 0
    for sid in students:
        if quota.get(sid, 0) <= 0:
            continue
        if np.any((ta_global[sid] > 0) & (T_group > 0)):
            continue
        cnt += 1
    return cnt


def _count_same_day_sessions(T_global: np.ndarray, T_group: np.ndarray) -> int:
    inds = np.argwhere(T_group > 0)
    seen_dw = set()
    hits = 0
    for d, a, w in inds:
        key = (d, w)
        if key in seen_dw: continue
        seen_dw.add(key)
        if np.any(T_global[d, :, w] > 0): hits += 1
    return hits


def _get_weekly_load_penalty(T_global: np.ndarray, T_group: np.ndarray) -> float:
    """
    Penalizes assigning a group to a TA whose weeks are already heavy.
    Uses a squared penalty so a 3rd class on the same week is strongly discouraged.
    """
    group_activity_per_week = np.sum(T_group, axis=(0, 1))
    active_week_indices = np.where(group_activity_per_week > 0)[0]

    penalty = 0
    for w in active_week_indices:
        existing_load_atoms = np.sum(T_global[:, :, w])
        penalty += (existing_load_atoms ** 2)

    return penalty * 0.01


def _calculate_penalty(T_global, T_group, current_load):
    bb = _count_back_to_back(T_global, T_group)
    sd = _count_same_day_sessions(T_global, T_group)
    wl = _get_weekly_load_penalty(T_global, T_group)
    return (bb * BB_WEIGHT) + (sd * SAME_DAY_WEIGHT) + (wl * WEEK_LOAD_WEIGHT) + (current_load * LOAD_WEIGHT)


class AssignmentError(RuntimeError):
    def __init__(self, message, assign, stud_load, unassigned, quota_diff):
        super().__init__(message)
        self.assign = assign
        self.stud_load = stud_load
        self.unassigned = unassigned
        self.quota_diff = quota_diff


def optimize_assignment(
    assign: Dict[str, str],
    group_tensors: Dict[str, np.ndarray],
    base_ta_logs: Dict[str, np.ndarray],
    verbose: bool = False
) -> Dict[str, str]:
    """Pairwise swap optimization — keep swapping while total penalty improves."""
    improved = True
    iteration = 0

    ta_groups = {}
    for g, ta in assign.items():
        ta_groups.setdefault(ta, []).append(g)

    while improved:
        improved = False
        iteration += 1
        if verbose: print(f"[algo] Optimization Pass {iteration}...")

        tas = list(ta_groups.keys())

        for i in range(len(tas)):
            for j in range(i + 1, len(tas)):
                ta1, ta2 = tas[i], tas[j]

                for g1 in list(ta_groups[ta1]):
                    for g2 in list(ta_groups[ta2]):

                        t1_base = base_ta_logs[ta1].copy()
                        for g in ta_groups[ta1]:
                            if g != g1: t1_base = np.maximum(t1_base, group_tensors[g])

                        t2_base = base_ta_logs[ta2].copy()
                        for g in ta_groups[ta2]:
                            if g != g2: t2_base = np.maximum(t2_base, group_tensors[g])

                        p1_curr = _calculate_penalty(t1_base, group_tensors[g1], len(ta_groups[ta1]))
                        p2_curr = _calculate_penalty(t2_base, group_tensors[g2], len(ta_groups[ta2]))
                        current_total = p1_curr + p2_curr

                        # Hard constraint check for the swap
                        if np.any((t1_base > 0) & (group_tensors[g2] > 0)): continue
                        if np.any((t2_base > 0) & (group_tensors[g1] > 0)): continue

                        p1_new = _calculate_penalty(t1_base, group_tensors[g2], len(ta_groups[ta1]))
                        p2_new = _calculate_penalty(t2_base, group_tensors[g1], len(ta_groups[ta2]))
                        new_total = p1_new + p2_new

                        if new_total < current_total:
                            if verbose:
                                print(f"   -> Swapping {g1}({ta1}) <-> {g2}({ta2}) | Score: {current_total:.1f} -> {new_total:.1f}")

                            assign[g1] = ta2
                            assign[g2] = ta1
                            ta_groups[ta1].remove(g1)
                            ta_groups[ta1].append(g2)
                            ta_groups[ta2].remove(g2)
                            ta_groups[ta2].append(g1)
                            improved = True
                            break
                    if improved: break
                if improved: break

    return assign


def assign_groups(
    group_info : Dict[str, List[dict]],
    students   : List[str],
    quota      : Dict[str, int],
    log_path   : Path,
    run_label  : str,
    verbose    : bool = False,
) -> Tuple[Dict[str, str], Dict[str, int]]:

    total_quota = sum(quota.get(sid, 0) for sid in students)
    if total_quota != len(group_info):
        raise ValueError(
            f"Total quota ({total_quota}) != groups ({len(group_info)}). "
            "Check the assignment Excel sheet."
        )

    base_ta_logs = {sid: readTA_log(sid, log_path) for sid in students}
    ta_global    = {sid: base_ta_logs[sid].copy() for sid in students}
    stud_load    = {sid: 0 for sid in students}
    assign       = {}

    group_tensors = {grp: slot2tensor(slots) for grp, slots in group_info.items()}

    temp_ta_global = {sid: base_ta_logs[sid].copy() for sid in students}
    group_scores = []
    for grp in group_info.keys():
        cand_cnt = _candidate_count_for_group(grp, students, quota, temp_ta_global, group_tensors)
        size     = np.sum(group_tensors[grp] > 0)
        group_scores.append((cand_cnt, -size, grp))

    group_scores.sort()
    ordered_groups = [g for _, _, g in group_scores]

    # Greedy assignment
    for grp in ordered_groups:
        T_group = group_tensors[grp]
        candidates = []

        for sid in students:
            if stud_load[sid] >= quota.get(sid, 0): continue
            T_curr = ta_global[sid]
            if np.any((T_curr > 0) & (T_group > 0)): continue

            pen = _calculate_penalty(T_curr, T_group, stud_load[sid])
            rem = quota[sid] - stud_load[sid]
            candidates.append((pen, -rem, sid))

        if not candidates:
            quota_diff = {sid: stud_load[sid] - quota.get(sid, 0) for sid in students}
            raise AssignmentError(
                f"Cannot assign {grp} to anyone.",
                assign, stud_load, [grp], quota_diff,
            )

        candidates.sort()
        best_sid = candidates[0][2]

        assign[grp] = best_sid
        stud_load[best_sid] += 1
        ta_global[best_sid] = np.maximum(ta_global[best_sid], T_group)

    # Optimization pass
    assign = optimize_assignment(assign, group_tensors, base_ta_logs, verbose)

    # Finalize and write log
    final_load           = {sid: 0 for sid in students}
    final_course_tensor  = {sid: np.zeros_like(base_ta_logs[sid]) for sid in students}
    final_global_tensor  = {sid: base_ta_logs[sid].copy() for sid in students}

    for grp, sid in assign.items():
        final_load[sid] += 1
        T = group_tensors[grp]
        final_course_tensor[sid] = np.maximum(final_course_tensor[sid], T)
        final_global_tensor[sid] = np.maximum(final_global_tensor[sid], T)

    for sid in students:
        updateTA_log(
            log_path, run_label, sid,
            tensor2schedule(final_course_tensor[sid]),
            tensor2schedule(final_global_tensor[sid]),
        )

    return assign, final_load
