# Zones and their device class

Each zone on the panel becomes a binary sensor that is on while the zone's input is not closed,
for example a door open or motion detected. Home Assistant shows such a sensor according to its
*device class*: door, motion, smoke and so on.

The panel does not tell what kind of detector a zone has. It only reports the zone's **type**,
which is its alarm function (alarm, entry/exit, fire, ...). A motion detector and a door contact
are usually both of type *alarm* or *entry/exit*. The integration therefore picks the device
class like this:

1. **From the zone type**, when the type says what the zone is:

   | Zone type | Device class |
   |---|---|
   | Fire | Smoke |
   | Fire exit | Door |
   | Tamper | Tamper |
   | Medical | Safety |
   | Fault | Problem |
   | Seismic | Vibration |

2. **Otherwise from the zone's name**, if it contains one of these English words. Case does not
   matter, and the first match in this order wins:

   | Words in the zone name | Device class |
   |---|---|
   | motion, pir | Motion |
   | glass | Vibration |
   | window | Window |
   | garage door | Garage door |
   | door, gate | Door |
   | smoke | Smoke |
   | fire, heat | Heat |
   | water, leak | Moisture |
   | tamper | Tamper |

   If your zones are named in another language, or the guess is wrong, set it yourself (below).

3. **Otherwise an entry/exit zone becomes a door.** The name goes first because an entry/exit
   zone is just as often a motion detector in the hallway ("Hall PIR" becomes motion).

4. **Otherwise no device class**: the sensor shows plain on/off.

## Setting it yourself

In Home Assistant, open the zone's entity, choose **Settings** and set **Show as** to the right
kind. That choice is kept, and the integration no longer changes it.

## Other zone entities

- **Zone problem** (disabled by default): on when the zone's status is anything but normal,
  inhibited, isolated or "not used recently". That covers tamper, trouble, masked and alarm.
- **Inhibit** and **isolate** switches (disabled by default): only created for zones where the
  panel allows it.
