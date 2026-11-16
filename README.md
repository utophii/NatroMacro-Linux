# Natro Linux

A native port of NatroMacro for Arch Linux, Wayland, Niri, and Sober Flatpak.
Built with Python, Qt/PySide6, and small C modules for Wayland and image matching.

**This is a development build. Full feature parity with NatroMacro has not yet been achieved.**
See [docs/STATUS.md](docs/STATUS.md) for the current implementation and verification status.

## Running from this directory

```bash
cd ~/NatroMacro-Linux
make -C native
python -m natro_linux ui
```

Requires Python 3.11+, PySide6, NumPy, Niri, grim, and Sober Flatpak.
Building the C modules requires a compiler, make, wayland-scanner, and the Wayland and xkbcommon headers.
AutoHotkey, Wine, X11/XTest, and OpenCV are not used.

Before running the macro in the game, enter your actual hive slot, bee count, and base movement speed.
Choose a field, pattern, and duration in the field settings tab. The defaults are examples.
Use the save settings button to store your configuration separately from the source files.

The macro focuses Sober. Execution stops if the game loses focus.
After resuming from a pause, the cycle restarts from the hive. The native helper releases held keys
and buttons when the connection closes or the executor sends no messages for two seconds.

Movement buffs are read in a separate thread, so Niri capture does not block key release at the end
of a movement step. If the data is more than two seconds old, movement stops with an error.
Speed entries in the log show the calculated value and changes in detected buffs, not a measurement
of actual movement. The distance calculation includes the initial 0.125 studs from the original `Walk()`.

Control commands for use from another terminal:

```bash
python -m natro_linux status
python -m natro_linux pause
python -m natro_linux resume
python -m natro_linux stop
```

You can bind these commands to keyboard shortcuts in your Niri configuration.
The application does not modify the compositor configuration automatically.

## Checking image recognition

```bash
python -m natro_linux doctor --check-input
python -m natro_linux snapshot /tmp/natro-bss.png --focus
python -m natro_linux inspect /tmp/natro-bss.png
```

`doctor --check-input` creates and closes virtual devices without sending game input.
`snapshot --focus` focuses the window and saves a screenshot.
`inspect` reads a saved PNG and reports BSS recognition, buffs, nectar, and visible quests.
It does not connect to Wayland or send input.

BSS recognition does not require an open game menu. It checks the Pollen label and the layout
of both top panels, Honey and Pollen. After focusing the window, recognition is attempted up to
three times without sending game input.

Failed initial recognition saves `unrecognized-bss.png` in the state directory.
Subsequent errors save the most recent available frame as `last-error.png` and an error report
as `last-error.json`. The log includes the frame path and age. Saving these diagnostics does not
capture a new frame or change focus. The default state directory is `~/.local/state/natro-linux`.

In Niri 26.04, tiled window coordinates may be absent from IPC. In that case, capture uses
`grim -T`, falling back to `niri msg action screenshot-window` when necessary.
The latter replaces the clipboard contents with the screenshot. The `snapshot` command reports
which capture method was used. Window positioning for mouse input is determined separately
by matching patches between the window screenshot and the monitor screenshot.

The current templates expect the original game UI size. Start with a capture scale of 1.
Restart the macro after resizing the window or moving it to another monitor.

## Memory Match

If a card does not reveal or its item cannot be read, the solver continues with other cards.
Unread cards are not stored as items and can be revisited after the others. A failed read does
not count as a matched pair or end the last chance prematurely. Retries remain bounded for a
completely unresponsive game window.

## Planters

The planter settings tab supports fixed fields and selection based on nectar.
In fixed-field mode, each of the three slots repeats its selected planter and field after harvesting.
In nectar mode, select the planters you own, the allowed fields, and your nectar priorities.
Planter selection uses NatroMacro's field bonus tables. Harvest timing is calculated from nectar
levels, full growth time, or a specified interval in hours.

Planters already placed manually are not imported automatically.
Glitter, field rotations within a single manual slot, and Puffshroom mode have not yet been ported.
The placement and harvesting cycle has only been tested with simulated recognition results.

State is saved before placement and harvesting. If execution stops during an unconfirmed action,
the slot is marked `uncertain` and remains occupied. To reconcile it, first check the planter
in the game, then close the application and use one of these commands:

```bash
python -m natro_linux planters
python -m natro_linux planter-reconcile 1 --remaining-hours 2
python -m natro_linux planter-reconcile 1 --collected
```

`--remaining-hours` sets the time remaining until harvest. `--collected` removes the record of a
planter that has already been harvested. These commands only update local records and do not
interact with the game. They cannot run while the application is open.

## Data and reproducibility

The upstream source is pinned to NatroMacro commit `094f9c7b97e27f05e23f04beebecb7396bff5fd9`.

```bash
python tools/fetch_assets.py
python tools/extract_assets.py
python tools/extract_game_data.py
python tools/port_routes.py
python -m unittest discover -s tests -v
```

The downloader verifies each original PNG against its Git blob SHA-1, resumes missing downloads,
reports progress, and enforces a deadline for each request process, including DNS resolution.
Existing source files with mismatched checksums are not overwritten.

The index contains 1050 template names. When building the working copy of `BLove.PNG`, the extractor
removes a malformed `sBIT` metadata chunk. This chunk must contain four values for RGBA, but the
original contains three. Other chunks, including the compressed pixel data, remain unchanged.
See the [PNG specification](https://www.w3.org/TR/png/#11sBIT).

Configuration: `$XDG_CONFIG_HOME/natro-linux/config.json`, defaulting to `~/.config/natro-linux/config.json`.
State: `$XDG_STATE_HOME/natro-linux/state.json`, defaulting to `~/.local/state/natro-linux/state.json`.
Both files are written atomically. IPC files are located in `$XDG_RUNTIME_DIR/natro-linux`.

## Attribution

Routes, patterns, images, and game tables are derived from
[NatroTeam/NatroMacro](https://github.com/NatroTeam/NatroMacro), licensed under GPL-3.0.
The `upstream` directory preserves the source files, and manifests record the origin of the data.
[Macro-Nation](https://github.com/IAmRobloxScripter-code/Macro-Nation) was reviewed as a reference
project. Its code is not included in this port. Wayland protocol license notices are preserved in the XML files.
