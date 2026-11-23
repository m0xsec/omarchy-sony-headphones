# 0010 — Keep one multipoint peer playing at a time

## Context

With a phone and this computer both connected over multipoint, Android's
auto-switch moves the headset to whichever device starts playing audio. The
headset does not stop the device it switched away from: that device keeps
playing into nothing, and the moment auto-switch notices, it pulls the headset
back. On a WH-1000XM6 the source bounced between the phone and the desktop
every few seconds for as long as both were playing.

The plugin could already pause local playback, but only when the user picked a
playback source in the panel (`Model.pausesLocalPlayback`); a switch the
headset made was deliberately left alone. It had no way at all to stop the
phone.

Two facts make both directions possible. The headset announces every change of
playback source on the multipoint device list (`V2_PERI_NOTIFY`), within a
second, while the session is held. And Sony's v2 protocol carries remote
playback control: `PLAY_SET_STATUS` (`0xA4`) with the "playback control" inquired
type (`0x01`), enable (`0x00`) and a control byte, where pause is `0x01` — the
layout libmdr names `SetPlayStatusPlaybackController`
(mos9527/SonyHeadphonesClient, `libmdr/include/mdr/Generated/ProtocolV2T1.hpp`).
The headset forwards that control over AVRCP to the current playback source.
Sent from this computer with the phone as the source, it paused the phone on a
WH-1000XM6 (firmware 3.1.5).

## Decision

A `pauseOnSwitch` setting, on by default, keeps one peer playing:

- **Away from this machine.** Each state line is compared with the last; when
  the source moves from this machine to another peer (`Model.switchedAway`),
  every local MPRIS player that can pause is paused (`Service.applyLine`). The
  daemon stamps its own adapter address on every state line
  (`adapter_address`, `local_address`), because the device list names this
  machine like any other peer. The pause is provisional, because a phone
  reports "playing" for muted autoplay too (a video feed on unlock played 1-5 s
  at a time). The headset also announces the source's own play status
  (`PLAY_NTFY_STATUS`, `0xA5`: `[0xA5, 0x01, enable, status, music/call]`,
  status `0x01` play, `0x02` pause, `0x03` stop; `state["source_playing"]`). If
  the source reports a pause within 6 seconds, the players that were paused
  resume and the source is switched back here. At most one undo per 30 seconds:
  a peer that keeps taking the headset is playing for real, and resuming each
  time would only give it something to fight. A first attempt that waited 6
  seconds before pausing at all was dropped: with both devices streaming, the
  headset flipped back within 3-4 seconds, so the wait never ended and the
  desktop never paused.
- **Toward this machine.** A local player that starts while another peer owns
  playback (`Service.localPlaybackStarted`, `Model.remoteSource`) sends the
  `pause-source` command. A player reports Playing before its audio reaches the
  headset, so the phone is still the source when the pause arrives.
  `Daemon.pause_source` re-checks the source at send time and sends nothing if
  it is already this machine, since a pause then would stop the desktop that
  just started.

`pause-source` uses only a session the daemon already holds. It never
reclaims one: if the phone's app took the session, it keeps it
([ADR 0008](0008-session-policy-and-manual-release.md)). Failures are silent in
the panel, because a pause that was not needed is not a fault.

## Consequences

- Auto-switch settles on the device the user last started, instead of
  bouncing between the two.
- Both directions depend on a held session. A released session sees no source
  changes and sends no pauses; the setting then does nothing, by design.
- Pausing is all-or-nothing on this machine: every player that can pause is
  asked, including one playing to another output, the same as a panel pick.
- A brief blip costs a few seconds of desktop audio rather than leaving it
  paused, and only the first blip in 30 seconds is undone.
- Some swapping is outside the plugin's reach. Google Play Services' Fast Pair
  "Audio switch" kept taking the headset back for a phone every few seconds,
  with nothing playing and the daemon idle; turning it off on the phone was the
  fix. The usage guide's troubleshooting section says so.
- The phone side is confirmed on one model and firmware. Another v2 device that
  ignores `PLAY_SET_STATUS` simply does not pause the phone; nothing else
  changes.
