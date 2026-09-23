SEA EXPLORER BOT — one-folder build
=====================================

WHAT IT DOES
------------
- Uses a headless scrcpy 4.1 H.264 stream decoded in PC memory. No scrcpy
  desktop window, window capture, foreground focus, or screen-coordinate mouse
  control is needed. ADB is still used to launch the server and tap menus.
- Detects gold collectibles and prioritizes a safe target over blind sweeping.
- Holds one finger continuously via scrcpy's control socket and steers smoothly.
- Detects purple hazards ahead of the diver and gives escape priority over loot.
- Rejects the red coral that previously caused false bomb evades.
- Claims the normal end-of-run reward.
- Closes the optional purple Win Machine instead of pressing SPIN/ad.
- Periodically upgrades BAG SIZE because the recordings show the bag filling
  much earlier than the oxygen supply is exhausted.
- Prioritizes OXYGEN purchases because the requested goal is Oxygen Level 2000.
- Saves progress in progress_state.json so stopping/restarting does not reset
  its purchase counter.
- Uses normalized screen coordinates, tuned from the supplied 720x1584 recordings.

ECONOMY POLICY
--------------
The recordings show Bag 6 -> 7 costing only ~117 coins while Oxygen 160 -> 170
costs ~563 coins. A 7-slot bag also appears to finish a run while substantial
oxygen remains. That makes bag capacity an income bottleneck.

The bot therefore:
  Oxygen < 500:  one bag attempt every completed run
  Oxygen < 1000: one bag attempt every 3 runs
  Oxygen < 1500: one bag attempt every 5 runs
  Oxygen >=1500: one bag attempt every 8 runs

At every HOME screen it then buys as many oxygen upgrades as currently
affordable (maximum 8 in a batch). This keeps most spending directed at the
2000-level objective without starving the income upgrade.

GETTING STARTED WITH THE WINDOW
-------------------------------
1. Install Python 3.11+ and the packages in requirements.txt:
       pip install -r requirements.txt
2. Install Android platform-tools (adb) and the official scrcpy 4.1 Windows bundle.
   Keep scrcpy.exe and scrcpy-server together. Set scrcpy_path in config.json
   if the bundle is not in Downloads or PATH. The headless path uses its server
   only; it does not launch scrcpy.exe or create a mirror window.
3. Double-click run_bot.bat to open the Sea Explorer dashboard.
4. Choose USB or Wireless, connect to the phone, and open Sea Explorer on it.
5. Press Start. The dashboard controls the selected phone directly through ADB.
   No mirror window needs to appear or stay focused. On smaller screens, use the
   Connection & dive / Checkpoints tabs. A Stop button remains visible at the
   top while a dive is running.

The Sea Explorer Bot desktop shortcut opens the dashboard and automatically
starts the bot on the last selected USB or Wireless device. With one authorized
USB phone and no saved selection, it chooses that phone. If the selected phone
is unavailable, the dashboard stays open so you can connect it manually.
Run create_desktop_shortcut.ps1 to recreate the shortcut after moving this
folder. The custom icon is sea_explorer.ico.

USB: enable USB debugging in Android developer options, connect the cable,
unlock the phone, accept the RSA prompt, then use Refresh and Connect in the
dashboard. Choose the exact authorized device if multiple are listed.

Wireless: enable Wireless debugging in Android developer options. If the phone
has not been paired with this PC, choose "Pair device with pairing code" on
the phone, enter its pairing address and six-digit code in the dashboard, and
press Pair. Then enter the phone's regular wireless debugging IP:port address
and press Connect. The pairing and connection ports can differ. Pairing codes
are not saved. Wireless debugging may need a new address after reconnecting
to Wi-Fi. A previously paired phone can usually go straight to Connect.

On the first run the bot reads the foreground Android package and remembers it.
No package name needs to be typed manually.

CHECKPOINTS
-----------
The dashboard shows the requested milestones:

  #2    Reach 300 meter      15 Coins
  #3    Reach 400 meter      34 Coins
  #4    Reach 500 meter      48 Coins
  #5    Reach 600 meter     472 Coins
  #6    Reach 700 meter     472 Coins
  #7    Reach 900 meter    4719 Coins
  #8   Reach 1000 meter   14157 Coins
  #9   Reach 2000 meter   94380 Coins

Use the Mark reached control when the game confirms a milestone. These marks
are saved locally and can be changed later. They are manual because the bot
does not currently read the game's depth counter or verify coin payouts.

CURRENT STARTING LEVEL
----------------------
config.json currently has:
    "starting_oxygen_level": 230

The saved progress_state.json currently shows Oxygen Level 230. If you buy
oxygen manually later, update the saved oxygen
estimate to match the displayed level. After that, the bot tracks its own
confirmed purchases.

SAFETY / TEST MODE
------------------
For command-line use without the dashboard, run sea_explorer_bot.py directly.
With multiple ADB devices, select exactly one using --serial. The --transport
wireless option avoids the USB stay-awake command. For example:
    python sea_explorer_bot.py --serial 192.0.2.10:5555 --transport wireless

To watch detection without any taps/movement:
    python sea_explorer_bot.py --dry-run

Offline test against a recording:
    python sea_explorer_bot.py --analyze-video "recording.mp4"

Ctrl+C safely releases the held virtual touch and writes progress.

HEADLESS CAPTURE AND CONTROL
----------------------------
Normal pipeline: Android MediaCodec -> scrcpy 4.1 server -> ADB-forwarded H.264
packets -> isolated PyAV decoder process -> OpenCV BGR frame -> hazard/item vision -> target planner
-> scrcpy control socket. Only the newest decoded frame is retained. Gameplay
touches are held across movements, with small interpolated moves and a release
on stop, stale stream, or decoder failure. Menus still use occasional ADB taps.

Normal config is capture_backend/control_backend = scrcpy_stream. For optional
troubleshooting with a visible preview, use scrcpy_window for both settings;
that older path requires the mirror to stay unobstructed and focused. The
adb_direct fallback is slower but needs no scrcpy installation. Pointer Location
and Show Taps can be enabled on Android to inspect the actual held touch.

The chosen H.264 codec and 720px max dimension reflect measured throughput on
the connected OPPO phone. A claimed 10-40 ms *end-to-end* latency was not
verified; see docs/headless_capture_benchmark.md for what was measured and the
remaining latency boundaries.

FILES
-----
sea_explorer_bot.py   main program
sea_explorer_ui.py    dashboard with connection, progress, and checkpoints
connection_manager.py USB/wireless ADB connection helpers
checkpoint_state.py   milestone definitions and saved marks
headless_scrcpy.py    default video/control socket transport (no desktop window)
scrcpy_binary.py      locate the matching scrcpy 4.1 server bundle
scrcpy_runtime.py     optional visible mirror for troubleshooting
scrcpy_touch.py       optional visible-mirror touch driver
scrcpy_capture.py     optional visible-mirror capture
config.json           goal, economy policy, coordinates, timing
run_bot.bat           one-click dashboard launcher
dry_run.bat           no-touch detector test
requirements.txt      Python packages
progress_state.json   created automatically after first run
checkpoint_state.json created when milestone marks are changed
ui_settings.json      created when dashboard preferences are saved
sea_explorer.log      created automatically


LIVE FIX 1
----------
HOME detection was updated after the first real-phone run.
The turquoise HOME background can merge with the SHOP button in HSV, so HOME
now keys off the two isolated lower BAG SIZE + OXYGEN LEVEL purchase buttons.
This fixes the observed `STATE - -> UNKNOWN` on the supplied live HOME frame.
