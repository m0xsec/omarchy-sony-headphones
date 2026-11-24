"""The audio codec, chosen through PipeWire, and the renegotiation it causes.

The card layouts below are the ones `pactl --format=json list cards` printed
for a WH-1000XM6 on 2026-11-23, before and after its connection quality moved
to a stable connection. The stand-ins answer for PipeWire the same way, so a
codec switch and a connection-quality change run end to end without radio.
"""

import json
import subprocess
import unittest
from unittest import mock

import support

sonyhp = support.sonyhp

ADDRESS = "AA:BB:CC:DD:EE:FF"


def profile(codec, available=True):
    return {"description": f"High Fidelity Playback (A2DP Sink, codec {codec})", "available": available}


def xm6_card(active="a2dp-sink", stable=False):
    profiles = {
        "off": {"description": "Off", "available": True},
        "headset-head-unit": {"description": "Headset Head Unit (HSP/HFP, codec MSBC)", "available": True},
    }
    if stable:
        # A stable connection takes LDAC away, and `a2dp-sink` then means AAC.
        profiles.update({"a2dp-sink": profile("AAC"), "a2dp-sink-sbc_xq": profile("SBC-XQ"),
                         "a2dp-sink-sbc": profile("SBC")})
    else:
        profiles.update({"a2dp-sink": profile("LDAC"), "a2dp-sink-aac": profile("AAC"),
                         "a2dp-sink-sbc_xq": profile("SBC-XQ"), "a2dp-sink-sbc": profile("SBC")})
    return {
        "name": "bluez_card.AA_BB_CC_DD_EE_FF",
        "active_profile": active,
        "properties": {"api.bluez5.address": ADDRESS},
        "profiles": profiles,
    }


class TestProfiles(unittest.TestCase):
    def test_codecs_come_from_the_description_best_first(self):
        codecs = sonyhp.read_audio_codecs(xm6_card())
        self.assertEqual(codecs, {"codecs": ["LDAC", "AAC", "SBC-XQ", "SBC"], "a2dp_codec": "LDAC"})

    def test_a_shifted_profile_name_is_read_by_its_codec(self):
        codecs = sonyhp.read_audio_codecs(xm6_card(stable=True))
        self.assertEqual(codecs, {"codecs": ["AAC", "SBC-XQ", "SBC"], "a2dp_codec": "AAC"})

    def test_headset_and_unavailable_profiles_are_left_out(self):
        card = xm6_card(active="headset-head-unit")
        card["profiles"]["a2dp-sink-aac"]["available"] = False
        codecs = sonyhp.read_audio_codecs(card)
        self.assertEqual(codecs["codecs"], ["LDAC", "SBC-XQ", "SBC"])
        self.assertIsNone(codecs["a2dp_codec"])

    def test_a_profile_name_that_is_not_plain_is_never_offered(self):
        card = xm6_card()
        card["profiles"]["a2dp-sink; rm -rf"] = profile("aptX")
        card["profiles"]["--help"] = profile("LC3")
        self.assertEqual(sonyhp.read_audio_codecs(card)["codecs"], ["LDAC", "AAC", "SBC-XQ", "SBC"])

    def test_the_list_is_bounded(self):
        card = xm6_card()
        card["profiles"] = {f"a2dp-sink-x{i}": profile(f"X{i}") for i in range(40)}
        self.assertEqual(len(sonyhp.read_audio_codecs(card)["codecs"]), sonyhp.MAX_CODECS)

    def test_no_card_offers_nothing(self):
        self.assertEqual(sonyhp.read_audio_codecs(None), {"codecs": [], "a2dp_codec": None})


class TestPactl(unittest.TestCase):
    def test_the_card_is_found_by_address(self):
        other = dict(xm6_card(), name="bluez_card.11_22", properties={"api.bluez5.address": "11:22:33:44:55:66"})
        listing = json.dumps([{"name": "alsa_card.pci"}, other, xm6_card()])
        with mock.patch.object(sonyhp, "pactl", return_value=listing):
            self.assertEqual(sonyhp.bluez_card(ADDRESS.lower())["name"], "bluez_card.AA_BB_CC_DD_EE_FF")

    def test_garbage_from_pactl_is_no_card(self):
        for output in (None, "", "not json", json.dumps({"name": "x"})):
            with mock.patch.object(sonyhp, "pactl", return_value=output):
                self.assertIsNone(sonyhp.bluez_card(ADDRESS))

    def test_pactl_runs_from_its_path_with_a_minimal_environment(self):
        done = subprocess.CompletedProcess([], 0, stdout="[]", stderr="")
        with mock.patch.object(sonyhp, "PACTL", "/usr/bin/pactl"), \
                mock.patch.object(sonyhp.subprocess, "run", return_value=done) as run, \
                mock.patch.object(sonyhp, "is_private_dir", return_value=True), \
                mock.patch.dict(sonyhp.os.environ, {"XDG_RUNTIME_DIR": "/run/user/1000",
                                                    "LD_PRELOAD": "/tmp/evil.so"}):
            self.assertEqual(sonyhp.pactl("list"), "[]")
        argv, kwargs = run.call_args
        self.assertEqual(argv[0], ["/usr/bin/pactl", "list"])
        self.assertEqual(kwargs["env"], {"PATH": "/usr/bin:/bin", "LC_ALL": "C",
                                         "XDG_RUNTIME_DIR": "/run/user/1000"})

    def test_an_unverified_runtime_directory_is_not_passed(self):
        done = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with mock.patch.object(sonyhp, "PACTL", "/usr/bin/pactl"), \
                mock.patch.object(sonyhp.subprocess, "run", return_value=done) as run, \
                mock.patch.object(sonyhp, "is_private_dir", return_value=False), \
                mock.patch.dict(sonyhp.os.environ, {"XDG_RUNTIME_DIR": "/tmp/shared"}):
            sonyhp.pactl("list")
        self.assertNotIn("XDG_RUNTIME_DIR", run.call_args[1]["env"])

    def test_a_failing_or_missing_pactl_is_none(self):
        with mock.patch.object(sonyhp, "PACTL", None):
            self.assertIsNone(sonyhp.pactl("list"))
        failed = subprocess.CompletedProcess([], 1, stdout="", stderr="boom")
        with mock.patch.object(sonyhp, "PACTL", "/usr/bin/pactl"), \
                mock.patch.object(sonyhp.subprocess, "run", return_value=failed):
            self.assertIsNone(sonyhp.pactl("list"))


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class TestPipeWireSwitch(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.audio = sonyhp.PipeWireAudio(ADDRESS, clock=self.clock, sleep=self.clock.sleep)
        self.cards = [xm6_card()]

    def card(self, address):
        return self.cards[0]

    def test_a_switch_counts_once_the_card_reports_it(self):
        def set_profile(*args, **kwargs):
            self.cards[0] = xm6_card(active="a2dp-sink-aac")
            return ""
        with mock.patch.object(sonyhp, "bluez_card", side_effect=self.card), \
                mock.patch.object(sonyhp, "pactl", side_effect=set_profile) as pactl:
            self.audio.switch("AAC")
        pactl.assert_called_once_with("set-card-profile", "bluez_card.AA_BB_CC_DD_EE_FF", "a2dp-sink-aac")

    def test_a_switch_that_never_shows_is_refused_with_a_reason(self):
        with mock.patch.object(sonyhp, "bluez_card", side_effect=self.card), \
                mock.patch.object(sonyhp, "pactl", return_value=""):
            with self.assertRaisesRegex(ValueError, "another Bluetooth device"):
                self.audio.switch("SBC")
        self.assertGreaterEqual(self.clock.now, sonyhp.CODEC_SWITCH_TIMEOUT)

    def test_a_codec_not_on_offer_is_refused_without_asking(self):
        with mock.patch.object(sonyhp, "bluez_card", side_effect=self.card), \
                mock.patch.object(sonyhp, "pactl") as pactl:
            with self.assertRaisesRegex(ValueError, "not on offer.*LDAC, AAC, SBC-XQ, SBC"):
                self.audio.switch("aptX")
        pactl.assert_not_called()

    def test_pactl_refusing_is_reported(self):
        with mock.patch.object(sonyhp, "bluez_card", side_effect=self.card), \
                mock.patch.object(sonyhp, "pactl", return_value=None):
            with self.assertRaisesRegex(ValueError, "refused"):
                self.audio.switch("AAC")


def recording_link(**kwargs):
    seen = []
    link = sonyhp.DemoLinkV2(on_change=lambda state: seen.append(dict(state)), **kwargs)
    link.refresh()
    seen.clear()
    return link, seen


class TestApplyCodec(unittest.TestCase):
    def test_the_demo_offers_its_codecs_after_a_refresh(self):
        link, _ = recording_link()
        self.assertEqual(link.state["codecs"], ["LDAC", "AAC", "SBC-XQ", "SBC"])
        self.assertEqual(link.state["a2dp_codec"], "LDAC")
        self.assertFalse(link.state["audio_settling"])

    def test_a_switch_lands_and_the_headphones_agree(self):
        link, seen = recording_link()
        sonyhp.apply_setting(link, "codec", "AAC")
        self.assertEqual(link.state["a2dp_codec"], "AAC")
        self.assertEqual(link.state["codec"], "AAC")
        self.assertEqual(link.state["pending"], [])
        self.assertEqual(link.state["refused"], {})
        self.assertFalse(link.state["audio_settling"])
        self.assertTrue(any(state["audio_settling"] for state in seen),
                        "the switch must be published as settling while it runs")

    def test_sbc_xq_is_reported_by_the_headphones_as_sbc(self):
        link, _ = recording_link()
        sonyhp.apply_setting(link, "codec", "SBC-XQ")
        self.assertEqual(link.state["a2dp_codec"], "SBC-XQ")
        self.assertEqual(link.state["codec"], "SBC")

    def test_a_busy_codec_is_a_refusal_on_the_row(self):
        link, _ = recording_link()
        link.audio.busy.add("SBC")
        state = sonyhp.apply_setting(link, "codec", "SBC")
        self.assertIn("another Bluetooth device", state["refused"]["codec"])
        self.assertEqual(state["a2dp_codec"], "LDAC")
        self.assertEqual(state["pending"], [])
        self.assertFalse(state["audio_settling"])

    def test_a_codec_not_on_offer_never_claims_to_be_in_flight(self):
        link, seen = recording_link()
        with self.assertRaisesRegex(ValueError, "not on offer"):
            sonyhp.apply_setting(link, "codec", "aptX")
        self.assertEqual(link.state["pending"], [])
        self.assertFalse(any(state.get("pending") for state in seen))

    def test_the_v1_stand_in_switches_too(self):
        link = sonyhp.DemoLink()
        link.refresh()
        self.assertEqual(link.state["codecs"], ["LDAC", "AAC", "SBC"])
        sonyhp.apply_setting(link, "codec", "AAC")
        self.assertEqual(link.state["a2dp_codec"], "AAC")
        self.assertEqual(link.state["codec"], "AAC")

    def test_codec_is_no_protocol_builders_key(self):
        state = sonyhp.initial_state()
        for builder in (sonyhp.setting_requests, sonyhp.setting_requests_v2):
            with self.assertRaises(ValueError):
                builder(state, "codec", "AAC")
        self.assertNotIn("codec", sonyhp.SETTING_KEYS)
        self.assertIn("codec", sonyhp.HOST_SETTING_KEYS)


class TestConnectionQuality(unittest.TestCase):
    def test_a_stable_connection_takes_ldac_off_the_codec_list(self):
        link, seen = recording_link()
        sonyhp.apply_setting(link, "connection-quality", "stable")
        self.assertEqual(link.state["codecs"], ["AAC", "SBC-XQ", "SBC"])
        self.assertEqual(link.state["a2dp_codec"], "AAC")
        self.assertEqual(link.state["codec"], "AAC")
        self.assertFalse(link.state["audio_settling"])
        self.assertTrue(any(state["audio_settling"] for state in seen))
        sonyhp.apply_setting(link, "connection-quality", "sound-quality")
        self.assertEqual(link.state["codecs"], ["LDAC", "AAC", "SBC-XQ", "SBC"])
        self.assertEqual(link.state["a2dp_codec"], "LDAC")

    def test_other_settings_never_mark_the_audio_link_settling(self):
        link, seen = recording_link()
        sonyhp.apply_setting(link, "dsee", "off")
        self.assertFalse(any(state["audio_settling"] for state in seen))


class FrozenAudio:
    """An audio side whose list never moves, as a WH-1000XM5 once left it."""

    def __init__(self, codecs):
        self.codecs = codecs
        self.reconnects = 0

    def read(self):
        return {"codecs": list(self.codecs), "a2dp_codec": self.codecs[0]}

    def switch(self, codec):
        raise ValueError("unused")

    def reconnect(self):
        self.reconnects += 1
        self.codecs = ["LDAC"] + [c for c in self.codecs if c != "LDAC"]


class TestSettle(unittest.TestCase):
    def slow_link(self, audio):
        link = sonyhp.DemoLinkV2()
        link.refresh()
        link.audio = audio
        link.answers_immediately = False
        return link

    def test_an_audio_link_that_does_not_renegotiate_is_reconnected_once(self):
        audio = FrozenAudio(["AAC", "SBC"])
        link = self.slow_link(audio)
        link.read_audio()
        with mock.patch.object(sonyhp, "QUALITY_RENEGOTIATION_GRACE", 0.0), \
                mock.patch.object(sonyhp, "QUALITY_SETTLE_TIMEOUT", 3.0), \
                mock.patch.object(link, "pump"):
            sonyhp.settle_connection_quality(link, ["AAC", "SBC"])
        self.assertEqual(audio.reconnects, 1)
        self.assertEqual(link.state["codecs"], ["LDAC", "AAC", "SBC"])

    def test_without_audio_to_begin_with_there_is_nothing_to_wait_for(self):
        audio = FrozenAudio(["AAC"])
        link = self.slow_link(audio)
        with mock.patch.object(link, "pump") as pump:
            sonyhp.settle_connection_quality(link, [])
        pump.assert_not_called()
        self.assertEqual(audio.reconnects, 0)

    def test_a_control_link_taken_mid_settle_is_left_to_the_daemon(self):
        # The phone's Sound Connect app can take the session once the headset
        # comes back; the write already landed, so nothing is raised.
        audio = FrozenAudio(["AAC"])
        link = self.slow_link(audio)
        with mock.patch.object(link, "pump", side_effect=ConnectionResetError("reset")), \
                mock.patch.object(sonyhp, "QUALITY_RENEGOTIATION_GRACE", 99.0):
            sonyhp.settle_connection_quality(link, ["AAC"])

    def test_a_write_that_fails_clears_the_settling_flag(self):
        link, _ = recording_link()
        with mock.patch.object(link, "write", side_effect=sonyhp.NotConnected("gone")):
            with self.assertRaises(sonyhp.NotConnected):
                sonyhp.apply_setting(link, "connection-quality", "stable")
        self.assertFalse(link.state["audio_settling"])


class LateAudio:
    """An audio link that is not up when the control link connects."""

    def __init__(self):
        self.up = False

    def read(self):
        if not self.up:
            return {"codecs": [], "a2dp_codec": None}
        return {"codecs": ["LDAC", "AAC"], "a2dp_codec": "LDAC"}


class TestAudioPoll(unittest.TestCase):
    def daemon(self):
        clock = FakeClock()
        daemon = sonyhp.Daemon(clock=clock)
        link = sonyhp.DemoLinkV2()
        link.audio = LateAudio()
        link.refresh()
        daemon.link = link
        return daemon, clock, link

    def test_an_audio_link_that_comes_up_later_is_noticed(self):
        # A control link that connected while the A2DP link was down kept an
        # empty list, and a hidden codec row, until the codec next changed.
        daemon, clock, link = self.daemon()
        self.assertEqual(link.state["codecs"], [])
        link.audio.up = True
        with mock.patch.object(daemon, "publish") as publish:
            daemon.poll_audio()
        self.assertEqual(link.state["codecs"], ["LDAC", "AAC"])
        publish.assert_called_once_with()

    def test_the_poll_is_paced_and_quiet_when_nothing_moved(self):
        daemon, clock, link = self.daemon()
        with mock.patch.object(daemon, "publish") as publish:
            daemon.poll_audio()
            link.audio.up = True
            daemon.poll_audio()
            self.assertEqual(link.state["codecs"], [])
            clock.now += daemon.AUDIO_POLL_INTERVAL
            daemon.poll_audio()
        self.assertEqual(link.state["codecs"], ["LDAC", "AAC"])
        publish.assert_called_once_with()

    def test_a_settling_link_is_left_to_the_change_in_flight(self):
        daemon, clock, link = self.daemon()
        link.audio.up = True
        link.state["audio_settling"] = True
        daemon.poll_audio()
        self.assertEqual(link.state["codecs"], [])

    def test_no_link_no_poll(self):
        daemon = sonyhp.Daemon()
        daemon.poll_audio()


class TestDaemonAndCli(unittest.TestCase):
    def test_the_precheck_refuses_a_codec_pipewire_did_not_offer(self):
        daemon = sonyhp.Daemon()
        known = sonyhp.initial_state()
        known.update(protocol="v2", codecs=["LDAC", "AAC"])
        with mock.patch.object(daemon, "_known_state", return_value=known):
            self.assertIn("not on offer",
                          daemon.precheck_setting({"cmd": "set", "key": "codec", "value": "SBC"}))
            self.assertIsNone(daemon.precheck_setting({"cmd": "set", "key": "codec", "value": "AAC"}))
        known["codecs"] = []
        with mock.patch.object(daemon, "_known_state", return_value=known):
            self.assertIsNone(daemon.precheck_setting({"cmd": "set", "key": "codec", "value": "SBC"}))

    def test_slow_settings_wait_longer_for_the_daemon(self):
        answer = {"ok": True, "state": {}}
        with mock.patch.object(sonyhp, "ask_daemon", return_value=answer) as ask:
            for key in ("codec", "connection-quality"):
                sonyhp.run_request({"cmd": "set", "key": key, "value": "x"}, None)
                self.assertEqual(ask.call_args[1]["timeout"], sonyhp.SLOW_REQUEST_TIMEOUT)
            sonyhp.run_request({"cmd": "set", "key": "dsee", "value": "on"}, None)
            self.assertEqual(ask.call_args[1]["timeout"], 10.0)

    def test_the_cli_accepts_codec(self):
        args = sonyhp.build_parser().parse_args(["set", "codec", "AAC"])
        self.assertEqual((args.key, args.value), ("codec", "AAC"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
