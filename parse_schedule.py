#!/usr/bin/env python3
import argparse
import json
import re
import subprocess
import unicodedata
from datetime import date, datetime
from pathlib import Path

DATA_FILE = Path(__file__).with_name("weekly.json")


def remove_vietnamese_accents(text: str) -> str:
    """Convert accented Vietnamese characters to standard unaccented characters."""
    if not text:
        return ""
    text = text.replace("đ", "d").replace("Đ", "D")
    normalized = unicodedata.normalize("NFD", text)
    stripped = "".join(c for c in normalized if unicodedata.category(c) != "Mn")
    return unicodedata.normalize("NFC", stripped)


def normalize_name(value):
    """Normalize name to lowercase alphanumeric characters (supports unaccented)."""
    if not value:
        return ""
    no_accent = remove_vietnamese_accents(str(value).strip().lower())
    return re.sub(r"[^a-z0-9]+", "", no_accent)


def normalize_text(value: str) -> str:
    """Normalize text: remove accents, lowercase, single space separated."""
    if not value:
        return ""
    no_accent = remove_vietnamese_accents(str(value).strip().lower())
    return re.sub(r"[^a-z0-9]+", " ", no_accent).strip()


def get_person_name(obj):
    if isinstance(obj, dict):
        return obj.get("name") or ""
    return str(obj or "")


def get_person_info(obj):
    if isinstance(obj, dict):
        return {
            "id": obj.get("id"),
            "name": obj.get("name") or "",
            "email": obj.get("email") or "",
        }
    return {
        "id": None,
        "name": str(obj or ""),
        "email": "",
    }


def matches_person(query: str, person_obj) -> tuple[bool, str]:
    """
    Smart match between search query and teacher information:
    1. Match ID if query is numeric.
    2. Exact match on full name or email (accented or unaccented).
    3. Match username of email (part before @).
    4. Token-based word match (e.g. 'Xuan Huu' -> 'Tran Xuan Huu', 'Khoa' -> 'Nguyen Thanh Khoa').
    5. Prefix match for single-word queries (e.g. 'Tim' -> 'Timothy').
    Returns (is_matched, match_type): ('exact', 'email', 'token', 'prefix') or (False, "").
    """
    info = get_person_info(person_obj)
    name = info["name"]
    email = info["email"]

    q_norm = normalize_text(query)
    if not q_norm:
        return False, ""

    t_norm = normalize_text(name)
    e_norm = normalize_text(email)

    if not t_norm and not e_norm:
        return False, ""

    # 1. Match teacher ID
    if info.get("id") is not None and str(info["id"]) == str(query).strip():
        return True, "exact"

    # 2. Exact match on full name or email
    if q_norm == t_norm or (e_norm and q_norm == e_norm):
        return True, "exact"

    # 3. Match username of email (part before @)
    if email:
        username = email.split("@")[0]
        if q_norm == normalize_text(username):
            return True, "email"

    # 4. Token-based word match
    q_tokens = q_norm.split()
    t_tokens = t_norm.split()
    if q_tokens and all(any(qt == tt for tt in t_tokens) for qt in q_tokens):
        match_type = "exact" if len(q_tokens) == len(t_tokens) else "token"
        return True, match_type

    # 5. Prefix match for single word
    if len(t_tokens) == 1 and t_norm.startswith(q_norm):
        return True, "prefix"

    return False, ""


def make_session_summary(item, role="main"):
    class_ref = item.get("classRef") or {}
    return {
        "date": item.get("sessionDate"),
        "start": item.get("startTime"),
        "end": item.get("endTime"),
        "teacher": get_person_name(item.get("teacher")),
        "coverTeacher": get_person_name(item.get("coverTeacher")),
        "role": role,
        "status": item.get("status"),
        "room": item.get("room"),
        "classCode": (class_ref.get("code") or ""),
        "className": (class_ref.get("name") or ""),
        "notes": item.get("notes"),
    }


def load_data(path=DATA_FILE):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_weekday_name(iso_date):
    if not iso_date:
        return ""
    try:
        return datetime.strptime(iso_date, "%Y-%m-%d").strftime("%A")
    except ValueError:
        return ""


def get_distinct_matched_teachers(data, query):
    """Find list of distinct teachers matching search query."""
    seen = {}
    for item in data:
        for key in ("teacher", "coverTeacher"):
            person = item.get(key)
            if not person:
                continue
            is_match, match_type = matches_person(query, person)
            if is_match:
                info = get_person_info(person)
                identifier = info["id"] if info["id"] is not None else info["name"]
                if identifier not in seen:
                    seen[identifier] = (info, match_type)
    return seen


def get_teacher_sessions(data, teacher_name, include_cover=True):
    results = []

    for item in data:
        t_match, _ = matches_person(teacher_name, item.get("teacher"))
        c_match, _ = matches_person(teacher_name, item.get("coverTeacher")) if include_cover else (False, "")

        if not t_match and not c_match:
            continue

        if c_match:
            role = "cover"
        elif item.get("status") == "covered":
            role = "covered_by_other"
        else:
            role = "main"

        summary = make_session_summary(item, role=role)
        summary["weekday"] = get_weekday_name(summary["date"])
        results.append(summary)

    return sorted(results, key=lambda item: (item["date"] or "", item["start"] or ""))


def find_teacher_exact_or_fuzzy(data, teacher_name, include_cover=True):
    exact_matches = []
    fuzzy_matches = []

    for item in data:
        t_match, t_type = matches_person(teacher_name, item.get("teacher"))
        c_match, c_type = matches_person(teacher_name, item.get("coverTeacher")) if include_cover else (False, "")

        if not t_match and not c_match:
            continue

        role = "cover" if c_match else ("covered_by_other" if item.get("status") == "covered" else "main")
        summary = make_session_summary(item, role=role)

        if t_type in ("exact", "email") or c_type in ("exact", "email"):
            exact_matches.append(summary)
        else:
            fuzzy_matches.append(summary)

    return exact_matches, fuzzy_matches


def get_timetable_for_teacher(data, teacher_name, target_date=None, include_cover=True):
    target_date = target_date or date.today().isoformat()
    results = []

    for item in data:
        session_date = str(item.get("sessionDate") or "")
        if session_date != target_date:
            continue

        t_match, _ = matches_person(teacher_name, item.get("teacher"))
        c_match, _ = matches_person(teacher_name, item.get("coverTeacher")) if include_cover else (False, "")

        if not t_match and not c_match:
            continue

        role = "cover" if c_match else ("covered_by_other" if item.get("status") == "covered" else "main")
        summary = make_session_summary(item, role=role)
        results.append(summary)

    return sorted(results, key=lambda item: (item["date"] or "", item["start"] or ""))


def get_remaining_week_sessions(data, teacher_name, target_date=None, include_cover=True):
    target_date = target_date or date.today().isoformat()
    results = []
    try:
        target_day = date.fromisoformat(target_date)
    except ValueError:
        return results

    for item in get_teacher_sessions(data, teacher_name, include_cover=include_cover):
        item_date = item.get("date")
        if not item_date:
            continue
        try:
            if date.fromisoformat(item_date) >= target_day:
                enriched = dict(item)
                enriched["weekday"] = get_weekday_name(item_date)
                results.append(enriched)
        except ValueError:
            continue

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Parse IMS weekly timetable JSON and filter a teacher's lessons plus cover sessions."
    )
    parser.add_argument("teacher_arg", nargs="?", default=None, help="Teacher to look up (positional, e.g. 'Leon' or 'Khoa')")
    parser.add_argument("--teacher", "-t", default=None, help="Teacher to look up via flag (default: Leon)")
    parser.add_argument("--date", default=date.today().isoformat(), help="Date to filter in YYYY-MM-DD format (default: today)")
    parser.add_argument("--week", action="store_true", help="Print the full week schedule for the selected teacher instead of only today")
    parser.add_argument("--weeks", "-w", type=int, default=None, help="Number of weeks to fetch when used with --update (e.g. 2)")
    parser.add_argument("--no-cover", action="store_true", help="Exclude cover teaching sessions")
    parser.add_argument("--update", action="store_true", help="Fetch the latest weekly timetable from IMS before filtering")
    args = parser.parse_args()

    if args.update:
        update_script = Path(__file__).with_name("update_schedule.sh")
        if update_script.exists():
            import os
            env = os.environ.copy()
            env["IMS_INVOKED_FROM_PARSE"] = "1"
            cmd = [str(update_script)]
            if args.weeks:
                cmd.extend(["--weeks", str(args.weeks)])
            subprocess.run(cmd, check=True, env=env)
            print("")
        else:
            print(f"Error: Update script not found at {update_script}")
        if not args.teacher and not args.teacher_arg:
            return

    if args.teacher_arg and re.match(r"^\d{4}-\d{2}-\d{2}$", args.teacher_arg):
        args.date = args.teacher_arg
        teacher = args.teacher or "Leon"
    else:
        teacher = args.teacher or args.teacher_arg or "Leon"
    include_cover = not args.no_cover

    data = load_data()

    print(f"Analyzing file: {DATA_FILE.name}")
    print(f"Date: {args.date}")

    matched_teachers = get_distinct_matched_teachers(data, teacher)
    if len(matched_teachers) > 1:
        names_str = ", ".join(
            f"{info['name']} ({info['email'] or 'ID:' + str(info['id'])})"
            for info, _ in matched_teachers.values()
        )
        print(f"\n[Note] Keyword '{teacher}' matched {len(matched_teachers)} teachers: {names_str}")

    teacher_sessions = get_teacher_sessions(data, teacher, include_cover=include_cover)
    if args.week:
        print(f"\nFull week schedule for teacher {teacher}:")
        if teacher_sessions:
            print(json.dumps(teacher_sessions, ensure_ascii=False, indent=2))
        else:
            print(f"No classes found for teacher {teacher} this week.")
        return

    exact_matches, fuzzy_matches = find_teacher_exact_or_fuzzy(data, teacher, include_cover=include_cover)
    teacher_lessons_today = get_timetable_for_teacher(data, teacher, args.date, include_cover=include_cover)
    if teacher_lessons_today:
        print(f"\n1) Today's schedule for teacher {teacher}:")
        print(json.dumps(teacher_lessons_today, ensure_ascii=False, indent=2))
    else:
        print(f"\n1) Today's schedule for teacher {teacher}:")
        if exact_matches:
            print(f"No classes scheduled for {teacher} on {args.date}, but an exact match exists in the database.")
        elif fuzzy_matches:
            print(f"No exact match for {teacher} today, but similar names were found this week.")
        else:
            print(f"Teacher {teacher} not found in database for this date.")

        remaining = get_remaining_week_sessions(data, teacher, args.date, include_cover=include_cover)
        print(f"\nRemaining sessions this week for {teacher}:\n")
        if remaining:
            print(json.dumps(remaining, ensure_ascii=False, indent=2))
        else:
            print(f"No sessions found this week for {teacher}.")


if __name__ == "__main__":
    main()
