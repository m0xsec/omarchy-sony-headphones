# 0011 — v2 support lists refine the model table

## Context

ADR 0005 made features data: a per-protocol ceiling, refined per known model.
That table is only as good as the hardware behind it, and on v2 it was a guess
for every model but the WH-1000XM6. A WH-1000XM5 capture
(gabamnml/omarchy-sony-headphones#3, firmware 2.5.1) showed the guess wrong in
three places: the XM5's equalizer is the classic clear-bass + five-band layout
with v1's preset codes, its connection quality rides audio subtype `0x00`
rather than the XM6's `0x02`, and it has a touch-panel switch the table did
not offer.

A v2 device also describes itself. Asked `06 00` on each message table, it
returns the support functions it implements, and its general-settings slots
(`0xd1`–`0xd4`) answer `d0 <slot> 00` with their own names. Captures of both
an XM5 and an XM6 (firmware 3.1.5) show the lists are real but not uniform:
the XM6 lists voice guidance as `0x42` where the XM5 lists `0x41`, and lists
none of the codes the XM5 uses for connection quality, though it has it. A
list read with one model's codes in mind would hide working controls on the
other.

## Decision

- During v2 negotiation the link asks for both support lists, then the names
  of every general-settings slot they list (`Link.read_support`).
- Where a capture pins a code, the list decides the dialect without probing:
  `0x6d` → ambient subtype `0x19`, `0x6b` → `0x17`; `0x56`/`0x57` → the
  ten-band equalizer under `0x04`, `0x50` → the classic one under `0x00`;
  `0xe1` → connection quality under `0x00`; `0x28` without `0x20` → the
  battery-with-threshold subtype. Anything unlisted falls back to the probes
  ADR 0006 already runs.
- The list only ever *withholds* a feature whose codes are pinned on every
  captured model (`V2_FN_FEATURES`); features with no pinned code stay with
  the model table. A device that answers no list keeps the table as it was.
- The touch panel is discovery-only on v2 (`V2_DISCOVERED_FEATURES`): offered
  when, and only when, a slot names itself `TOUCH_PANEL_SETTING`, never on a
  model name or the ceiling.
- The equalizer's dialect is read from the reply's band count, not its
  subtype, because the XM6 answers ten bands under `0x00` as well.

Switching the touch panel off needs the headphones' consent: it is refused
unless fixed-message alerts are enabled first, and the headphones then ask.
The helper answers yes to that one alert, and only inside `apply_setting` for
a write that enabled alerts (`Link.confirm_alerts`). No other alert is ever
answered; an alert the plugin did not provoke stays the user's to accept on
the device (ADR 0009). The reconnect variant of that alert means the
headphones will drop the link to apply the change, so the drop that follows
is raised as `Reconnecting` — success — and the daemon reconnects without
re-sending the change.

## Consequences

- An XM5 is offered its real controls with its real layouts, and a v2 model
  the plugin has never seen gets the controls it lists rather than a blind
  ceiling.
- Negotiation costs two to six more round trips on a v2 link and saves the
  probes the lists settle; on a WH-1000XM6 it measured under two seconds.
- Adding a code to `V2_FN_FEATURES` needs a capture from every model that
  carries the feature, or it can hide a working control.
- The XM6's touch panel is offered on the strength of its slot name. Its off
  switch was verified on hardware on 2026-11-23: the XM6 raises the
  non-reconnect alert (`0x0a`) and applies the change at once.
