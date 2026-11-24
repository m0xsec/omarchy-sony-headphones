# 0012 — Codec choice through PipeWire, and a settling audio link

## Context

The headphones report which codec the audio link uses but do not choose it:
the computer does, and on Omarchy that is PipeWire, which offers one A2DP
profile per codec the pair can use. Picking a codec is picking the card's
profile. gabamnml/omarchy-sony-headphones#3 (Vitor Pacheco) did this with
`pactl` and saw it through a connection-quality change on a WH-1000XM5,
including reconnecting the A2DP profile when PipeWire never noticed the
change.

On a WH-1000XM6 (firmware 3.1.5, 2026-11-23, with a phone also connected over
multipoint) three things showed up that the port has to answer for:

- **Every profile switch looks like a source change.** For 0.3–0.5 s the
  headset names the phone as the playback source, then this computer again.
  With `pauseOnSwitch` (ADR 0010) that is a switch away, so local players were
  paused, and nothing resumed them, because the phone never reported a pause.
- **The XM6 renegotiates by itself.** A stable connection took the A2DP card
  to `off` within 0.2 s and brought it back about 6 s later without LDAC (the
  plain `a2dp-sink` profile then meant AAC), and the reverse restored LDAC the
  same way. The source read as none for the whole gap. The reconnect fallback
  was never needed.
- **The phone's app takes the session afterwards.** About 9 s after each
  connection-quality change the control link reset with the headset still
  connected: Sound Connect on the phone claiming the session once the headset
  re-announced itself.

## Decision

- The codec is a host setting, `codec` (`HOST_SETTING_KEYS`), not a headphone
  one: no protocol builder sees it, so both generations get it. Its options
  are whatever PipeWire offers now (`state["codecs"]`, best first) and the one
  in use (`state["a2dp_codec"]`). Codecs are matched by the name in a profile's
  description, never by the profile's name. The row is hidden when nothing is
  offered.
- `Link.audio` is a seam like `Transport`: `PipeWireAudio` runs `pactl` from
  an absolute path with `PATH`, `LC_ALL` and a verified-private
  `XDG_RUNTIME_DIR` only (ADR 0002), and the demo stands `DemoAudio` in. Only
  profile names of a plain `a2dp-sink…` shape are ever passed back to `pactl`,
  and the list is bounded.
- A switch counts once the card reports it. One PipeWire does not carry out
  (usually another Bluetooth device holding that codec) is a refusal on the
  row, with PipeWire's reason, not a lost connection.
- While a codec switch or a connection-quality change has the audio link
  renegotiating, the daemon publishes `audio_settling: true`.
  `Model.switchedAway` ignores a source change in a line that carries it, and
  the codec and connection-quality rows grey out until it clears.
- After a connection-quality change the daemon waits for PipeWire's codec list
  to change and read the same twice, up to 15 s, and asks the headphones for
  the codec until they name one. If the list has not moved after 3 s it
  reconnects the A2DP profile once, the XM5 case; an XM6 never reaches that.
  The CLI waits 30 s for these two settings rather than 10, since giving up
  would fall back to dialling the headphones the daemon still holds.
- A control link lost while the audio link settles is not raised: the write
  already landed. The daemon's ordinary read path then classifies the reset,
  and a phone taking the session is stepped aside for as ADR 0008 says, which
  the panel already explains.

## Consequences

- Choosing a codec, or a connection quality, no longer pauses the desktop on a
  multipoint setup.
- A connection-quality change blocks the daemon for the 6–8 s the XM6 takes;
  other clients wait their turn.
- With Sound Connect on the phone, a connection-quality change hands the
  session to the phone a few seconds later. That is the phone's right
  (ADR 0008); reclaiming takes it back.
- WirePlumber remembers the last profile picked and restores it when the audio
  reconnects, so a codec chosen here survives a reconnect.
- The XM5 path (the reconnect fallback) is carried from PR #3's hardware notes
  and runs only against a stand-in here.
