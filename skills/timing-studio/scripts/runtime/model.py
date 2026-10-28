"""Integer-femtosecond timing model, edge inspector, and Verilog-A exporter.

No GUI/simulator dependencies. Edge times denote the START of the voltage
transition, not its threshold crossing. A finite run holds its final value.
"""
from __future__ import annotations

from bisect import bisect_right
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import json
import math
import re

UNITS = {"fs": 1, "ps": 1000, "ns": 1000000, "us": 1000000000,
         "ms": 1000000000000, "s": 1000000000000000}
MAX_FS = 9007199254740991
MAX_SIGNALS = 64
# Flat legacy imports can legitimately contain up to the expansion budget.
MAX_ITEMS = 50000
MAX_EXPANDED = 50000
MAX_DEPTH = 3
MAX_CYCLES = 1000000000
_RESERVED = set("""
above abs absdelay abstol acos acosh ac_stim adms_always adms_initial analog
analysis and asin asinh assert assign atan atan2 atanh automatic begin branch
buf bufif0 bufif1 case casex casez ceil cmos connect connectmodule connectrules
cos cosh cross ddt ddt_nature ddt_natures default defparam disable discipline
discrete domain driver_update electrical else end endcase endconnectrules
enddiscipline endfunction endgenerate endmodule endnature endparamset
endprimitive endspecify endtable endtask event exclude exp final final_step
flicker_noise floor flow for force forever fork from function generate genvar
ground highz0 highz1 hypot idt idtmod idt_nature idt_natures if ifnone inf initial
initial_step inout input integer join laplace_nd laplace_np laplace_zd laplace_zp
large last_crossing limexp ln localparam log logic macromodule max medium min
module nand nature negedge net_resolution nmos noise_table nor not notif0 notif1
or output parameter paramset pmos posedge potential pow primitive pull0 pull1
pulldown pullup rcmos real realtime reg release repeat rnmos rpmos rtran rtranif0
rtranif1 scalared sin sinh slew small specify specparam sqrt string strong0
strong1 supply0 supply1 table tan tanh task time timer tran tranif0 tranif1
transition tri tri0 tri1 triand trior trireg units vectored wait wand weak0 weak1
while white_noise wire wor wreal xnor xor zi_nd zi_np zi_zd zi_zp access aliasparam
always cell config deassign design edge endconfig incdir include instance
liblist library noise_table_log signed unsigned use uwire alias bit byte chandle
class const constraint context cover covergroup coverpoint dist do endclass
endgroup endinterface endpackage endprogram endproperty endsequence enum extern
foreach forkjoin import int interface join_any join_none local longint modport
new null package packed priority program property protected pure rand randc
randcase randsequence return sequence shortint shortreal static struct super
tagged this throughout timeprecision timeunit type typedef union unique var
virtual void with within i v ref vref vlow vhigh trise tfall time_scale cycles
""".split())


class ModelError(ValueError):
    pass


def valid_name(name):
    return (isinstance(name, str)
            and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name) is not None
            and name.lower() not in _RESERVED
            and not name.lower().startswith("_tw_"))


def _integer(value, minimum=0, maximum=MAX_FS):
    return isinstance(value, int) and not isinstance(value, bool) and minimum <= value <= maximum


def to_fs(value, unit="ns"):
    """Parse a display value exactly; never silently round sub-fs input."""
    if not isinstance(unit, str) or unit not in UNITS:
        raise ModelError("未知时间单位。")
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ModelError("时间必须是非负数字。")
    s = str(value).strip()
    if len(s) > 80:
        raise ModelError("时间数字过长。")
    try:
        number = Decimal(s)
        if not number.is_finite() or number < 0:
            raise ModelError("时间必须是有限的非负数字。")
        digits = number.as_tuple()
        coefficient = int("".join(str(d) for d in digits.digits))
        if not coefficient:
            return 0
        shift = digits.exponent + len(str(UNITS[unit])) - 1
        if shift > 16:
            raise ModelError("时间超过支持范围（约 9 秒）。")
        if shift >= 0:
            ticks = coefficient * 10 ** shift
        else:
            if -shift > len(digits.digits):
                raise ModelError("时间精度不能小于 1 fs。")
            divisor = 10 ** -shift
            if coefficient % divisor:
                raise ModelError("时间精度不能小于 1 fs。")
            ticks = coefficient // divisor
        if ticks > MAX_FS:
            raise ModelError("时间超过支持范围（约 9 秒）。")
        return ticks
    except (InvalidOperation, OverflowError) as exc:
        raise ModelError("时间必须是有效的十进制数字。") from exc


def format_time(ticks, unit="ns", with_unit=False):
    if not isinstance(unit, str) or unit not in UNITS or not isinstance(ticks, int):
        raise ModelError("无效时间或单位。")
    text = format(Decimal(ticks) / UNITS[unit], "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text + (" " + unit if with_unit else "")


_TIME_SUFFIXES = {"f": "fs", "fs": "fs", "p": "ps", "ps": "ps",
                  "n": "ns", "ns": "ns", "u": "us", "us": "us",
                  "µ": "us", "µs": "us", "μ": "us", "μs": "us",
                  "m": "ms", "ms": "ms", "s": "s"}
_INPUT_SUFFIXES = {"fs": "f", "ps": "p", "ns": "n", "us": "u", "ms": "m", "s": "s"}


def parse_time(text, default_unit="ns"):
    """Parse an attached engineering suffix (1n, 250p, 0.5u), exactly.

    Suffixless input uses the current display unit. SI suffixes are explicitly
    lowercase: accepting e.g. M as m would silently misinterpret a magnitude.
    """
    if not isinstance(default_unit, str) or default_unit not in UNITS:
        raise ModelError("未知默认时间单位。")
    if isinstance(text, bool) or not isinstance(text, (str, int, float, Decimal)):
        raise ModelError("请输入时间，例如 1n、250p 或 0.5u。")
    raw = str(text).strip()
    if len(raw) > 80:
        raise ModelError("时间数字过长。")
    match = re.fullmatch(r"([+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)\s*([A-Za-zµμ]*)", raw)
    if not match:
        raise ModelError("时间格式无效；请输入非负时间，例如 1n、250p 或 0.5u。")
    value, suffix = match.groups()
    if suffix and suffix not in _TIME_SUFFIXES:
        raise ModelError("时间单位无效；请使用小写 f、p、n、u、m、s（也可写 ns、ps 等）。")
    return to_fs(value, _TIME_SUFFIXES[suffix] if suffix else default_unit)


def format_time_input(ticks, unit="ns"):
    """Format exact integer ticks with an attached input suffix, e.g. 1n."""
    if not _integer(ticks) or not isinstance(unit, str) or unit not in UNITS:
        raise ModelError("无效时间或单位。")
    return format_time(ticks, unit) + _INPUT_SUFFIXES[unit]


def _item_duration(item):
    if item["type"] == "segment":
        return item["duration_fs"]
    return sum(_item_duration(child) for child in item["items"]) * item["count"]


def signal_duration(signal):
    """Duration of valid signal's explicit content; excludes end hold."""
    return sum(_item_duration(item) for item in signal["items"])


def _inspect_items(items, label, ids, stats, errors, depth=0, ancestors=None):
    ancestors = set() if ancestors is None else ancestors
    if not isinstance(items, list) or not items:
        errors.append(label + "：至少需要一段波形。")
        return 0, 0
    if len(items) > MAX_ITEMS:
        errors.append(label + "：直接片段数量超过限制。")
        return 0, MAX_EXPANDED + 1
    duration = expanded = 0
    for i, item in enumerate(items):
        loc = "%s / 第 %s 项" % (label, i + 1)
        stats[0] += 1
        if stats[0] > MAX_ITEMS:
            if stats[0] == MAX_ITEMS + 1:
                errors.append("工程定义项总数不能超过 %d。" % MAX_ITEMS)
            return duration, MAX_EXPANDED + 1
        if not isinstance(item, dict):
            errors.append(loc + "：片段格式无效。")
            continue
        identity = item.get("id")
        if not isinstance(identity, str) or not identity or len(identity) > 128:
            errors.append(loc + "：需要有效 id（1–128 字符）。")
        elif identity in ids:
            errors.append(loc + "：id 重复：" + identity)
        else:
            ids.add(identity)
        if id(item) in ancestors:
            errors.append(loc + "：片段存在循环引用。")
            continue
        kind = item.get("type")
        if kind == "segment":
            if not _integer(item.get("level"), 0, 1):
                errors.append(loc + "：电平必须是整数 0 或 1。")
            if not _integer(item.get("duration_fs"), 1):
                errors.append(loc + "：时长必须是 1 fs 以上的整数。")
            else:
                duration += item["duration_fs"]
            expanded += 1
        elif kind == "repeat":
            if depth >= MAX_DEPTH:
                errors.append(loc + "：重复块最多嵌套 %d 层。" % MAX_DEPTH)
                continue
            if "name" in item and (not isinstance(item["name"], str) or len(item["name"]) > 128):
                errors.append(loc + "：块名称不能超过 128 字符。")
            count = item.get("count")
            if not _integer(count, 1, MAX_EXPANDED):
                errors.append(loc + "：重复次数必须在 1–%d 之间。" % MAX_EXPANDED)
                count = 1
            child_duration, child_expanded = _inspect_items(
                item.get("items"), loc, ids, stats, errors, depth + 1,
                ancestors | {id(item)})
            duration += count * child_duration
            expanded += count * child_expanded
        else:
            errors.append(loc + "：未知片段类型。")
    return duration, expanded


def validate(project):
    errors, warnings = [], []
    result = {"errors": errors, "warnings": warnings}
    if not isinstance(project, dict):
        errors.append("工程必须是 JSON 对象。")
        return result
    if not _integer(project.get("version"), 2, 2):
        errors.append("需要 version: 2 的工程；旧版请通过导入功能转换。")
    if not valid_name(project.get("module_name")):
        errors.append("模块名需为有效英文标识符，且不能使用 Verilog-A 保留名称。")
    if not isinstance(project.get("unit"), str) or project.get("unit") not in UNITS:
        errors.append("时间单位无效。")
    voltages = [project.get("vlow"), project.get("vhigh")]
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or
           (isinstance(v, int) and abs(v) > 10 ** 300) or not math.isfinite(v) for v in voltages):
        errors.append("电压必须为有限数字。")
    elif voltages[1] <= voltages[0]:
        errors.append("高电平电压必须大于低电平电压。")
    for key, label in (("rise_fs", "上升时间"), ("fall_fs", "下降时间"), ("period_fs", "大周期")):
        if not _integer(project.get(key), 1):
            errors.append(label + "必须是 1 fs 以上的整数。")
    if not _integer(project.get("cycles"), 0, MAX_CYCLES):
        errors.append("大周期次数必须为 0–1000000000 的整数；0 表示无限重复。")
    signals = project.get("signals")
    if not isinstance(signals, list) or not 1 <= len(signals) <= MAX_SIGNALS:
        errors.append("工程需要 1–%d 路信号。" % MAX_SIGNALS)
        return result
    names, ids, totals, stats = set(), set(), [], [0]
    expanded_total = 0
    for i, signal in enumerate(signals):
        label = "信号 %d" % (i + 1)
        if not isinstance(signal, dict):
            errors.append(label + "格式无效。")
            continue
        name = signal.get("name")
        if not valid_name(name):
            errors.append(label + "名称无效或属于保留名称（如 vref、V、I）。")
        elif name in names:
            errors.append("信号名称重复：" + name)
        elif name == project.get("module_name"):
            errors.append("信号名称不能与模块名称相同：" + name)
        if isinstance(name, str):
            names.add(name)
            label = name[:80]
        identity = signal.get("id")
        if not isinstance(identity, str) or not identity or len(identity) > 128:
            errors.append(label + "需要有效 id。")
        elif identity in ids:
            errors.append(label + "的 id 重复。")
        else:
            ids.add(identity)
        duration, expanded = _inspect_items(signal.get("items"), label, ids, stats, errors)
        totals.append((signal, duration))
        expanded_total += expanded
        if duration > MAX_FS:
            errors.append(label + "总时长超过支持范围（约 9 秒）。")
        if _integer(project.get("period_fs"), 1) and duration > project["period_fs"]:
            errors.append(label + "超出大周期；请增大大周期或缩短片段。")
    if expanded_total > MAX_EXPANDED:
        errors.append("展开后总段数不能超过 %d；请减少重复次数或片段数量。" % MAX_EXPANDED)
    if errors:
        return result
    for signal, duration in totals:
        if duration < project["period_fs"]:
            warnings.append(signal["name"] + "：末尾保持最后电平至大周期结束。")
        segments = _merged_segments(expand_signal(signal))
        if any(s["end_fs"] - s["start_fs"] <= max(project["rise_fs"], project["fall_fs"])
               for s in segments if len(segments) > 1):
            warnings.append(signal["name"] + "：存在不长于上升/下降时间的片段，实际电压可能来不及到达目标值。")
    if project["cycles"] > 0 and project["cycles"] * project["period_fs"] > MAX_FS:
        warnings.append("有限运行总时长较长；仿真器使用浮点绝对时间，远期边沿精度取决于仿真器。")
    return result


def _require(project):
    validation = validate(project)
    if validation["errors"]:
        raise ModelError("\n".join(validation["errors"]))


def expand_signal(signal):
    """Expand repetitions, keeping original item identity and occurrence path.

    Requires a valid signal. Each repeat_path index is zero-based. Returned
    segments are NOT merged, so the editor can select original source items.
    """
    out = []
    def walk(items, start, path, depth):
        if depth > MAX_DEPTH:
            raise ModelError("重复嵌套过深。")
        cursor = start
        for item in items:
            if item["type"] == "segment":
                end = cursor + item["duration_fs"]
                out.append({"start_fs": cursor, "end_fs": end,
                            "level": item["level"], "item_id": item["id"],
                            "repeat_path": list(path)})
                if len(out) > MAX_EXPANDED:
                    raise ModelError("展开波形超出段数限制。")
                cursor = end
            elif item["type"] == "repeat":
                if not _integer(item["count"], 1, MAX_EXPANDED):
                    raise ModelError("重复次数无效。")
                width = sum(_item_duration(child) for child in item["items"])
                for index in range(item["count"]):
                    occurrence = {"id": item["id"], "name": item.get("name", "重复块"),
                                  "index": index, "count": item["count"],
                                  "start_fs": cursor, "end_fs": cursor + width}
                    cursor = walk(item["items"], cursor, path + [occurrence], depth + 1)
            else:
                raise ModelError("未知片段类型。")
        return cursor
    walk(signal["items"], 0, [], 0)
    return out


def _merged_segments(segments):
    merged = []
    for seg in segments:
        if merged and merged[-1]["level"] == seg["level"]:
            merged[-1]["end_fs"] = seg["end_fs"]
        else:
            merged.append(dict(seg))
    return merged


def _signal_events(signal, period_fs, cyclic):
    segments = _merged_segments(expand_signal(signal))
    events = []
    previous = segments[0]["level"]
    for segment in segments[1:]:
        level = segment["level"]
        events.append({"signal_id": signal["id"], "signal_name": signal["name"],
                       "time_fs": segment["start_fs"], "direction": "rise" if level else "fall",
                       "from_level": previous, "to_level": level, "item_id": segment["item_id"],
                       "cycle_offset": 0, "boundary": False})
        previous = level
    if cyclic and previous != segments[0]["level"]:
        level = segments[0]["level"]
        events.append({"signal_id": signal["id"], "signal_name": signal["name"],
                       "time_fs": 0, "direction": "rise" if level else "fall",
                       "from_level": previous, "to_level": level,
                       "item_id": segments[0]["item_id"], "cycle_offset": 0, "boundary": True})
    return events


def analyze_edges(project, threshold_fs, limit=1000, *, signal_id=None, relation_kind="all"):
    """Check one representative macroperiod, including its cyclic seam.

    Simultaneous groups and near pairs share the display limit. For cyclic
    pairs, only the shorter circular direction is retained (ties prefer the
    current period). second.cycle_offset == 1 marks a next-period edge.
    Phase-zero boundary edges describe later period restarts, NOT time zero
    initialization; the finite final stop has no restart edge. Signal and type
    filters apply BEFORE the result limit. Selected-signal queries search an
    ordered index of that signal's edges instead of enumerating unrelated pairs.
    """
    _require(project)
    if not _integer(threshold_fs):
        raise ModelError("接近阈值必须是非负整数 fs。")
    if not _integer(limit, 1, 100000):
        raise ModelError("关系显示上限必须在 1–100000 之间。")
    if relation_kind not in ("all", "simultaneous", "near"):
        raise ModelError("未知边沿关系类型。")
    if signal_id is not None and signal_id not in {s["id"] for s in project["signals"]}:
        raise ModelError("所选信号不存在。")
    period = project["period_fs"]
    cyclic = project["cycles"] != 1
    threshold = min(threshold_fs, period)
    edges = sorted((edge for signal in project["signals"]
                    for edge in _signal_events(signal, period, cyclic)),
                   key=lambda e: (e["time_fs"], e["signal_name"], e["signal_id"]))
    simultaneous, near = [], []
    result = {"simultaneous": simultaneous, "near": near, "truncated": False,
              "edge_count": len(edges), "period_fs": period,
              "threshold_fs": threshold, "cyclic": cyclic,
              "basis": "transition_start", "notices": [],
              "signal_id": signal_id, "relation_kind": relation_kind}
    if threshold != threshold_fs:
        result["notices"].append("接近阈值超过大周期，已按一个大周期检查。")
    if cyclic:
        result["notices"].append("0 时刻的回环边沿只表示后续周期衔接；首次启动及有限重复的最终停止不产生此边沿。")
    def capacity():
        if len(simultaneous) + len(near) >= limit:
            result["truncated"] = True
            return False
        return True
    def finished():
        near.sort(key=lambda r: (r["delta_fs"], r["first"]["time_fs"], r["first"]["signal_name"], r["second"]["signal_name"]))
        return result
    i = 0
    while i < len(edges):
        end = i + 1
        while end < len(edges) and edges[end]["time_fs"] == edges[i]["time_fs"]:
            end += 1
        if (relation_kind != "near" and end - i > 1
                and (signal_id is None or any(e["signal_id"] == signal_id for e in edges[i:end]))):
            if not capacity():
                return finished()
            simultaneous.append({"time_fs": edges[i]["time_fs"], "edges": edges[i:end]})
        i = end
    if relation_kind == "simultaneous" or not threshold or not edges:
        return finished()
    times = [edge["time_fs"] for edge in edges]
    selected_edges = [e for e in edges if e["signal_id"] == signal_id] if signal_id is not None else edges
    selected_times = [e["time_fs"] for e in selected_edges] if signal_id is not None else times
    if not selected_edges:
        return finished()
    # Each positive-time unordered pair is inspected at most once per direction.
    # Skip equal-time groups in O(log n), avoiding a quadratic simultaneous scan.
    within_threshold = min(threshold, period // 2) if cyclic else threshold
    for i, first in enumerate(edges):
        candidates, candidate_times = ((edges, times) if signal_id is None or first["signal_id"] == signal_id
                                       else (selected_edges, selected_times))
        j = bisect_right(candidate_times, first["time_fs"])
        while j < len(candidates) and candidate_times[j] - first["time_fs"] <= within_threshold:
            if not capacity():
                return finished()
            near.append({"first": first, "second": candidates[j],
                         "delta_fs": candidate_times[j] - first["time_fs"], "cross_cycle": False})
            j += 1
    if cyclic:
        # The strictly shorter wrap direction avoids double reporting at T/2.
        wrap_threshold = min(threshold, (period - 1) // 2)
        for first in edges:
            if first["time_fs"] + wrap_threshold < period:
                continue
            candidates = edges if signal_id is None or first["signal_id"] == signal_id else selected_edges
            for second in candidates:
                delta = period + second["time_fs"] - first["time_fs"]
                if delta > wrap_threshold:
                    break
                if delta <= 0:
                    continue
                if not capacity():
                    return finished()
                shifted = dict(second, cycle_offset=1)
                near.append({"first": first, "second": shifted,
                             "delta_fs": delta, "cross_cycle": True})
    return finished()


def _real(value):
    text = repr(float(value))
    return text


def compile_va(project):
    """Return Verilog-A using periodic timers and bounded finite-run counters.

    Leave timer tolerance to the simulator: explicit zero tolerance loses
    events in the validated Spectre version. Gate assignments inside the
    event so finite runs hold their final state, although timers keep waking.
    """
    _require(project)
    signals = project["signals"]
    ports = [signal["name"] for signal in signals]
    period = project["period_fs"]
    lines = [
        "// Timing Wave Studio Desktop — generated Verilog-A stimulus.",
        "// Edge positions are transition START times, not threshold crossings.",
        "// Short tracks hold to the macroperiod; finite runs hold final levels.",
        "// Finite runs ignore later timer events; periodic timers keep waking.",
        "// Waveform edges use event schedules, never dense samples.",
        "// time_scale scales every event time and the rise/fall durations.",
        "// Connect vref to the intended voltage reference (usually ground).",
        "// Transient/open-loop use. PSS/PAC and feedback are not validated.",
        '`include "constants.vams"', '`include "disciplines.vams"', "",
        "module %s(%s);" % (project["module_name"], ", ".join(ports + ["vref"])),
        "  output " + ", ".join(ports) + ";", "  input vref;",
        "  electrical " + ", ".join(ports + ["vref"]) + ";", "",
        "  parameter real vlow = %s;" % _real(project["vlow"]),
        "  parameter real vhigh = %s from (vlow:inf);" % _real(project["vhigh"]),
        "  parameter real trise = %de-15 from (0:inf);" % project["rise_fs"],
        "  parameter real tfall = %de-15 from (0:inf);" % project["fall_fs"],
        "  parameter real time_scale = 1.0 from (0:inf);",
        "  parameter integer cycles = %d from [0:%d]; // 0 = forever" % (project["cycles"], MAX_CYCLES), "",
    ]
    initial, schedules = [], []
    for si, signal in enumerate(signals):
        initial.append(expand_signal(signal)[0]["level"])
        lines.append("  integer _tw_level_%d;" % si)
        # Include potential wrap even for cycles=1, so changing the exported
        # cycles parameter also changes the waveform correctly.
        for edge in _signal_events(signal, period, True):
            ei = len(schedules)
            schedules.append((si, edge, ei))
            lines.append("  integer _tw_count_%d;" % ei)
    lines += ["", "  analog begin", "    @(initial_step) begin"]
    for si, level in enumerate(initial):
        lines.append("      _tw_level_%d = %d; // %s" % (si, level, ports[si]))
    for _, edge, ei in schedules:
        lines.append("      _tw_count_%d = %d;" % (ei, 1 if edge["boundary"] else 0))
    lines += ["    end", ""]
    for si, edge, ei in schedules:
        tick = period if edge["boundary"] else edge["time_fs"]
        count = "_tw_count_%d" % ei
        lines += [
            "    // %s %s at %s %s%s" % (ports[si], edge["direction"], format_time(tick, project["unit"]),
                                                     project["unit"], " (next macroperiod)" if edge["boundary"] else ""),
            "    @(timer(%de-15 * time_scale, %de-15 * time_scale)) begin" % (tick, period),
            "      if ((cycles == 0) || (%s < cycles)) begin" % count,
            "        _tw_level_%d = %d;" % (si, edge["to_level"]),
            "        if (cycles > 0) %s = %s + 1;" % (count, count),
            "      end", "    end", "",
        ]
    for si, port in enumerate(ports):
        lines.append("    V(%s, vref) <+ transition(_tw_level_%d ? vhigh : vlow, 0.0, trise * time_scale, tfall * time_scale);" % (port, si))
    lines += ["  end", "endmodule", ""]
    return "\n".join(lines)


def simplify_project(project):
    """Copy a valid project, flattening legacy repeated blocks without loss.

    Integer edge positions and the implicit final hold are preserved. Existing
    plain tracks retain their segment identities and editable boundaries.
    Legacy tracks are merged at equal-level seams and get unique deterministic
    IDs for repeated occurrences. Oversized input is rejected, never truncated.
    """
    _require(project)
    try:
        result = deepcopy(project)
    except RecursionError as exc:
        raise ModelError("工程元数据嵌套过深。") from exc

    reserved = {signal["id"] for signal in result["signals"]}
    originals = {}
    def collect(items):
        for item in items:
            reserved.add(item["id"])
            if item["type"] == "repeat":
                collect(item["items"])
            else:
                originals[item["id"]] = item
    for signal in result["signals"]:
        collect(signal["items"])
    used = {signal["id"] for signal in result["signals"]}
    serial = 0
    for signal in result["signals"]:
        if all(item["type"] == "segment" for item in signal["items"]):
            used.update(item["id"] for item in signal["items"])
            continue
        flattened = []
        for part in expand_signal(signal):
            length = part["end_fs"] - part["start_fs"]
            if flattened and flattened[-1]["level"] == part["level"]:
                flattened[-1]["duration_fs"] += length
                continue
            item = deepcopy(originals[part["item_id"]])
            identity = item["id"]
            if identity in used:
                while True:
                    serial += 1
                    identity = "%s_flat_%d" % (item["id"][:105], serial)
                    if identity not in reserved and identity not in used:
                        break
            used.add(identity)
            item.update(id=identity, duration_fs=length)
            flattened.append(item)
        signal["items"] = flattened
    _require(result)
    return result


def migrate_project(project):
    """Copy v2 or migrate the previous browser v1 project; reject bad imports."""
    if not isinstance(project, dict):
        raise ModelError("工程必须是 JSON 对象。")
    if _integer(project.get("version"), 2, 2):
        _require(project)
        try:
            result = deepcopy(project)
        except RecursionError as exc:
            raise ModelError("工程元数据嵌套过深。") from exc
    elif _integer(project.get("version"), 1, 1):
        unit = project.get("unit")
        signals = project.get("signals")
        if not isinstance(signals, list) or not 1 <= len(signals) <= MAX_SIGNALS:
            raise ModelError("旧版工程信号列表无效。")
        result = {"version": 2, "module_name": project.get("moduleName"), "unit": unit,
                  "vlow": project.get("vLow"), "vhigh": project.get("vHigh"),
                  "rise_fs": to_fs(project.get("rise"), unit),
                  "fall_fs": to_fs(project.get("fall"), unit),
                  "cycles": project.get("repeat"), "signals": []}
        for i, signal in enumerate(signals):
            if not isinstance(signal, dict) or not isinstance(signal.get("segments"), list):
                raise ModelError("旧版信号格式无效。")
            items = []
            if not 1 <= len(signal["segments"]) <= MAX_ITEMS:
                raise ModelError("旧版信号片段数量无效。")
            for j, segment in enumerate(signal["segments"]):
                if not isinstance(segment, dict):
                    raise ModelError("旧版片段格式无效。")
                items.append({"type": "segment", "id": "migrated_item_%d_%d" % (i, j),
                              "level": segment.get("level"), "duration_fs": to_fs(segment.get("duration"), unit)})
            result["signals"].append({"id": "migrated_signal_%d" % i,
                                       "name": signal.get("name"), "items": items})
        result["period_fs"] = max(signal_duration(signal) for signal in result["signals"])
    else:
        raise ModelError("不支持此工程版本。")
    _require(result)
    return simplify_project(result)


def load_project(text):
    if not isinstance(text, str) or len(text) > 10_000_000:
        raise ModelError("工程文件过大或格式无效。")
    def reject_constant(value):
        raise ModelError("JSON 不允许非有限数字：" + value)
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ModelError("JSON 字段重复：" + key)
            result[key] = value
        return result
    try:
        project = json.loads(text, parse_constant=reject_constant, object_pairs_hook=unique_object)
    except (ValueError, RecursionError) as exc:
        raise ModelError("工程 JSON 格式无效：" + str(exc)) from exc
    return migrate_project(project)


def default_project():
    def signal(identity, name, pairs):
        return {"id": identity, "name": name, "items": [
            {"id": "%s_seg%d" % (identity, i), "type": "segment", "level": level,
             "duration_fs": duration * UNITS["ns"]} for i, (level, duration) in enumerate(pairs)]}
    return {"version": 2, "module_name": "timing_stimulus", "unit": "ns", "vlow": 0.0,
            "vhigh": 1.0, "rise_fs": 200000, "fall_fs": 200000,
            "cycles": 0, "period_fs": 100 * UNITS["ns"], "signals": [
                signal("s1", "reset", [(1, 20), (0, 80)]),
                signal("s2", "sample", [(0, 20), (1, 30), (0, 50)]),
                signal("s3", "integrate", [(0, 52), (1, 38), (0, 10)]),
                signal("s4", "readout", [(0, 90), (1, 10)])]}


def incremental_example():
    """Example: reset 20 ns, 100 x 10 ns conversion, 20 ns readout."""
    project = default_project()
    project["module_name"] = "incremental_adc_stimulus"
    project["period_fs"] = 1040 * UNITS["ns"]
    def seg(identity, level, duration):
        return {"type": "segment", "id": identity, "level": level,
                "duration_fs": to_fs(duration, "ns")}
    def phase(identity, name, pattern):
        return {"id": identity, "name": name, "items": [
            seg(identity + "_reset", 0, 20),
            {"type": "repeat", "id": identity + "_conversion", "name": "转换小周期",
             "count": 100, "items": [seg(identity + "_p%d" % i, level, duration)
                                      for i, (level, duration) in enumerate(pattern)]},
            seg(identity + "_read", 0, 20)]}
    project["signals"] = [
        {"id": "reset_s", "name": "reset", "items": [seg("reset_high", 1, 20), seg("reset_low", 0, 1020)]},
        phase("phi1_s", "phi1", [(0, 1), (1, 3), (0, 6)]),
        phase("phi2_s", "phi2", [(0, 6), (1, 3), (0, 1)]),
        {"id": "read_s", "name": "readout", "items": [seg("read_low", 0, 1020), seg("read_high", 1, 10), seg("read_end", 0, 10)]},
    ]
    return project
