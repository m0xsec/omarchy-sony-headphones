# 0013 — Act on headset events in the widget

## Context

While the session is held, a WH-1000XM6 (firmware 3.1.5) reports every touch
gesture, button press, wear change and multipoint hand-off on the control
channel: `c9 01` followed by three length-prefixed ASCII strings, such as
`keyRHold` for touch and hold and `opGoogle` for the action the headset took.
It reports wear as `f5 10 01 <state>`. Touch and hold hands the phone's
assistant the gesture and does nothing on the desktop, though the helper sees
it, and a desktop action for it was wanted, Voxtype dictation in particular.

Two constraints shape where such an action runs. The helper runs with a
deliberately minimal environment (ADR 0002), so a desktop program started from
it could not reach Wayland, the audio server or its own daemon. And the event
strings come from the device, which ADR 0009 treats as untrusted.

The trace also settles what a hold is: one event, with no release, and not
always paired with an `op*` (one hold produced `keyRHold` alone).

## Decision

- The helper decodes the event log (`parse_v2_event`) into two state fields:
  `last_event`, the name, and `event_seq`, a counter that moves with every
  event so a repeated gesture is still a change. Only plain identifiers of up
  to 32 characters are kept; a malformed frame changes nothing. Wear becomes
  `worn`. The helper runs nothing.
- The widget acts. `Service.qml` runs in the shell with the user's session
  environment, and on a state line where `Model.holdTriggered` sees a new
  `keyRHold` it runs the `holdAction` widget setting through
  `Quickshell.execDetached(["bash", "-lc", holdAction])`. The command is the
  user's own, written in their settings; nothing from the headset is ever put
  into it. The event decides only whether it runs.
- A hold fires once per new count, keyed on the gesture rather than the action.
  A widget's first line, which has no earlier count, never fires, and neither
  does a fresh daemon's, which has no event. So neither a restart nor a
  reconnect replays an old hold.
- `holdAction` is empty by default, so nothing changes for anyone who does not
  set it.

## Consequences

- Touch and hold can drive Voxtype (`voxtype record toggle`), or anything
  else a terminal could run. Without a release event, a toggle is the natural
  fit and push-to-talk is not possible.
- It works only while this computer holds the control session, and only on v2
  headphones that send the event log. A phone holding the session sees the
  hold instead.
- The phone's assistant still answers the same hold if one is set up in Sound
  Connect; the plugin cannot suppress that. Setting the assistant to none
  there makes the hold this computer's alone.
- Other gestures are decoded and published but bound to nothing yet. Binding
  another is a widget change, not a helper one.
