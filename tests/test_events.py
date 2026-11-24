"""The headset's event log and wear state, and the widget wiring that acts on them.

The frames are the ones a WH-1000XM6 (firmware 3.1.5) sent on 2026-11-23:
`c9 01` and three length-prefixed strings for every gesture, and
`f5 10 01 <state>` for wear detection.
"""

import os
import re
import unittest

import support

sonyhp = support.sonyhp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def frame(hexstring):
    return bytes.fromhex(hexstring)


# As captured: touch and hold (action first, then the gesture), a double tap,
# a wear change, and a multipoint hand-off.
OP_GOOGLE = frame("c90100086f70476f6f676c6500")
KEY_HOLD = frame("c901086b657952486f6c640000")
KEY_DOUBLE_TAP = frame("c9010d6b657952446f75626c655461700000")
UNIT_REMOVE = frame("c9010a756e697452656d6f76650000")
MP_CHANGE = frame("c90100096d704368674132647000")


class TestEventFrames(unittest.TestCase):
    def test_the_captured_frames_decode(self):
        self.assertEqual(sonyhp.parse_v2_event(OP_GOOGLE), ["opGoogle"])
        self.assertEqual(sonyhp.parse_v2_event(KEY_HOLD), ["keyRHold"])
        self.assertEqual(sonyhp.parse_v2_event(KEY_DOUBLE_TAP), ["keyRDoubleTap"])
        self.assertEqual(sonyhp.parse_v2_event(UNIT_REMOVE), ["unitRemove"])
        self.assertEqual(sonyhp.parse_v2_event(MP_CHANGE), ["mpChgA2dp"])

    def test_a_length_that_overruns_the_frame_is_nothing(self):
        self.assertEqual(sonyhp.parse_v2_event(frame("c9011f6b6579")), [])

    def test_names_that_are_not_plain_identifiers_are_dropped(self):
        for name in (b"key Hold", b"key;rm", b"1key", b"k" * 40, "keý".encode("utf-8")):
            payload = bytes([0xC9, 0x01, len(name)]) + name + b"\x00\x00"
            self.assertEqual(sonyhp.parse_v2_event(payload), [], name)

    def test_other_frames_are_not_events(self):
        for payload in (b"", frame("c9"), frame("c90200"), frame("a92008")):
            self.assertEqual(sonyhp.parse_v2_event(payload), [])


class TestOperationLog(unittest.TestCase):
    def test_the_switch_on_request(self):
        self.assertEqual(sonyhp.v2_operation_log_request(),
                         (sonyhp.MSG_COMMAND_1, bytes([0xC4, 0x01, 0x00])))

    def test_negotiation_asks_for_the_log(self):
        link = sonyhp.DemoLinkV2.__new__(sonyhp.DemoLinkV2)
        sent = []
        device = sonyhp.DemoDeviceV2()
        real = device.respond

        def respond(msg_type, payload):
            sent.append(bytes(payload))
            return real(msg_type, payload)
        device.respond = respond
        sonyhp.DemoLinkV2.__init__(link, device=device)
        self.assertIn(bytes([0xC4, 0x01, 0x00]), sent)

    def test_sub_entries_after_the_value_are_not_read_as_names(self):
        # key "keyRHold", empty value, then one sub-entry ("dur", "long").
        payload = (bytes([0xC9, 0x01, 8]) + b"keyRHold" + b"\x00" + b"\x01"
                   + bytes([3]) + b"dur" + bytes([4]) + b"long")
        self.assertEqual(sonyhp.parse_v2_event(payload), ["keyRHold"])


class TestEventState(unittest.TestCase):
    def setUp(self):
        self.state = sonyhp.initial_state()

    def apply(self, payload):
        return sonyhp.apply_payload_v2(self.state, sonyhp.MSG_COMMAND_1, payload)

    def test_each_event_moves_the_count(self):
        self.assertTrue(self.apply(OP_GOOGLE))
        self.assertTrue(self.apply(KEY_HOLD))
        self.assertEqual((self.state["last_event"], self.state["event_seq"]), ("keyRHold", 2))

    def test_a_repeated_gesture_is_still_a_change(self):
        self.apply(KEY_HOLD)
        self.assertTrue(self.apply(KEY_HOLD))
        self.assertEqual(self.state["event_seq"], 2)

    def test_a_malformed_event_changes_nothing(self):
        self.assertFalse(self.apply(frame("c9011f6b6579")))
        self.assertEqual(self.state["event_seq"], 0)
        self.assertIsNone(self.state["last_event"])

    def test_wear_state(self):
        self.assertTrue(self.apply(frame("f5100100")))
        self.assertIs(self.state["worn"], True)
        self.assertTrue(self.apply(frame("f5100101")))
        self.assertIs(self.state["worn"], False)

    def test_an_unknown_wear_shape_is_ignored(self):
        for payload in ("f5100102", "f51101", "f511010000", "f5100200"):
            self.assertFalse(self.apply(frame(payload)), payload)
        self.assertIsNone(self.state["worn"])

    def test_a_link_folds_events_through_dispatch(self):
        link = sonyhp.DemoLinkV2()
        link.refresh()
        seen = []
        link.on_change = lambda state: seen.append((state["last_event"], state["event_seq"]))
        link.dispatch(sonyhp.MSG_COMMAND_1, KEY_HOLD)
        link.dispatch(sonyhp.MSG_COMMAND_1, KEY_HOLD)
        self.assertEqual(seen, [("keyRHold", 1), ("keyRHold", 2)])


class TestWidgetWiring(unittest.TestCase):
    """The QML is read as text, the way test_cli keeps the launch lines in step."""

    @classmethod
    def setUpClass(cls):
        def read(name):
            with open(os.path.join(ROOT, name), encoding="utf-8") as handle:
                return handle.read()
        cls.service, cls.panel, cls.manifest = read("Service.qml"), read("Panel.qml"), read("manifest.json")

    def test_the_action_runs_in_the_session_environment_untouched(self):
        # The user's own command, never assembled from anything the headset sent.
        self.assertIn('Quickshell.execDetached(["bash", "-lc", holdAction])', self.service)
        self.assertNotRegex(self.service, r"holdAction\s*\+")

    def test_the_action_runs_only_on_a_new_hold(self):
        self.assertRegex(self.service,
                         r'holdAction\s*!==\s*""\s*&&\s*Model\.holdTriggered\(state,\s*next\)')

    def test_the_setting_reaches_the_service(self):
        self.assertRegex(self.panel, r'setting\(\s*"holdAction"')
        self.assertRegex(self.panel, r"holdAction\s*:\s*root\.holdAction")
        self.assertTrue(re.search(r'"key":\s*"holdAction"', self.manifest))
        self.assertTrue(re.search(r'"holdAction":\s*""', self.manifest))


if __name__ == "__main__":
    unittest.main(verbosity=2)
