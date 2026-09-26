# Dapple

Dapple sets static multi-color patterns on Twinkly lights. Click **Dapple** in the sidebar to
open it. If it isn't there, turn on **Show in sidebar** on this app's Info tab.

## Getting started

1. On the **Strands** tab, add each strand by its address. The Twinkly app shows it under the
   device's settings. Give each strand a fixed address in your router first (a "DHCP
   reservation"), or Dapple may lose track of it one day.
2. On the **Pattern** tab, pick colors and press **Apply**.

The [README](https://github.com/andrewfraley/dapple/blob/main/README.md) covers groups,
patterns and presets in full.

## Home Assistant lights

If the Mosquitto broker app is installed, Dapple has already filled in its **Home Assistant**
tab. Turn on **Connect to Home Assistant** and press **Save**, and each group appears as a
light, with your presets as its effects. If you install Mosquitto later, or reinstall it,
restart this app so Dapple picks up its login.

[Dapple with Home Assistant](https://github.com/andrewfraley/dapple/blob/main/HOME_ASSISTANT.md)
has the details, including automations.

## Network

Dapple needs no port to show in the sidebar. To reach its page or REST API from elsewhere on
your network, set a port on the **Network** tab. Dapple has no login of its own, so only do
this on a network you trust.

## Your settings

Your strands, groups and presets live inside this app, and Home Assistant's backups include
them. Uninstalling the app deletes them.
