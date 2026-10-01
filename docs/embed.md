English | [简体中文](embed.zh-CN.md)

# Embedding the pad in the whiteboard app

Other web apps can live in the whiteboard app's storage directory, be served by the whiteboard server, and embed a pad that syncs with the Mac. For example, a practice page can show a question image and an answer field, with one board per question.

This document covers apps hosted by the whiteboard app. The pad's full interface (options, methods, events, metadata format) is in the [inksync interface reference](../packages/inksync/README.md), which also covers storing boards on your own server without running the whiteboard app.

```js
import { createInkPad } from "/inksync/inkpad.js";

const pad = createInkPad(document.querySelector("#answer-pad"), {
  space: "qb",                       // the app name
  board: "9709-s23-12-q3",
  create: {
    name: "9709 s23 P12 Q3",
    canvas: { mode: "fixed", width: 800, height: 1400 },
    background: { pattern: "blank" },
    layers: [{ src: "/apps/qb/img/9709-s23-12-q3.png", x: 0, y: 0, width: 800 }],
    data: { paper: "9709_s23_12", question: 3 },
  },
});

pad.on("history", ({ undo, redo }) => { /* enable or disable buttons */ });
pad.setTool({ tool: "eraser" });
await pad.open("9709-s23-12-q4", { create: { name: "9709 s23 P12 Q4" } });   // next question
```

## Installing an app

An app is a static folder in `apps/` under the storage directory; the whiteboard server serves it at `/apps/<name>/`.

```
<storage directory>/apps/
  qb/
    index.html
    img/…
```

| Rule | Value |
| --- | --- |
| Folder name | Must match `^[a-z0-9-]{1,32}$`; other names are ignored. |
| Entry page | `index.html`. A folder without it is not an app. |
| Paths | Only files inside the app folder are served; requests that leave it through `..` or a symbolic link return 404. |
| Caching | Every response carries `Cache-Control: no-cache`, so the iPad loads changed files after a refresh. |

`examples/apps/demo/` in this repository is a complete example: two questions with a board each, a question image, tool buttons and an answer field. Copy it into `apps/` and open `http://your-mac.local:8848/apps/demo/` on the iPad.

> **Note**
> The page should be served by the whiteboard server. The page and the pad then share an origin and need no cross-origin setup, and the iPad shell hands Apple Pencil samples to the pad at about 240 per second, as it does in the whiteboard.

## The app's space

Each installed app has its own space; set the `space` option to the app name. The space's boards are stored in `<storage directory>/spaces/<app name>/`, apart from the user's own boards:

| Item | Description |
| --- | --- |
| Board id | Needs to be unique only within the app. The same id in different apps names different boards. |
| Creating | A missing board is created from `create`. Devices without the Manage boards permission can create at most 60 boards per minute; beyond that they receive the `error` event (`reason: "rate"`). The number of boards is not limited. |
| Writing, clearing | Any device that can write. |
| Editing metadata, unlocking | Requires the Manage boards permission. |
| Image layers | `src` in `layers` must be a path on this server (starting with `/`), usually under `/apps/<name>/`. |
| Removing the app | After `apps/<name>/` is deleted the space can no longer be connected to; the boards in `spaces/<name>/` stay and come back when an app of the same name is installed again. |

App board ids are easy to guess, so any device on the local network that can write can open the app's boards. To separate users, run inksync on your own server and decide in its `Policy`; see [examples/qb-server](../examples/qb-server/server.py).

## Viewing on the Mac

On a device with the Manage boards permission, the board chooser shows one entry per installed app. It lists the app's boards page by page; clicking a board opens it read-only, image layers included. App boards do not appear among the user's own boards, and the iPad does not follow them.

## iPad home

When at least one app is installed, the Mac's whiteboard settings show iPad Home. After an app is chosen, the iPad opens that app instead of the whiteboard, both in the iPad shell and from the Home Screen icon. The Mac window is not affected.

| Address | Result |
| --- | --- |
| `/` on the iPad | Redirects to `/apps/<name>/`. |
| `/?home=whiteboard` | Opens the whiteboard without redirecting. Links from an app back to the whiteboard use this address. |
| `/` on the Mac | Opens the whiteboard. |

The setting is stored as `ipad_home` in the config file. After the app is removed, the iPad opens the whiteboard again.

## Upgrading from 1.0.x

| 1.0.x | 1.1 |
| --- | --- |
| `import … from "/sdk/inkpad.js"` | `import … from "/inksync/inkpad.js"`. The old address `/sdk/inkpad.js` redirects there, but 1.0.x options must be changed as below. |
| `app: "qb"` | `space: "qb"`. |
| `name`, `kind`, `folder`, `underlay` options | Go into `create`: `name`; `canvas`; `layers` (`underlay: {src, width}` becomes `layers: [{src, x: 0, y: 0, width}]`). App boards have no folders; put grouping into `data`. |
| `pad.state`, `pad.net.status` | `pad.snapshot()`, `pad.status`. The returned object has only the members listed in the interface reference. |
| `setTool({...pad.tool, tool})` | `setTool({tool})`; fields not given are kept. |
| `unlock()` in the `locked` event | Unchanged; whether it is allowed is `pad.caps.unlock`. |

Boards created by apps in 1.0.x move into their app's space the first time 1.1 starts, keeping their ids.
