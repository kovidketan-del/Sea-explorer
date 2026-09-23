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
The original in-process benchmark above predates the fix. Decoding now runs in
a child process, passing BGR frames over a pipe; if native decoding faults,
the bot sees EOF, releases held touch, and stops rather than crashing its own
process. In a repeat launcher/Pointer Location benchmark of the isolated H.264
path, 55.1 changing frames/s were delivered; new-frame wait median/p95 was
15.6/52.2 ms; decoder and BGR conversion medians were 1.1/3.2 ms. The parent
and decoder together consumed about 40% of one CPU core during pointer
animation. These short samples do **not** establish that the final path has
lower end-to-end latency than the visible-window baseline: the new p95 wait is
higher than the earlier in-process sample, and a touch-to-overlay response
varied from 80 to 245 ms. Stability and the lack of a visible window are the
verified benefits of the isolation so far.

For repeatable checks, run the benchmark scripts while the same phone and USB
connection are available. The bounded no-purchase live check is
`scripts/bounded_live_headless.py --seconds 75 --out <receipt.json>`; it releases
the held touch, closes sockets, and returns the phone to Home at the end.

## Live-test status

The first 75-second bounded attempt started the headless stream and exited
cleanly, but recorded zero collection/avoidance decisions. Its frame showed
the Android **keyguard** rather than Sea Explorer; therefore it is **not**
accepted as a gameplay validation. The bot and bounded test now refuse to run
when `dumpsys window policy` says the keyguard is showing. A repeat after the
phone is unlocked is still required to assess live control, reaction latency,
collection, hazard avoidance, and long-run stability. No claim about those
gameplay outcomes is made from the locked-phone attempt.
