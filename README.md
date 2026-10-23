# SPC FlexC for Home Assistant

Connect a **Vanderbilt SPC** intrusion alarm panel straight to Home Assistant, with no extra
hardware. SPC was formerly sold as Siemens SPC; Vanderbilt is now part of ACRE.

The panel talks to Home Assistant over **FlexC**, its own encrypted IP reporting protocol. You
set Home Assistant up on the panel as a FlexC receiver, and the panel connects to it by itself.
Everything stays on your local network.

This is not the same as the built-in [SPC integration](https://www.home-assistant.io/integrations/spc/),
which needs Vanderbilt's *SPC Web Gateway*. This integration does not use or need that gateway.

## Features

- **Alarm panel** per area: arm away (full set), arm home (part set A), arm night (part set B)
  and disarm. Optionally force arming and require a code in Home Assistant.
- **Binary sensors** per zone: door, motion, smoke and so on, from the zone type or name (see
  [docs/zones.md](docs/zones.md)). There is also a problem sensor per zone (disabled by default).
- **Switches** for the panel's outputs (mapping gates). Zone inhibit and isolate switches are
  disabled by default.
- **Locks** for doors: lock, normal mode and open.
- **Sensors:** battery and aux voltage, aux current, mains frequency, door status and the last
  event.
- **Button:** clear alerts.
- **Diagnostics:** whether the panel is connected.
- **Events:** every panel event is fired on the event bus as `spc_flexc_event`, with fields
  such as `ev_desc`, `sia_code`, `zone_id`, `zone_name`, `area_id` and `user_name`, for use in
  automations.

The panel pushes its events as they happen, and the integration also reads the full state every
30 seconds (adjustable).

## Requirements

- A Vanderbilt SPC panel with FlexC (Communications → FlexC in its web interface).
- Engineer access to the panel's web interface, to set up the receiver.
- The panel must be able to reach Home Assistant on a TCP port (52000 by default) on your
  network.

## Installation

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=henrikekblad&repository=spc-flexc-home-assistant&category=integration)

1. In HACS, add this repository as a custom repository (category *Integration*), or use the button
   above.
2. Install **SPC FlexC** and restart Home Assistant.

## Setup

1. Set up the panel as described in [docs/panel-setup.md](docs/panel-setup.md): a user for Home
   Assistant, an event profile, and a FlexC receiver pointing at Home Assistant with a fixed
   encryption key.
2. In Home Assistant, go to **Settings → Devices & services → Add integration → SPC FlexC** (or use
   the button below) and enter the values you set on the panel:
   - the port,
   - the encryption key,
   - the SPT account code,
   - the RCT ID,
   - the panel user's name and web password (or the command profile's user, see the setup guide).

   [![Open your Home Assistant instance and start setting up SPC FlexC.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=spc_flexc)

3. Home Assistant opens the port and waits for the panel to connect. Once the panel has
   answered, the integration is added.

To change any of these values later, use **Reconfigure**. If the panel stops accepting the user
(for example after its password was changed), Home Assistant asks you to sign in again.

## Compatibility

| Panel | Firmware | Status |
|---|---|---|
| SPC5300 | 3.13.7 | tested |
| Other SPC4000/5000/6000 models | with FlexC | expected to work, not yet tested |

Reports for other models and firmware versions are welcome as issues.

## Security

- The FlexC link is encrypted with the key you choose; use a random one.
- The panel user only needs the rights for what you want to do from Home Assistant.
- Keep the port reachable from the panel only, not from the internet.

## How it works

FlexC is the IP reporting protocol SPC panels use to send events to, and take commands from, a
receiving station. Vanderbilt does not publish a specification for it. This integration
implements the receiving side of the protocol from scratch, in Python, so that Home Assistant
can work with panels its users own. It contains no code, keys or certificates from any other
product. The only fixed value it shares with other FlexC implementations is the protocol's
initialization vector, which every receiver must use to talk to a panel. The encryption key is
the one you choose and enter on the panel.

## Disclaimer

This is an independent project, not affiliated with or endorsed by Vanderbilt, ACRE or Siemens.
It was written for interoperability with equipment the author owns. An alarm system protects
people and property: test the integration carefully, and do not rely on it as your only way of
being alerted.

## License

MIT
