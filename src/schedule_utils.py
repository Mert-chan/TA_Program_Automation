import pathlib
import numpy as np
import re

DAYS  = ["M","T","W","TH","F"]
WEEKS = [1,2,3,4,5,6,7,'R',8,9,10,11,12,13]

DAY_IDX  = {d:i for i,d in enumerate(DAYS)}
WEEK_IDX = {w:i for i,w in enumerate(WEEKS)}

# All time ranges that appear in NTU timetable HTML files.
# If a new time slot shows up, add it here — validate_time_periods will catch mismatches.
RAW_TIMES = [
    "08:30-09:20",
    "09:30-10:20", "09:30-10:50", "09:30-11:20", "09:30-12:20",
    "10:30-11:20", "10:30-11:50", "10:30-12:20", "10:30-13:20",
    "11:30-12:20", "11:30-12:50", "11:30-13:20",
    "12:30-13:20", "12:30-14:20",
    "13:30-14:20", "13:30-14:50", "13:30-15:20", "13:30-16:20", "13:30-16:30",
    "14:30-15:20", "14:30-15:50", "14:30-16:20", "14:30-17:20",
    "15:30-16:20", "15:30-16:50",
    "16:30-17:20", "16:30-17:50", "16:30-18:20", "16:30-19:20",
    "17:30-18:20", "17:30-18:50", "17:30-19:20",
    "18:30-21:20",
    "19:00-20:20","19:00-21:50",
    "19:30-21:20",
    "21:00-21:50",
]


def _to_minutes(hhmm: str) -> int:
    h, m = map(int, hhmm.split(":"))
    return 60*h + m

def _parse_range(rng: str):
    s, e = rng.split("-")
    return _to_minutes(s), _to_minutes(e)

_bounds = sorted({b for rng in RAW_TIMES for b in _parse_range(rng)})
ATOM_RANGES_MIN = [(_bounds[i], _bounds[i+1]) for i in range(len(_bounds)-1)]
ATOMS = [
    f"{s//60:02d}:{s%60:02d}-{e//60:02d}:{e%60:02d}"
    for s, e in ATOM_RANGES_MIN
]

N_DAY   = len(DAYS)
N_ATOM  = len(ATOMS)
N_WEEK  = len(WEEKS)


def schedule2tensor(schedule_str: str) -> np.ndarray:
    """
    Parse a compact schedule string into a 3D binary tensor (Days x Atoms x Weeks).

    Example input lines (separated by newline or semicolon):
      M: 09:30-11:20 (wk1-3, wk5, wk7-R, wk9-10), 14:30-17:20 (wk2, wk4-6)
      T: 10:30-13:20 (wk1, wk3-4)
      M/W/F: 09:30-12:20 (wk6-7, wk10-11)
    """
    T = np.zeros((N_DAY, N_ATOM, N_WEEK), dtype=np.uint8)
    schedule_str = schedule_str.replace(";", "\n")
    seg_pattern = re.compile(r'(\d{2}:\d{2}\s*-\s*\d{2}:\d{2})\s*\(([^)]*)\)')

    for line in schedule_str.splitlines():
        line = line.strip()
        if not line:
            continue

        day_part, rest = line.split(":", 1)
        day_tokens = [d.strip().upper() for d in day_part.split("/") if d.strip()]
        rest = rest.replace("–", "-").replace("—", "-")

        for time_part, week_part in seg_pattern.findall(rest):
            time_part = time_part.strip()
            start_str, end_str = [x.strip() for x in time_part.split("-", 1)]
            start_min, end_min = _to_minutes(start_str), _to_minutes(end_str)

            atom_idx = [
                i for i, (s, e) in enumerate(ATOM_RANGES_MIN)
                if s >= start_min and e <= end_min
            ]

            week_labels = []
            for token in week_part.split(","):
                token = token.strip()
                if not token:
                    continue
                token = token.lower()
                if token.startswith("wk"):
                    token = token[2:]
                if "-" in token:
                    a, b = [x.strip() for x in token.split("-", 1)]
                    a_obj = int(a) if a.isdigit() else a.upper()
                    b_obj = int(b) if b.isdigit() else b.upper()
                    ia, ib = WEEK_IDX[a_obj], WEEK_IDX[b_obj]
                    for wi in range(ia, ib + 1):
                        week_labels.append(WEEKS[wi])
                else:
                    val = int(token) if token.isdigit() else token.upper()
                    week_labels.append(val)

            week_idx = sorted({WEEK_IDX[w] for w in week_labels})

            for d in day_tokens:
                di = DAY_IDX[d]
                for ai in atom_idx:
                    for wi in week_idx:
                        T[di, ai, wi] = 1

    return T


def slot2tensor(slot_or_slots) -> np.ndarray:
    """
    Convert one slot or a list of slots from parseTimetable() to a schedule tensor.
    Slot example: {"day": "M", "time": "09:30-12:20", "weeks": [7, 8, 11, 12]}
    """
    if isinstance(slot_or_slots, dict):
        slots = [slot_or_slots]
    else:
        slots = list(slot_or_slots)

    segs = []

    for slot in slots:
        weeks = slot["weeks"]
        idxs  = sorted({WEEK_IDX[w] for w in weeks})
        parts = []
        i = 0
        while i < len(idxs):
            s = idxs[i]
            e = s
            i += 1
            while i < len(idxs) and idxs[i] == e + 1:
                e = idxs[i]
                i += 1
            ws = WEEKS[s]
            we = WEEKS[e]
            if s == e:
                parts.append(f"wk{ws}")
            else:
                parts.append(f"wk{ws}-{we}")
        week_expr = ", ".join(parts)
        segs.append(f"{slot['day']}: {slot['time']} ({week_expr})")

    schedule_str = "; ".join(segs)
    return schedule2tensor(schedule_str)


def tensor2schedule(tensor: np.ndarray) -> str:
    """
    Inverse of schedule2tensor. Returns a compact canonical string.
    One line per day; contiguous atoms with identical week sets are merged;
    weeks are compressed into ranges (wk1-3, wk5, wk7-R, ...).
    """
    assert tensor.shape == (N_DAY, N_ATOM, N_WEEK)
    lines = []

    for di, day in enumerate(DAYS):
        day_slice = tensor[di]
        segments = []
        i = 0
        while i < N_ATOM:
            mask = day_slice[i] > 0
            if not mask.any():
                i += 1
                continue

            start_atom = i
            pattern = tuple(mask)
            i += 1
            while i < N_ATOM and tuple(day_slice[i] > 0) == pattern:
                i += 1
            end_atom = i - 1

            s_min = ATOM_RANGES_MIN[start_atom][0]
            e_min = ATOM_RANGES_MIN[end_atom][1]
            sh, sm = divmod(s_min, 60)
            eh, em = divmod(e_min, 60)
            time_str = f"{sh:02d}:{sm:02d}-{eh:02d}:{em:02d}"

            w_idx = [j for j, v in enumerate(pattern) if v]
            w_ranges = []
            j = 0
            while j < len(w_idx):
                s = w_idx[j]
                e = s
                j += 1
                while j < len(w_idx) and w_idx[j] == e + 1:
                    e = w_idx[j]
                    j += 1
                a = WEEKS[s]
                if s == e:
                    w_ranges.append(f"wk{a}")
                else:
                    b = WEEKS[e]
                    w_ranges.append(f"wk{a}-{b}")

            segments.append(f"{time_str} ({', '.join(w_ranges)})")

        if segments:
            lines.append(f"{day}: " + ", ".join(segments))

    return "\n".join(lines)


import matplotlib.pyplot as plt

def tensor2visual(tensor):
    """Visualize a schedule tensor — one subplot per day, weeks on Y, time atoms on X."""
    n_days = len(DAYS)
    fig, axes = plt.subplots(n_days, 1, figsize=(16, 2*n_days), sharex=True, sharey=True)

    if n_days == 1:
        axes = [axes]

    for idx, (ax, day) in enumerate(zip(axes, DAYS)):
        day_data = tensor[idx].T
        ax.imshow(day_data, aspect='auto', cmap='Blues', interpolation='nearest', vmin=0, vmax=1)
        ax.set_yticks(range(len(WEEKS)))
        ax.set_yticklabels([str(w) for w in WEEKS])
        ax.set_ylabel(f'{day}', fontsize=12, fontweight='bold', rotation=0, ha='right', va='center')

        if idx == 0:
            ax.set_xticks(range(len(ATOMS)))
            ax.set_xticklabels(ATOMS, rotation=45, ha='right', fontsize=8)
            ax.xaxis.tick_top()
            ax.xaxis.set_label_position('top')
            ax.set_xlabel('Time Periods', fontsize=10, fontweight='bold')

        ax.grid(True, which='both', color='gray', linewidth=0.5, alpha=0.3)
        ax.set_xticks(np.arange(len(ATOMS)) - 0.5, minor=True)
        ax.set_yticks(np.arange(len(WEEKS)) - 0.5, minor=True)

    plt.tight_layout()


from pathlib import Path
import csv
import os


def createTA_schedule_log(sem_folder: Path, semester: str = "Sem 2", verbose: bool = False):
    """Create the TA schedule log CSV if it does not exist (header only)."""
    log_path = os.path.join(sem_folder, f"{semester}_TA_schedule_log.csv")
    if not Path(log_path).exists():
        with open(log_path, mode='w', newline='', encoding='utf-8') as csvfile:
            fieldnames = ['Run_Label', 'NTU ID', 'Course Schedule', 'Global Schedule']
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
        if verbose:
            print(f"[log] Created TA schedule log at: {log_path}")
    return log_path


def readTA_log(studentID: str, logPath) -> np.ndarray:
    """
    Return the most recent Global Schedule tensor for the given TA.
    Falls back to a zero tensor if the TA has no log entry yet.
    """
    logPath = Path(logPath)
    if not logPath.exists():
        return np.zeros((N_DAY, N_ATOM, N_WEEK), dtype=np.uint8)

    latest_label = None
    latest_sched = None

    with open(logPath, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        sid = studentID.strip()
        for row in reader:
            if row.get("NTU ID", "").strip() != sid:
                continue
            rl = row.get("Run_Label", "").strip()
            if not rl:
                continue
            if latest_label is None or rl > latest_label:
                latest_label = rl
                latest_sched = row.get("Global Schedule", "").strip()

    if not latest_sched:
        return np.zeros((N_DAY, N_ATOM, N_WEEK), dtype=np.uint8)

    return schedule2tensor(latest_sched)


def updateTA_log(log_Path: Path, run_label: str, studentID: str,
                 course_schedule: str, global_schedule: str, verbose: bool = False):
    """Append a new entry to the TA schedule log CSV."""
    log_Path = Path(log_Path)

    def norm_schedule(sch: str) -> str:
        parts = [p.strip() for p in sch.splitlines() if p.strip()]
        return "; ".join(parts)

    with open(log_Path, mode='a', newline='', encoding='utf-8') as csvfile:
        fieldnames = ['Run_Label', 'NTU ID', 'Course Schedule', 'Global Schedule']
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writerow({
            'Run_Label': run_label,
            'NTU ID': studentID,
            'Course Schedule': norm_schedule(course_schedule),
            'Global Schedule': norm_schedule(global_schedule)
        })
    if verbose:
        print(f"[log] Updated TA schedule log: {log_Path} | run={run_label}")
