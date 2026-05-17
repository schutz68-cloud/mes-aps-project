from sqlalchemy import text


HORIZON_START_MINUTE_OF_DAY = 360


def _load_active_shifts(db):
    return db.execute(
        text(
            """
            SELECT
                id,
                start_minute_of_day,
                end_minute_of_day,
                COALESCE(prep_minutes, 0) AS prep_minutes,
                COALESCE(finish_minutes, 0) AS finish_minutes
            FROM shift_templates
            WHERE COALESCE(is_active, true) = true
            ORDER BY start_minute_of_day, id
            """
        )
    ).mappings().all()


def _load_breaks_by_shift(db):
    breaks_by_shift = {}
    for row in db.execute(
        text(
            """
            SELECT
                shift_template_id,
                start_minute_of_shift,
                end_minute_of_shift
            FROM shift_template_breaks
            ORDER BY start_minute_of_shift, id
            """
        )
    ).mappings().all():
        breaks_by_shift.setdefault(row["shift_template_id"], []).append(row)

    return breaks_by_shift


def _get_relative_shift_bounds(shift):
    shift_start = (
        int(shift["start_minute_of_day"]) - HORIZON_START_MINUTE_OF_DAY
    ) % 1440
    shift_end = (
        int(shift["end_minute_of_day"]) - HORIZON_START_MINUTE_OF_DAY
    ) % 1440

    if shift_end <= shift_start:
        shift_end += 1440

    return shift_start, shift_end


def build_work_intervals(db, max_end_time: int) -> list[tuple[int, int]]:
    shifts = _load_active_shifts(db)
    breaks_by_shift = _load_breaks_by_shift(db)

    if not shifts:
        return []

    horizon_limit = max(int(max_end_time or 0), 0) + 1440
    days = horizon_limit // 1440 + 2
    intervals = []

    for day in range(days):
        day_offset = day * 1440

        for shift in shifts:
            shift_start, shift_end = _get_relative_shift_bounds(shift)
            absolute_shift_start = day_offset + shift_start
            work_start = absolute_shift_start + int(shift["prep_minutes"] or 0)
            work_end = day_offset + shift_end - int(shift["finish_minutes"] or 0)
            cursor = work_start

            for shift_break in breaks_by_shift.get(shift["id"], []):
                break_start = (
                    absolute_shift_start
                    + int(shift_break["start_minute_of_shift"] or 0)
                )
                break_end = (
                    absolute_shift_start
                    + int(shift_break["end_minute_of_shift"] or 0)
                )

                if cursor < break_start:
                    intervals.append((cursor, min(break_start, work_end)))

                cursor = max(cursor, break_end)

            if cursor < work_end:
                intervals.append((cursor, work_end))

    return [
        (start, end)
        for start, end in sorted(intervals)
        if end > start and start <= horizon_limit
    ]


def build_non_working_intervals(
    db,
    from_minute: int,
    to_minute: int,
) -> list[dict]:
    shifts = _load_active_shifts(db)
    breaks_by_shift = _load_breaks_by_shift(db)

    if not shifts or to_minute <= from_minute:
        return []

    from_day = from_minute // 1440 - 1
    to_day = to_minute // 1440 + 2
    shift_spans = []
    intervals = []

    def add_interval(start, end, kind):
        clipped_start = max(int(start), from_minute)
        clipped_end = min(int(end), to_minute)
        if clipped_end > clipped_start:
            intervals.append(
                {
                    "start": clipped_start,
                    "end": clipped_end,
                    "kind": kind,
                }
            )

    for day in range(from_day, to_day + 1):
        day_offset = day * 1440

        for shift in shifts:
            shift_start, shift_end = _get_relative_shift_bounds(shift)
            absolute_shift_start = day_offset + shift_start
            absolute_shift_end = day_offset + shift_end
            shift_spans.append((absolute_shift_start, absolute_shift_end))

            prep_end = absolute_shift_start + int(shift["prep_minutes"] or 0)
            add_interval(absolute_shift_start, prep_end, "shift_break")

            for shift_break in breaks_by_shift.get(shift["id"], []):
                add_interval(
                    absolute_shift_start
                    + int(shift_break["start_minute_of_shift"] or 0),
                    absolute_shift_start
                    + int(shift_break["end_minute_of_shift"] or 0),
                    "shift_break",
                )

            finish_start = absolute_shift_end - int(shift["finish_minutes"] or 0)
            add_interval(finish_start, absolute_shift_end, "shift_break")

    ordered_shift_spans = sorted(shift_spans)
    previous_end = None
    for shift_start, shift_end in ordered_shift_spans:
        if previous_end is not None and shift_start > previous_end:
            add_interval(previous_end, shift_start, "between_shifts")
        previous_end = max(previous_end or shift_end, shift_end)

    return sorted(intervals, key=lambda row: (row["start"], row["end"], row["kind"]))


def is_inside_work_interval(db, start_time: int, end_time: int) -> bool:
    work_intervals = build_work_intervals(db, end_time)
    return any(
        start_time >= interval_start and end_time <= interval_end
        for interval_start, interval_end in work_intervals
    )
