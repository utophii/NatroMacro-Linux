# Port status

Updated November 12, 2026. The full port is not yet complete.

| Component | Implementation | Confirmed checks |
| --- | --- | --- |
| Niri and capture | IPC, window selection, multiple capture methods, window position detection | The user captured Sober on Niri 26.04 using `screenshot-window` |
| Wayland input | Virtual keyboard and pointer, key and button release, connection monitoring | Device creation in the user's session, game input has not yet been verified |
| Routes and patterns | 91 routes and 12 patterns translated from AHK to Python | 2472 combinations of route, movement method, hive slot, and bee count completed without leaving keys held |
| Images | 248 original PNGs and embedded templates, 1050 names | All original PNGs match the pinned Git tree, all templates decode |
| Main cycle | Reset, hive detection, cannon, gathering, sprinklers, return, conversion | Unit tests, a complete in-game cycle has not yet been verified |
| Items | Wealth Clock and six dispensers, hotbar slots | Routes and timers, receiving rewards in the game has not yet been verified |
| Memory Match | Five games, memory of revealed cards, pair selection, waiting for stable reveals, handling disappearing pairs | The window and 16 closed cards were recognized in the user's screenshot. Failed clicks and disappearing pairs were tested. A complete in-game run has not yet been verified |
| Planters | 17 field tables, 12 types, fixed fields and nectar-based selection, timers, placement, harvesting, tracking uncertain outcomes | Formulas, selection without duplicates, persistence across restarts, cancellation and rejected placement with simulated responses |
| Quests | Tables for 72 Polar Bear, Black Bear, Bucko Bee, and Riley Bee quests, reading visible titles and progress bars | Four quests and their objectives were recognized in the user's screenshot |
| Interface | Fields, general settings, items, planters, diagnostics, log | Offscreen Qt launch, saving and restoring settings |

## Remaining work

- Complete in-game validation of the main cycle and mouse positioning in Sober.
- Automatic quest completion and turn-in, quest log scrolling, Brown Bear, and Honey Bee.
- Enemies, Vicious Bee, night events, Mondo Chick, and seasonal events.
- Boosters and related gathering priority scenarios.
- Full Planters+ parity, manual field rotations, glitter, Puffshroom mode, and importing existing planters.
- Fixing and validating reconnection in the game. The current preliminary implementation is disabled by default.
- Verifying interruption of long patterns when the backpack fills or game events occur during a pattern.
- Webhooks, charts, and full parity with the original statistics.
- Arch packaging, application menu integration, and validation of the installed build.

## Validation limitations

The development sandbox cannot connect to Niri or Wayland session sockets.
The user ran capture and device creation checks from a regular terminal.
The BSS screenshot was checked locally without sending game input. This confirms recognition
of that specific frame, not successful route execution, planter placement, or quest completion in the game.

The user's saved screenshots are not included in the project source or test fixtures.

Initial BSS recognition no longer depends on an open menu.
It passes on the provided frames with the quest log both open and closed.
A screenshot from another game is rejected. All 58 automated tests pass, including rejection
before creating an input device, retrying capture after focusing the window, and saving diagnostics
after releasing keys and buttons.
