# Gaming benchmarks

Nothing becomes the default (kernel, scheduler, Mesa) until it is measured here. This folder holds
the method and, once someone has run it on real machines, the results. **No results have been
recorded yet:** they need a desktop with the games installed and an AMD and an NVIDIA graphics
card, which the build environment does not have.

## What to compare

| Candidate | Against | Why |
|---|---|---|
| Gaming kernel (Ubuntu HWE, `linux-image-generic-hwe-24.04`) | The standard 6.8 kernel | ntsync, sched_ext, newer scheduler and drivers |
| A `sched_ext` scheduler (e.g. `scx_lavd`) on the gaming kernel | The kernel's default scheduler | Only if a build of the schedulers exists (see `docs/gaming.md`) |
| Newer Mesa (Ubuntu 26.04's 26.0, rebuilt for 24.04) | Mesa 25.2 from `noble-updates` | Only if a rebuild exists |

Change one thing at a time. The same machine, the same driver, the same game settings.

## The five games

Pick five that you can run on both machines and write them in the results table:

1. a native Linux game
2. a Proton DX11 game
3. a Proton DX12 game
4. a CPU-heavy game (a strategy or simulation title)
5. a Windows `.exe` run through Axon APEX (`axon-apex-run`)

Use a repeatable scene: a built-in benchmark, or the same 60 seconds of a save game.

## Recording a run

1. Put this in `~/.config/MangoHud/MangoHud.conf`, with the folder named for the configuration and game
   (these keys are in MangoHud's own example config):

   ```
   output_folder=/home/you/bench/stock/cyberpunk
   autostart_log=10
   log_duration=60
   log_interval=0
   ```

2. Start the game with `mangohud %command%` (Steam) or `MANGOHUD=1 axon-apex-run game.exe`. The log is written
   10 seconds in and stops after 60 seconds.
3. Rename each log `run1.csv`, `run2.csv`, `run3.csv`. **Three runs per game and configuration** (the script
   warns about fewer, or about runs that differ by more than 5%).
4. Repeat for the other configuration, with `output_folder` pointing to `bench/axon/cyberpunk`, and so on.

```
bench/
  stock/<game>/run1.csv run2.csv run3.csv
  axon/<game>/run1.csv  run2.csv  run3.csv
```

## Reading the results

```bash
scripts/bench-mangohud.py summarize bench/stock/cyberpunk/*.csv
scripts/bench-mangohud.py compare bench/stock bench/axon --markdown > docs/benchmarks/results-amd-<date>.md
```

"1% low" is the average frame rate of the slowest 1% of frames. Add the machine (CPU, graphics card, driver,
kernel) and the date at the top of each results file. Commit one file per machine.

## Decision rule

A change becomes a default only if it is no worse than the baseline in every game on both machines
(within the 5% noise) and clearly better in at least one: both the average and the 1% low. Otherwise it stays
optional in Game Center.
