# Setting up the alarm panel

The panel connects to Home Assistant by itself over its FlexC protocol. You set up four things
in the panel's web interface:

1. a user the integration logs in as,
2. an event profile (which events are sent),
3. a receiver (a FlexC "ATS") pointing at Home Assistant,
4. the encryption key for the connection.

You need an engineer login to the panel's web interface. Do this on your local network.

> The panel does not send events while it is in engineer mode. Log out of engineer mode when you
> are done, before testing the integration.

## 1. Enter engineer mode

Log in to the panel's web interface with an engineer code and choose **Full Engineer** mode.

![Choosing Full Engineer mode](images/01-engineer-mode.png)

## 2. Create a user for Home Assistant

Add a new user, for example `homeassistant`, with the **Manager** user profile.

- Use letters, digits and common symbols; the panel tells you if something is not allowed.
- A double quote (`"`) cannot be used in the username or password.

![A new user with the Manager profile](images/02-user.png)

The integration logs in with the user's **web password**, not the PIN. A new user has no web
password yet. To set one, log in to the web interface as that user with its PIN, then go to
**Configuration → Change Own PIN → Change Web Password**.

![Setting the user's web password](images/03-web-password.png)

## 3. Add an event profile

Go to **Communications → FlexC → Event Profiles** and click **Add**.

- Name it, for example `Home Assistant`.
- Tick the report box for every event type. You can narrow this down later.

![Event profile with all event types reported](images/04-event-profile.png)

## 4. Add a receiver for Home Assistant

Go to **Communications → FlexC → FlexC ATS** and choose **Add Custom ATS**. Change these settings:

| Setting | Value |
|---|---|
| ATS Name | `Home Assistant` |
| Event Profile | the profile from step 3 |
| ATS Polling Timeout | 20 seconds |
| Generate FTC | off |
| Re-queue Events | off |

![The receiver for Home Assistant](images/05-ats-edit.png)

Then choose **Add ATP to FlexC RCT** and set:

| Setting | Value |
|---|---|
| SPT Account Code | a number, e.g. `999`; the same goes into the integration |
| RCT URL or IP Address | the IP address of your Home Assistant |
| ATP Category | Cat 6 (Ethernet) |
| RCT TCP port | `52000`, or the port you choose in the integration |
| RCT ID | a number, e.g. `1`; the same goes into the integration |

![The ATP pointing at Home Assistant](images/06-atp.png)

Save, and the new receiver shows up in the list next to any receivers the panel already has:

![The receivers on the panel](images/05-ats.png)

The SPT account code must not be `0`: the panel then connects but never starts polling. An
ATS created with **Import ATS** gets account code `0`, so set it again after importing.

Give Home Assistant its own ATS, rather than adding an extra ATP to an existing one. The ATPs
inside one ATS are backup paths to the same receiver.

## 5. Set the encryption key

Open **Advanced ATP Settings** and set:

| Setting | Value |
|---|---|
| Encryption Key Mode | Fixed Encryption |
| Encryption key | 64 hexadecimal characters (0–9, a–f) of your own choosing |

The integration needs the same key.

![Fixed encryption key](images/07-encryption.png)

One way to make a random key on any computer with Python is
`python3 -c "import secrets; print(secrets.token_hex(32))"`.

## 6. Leave engineer mode and check the connection

Save and log out of engineer mode. Once the integration is set up in Home Assistant, the
receiver's status in the panel shows that it is connected. The integration also shows that the
panel is connected.

![The receiver connected](images/08-status.png)

## Compatibility

| Panel | Firmware | Status |
|---|---|---|
| SPC5300 | 3.13.7 | tested |
| Other SPC4000/5000/6000 models | with FlexC | expected to work, not yet tested |

Please report other models and firmware versions in an issue.
