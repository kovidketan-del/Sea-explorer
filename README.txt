SEA EXPLORER BOT — one-folder build
=====================================

WHAT IT DOES
------------
- Uses ADB + OpenCV only.
- Does NOT try to recognize every treasure.
- Sweeps the playable area in a fast serpentine pattern.
- Detects the spiky purple zombie/puffer hazard and diverts to a safer corner.
- Rejects the smoother purple pipe/coral shapes seen in the supplied recordings.
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
  Oxygen < 500:  one bag attempt every 2 completed runs
  Oxygen < 1000: one bag attempt every 3 runs
  Oxygen < 1500: one bag attempt every 5 runs
  Oxygen >=1500: one bag attempt every 8 runs

At every HOME screen it then buys as many oxygen upgrades as currently
affordable (maximum 8 in a batch). This keeps most spending directed at the
2000-level objective without starving the income upgrade.

IMPORTANT BEFORE FIRST RUN
--------------------------
1. Install Python 3.11+.
2. Install requirements:
       pip install -r requirements.txt
3. Connect the SAME Android test phone by USB.
4. Enable USB debugging and accept the RSA prompt.
5. Open Sea Explorer on the phone and leave it visible.
6. Double-click run_bot.bat.

On the first run the script reads the foreground Android package and remembers
it. No package name needs to be typed manually.

CURRENT STARTING LEVEL
----------------------
config.json currently has:
    "starting_oxygen_level": 170

That matches the end of the supplied recording. If you manually buy more oxygen
BEFORE the bot's first run, change this number to your actual displayed Oxygen
Level. Once the bot starts buying upgrades itself, leave progress_state.json
alone; it tracks successful purchases.

SAFETY / TEST MODE
------------------
To watch detection without any taps/movement:
    python sea_explorer_bot.py --dry-run

Offline test against a recording:
    python sea_explorer_bot.py --analyze-video "recording.mp4"

Ctrl+C safely releases the held virtual touch and writes progress.

WHY FAST SWEEP INSTEAD OF ITEM RECOGNITION?
-------------------------------------------
The user's idea is sound for this game: if the diver follows a held drag and
collectibles are acquired by contact, recognizing every item is unnecessary.
A dense sweep covers the screen regardless of treasure artwork. The only
important visual problem is avoiding the dangerous purple spiky enemy.

"Super-fast finger" does not necessarily make the diver itself infinitely fast:
the game can cap avatar speed. The script therefore sends a sequence of far
targets rather than thousands of tiny points; this keeps the virtual finger
moving aggressively while allowing the game physics to follow.

FILES
-----
sea_explorer_bot.py   main program
config.json           goal, economy policy, coordinates, timing
run_bot.bat           one-click Windows launcher
dry_run.bat           no-touch detector test
requirements.txt      Python packages
progress_state.json   created automatically after first run
sea_explorer.log      created automatically


LIVE FIX 1
----------
HOME detection was updated after the first real-phone run.
The turquoise HOME background can merge with the SHOP button in HSV, so HOME
now keys off the two isolated lower BAG SIZE + OXYGEN LEVEL purchase buttons.
This fixes the observed `STATE - -> UNKNOWN` on the supplied live HOME frame.
