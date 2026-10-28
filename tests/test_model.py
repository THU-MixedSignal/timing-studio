"""Independent timing fixtures; run python -m unittest discover -s tests -v."""
import copy
import json
import re
import unittest

import model


def signal(identity, pairs):
    return {"id": identity, "name": identity, "items": [
        {"type": "segment", "id": identity + "_" + str(i), "level": level, "duration_fs": duration}
        for i, (level, duration) in enumerate(pairs)]}


def project(signals, period=100, cycles=0):
    return {"version": 2, "module_name": "test_wave", "unit": "fs", "vlow": 0., "vhigh": 1.,
            "rise_fs": 1, "fall_fs": 1, "period_fs": period, "cycles": cycles, "signals": signals}


class ExportedTimers:
    """Execute the supported exported event subset, without model event helpers.

    This checks logical schedules and counter bounds only. Simulator tolerance,
    analog transitions and numerical precision require the Spectre regressions.
    Reject an unrecognized event body instead of silently skipping a timer.
    """

    def __init__(self, va):
        initial = re.search(r"@\(initial_step\) begin(.*?)\n    end", va, re.S).group(1)
        self.levels = {int(i): int(v) for i, v in re.findall(r"_tw_level_(\d+) = ([01]);", initial)}
        self.counts = {int(i): int(v) for i, v in re.findall(r"_tw_count_(\d+) = ([01]);", initial)}
        pattern = (
            r"@\(timer\((?P<start>\d+)e-15 \* time_scale, (?P<period>\d+)e-15 \* time_scale\)\) begin\s+"
            r"if \(\(cycles == 0\) \|\| \(_tw_count_(?P<count>\d+) < cycles\)\) begin\s+"
            r"_tw_level_(?P<signal>\d+) = (?P<level>[01]);\s+"
            r"if \(cycles > 0\) _tw_count_(?P=count) = _tw_count_(?P=count) \+ 1;\s+end\s+end"
        )
        self.timers = [{key: int(value) for key, value in m.groupdict().items()}
                       for m in re.finditer(pattern, va)]
        if len(self.timers) != va.count("@(timer") or len(self.timers) != len(self.counts):
            raise AssertionError("Unrecognized or unguarded exported timer body")

    def run(self, cycles, stop, initial_counts=None):
        levels = dict(self.levels)
        counts = dict(self.counts if initial_counts is None else initial_counts)
        wakeups = sorted((tick, i) for i, event in enumerate(self.timers)
                         for tick in range(event["start"], stop, event["period"]))
        actions = []
        for tick, i in wakeups:
            event = self.timers[i]
            counter = event["count"]
            if cycles == 0 or counts[counter] < cycles:
                levels[event["signal"]] = event["level"]
                actions.append((tick, event["signal"], event["level"]))
                if cycles > 0:
                    counts[counter] += 1
        return sorted(actions), levels, counts


class TimingModelTests(unittest.TestCase):
    def test_default_and_incremental_examples(self):
        self.assertEqual(model.validate(model.default_project())["errors"], [])
        p = model.incremental_example()
        self.assertEqual(model.validate(p)["errors"], [])
        self.assertEqual(p["period_fs"], 1040_000_000)
        for s in p["signals"]:
            self.assertEqual(model.signal_duration(s), p["period_fs"])
        phase = model.expand_signal(p["signals"][1])
        highs = [seg for seg in phase if seg["level"]]
        # Independent schedule: first rising edge 21 ns, then every 10 ns.
        self.assertEqual([(s["start_fs"], s["end_fs"]) for s in highs],
                         [((21 + 10 * n) * 1_000_000, (24 + 10 * n) * 1_000_000) for n in range(100)])
        self.assertEqual(highs[-1]["repeat_path"][-1]["index"], 99)

    def test_decimal_input_is_exact(self):
        self.assertEqual(model.to_fs("0.000001", "ns"), 1)
        self.assertEqual(model.to_fs("1.025e-2", "ns"), 10250)
        self.assertEqual(model.to_fs("9007199254.740991", "ns"), model.MAX_FS)
        for value in ("0.0000001", "1e-999999999", "1e999999999", "nan", "inf", "-1",
                      "0.000001000000000000000000000000000001"):
            with self.subTest(value=value), self.assertRaises(model.ModelError):
                model.to_fs(value, "ns")
        self.assertEqual(model.to_fs("0e999999999", "ns"), 0)
        self.assertEqual(model.format_time(1, "ns"), "0.000001")

    def test_repeat_expansion_keeps_occurrence_and_source(self):
        s = signal("sig", [(0, 1), (1, 2), (0, 3)])
        s["items"] = [{"type": "repeat", "id": "rep", "name": "three", "count": 3, "items": s["items"]}]
        expanded = model.expand_signal(s)
        self.assertEqual([(e["start_fs"], e["end_fs"], e["level"]) for e in expanded],
                         [(0, 1, 0), (1, 3, 1), (3, 6, 0),
                          (6, 7, 0), (7, 9, 1), (9, 12, 0),
                          (12, 13, 0), (13, 15, 1), (15, 18, 0)])
        self.assertEqual([e["item_id"] for e in expanded[1::3]], ["sig_1"] * 3)
        self.assertEqual([e["repeat_path"][0]["start_fs"] for e in expanded[1::3]], [0, 6, 12])

    def test_simultaneous_near_and_same_signal_order(self):
        p = project([signal("clk_a", [(0, 10), (1, 30)]),
                     signal("clk_b", [(0, 10), (1, 1), (0, 29)])], period=40)
        result = model.analyze_edges(p, 1)
        self.assertEqual(len(result["simultaneous"]), 1)
        group = result["simultaneous"][0]
        self.assertEqual(group["time_fs"], 10)
        self.assertEqual([e["signal_name"] for e in group["edges"]], ["clk_a", "clk_b"])
        self.assertEqual([(r["first"]["signal_name"], r["second"]["signal_name"], r["delta_fs"])
                          for r in result["near"]], [("clk_a", "clk_b", 1), ("clk_b", "clk_b", 1)])
        self.assertTrue(all(r["first"]["time_fs"] < r["second"]["time_fs"] for r in result["near"]))

    def test_cross_period_seam_is_real_and_marked(self):
        p = project([signal("late", [(0, 98), (1, 2)]), signal("early", [(0, 1), (1, 99)])])
        result = model.analyze_edges(p, 3)
        self.assertEqual(result["simultaneous"][0]["time_fs"], 0)
        self.assertTrue(all(e["boundary"] for e in result["simultaneous"][0]["edges"]))
        crosses = result["near"]
        pair = [r for r in crosses if r["first"]["time_fs"] == 98 and r["second"]["time_fs"] == 1]
        self.assertEqual(len(pair), 1)
        self.assertEqual(pair[0]["delta_fs"], 3)
        self.assertTrue(pair[0]["cross_cycle"])
        self.assertEqual(pair[0]["second"]["cycle_offset"], 1)
        p["cycles"] = 1
        one_shot = model.analyze_edges(p, 3)
        self.assertEqual(one_shot["simultaneous"], [])
        self.assertEqual(one_shot["near"], [])
        self.assertEqual(one_shot["edge_count"], 2)

    def test_initial_state_is_not_an_edge_and_equal_levels_merge(self):
        p = project([signal("high_a", [(1, 50), (1, 50)]), signal("high_b", [(1, 100)])])
        result = model.analyze_edges(p, 10)
        self.assertEqual(result["edge_count"], 0)
        self.assertEqual(result["simultaneous"], [])
        self.assertNotIn("@(timer", model.compile_va(p))

    def test_shorter_track_holds_and_has_no_end_edge(self):
        p = project([signal("short_sig", [(0, 10), (1, 10)])])
        self.assertEqual(model.signal_duration(p["signals"][0]), 20)
        result = model.analyze_edges(p, 0)
        self.assertEqual(result["edge_count"], 2)  # rise at 10, wrap at 0 (next cycle)
        self.assertTrue(model.validate(p)["warnings"])
        va = model.compile_va(p)
        self.assertIn("timer(100e-15", va)
        self.assertNotIn("timer(20e-15", va)

    def test_near_deduplicates_circular_pairs(self):
        p = project([signal("a_sig", [(0, 10), (1, 90)]), signal("b_sig", [(0, 80), (1, 20)])])
        r = model.analyze_edges(p, 100)
        identity = lambda e: (e["signal_id"], e["time_fs"])
        pairs = [frozenset((identity(v["first"]), identity(v["second"]))) for v in r["near"]]
        self.assertEqual(len(pairs), len(set(pairs)))
        desired = [v for v in r["near"] if {v["first"]["time_fs"], v["second"]["time_fs"]} == {10, 80}]
        self.assertEqual(len(desired), 1)
        self.assertEqual(desired[0]["delta_fs"], 30)
        self.assertTrue(desired[0]["cross_cycle"])

    def test_output_limit_is_explicit(self):
        p = project([signal("a_sig", [(0, 10), (1, 1), (0, 89)]),
                     signal("b_sig", [(0, 10), (1, 1), (0, 89)])])
        r = model.analyze_edges(p, 10, limit=1)
        self.assertEqual(len(r["simultaneous"]) + len(r["near"]), 1)
        self.assertTrue(r["truncated"])

    def test_va_finite_and_infinite_events_match_independent_fixture(self):
        # Parse the exported timers and execute their counters, independently
        # of the engine's event helpers. This catches final-boundary off-by-one.
        # Export a one-shot, then override cycles just as a netlist can do.
        p = project([signal("stim", [(0, 2), (1, 3), (0, 1), (1, 1)])], period=8, cycles=1)
        va = model.compile_va(p)
        exported = ExportedTimers(va)
        self.assertEqual(len(exported.timers), 4)
        for cycles in [0, 1, 2, 3]:
            with self.subTest(cycles=cycles):
                # Wake all timers beyond the finite stop to catch late changes.
                actual, levels, counts = exported.run(cycles, stop=40)
                iterations = 5 if cycles == 0 else cycles
                expected = []
                for k in range(iterations):
                    if k:
                        expected.append((8 * k, 0, 0))
                    expected.extend([(8 * k + 2, 0, 1), (8 * k + 5, 0, 0), (8 * k + 6, 0, 1)])
                self.assertEqual(actual, sorted(expected))
                self.assertEqual(levels, {0: 1})
                self.assertEqual(counts, {i: cycles for i in counts} if cycles else exported.counts)
        self.assertIn("trise * time_scale, tfall * time_scale", va)
        self.assertNotIn("timer(0e-15", va)

    def test_va_simultaneous_complements_and_wrap_hold_after_last_cycle(self):
        # Both opposite edges must execute at each shared time, including the
        # seam. No final seam is allowed to restore the initial values.
        for period in (8, 800_000, 1_000_000_000_000):
            with self.subTest(period=period):
                edge = period * 3 // 8
                p = project([signal("phase_a", [(0, edge), (1, period - edge)]),
                             signal("phase_b", [(1, edge), (0, period - edge)])], period=period)
                exported = ExportedTimers(model.compile_va(p))
                actions, levels, counts = exported.run(2, stop=6 * period)
                self.assertEqual(actions, [(edge, 0, 1), (edge, 1, 0),
                                           (period, 0, 0), (period, 1, 1),
                                           (period + edge, 0, 1), (period + edge, 1, 0)])
                self.assertEqual(levels, {0: 1, 1: 0})
                self.assertEqual(set(counts.values()), {2})

    def test_va_infinite_counters_do_not_grow_and_finite_counters_are_bounded(self):
        p = project([signal("stim", [(0, 2), (1, 6)])], period=8)
        exported = ExportedTimers(model.compile_va(p))
        actions, _, counts = exported.run(0, stop=8 * 1000)
        self.assertEqual(len(actions), 1999)
        self.assertEqual(counts, exported.counts)

        # Start immediately below the largest legal count. Executing later
        # wakeups must never push a Verilog-A integer beyond this bound.
        near_limit = {i: model.MAX_CYCLES - 1 for i in exported.counts}
        actions, _, counts = exported.run(model.MAX_CYCLES, stop=8 * 10,
                                          initial_counts=near_limit)
        self.assertEqual(actions, [(2, 0, 1), (8, 0, 0)])
        self.assertEqual(set(counts.values()), {model.MAX_CYCLES})
        actions, _, counts = exported.run(model.MAX_CYCLES, stop=8 * 10,
                                          initial_counts=counts)
        self.assertEqual(actions, [])
        self.assertEqual(set(counts.values()), {model.MAX_CYCLES})

    def test_names_injection_and_id_validation(self):
        for name in ["vref", "V", "I", "timer", "real", "module", "_tw_level_0", "x); evil("]:
            p = project([signal("stim", [(0, 100)])])
            p["signals"][0]["name"] = name
            self.assertTrue(model.validate(p)["errors"], name)
        p = project([signal("stim", [(0, 50), (1, 50)])])
        p["signals"][0]["items"][1]["id"] = "stim_0"
        self.assertTrue(model.validate(p)["errors"])

    def test_malformed_inputs_are_rejected_without_type_crash(self):
        for key, value in [("unit", []), ("unit", {}), ("period_fs", True), ("version", 2.0),
                           ("signals", {}), ("signals", [None]), ("vlow", 10 ** 500), ("vhigh", float("inf")),
                           ("module_name", []), ("cycles", 1.5)]:
            p = model.default_project()
            p[key] = value
            with self.subTest(key=key, value=repr(value)[:20]):
                self.assertTrue(model.validate(p)["errors"])
        for text in ['[]', '{"version":2,"version":2}', '{"version":2,"vlow":NaN}', '{}', '"hello"']:
            with self.subTest(text=text), self.assertRaises(model.ModelError):
                model.load_project(text)

    def test_nesting_and_expand_budget(self):
        p = project([signal("stim", [(0, 1)])])
        for depth in range(3):
            p["signals"][0]["items"] = [{"type": "repeat", "id": "block" + str(depth), "count": 2,
                                          "items": p["signals"][0]["items"]}]
        self.assertEqual(model.validate(p)["errors"], [])
        p["signals"][0]["items"] = [{"type": "repeat", "id": "too_deep", "count": 2,
                                      "items": p["signals"][0]["items"]}]
        self.assertTrue(model.validate(p)["errors"])
        p = project([signal("stim", [(0, 1), (1, 1)])], period=100000)
        p["signals"][0]["items"] = [{"type": "repeat", "id": "too_many", "count": 30000,
                                      "items": p["signals"][0]["items"]}]
        self.assertTrue(model.validate(p)["errors"])

    def test_v1_migration_preserves_schedule_and_does_not_mutate(self):
        old = {"version": 1, "moduleName": "old_wave", "unit": "ns", "vLow": 0, "vHigh": 1,
               "rise": 0.1, "fall": 0.2, "repeat": 2,
               "signals": [{"id": "old_id", "name": "clk_sig", "segments": [
                   {"level": 0, "duration": 2.5}, {"level": 1, "duration": 7.5}]}]}
        original = copy.deepcopy(old)
        p = model.load_project(json.dumps(old))
        self.assertEqual(old, original)
        self.assertEqual(p["period_fs"], 10_000_000)
        self.assertEqual(p["cycles"], 2)
        self.assertEqual([(x["start_fs"], x["end_fs"], x["level"]) for x in model.expand_signal(p["signals"][0])],
                         [(0, 2_500_000, 0), (2_500_000, 10_000_000, 1)])
        self.assertEqual(model.load_project(json.dumps(p)), p)


if __name__ == "__main__":
    unittest.main()
