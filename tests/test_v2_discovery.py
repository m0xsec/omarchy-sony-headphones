"""Protocol v2 discovery: support lists, named setting slots and the dialects they pick.

A v2 device lists what it implements, and its general-settings slots name
themselves. The lists here are the ones real headphones sent: a WH-1000XM6 on
firmware 3.1.5 and a WH-1000XM5 on firmware 2.5.1 (the latter captured for
gabamnml/omarchy-sony-headphones#3). The stand-ins answer with those lists,
so negotiation, the per-model dialects (XM5 classic equalizer, connection
quality under 0x00, touch panel behind an alert) and the reconnect that
applies a touch-panel change are all exercised through the real framing.
"""

import unittest
from unittest import mock

import support

sonyhp = support.sonyhp


def support_reply(codes, table=sonyhp.MSG_COMMAND_1):
    pairs = b"".join(bytes([code, 0xFF]) for code in codes)
    return table, bytes([sonyhp.V2_SUPPORT_RET, 0x00, len(codes)]) + pairs


class TestSupportLists(unittest.TestCase):
    def setUp(self):
        self.state = sonyhp.initial_state()

    def apply(self, msg_type, payload):
        return sonyhp.apply_payload_v2(self.state, msg_type, payload)

    def test_each_table_is_kept_apart(self):
        self.assertTrue(self.apply(*support_reply([0x20, 0x6B, 0xE1])))
        self.assertTrue(self.apply(*support_reply([0x41], sonyhp.MSG_COMMAND_2)))
        self.assertEqual(self.state["functions"], {"table1": [0x20, 0x6B, 0xE1], "table2": [0x41]})

    def test_the_priority_byte_is_not_a_function(self):
        self.apply(sonyhp.MSG_COMMAND_1, bytes([0x07, 0x00, 0x02, 0x20, 0x6D, 0xE2, 0x41]))
        self.assertEqual(self.state["functions"]["table1"], [0x20, 0xE2])

    def test_a_list_whose_length_disagrees_with_its_count_is_ignored(self):
        self.assertFalse(self.apply(sonyhp.MSG_COMMAND_1, bytes([0x07, 0x00, 0x03, 0x20, 0xFF])))
        self.assertIsNone(self.state["functions"])

    def test_the_captured_xm6_list_parses_whole(self):
        table = sonyhp.DemoDeviceV2.TABLE1
        self.apply(sonyhp.MSG_COMMAND_1, bytes([0x07, 0x00, len(table) // 2]) + table)
        self.assertEqual(len(self.state["functions"]["table1"]), 45)
        self.assertIn(sonyhp.V2_FN_ASC_NOISE_ADAPTATION, self.state["functions"]["table1"])
        self.assertNotIn(sonyhp.V2_FN_CONNECTION_MODE, self.state["functions"]["table1"])


class TestSettingSlots(unittest.TestCase):
    def setUp(self):
        self.state = sonyhp.initial_state()

    def apply(self, payload):
        return sonyhp.apply_payload_v2(self.state, sonyhp.MSG_COMMAND_1, payload)

    def test_the_touch_panel_slot_is_found_by_name(self):
        self.apply(sonyhp._capability_reply(0xD1, b"SIDETONE_SETTING", b"SIDETONE_SETTING_SUMMARY"))
        self.assertIsNone(self.state["touch_slot"])
        self.apply(sonyhp._capability_reply(0xD3, b"TOUCH_PANEL_SETTING"))
        self.assertEqual(self.state["touch_slot"], 0xD3)

    def test_the_captured_xm6_reply_is_read(self):
        # d1 d3 00 01 13 "TOUCH_PANEL_SETTING" 00, as a WH-1000XM6 sent it.
        self.apply(bytes.fromhex("d1d3000113") + b"TOUCH_PANEL_SETTING" + b"\x00")
        self.assertEqual(self.state["touch_slot"], 0xD3)

    def test_a_near_miss_name_is_no_match(self):
        self.apply(sonyhp._capability_reply(0xD1, b"TOUCH_PANEL_SETTING_X"))
        self.apply(bytes([0xD1, 0xD2, 0x00, 0x01, 0x08]) + b"TOUCH_PA")
        self.assertIsNone(self.state["touch_slot"])

    def test_a_slot_outside_the_general_settings_is_ignored(self):
        self.apply(sonyhp._capability_reply(0xC1, b"TOUCH_PANEL_SETTING"))
        self.assertIsNone(self.state["touch_slot"])

    def test_the_touch_value_is_inverted_and_read_only_from_its_slot(self):
        self.state["touch_slot"] = 0xD3
        self.apply(bytes([0xD7, 0xD2, 0x00, 0x01]))
        self.assertIsNone(self.state["touch_sensor"])
        self.apply(bytes([0xD7, 0xD3, 0x00, 0x00]))
        self.assertIs(self.state["touch_sensor"], True)
        self.apply(bytes([0xD9, 0xD3, 0x00, 0x01]))
        self.assertIs(self.state["touch_sensor"], False)

    def test_no_slot_means_no_touch_value(self):
        self.assertFalse(self.apply(bytes([0xD7, 0xD1, 0x00, 0x00])))


class TestRefineFeatures(unittest.TestCase):
    TABLE = ["battery", "equalizer", "dsee", "connection-quality", "speak-to-chat",
             "pause-when-taken-off", "voice-notifications", "auto-power-off",
             "listening-mode", "multipoint"]

    def test_no_list_keeps_the_table(self):
        state = sonyhp.initial_state()
        self.assertEqual(sonyhp.refine_v2_features(self.TABLE, state), sorted(self.TABLE))

    def test_a_pinned_feature_the_device_did_not_list_is_dropped(self):
        state = sonyhp.initial_state()
        state["functions"] = {"table1": [0x20, 0x50, 0xE2, 0xF1, 0x25], "table2": [0x41]}
        features = sonyhp.refine_v2_features(self.TABLE, state)
        self.assertNotIn("speak-to-chat", features)
        self.assertIn("pause-when-taken-off", features)

    def test_unpinned_features_stay_with_the_table(self):
        state = sonyhp.initial_state()
        state["functions"] = {"table1": [0x20], "table2": []}
        features = sonyhp.refine_v2_features(self.TABLE, state)
        for feature in ("connection-quality", "listening-mode", "multipoint"):
            self.assertIn(feature, features)

    def test_either_voice_guidance_code_counts(self):
        # The XM5 lists 0x41 and the XM6 0x42; both have voice guidance.
        for code in (0x41, 0x42):
            state = sonyhp.initial_state()
            state["functions"] = {"table1": [], "table2": [code]}
            self.assertIn("voice-notifications", sonyhp.refine_v2_features(self.TABLE, state))

    def test_the_touch_panel_comes_only_from_its_slot(self):
        state = sonyhp.initial_state()
        self.assertNotIn("touch-sensor", sonyhp.refine_v2_features(self.TABLE + ["touch-sensor"], state))
        state["touch_slot"] = 0xD1
        self.assertIn("touch-sensor", sonyhp.refine_v2_features(self.TABLE, state))

    def test_an_unknown_v2_name_is_not_offered_the_touch_panel(self):
        self.assertNotIn("touch-sensor", sonyhp.features_for("WH-9000 mystery", "v2"))


class TestXM6Negotiation(unittest.TestCase):
    def setUp(self):
        self.link = sonyhp.DemoLinkV2()
        self.link.refresh()

    def test_the_lists_settle_the_dialects(self):
        state = self.link.state
        self.assertEqual(state["asc_subtype"], 0x19)
        self.assertEqual(state["eq_subtype"], 0x04)
        self.assertEqual(state["eq_dialect"], "ten-band")
        self.assertEqual(state["connection_subtype"], 0x02)
        self.assertEqual(state["battery_subtype"], 0x00)
        self.assertEqual(state["touch_slot"], 0xD3)

    def test_the_xm6_keeps_its_whole_set_and_gains_the_touch_panel(self):
        features = set(self.link.state["features"])
        self.assertEqual(features, sonyhp.FEATURE_SETS["v2"]["WH-1000XM6"] | {"touch-sensor"})

    def test_the_touch_panel_reads_on(self):
        self.assertIs(self.link.state["touch_sensor"], True)

    def test_switching_the_touch_panel_off_answers_the_alert(self):
        sonyhp.apply_setting(self.link, "touch-sensor", "off")
        self.assertIs(self.link.state["touch_sensor"], False)
        self.assertEqual(self.link.state["refused"], {})
        sonyhp.apply_setting(self.link, "touch-sensor", "on")
        self.assertIs(self.link.state["touch_sensor"], True)

    def test_a_device_without_lists_still_negotiates_by_probing(self):
        device = sonyhp.DemoDeviceV2()
        device.lists_support = False
        link = sonyhp.DemoLinkV2(device=device)
        link.refresh()
        self.assertIsNone(link.state["functions"])
        self.assertEqual(link.state["asc_subtype"], 0x19)
        self.assertIsNone(link.state["touch_slot"])
        self.assertNotIn("touch-sensor", link.state["features"])
        self.assertEqual(set(link.state["features"]), sonyhp.FEATURE_SETS["v2"]["WH-1000XM6"])


class TestXM5(unittest.TestCase):
    """The WH-1000XM5 dialects, against the stand-in built from its captures."""

    def setUp(self):
        self.link = sonyhp.DemoLinkV2(model="WH-1000XM5")
        self.link.refresh()

    def test_negotiation_picks_the_xm5_dialects(self):
        state = self.link.state
        self.assertEqual(state["name"], "WH-1000XM5 (demo)")
        self.assertEqual(state["asc_subtype"], 0x17)
        self.assertEqual(state["eq_subtype"], 0x00)
        self.assertEqual(state["eq_dialect"], "classic")
        self.assertEqual(state["connection_subtype"], 0x00)
        self.assertEqual(state["touch_slot"], 0xD1)
        self.assertEqual(state["firmware"], "2.5.1")

    def test_features(self):
        features = set(self.link.state["features"])
        self.assertIn("touch-sensor", features)
        self.assertIn("connection-quality", features)
        self.assertIn("voice-notifications", features)
        self.assertNotIn("listening-mode", features)
        self.assertNotIn("multipoint", features)

    def test_the_classic_equalizer_reads_bass_and_five_bands(self):
        state = self.link.state
        self.assertEqual(state["eq_preset"], "off")
        self.assertEqual(state["eq_bass"], 0)
        self.assertEqual(state["eq_bands"], [0, 0, 0, 0, 0])

    def test_a_classic_preset_round_trips(self):
        sonyhp.apply_setting(self.link, "eq", "bass-boost")
        self.assertEqual(self.link.state["eq_preset"], "bass-boost")
        self.assertEqual(self.link.state["eq_bass"], 7)

    def test_the_ten_band_presets_are_refused(self):
        with self.assertRaisesRegex(ValueError, "unknown equalizer preset"):
            sonyhp.apply_setting(self.link, "eq", "heavy")

    def test_classic_bands_round_trip(self):
        sonyhp.apply_setting(self.link, "eq-bands", "3,1,-2,0,4,-10")
        self.assertEqual(self.link.state["eq_preset"], "manual")
        self.assertEqual(self.link.state["eq_bass"], 3)
        self.assertEqual(self.link.state["eq_bands"], [1, -2, 0, 4, -10])

    def test_ten_bands_are_refused(self):
        with self.assertRaisesRegex(ValueError, "6 values"):
            sonyhp.apply_setting(self.link, "eq-bands", "0,0,0,0,0,0,0,0,0,0")

    def test_connection_quality_rides_subtype_zero(self):
        requests = sonyhp.setting_requests_v2(self.link.state, "connection-quality", "stable")
        self.assertEqual(requests[0], (sonyhp.MSG_COMMAND_1, bytes([0xE8, 0x00, 0x01])))
        sonyhp.apply_setting(self.link, "connection-quality", "stable")
        self.assertEqual(self.link.state["connection_quality"], "stable")

    def test_voice_guidance_confirms_through_the_short_notification(self):
        sonyhp.apply_setting(self.link, "voice-notifications", "off")
        self.assertIs(self.link.state["voice_notifications"], False)
        self.assertEqual(self.link.state["refused"], {})

    def test_the_touch_panel_off_switch_enables_alerts_first(self):
        requests = sonyhp.setting_requests_v2(self.link.state, "touch-sensor", "off")
        self.assertEqual([p.hex() for _, p in requests], ["940000", "d8d10001", "d6d1"])
        requests = sonyhp.setting_requests_v2(self.link.state, "touch-sensor", "on")
        self.assertEqual([p.hex() for _, p in requests], ["d8d10000", "d6d1"])

    def test_the_touch_panel_round_trips(self):
        sonyhp.apply_setting(self.link, "touch-sensor", "off")
        self.assertIs(self.link.state["touch_sensor"], False)
        sonyhp.apply_setting(self.link, "touch-sensor", "on")
        self.assertIs(self.link.state["touch_sensor"], True)


class DroppingTransport(sonyhp.DemoTransport):
    """A stand-in that drops the link once its touch-panel alert is answered."""

    dropped = False

    def send(self, frame):
        super().send(frame)
        _, _, payload = sonyhp.decode_message(frame)
        if payload[:1] == bytes([sonyhp.V2_ALERT_SET_PARAM]):
            self.dropped = True

    def recv(self, timeout):
        if self.dropped and not self.pending:
            return b""
        return super().recv(timeout)


class TestAlerts(unittest.TestCase):
    def test_only_touch_panel_alerts_are_answered(self):
        self.assertEqual(
            sonyhp.alert_confirmation(sonyhp.MSG_COMMAND_1, bytes([0x99, 0x00, 0x0B, 0x01])),
            (sonyhp.MSG_COMMAND_1, bytes([0x98, 0x00, 0x0B, 0x01])))
        for payload in ([0x99, 0x00, 0x05, 0x01], [0x99, 0x01, 0x0B, 0x01], [0x99, 0x00, 0x0B]):
            self.assertIsNone(sonyhp.alert_confirmation(sonyhp.MSG_COMMAND_1, bytes(payload)))
        self.assertIsNone(sonyhp.alert_confirmation(sonyhp.MSG_COMMAND_2, bytes([0x99, 0x00, 0x0B, 0x01])))

    def test_an_alert_nobody_asked_for_goes_unanswered(self):
        link = sonyhp.DemoLinkV2(model="WH-1000XM5")
        with mock.patch.object(link, "write") as write:
            link.dispatch(sonyhp.MSG_COMMAND_1, bytes([0x99, 0x00, 0x0B, 0x01]))
            link._flush()
        write.assert_not_called()

    def test_the_announced_drop_is_reported_as_reconnecting(self):
        link = sonyhp.DemoLinkV2(model="WH-1000XM5")
        link.refresh()
        link.transport = DroppingTransport(link.device)
        with self.assertRaises(sonyhp.Reconnecting):
            sonyhp.apply_setting(link, "touch-sensor", "off")
        self.assertEqual(link.state["pending"], [])
        self.assertEqual(link.state["refused"], {})
        self.assertFalse(link.confirm_alerts)

    def test_an_unannounced_drop_is_still_a_lost_link(self):
        link = sonyhp.DemoLinkV2(model="WH-1000XM5")
        link.refresh()
        transport = DroppingTransport(link.device)
        transport.dropped = True
        link.transport = transport
        with self.assertRaises(sonyhp.NotConnected) as caught:
            sonyhp.apply_setting(link, "dsee", "off")
        self.assertNotIsInstance(caught.exception, sonyhp.Reconnecting)

    def test_the_daemon_treats_reconnecting_as_done_and_does_not_retry(self):
        daemon = sonyhp.Daemon()
        daemon.link = mock.Mock()
        calls = []

        def action(link):
            calls.append(link)
            raise sonyhp.Reconnecting("the headphones are reconnecting to apply touch-sensor")

        with mock.patch.object(daemon, "lose_link") as lose:
            self.assertIsNone(daemon.with_link(action))
        self.assertEqual(len(calls), 1)
        lose.assert_called_once()


class TestSubtypeReplies(unittest.TestCase):
    def setUp(self):
        self.state = sonyhp.initial_state()

    def apply(self, payload):
        return sonyhp.apply_payload_v2(self.state, sonyhp.MSG_COMMAND_1, bytes(payload))

    def test_connection_quality_reads_only_the_chosen_subtype(self):
        # An XM6 answers 0x00 as well as its own 0x02, and they need not agree.
        self.apply([0xE7, 0x00, 0x01])
        self.assertIsNone(self.state["connection_quality"])
        self.apply([0xE7, 0x02, 0x00])
        self.assertEqual(self.state["connection_quality"], "sound-quality")
        self.state["connection_subtype"] = 0x00
        self.apply([0xE7, 0x00, 0x01])
        self.assertEqual(self.state["connection_quality"], "stable")
        self.apply([0xE7, 0x02, 0x00])
        self.assertEqual(self.state["connection_quality"], "stable")

    def test_ten_bands_under_subtype_zero_are_still_ten_band(self):
        # What a WH-1000XM6 sends for `56 00`.
        self.apply([0x57, 0x00, 0x00, 0x0A] + [0x06] * 10)
        self.assertEqual(self.state["eq_dialect"], "ten-band")
        self.assertEqual(self.state["eq_bands"], [0] * 10)

    def test_six_values_are_the_classic_layout(self):
        self.apply([0x57, 0x00, 0x14, 0x06, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F])
        self.assertEqual(self.state["eq_dialect"], "classic")
        self.assertEqual(self.state["eq_preset"], "vocal")
        self.assertEqual(self.state["eq_bass"], 0)
        self.assertEqual(self.state["eq_bands"], [1, 2, 3, 4, 5])

    def test_the_battery_with_threshold_variant_is_read(self):
        self.apply([0x23, 0x08, 0x37, 0x01])
        self.assertEqual(self.state["battery"], 0x37)
        self.assertIs(self.state["charging"], True)

    def test_read_support_picks_the_threshold_battery_when_only_it_is_listed(self):
        link = sonyhp.DemoLinkV2()
        link.state["functions"] = None
        link.device.TABLE1 = bytes([0x28, 0xFF, 0x6D, 0xFF])
        link.read_support()
        self.assertEqual(link.state["battery_subtype"], 0x08)
        self.assertEqual(sonyhp.V2Protocol().poll_requests(link.state)[0],
                         (sonyhp.MSG_COMMAND_1, bytes([0x22, 0x08])))


if __name__ == "__main__":
    unittest.main(verbosity=2)
