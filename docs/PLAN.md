# Full port plan

Goal: native NatroMacro for Arch Linux, Wayland, Niri, and Sober Flatpak.
Agreed stack: Python and Qt (PySide6). Full feature parity is the goal, not the current status.

1. Preserve the NatroMacro source at a pinned commit with its GPL-3.0 license.
2. Implement Niri IPC, image capture, and a Wayland virtual keyboard and pointer.
3. Implement interruptible route execution, guaranteed key and button release, stopping on focus loss, and event logging.
4. Port all 91 routes and 12 patterns, validating the supported syntax.
5. Port recognition, calibration, gathering, and conversion.
6. Port item collection, boosters, quests, planters, enemies, and night events.
7. Port reconnection, settings, statistics, and the interface.
8. Prepare an Arch package, automated checks, and a Sober validation procedure.

Validation includes execution core unit tests, checks for every route, an offscreen UI launch,
and package build verification. Controlling Sober in the game requires access to the Niri session.
The current sandbox prohibits connecting to its socket.

Unverified in-game behavior must not be reported as confirmed compatibility.
Features that have not been ported must be clearly shown as unavailable.
