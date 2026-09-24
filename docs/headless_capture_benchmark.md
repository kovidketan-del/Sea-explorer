# Window-free capture: measured on the connected OPPO CPH2577

2026-09-23, USB ADB, 1080×2400 phone, scrcpy 4.1 bundle, Python 3.13,
PyAV 18.1.0. Android Pointer Location and Show Taps were enabled. Measurements
are local samples, not theoretical scrcpy or USB specifications.

## Before changing the capture path

The visible-mirror implementation already transmitted H.264 from Android to
the PC, but scrcpy decoded and rendered it into a Windows window. The bot then
used MSS to grab that window, converted it to BGR, and enlarged 324×720 back to
1080×2400 before OpenCV. Gameplay touches were sent as Windows mouse events,
so moving/minimizing/covering the window interfered with capture/control.
Older ADB screencap fallback took roughly 1.2–1.4 seconds per frame.

Launcher/Pointer Location baseline, `python scripts/benchmark_window_capture.py`:

| Measure | Visible window + MSS |
|---|---:|
| Immediate capture-call median / p95 | 18.4 / 42.1 ms |
| Capture calls per second (not unique video frames) | 44.5 |
| Vision HSV / hazards / items / state / decision medians on enlarged frame | 3.8 / 29.4 / 29.3 / 54.3 / 0.09 ms |
| Approximate median vision-plus-decision work | 116.9 ms |
| ADB shell touch invocation to first observed Pointer Location frame | 197–264 ms (4 trials) |
| Of that, ADB command dispatch alone | 125–163 ms |

The 18.4 ms capture call can return a **stale** rendered frame; it does not
measure phone-to-PC latency or effective unique-frame FPS. The Windows scrcpy
process consumed about 1.5% of one CPU core in a **static launcher** sample,
which must not be compared directly to active/animated-game CPU use.

## After: encoded stream and control socket

The new path starts only the matching scrcpy 4.1 Android server, ADB-forwards
its video and control sockets, decodes H.264 packets with PyAV in memory, and
keeps only the latest 324×720 BGR frame. It does not start `scrcpy.exe`, create
a desktop mirror, capture a window, enlarge frames, or use a Windows mouse.
OpenCV works at the original video size. Menu taps still use ADB; held gameplay
touches use the persistent scrcpy control socket.

Launcher/Pointer Location sample, `python scripts/benchmark_headless_capture.py
--codec h264 --max-fps 60`:

| Measure | H.264 headless | H.265 headless |
|---|---:|---:|
| Changing decoded frames per second | 57.3 | 35.0 |
| Wait for next new frame median / p95 | 16.6 / 26.2 ms | 17.0 / 80.3 ms |
| Packet decode median / p95 | 2.7 / 4.6 ms | 5.5 / 13.7 ms |
| BGR conversion median / p95 | 7.1 / 8.4 ms | 7.4 / 9.4 ms |
| HSV / hazards / items / state / decision medians | 0.42 / 2.24 / 1.77 / 3.76 / 0.06 ms | 1.18 / 2.45 / 4.41 / 7.53 / 0.07 ms |
| Bot Python CPU, animated Pointer Location (% of one core) | 56% | 49% |
| Control-socket touch to observed Pointer Location frame | 81–312 ms (4 trials) | 103–288 ms (4 trials) |

H.264 is the lower-latency choice on this device. The headless path cuts
measured per-frame vision work from roughly 117 ms to roughly 8 ms on the same
downscaled recording fixture. Adding median decode, BGR conversion, and vision
work yields roughly 18 ms of host work, **not** an end-to-end latency number.
The new-frame wait is a separate sampling delay, not another guaranteed fixed
stage. The measured touch-overlay response varied substantially, and no test
has isolated camera presentation, device encode, USB/ADB transport, and Android
input handling into trustworthy individual numbers. Device video PTS and host
clocks were not synchronized. Thus the suggested 10–40 ms end-to-end figure is
**not verified** and should not be promised for this setup.

No GPU counter was recorded; the implementation removes desktop rendering,
but GPU utilization cannot be inferred from CPU percentages alone. Baseline
and headless CPU samples above were not under identical animation conditions,
so they also do not prove an overall CPU reduction. These are explicit gaps,
not values to fill with estimates.

**Crash-safety update:** a gameplay run and a launcher-only held-touch test
exposed a native PyAV/FFmpeg access violation inside `decoder.decode()`.
Putting PyAV in a child process protected the parent and released held touch,
but a later gameplay run still lost its decoder after 55 seconds. Its earlier
short benchmark delivered 55.1 frames/s, with a new-frame wait median/p95 of
15.6/52.2 ms and roughly 40% of one CPU core across parent and child during
pointer animation; these are **historical PyAV figures, not final results**.
The implementation is switching to an isolated FFmpeg executable decoding
raw H.264 into BGR frames over pipes. Its performance and stability remain to
be measured. No lower end-to-end latency is claimed on the basis of the old
PyAV samples.

For repeatable checks, run the benchmark scripts while the same phone and USB
connection are available. The bounded no-purchase live check is
`scripts/bounded_live_headless.py --seconds 75 --out <receipt.json>`; it releases
the held touch, closes sockets, and returns the phone to Home at the end. Add
`--snapshots-dir <directory>` to save optional low-rate frames during a test;
normal bot operation does not write screenshots.

## Live-test status

The first 75-second bounded attempt started the headless stream and exited
cleanly, but recorded zero collection/avoidance decisions. Its frame showed
the Android **keyguard** rather than Sea Explorer; therefore it is **not**
accepted as a gameplay validation. The bot and bounded test now refuse to run
when `dumpsys window policy` says the keyguard is showing. A repeat after the
phone was unlocked exposed a native PyAV decoder fault, contained by the
decoder child process but not cured. A later 120-second run exposed a separate recognition
error: the downscaled HOME title was mistaken for the gameplay HUD, causing
central hovering instead of a dive. The exact HOME frame is now a regression
fixture, and downscaled gameplay frames remain recognized as PLAYING. These
failed or partial attempts do not establish collection, virus avoidance, or
long-run gameplay stability. A 55-second run with PyAV did complete two dives
and release touch when decoding failed, but it did not meet the 150-second
stability target. A further live result with the replacement decoder is required.
