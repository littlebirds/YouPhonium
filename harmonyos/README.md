# YouPhonium for HarmonyOS

This directory contains the HarmonyOS hybrid client. Recognition and score
storage remain on the existing YouPhonium Python server. The client embeds the
existing web interface with ArkWeb and adds a persistent LAN server setting and
native lifecycle notifications.

## Requirements

- DevEco Studio with the HarmonyOS 5.0.0 (API 12) SDK or a compatible newer SDK
- A HarmonyOS phone or tablet
- The phone/tablet and YouPhonium server on the same trusted Wi-Fi network

## Run

1. Start YouPhonium on the computer with `Start YouPhonium.command` or
   `Start YouPhonium.sh`.
2. Note the **HarmonyOS and other devices** URL printed by the launcher, for
   example `http://192.168.1.20:8000`. Do not enter the desktop-only
   `127.0.0.1` address in a simulator or physical device.
3. Open the `harmonyos` directory as a project in DevEco Studio.
4. Let DevEco Studio install/synchronize the matching SDK and Hvigor tooling.
5. Select an automatic debug signing configuration and run the `entry` module
   on a phone or tablet.
6. Enter the LAN URL in the app and tap **Connect**.

The address is stored locally. Use **Server** in the native header to change it.
Use **Reload** after restarting the server.

The root `oh-package.json5` deliberately does not list
`@ohos/hvigor-ohos-plugin`. It is a build plug-in bundled with DevEco Studio,
not an application dependency to download from the OHPM package registry.

## Architecture

- `EntryAbility.ets` publishes foreground/background lifecycle state.
- `Index.ets` owns the ArkWeb component, server settings, connection errors,
  and forwards lifecycle changes into the page.
- `frontend/app.js` exposes `window.YouPhoniumApp.onHostBackground()` and
  `onHostForeground()`. Backgrounding freezes playback at the audible position;
  foregrounding marks Web Audio for recovery on the next Play gesture.
- OMR, MusicXML editing, expanded MIDI generation, and the shared library remain
  unchanged on the Python server.

The module requests `INTERNET` and `GET_NETWORK_INFO` because the server is
reached over the local network. It does not embed HOMR or start a second server.

## Production notes

- Prefer a trusted private Wi-Fi network. The desktop launcher intentionally
  serves plain HTTP on the LAN; do not expose it to the public internet.
- Replace the placeholder bundle identifier before publishing if another ID is
  reserved in AppGallery Connect.
- Test downloads and the HTML file picker on every minimum-supported ArkWeb
  version. ArkWeb owns those browser interactions; recognition does not move to
  the native client.
