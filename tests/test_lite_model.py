"""Exact time inputs, legacy import preservation, and swept-edge editing."""
from copy import deepcopy
import json
import unittest

import model
from edit_ops import apply_edit


def track(pairs):
    return {"id": "test_signal", "name": "test_signal", "items": [
        {"type": "segment", "id": "part_%d" % i, "level": level, "duration_fs": length}
        for i, (level, length) in enumerate(pairs)]}


def fixture(signal, period):
    result = model.default_project()
    result.update(unit="fs", period_fs=period, rise_fs=1, fall_fs=1, signals=[signal])
    return result


def trace(signal, period):
    values = []
    for part in signal["items"]:
        values.extend([part["level"]] * part["duration_fs"])
    return values + [values[-1]] * (period - len(values))


class AttachedTimeInputTests(unittest.TestCase):
    def test_engineering_suffixes_and_scientific_notation(self):
        cases = {"1n": 1_000_000, "1ns": 1_000_000, "1p": 1000, "250p": 250_000,
                 "0.5u": 500_000_000, "0.5us": 500_000_000, "0.5µs": 500_000_000,
                 "0.5μs": 500_000_000, "1m": 10**12, "1ms": 10**12, "1f": 1,
                 "1fs": 1, "1s": 10**15, "1e-3n": 1000, "2E+2p": 200_000,
                 "  .25 n  ": 250_000, "+1n": 1_000_000, "0n": 0}
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(model.parse_time(text), expected)
        self.assertEqual(model.parse_time("1.5", "ps"), 1500)
        self.assertEqual(model.parse_time(2, "ns"), 2_000_000)
        self.assertEqual(model.parse_time("1e-9", "s"), 1_000_000)

    def test_invalid_or_ambiguous_suffixes_and_sub_femtosecond_rejected(self):
        for text in ["1N", "1NS", "1M", "1S", "1meg", "1sec", "1khz", "1nn", "-1n",
                     "nan", "inf", "1e-7n", "0.1f", "1,2n", "1e99999999n", True,
                     "0.000001000000000000000000000000000001n"]:
            with self.subTest(text=text), self.assertRaises(model.ModelError):
                model.parse_time(text)
        with self.assertRaises(model.ModelError):
            model.parse_time("1n", "bad_unit")

    def test_attached_format_roundtrips_without_float_rounding(self):
        self.assertEqual(model.format_time_input(1_000_000), "1n")
        self.assertEqual(model.format_time_input(250_000, "ps"), "250p")
        for ticks in [0, 1, 1001, 123456789, model.MAX_FS]:
            for unit in model.UNITS:
                with self.subTest(ticks=ticks, unit=unit):
                    text = model.format_time_input(ticks, unit)
                    self.assertNotIn(" ", text)
                    self.assertEqual(model.parse_time(text), ticks)


class FlatLegacyImportTests(unittest.TestCase):
    def test_flatten_preserves_all_integer_times_without_mutating_input(self):
        original = model.incremental_example()
        snapshot = deepcopy(original)
        flattened = model.simplify_project(original)
        self.assertEqual(original, snapshot)
        self.assertEqual(flattened["period_fs"], original["period_fs"])
        all_ids = set()
        for before, after in zip(original["signals"], flattened["signals"]):
            self.assertTrue(all(part["type"] == "segment" for part in after["items"]))
            expected = [(x["start_fs"], x["end_fs"], x["level"])
                        for x in model.expand_signal(before) if x["level"]]
            actual = [(x["start_fs"], x["end_fs"], x["level"])
                      for x in model.expand_signal(after) if x["level"]]
            self.assertEqual(actual, expected)
            for identity in [after["id"]] + [part["id"] for part in after["items"]]:
                self.assertNotIn(identity, all_ids)
                all_ids.add(identity)
        self.assertEqual(model.validate(flattened)["errors"], [])
        self.assertEqual(model.load_project(json.dumps(original)), flattened)
        self.assertEqual(model.simplify_project(flattened), flattened)
        # Port declarations, timer schedules, event counters, and output levels
        # are identical before and after conversion, independently of new IDs.
        self.assertEqual(model.compile_va(flattened), model.compile_va(original))

    def test_plain_tracks_keep_explicit_boundaries_and_ids(self):
        original = fixture(track([(0, 4), (0, 5), (1, 10)]), 20)
        flattened = model.simplify_project(original)
        self.assertEqual(flattened, original)
        flattened["signals"][0]["items"][0]["duration_fs"] = 1
        self.assertEqual(original["signals"][0]["items"][0]["duration_fs"], 4)

    def test_flat_import_supports_full_existing_expansion_budget(self):
        signal = track([(0, 1), (1, 1)])
        signal["items"] = [{"type": "repeat", "id": "legacy_repeat", "count": 25_000,
                            "items": signal["items"]}]
        original = fixture(signal, 50_000)
        flattened = model.simplify_project(original)
        self.assertEqual(len(flattened["signals"][0]["items"]), 50_000)
        self.assertEqual(model.validate(flattened)["errors"], [])
        self.assertEqual(model.signal_duration(flattened["signals"][0]), 50_000)
        original["signals"][0]["items"][0]["count"] = 25_001
        original["period_fs"] = 50_002
        with self.assertRaises(model.ModelError):
            model.simplify_project(original)


class SweptBoundaryTests(unittest.TestCase):
    def assert_normalized(self, signal, period):
        parts = signal["items"]
        self.assertEqual(sum(part["duration_fs"] for part in parts), period)
        self.assertTrue(all(part["duration_fs"] > 0 for part in parts))
        self.assertEqual(len({part["id"] for part in parts}), len(parts))
        self.assertTrue(all(a["level"] != b["level"] for a, b in zip(parts, parts[1:])))

    def test_exact_touch_merges_two_high_sections_without_one_fs_gap(self):
        signal = track([(0, 10), (1, 10), (0, 10), (1, 10), (0, 10)])
        apply_edit(signal, {"op": "boundary", "item_id": "part_1", "new_end_fs": 30}, 50)
        self.assertEqual([(p["level"], p["duration_fs"]) for p in signal["items"]],
                         [(0, 10), (1, 30), (0, 10)])
        self.assertEqual(signal["items"][1]["id"], "part_1")
        self.assert_normalized(signal, 50)

    def test_drag_rising_edge_left_merges_into_previous_high(self):
        signal = track([(0, 10), (1, 10), (0, 10), (1, 10), (0, 10)])
        apply_edit(signal, {"op": "boundary", "item_id": "part_2", "new_end_fs": 20}, 50)
        self.assertEqual([(p["level"], p["duration_fs"]) for p in signal["items"]],
                         [(0, 10), (1, 30), (0, 10)])
        self.assertEqual(signal["items"][1]["id"], "part_3")
        self.assert_normalized(signal, 50)

    def test_both_levels_and_directions_match_independent_swept_interval(self):
        for pairs in [[(0, 3), (1, 4), (0, 3), (1, 3), (0, 2)],
                      [(1, 3), (0, 4), (1, 3), (0, 3), (1, 2)],
                      [(0, 3), (1, 4)]]:
            initial = track(pairs)
            before = trace(initial, 18)
            for index in range(len(pairs) - 1):
                old_end = sum(length for _, length in pairs[:index + 1])
                for requested in range(-2, 21):
                    with self.subTest(pairs=pairs, index=index, end=requested):
                        signal = deepcopy(initial)
                        end = min(18, max(0, requested))
                        expected = before[:]
                        level = pairs[index][0] if end > old_end else pairs[index + 1][0]
                        for tick in range(min(old_end, end), max(old_end, end)):
                            expected[tick] = level
                        apply_edit(signal, {"op": "boundary", "item_id": "part_%d" % index,
                                            "new_end_fs": requested}, 18)
                        self.assertEqual(trace(signal, 18), expected)
                        self.assertEqual(model.validate(fixture(signal, 18))["errors"], [])
                        if end != old_end:
                            self.assert_normalized(signal, 18)

    def test_end_hold_preserved_when_edge_is_extended_beyond_explicit_content(self):
        signal = track([(0, 10), (1, 10)])
        apply_edit(signal, {"op": "boundary", "item_id": "part_0", "new_end_fs": 50}, 100)
        self.assertEqual(trace(signal, 100), [0] * 50 + [1] * 50)

    def test_terminal_hold_endpoint_does_not_create_artificial_edge(self):
        signal = track([(0, 10), (1, 10)])
        snapshot = deepcopy(signal)
        apply_edit(signal, {"op": "boundary", "item_id": "part_1", "new_end_fs": 50}, 100)
        self.assertEqual(signal, snapshot)


if __name__ == "__main__":
    unittest.main()
