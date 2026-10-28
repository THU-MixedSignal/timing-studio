"""Reversible flat-waveform edits; callers commit validated project snapshots."""
from copy import deepcopy
from uuid import uuid4


def uid(prefix="n"):
    return prefix + uuid4().hex[:12]


def segment(level, duration):
    return {"type": "segment", "id": uid(), "level": int(level), "duration_fs": int(duration)}


def locate(items, item_id):
    for index, item in enumerate(items):
        if item["id"] == item_id:
            return items, index, item
        if item["type"] == "repeat":
            found = locate(item["items"], item_id)
            if found:
                return found
    return None


def duration(item):
    if item["type"] == "segment":
        return item["duration_fs"]
    return item["count"] * sum(duration(x) for x in item["items"])


def fresh_copy(item):
    result = deepcopy(item)
    result["id"] = uid()
    if "items" in result:
        result["items"] = [fresh_copy(x) for x in result["items"]]
    return result


def has_repeat(items):
    return any(x["type"] == "repeat" for x in items)


def _paint_range(items, a, b, level, total, preferred_id=None):
    """Replace one half-open time interval without moving other event times."""
    source = deepcopy(items)
    length = sum(item["duration_fs"] for item in source)
    if length < total:
        source.append(segment(source[-1]["level"], total - length))
    pieces, cursor = [], 0
    for item in source:
        start, end = cursor, cursor + item["duration_fs"]
        cursor = end
        cuts = sorted({start, end, max(start, min(end, a)), max(start, min(end, b))})
        for index, (left, right) in enumerate(zip(cuts, cuts[1:])):
            part = deepcopy(item)
            part["duration_fs"] = right - left
            if index:
                part["id"] = uid()
            if left >= a and right <= b:
                part["level"] = level
                if preferred_id:
                    part["id"] = preferred_id
            if pieces and pieces[-1]["level"] == part["level"]:
                pieces[-1]["duration_fs"] += part["duration_fs"]
                if part["id"] == preferred_id:
                    pieces[-1]["id"] = preferred_id
            else:
                pieces.append(part)
    # A split segment keeps its old identity on at most one surviving piece.
    # A merged drag target retains its selected identity when it survives.
    used = set()
    for item in pieces:
        if item["id"] in used:
            item["id"] = uid()
        used.add(item["id"])
    return pieces


def apply_edit(signal, operation, period_fs):
    """Mutates a draft only; callers validate before committing the draft."""
    items = signal["items"]
    op = operation["op"]
    if op == "toggle":
        found = locate(items, operation["item_id"])
        if not found or found[2]["type"] != "segment":
            return
        found[2]["level"] = 1 - found[2]["level"]
    elif op == "boundary":
        found = locate(items, operation["item_id"])
        if not found:
            return
        if found[0] is not items or has_repeat(items):
            raise ValueError("旧工程需要先转换为普通时间段后再编辑。")
        _, index, item = found
        start = sum(duration(x) for x in items[:index])
        old_end = start + item["duration_fs"]
        end = max(0, min(period_fs, int(operation["new_end_fs"])))
        if end == old_end or index + 1 == len(items):
            # The final explicit endpoint is a held level, not a real edge.
            return
        right_item = items[index + 1]
        if right_item["level"] == item["level"]:
            return
        if end > old_end:
            level, identity = item["level"], item["id"]
        else:
            level, identity = right_item["level"], right_item["id"]
        signal["items"] = _paint_range(items, min(end, old_end), max(end, old_end),
                                        level, period_fs, identity)
    elif op == "paint":
        if has_repeat(items):
            raise ValueError("旧工程需要先转换为普通时间段后再编辑。")
        a, b = sorted(max(0, min(period_fs, int(operation[key]))) for key in ("start_fs", "end_fs"))
        if b <= a:
            return
        signal["items"] = _paint_range(items, a, b, int(operation["level"]), b)
    else:
        raise ValueError("未知编辑操作：" + str(op))
