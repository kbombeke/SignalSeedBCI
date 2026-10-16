<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/logo-dark.png">
    <img src="docs/logo.png" alt="SignalSeedBCI logo" width="160">
  </picture>
</p>

<h1 align="center">SignalSeedBCI</h1>

Build, simulate and run brain–computer interface (BCI) applications on macOS or Windows, with and without any hardware.

- **Visual pipeline editor** in the browser: drag processing steps onto a canvas,
  connect them, press Run, and watch the signals, spectra, classifier output and
  feedback live.
- **Live, recorded or simulated data**, through exactly the same path: the BioSemi
  amplifier (via a small Windows "bridge" laptop), a replayed recording
  (BDF/EDF/GDF/FIF/BrainVision/EEGLAB/XDF), or synthetic EEG.
- **Python all the way down**: preprocessing with MNE/SciPy, classifiers from
  scikit-learn/pyRiemann, and your own Python functions as pipeline steps.

Everything communicates over [Lab Streaming Layer](https://labstreaminglayer.org) (LSL),
so other tools (PsychoPy, LabRecorder, OpenViBE, Unity, …) can join in.

> The command is **`signalseedbci`** (short alias: **`ssbci`**); the Python package has
> the same name.

---

## Contents

1. [How it fits together](#how-it-fits-together)
2. [Installation on macOS](#installation-on-macos)
3. [Installation on Windows](#installation-on-windows)
4. [Quick start (no hardware needed)](#quick-start-no-hardware-needed)
5. [Using the editor](#using-the-editor)
6. [Example pipelines](#example-pipelines)
7. [Recording with the BioSemi ActiveTwo](#recording-with-the-biosemi-activetwo)
8. [Command line](#command-line)
9. [Writing pipelines by hand](#writing-pipelines-by-hand)
10. [Node reference](#node-reference)
11. [Adding your own Python / MNE code](#adding-your-own-python--mne-code)
12. [Troubleshooting](#troubleshooting)
13. [Development](#development)
14. [Status and known limitations](#status-and-known-limitations)

---

## How it fits together

```
   Windows laptop (next to the amplifier)         Mac (processing + editor)
 ┌────────────────────────────────────┐ network ┌──────────────────────────────────────┐
 │ ActiveTwo ─USB─► bridge            │── LSL ─►│ editor (signalseedbci gui) or        │
 │   (or ActiView TCP ─► bridge)      │"BioSemi"│ signalseedbci run pipeline.yaml      │
 │ trigger port ─► "BioSemi-Triggers" │+triggers│  source → re-reference → filter →    │
 └────────────────────────────────────┘         │  features → classifier → feedback    │
                                                │                                      │
                                                │ replay / synthetic / simulated       │
                                                │ ActiveTwo publish the *same* streams │
                                                └──────────────────────────────────────┘
```

This is setup C below. In setup B, both boxes are the same Windows laptop; in setup A
there is no amplifier, only the simulated sources on the right.

A **pipeline** is a graph of nodes saved as a YAML file in `pipelines/`. The same file
runs live, on a replayed recording, or offline at full speed for training and evaluation.
That way training and online use exactly the same (causal) preprocessing.

### Three ways to set it up

SignalSeedBCI is the same software on macOS and Windows: every command, the editor and the
pipelines work identically on both. What differs is where the amplifier can be connected:
BioSemi's USB driver is for **Windows**, so a live ActiveTwo always goes into a Windows
computer.

| Setup | Install on | Amplifier | Good for |
|---|---|---|---|
| **A. Mac only** | the Mac ([macOS](#installation-on-macos)) | none: synthetic data and recordings | developing pipelines, training on recordings, demos |
| **B. Windows only** | one Windows laptop ([Windows](#installation-on-windows)) | USB to that laptop | a portable, single-laptop BCI |
| **C. Windows bridge + Mac** | both | USB to the Windows laptop, which streams to the Mac over the network | keeping the processing and editor on the Mac |

Setups A and B are independent of each other: you never need the other operating system.
For recording with the amplifier in setup B or C, see
[Recording with the BioSemi ActiveTwo](#recording-with-the-biosemi-activetwo).

All setups need [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 for you; the
installers below take care of it) and a modern browser (Chrome, Safari, Firefox or Edge)
for the editor. Linux works like macOS.

## Installation on macOS

Install once, in Terminal:

```bash
# 1. install uv (or: brew install uv)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. get the code
git clone https://github.com/kbombeke/SignalSeedBCI.git
cd SignalSeedBCI

# 3. create the environment and install (Python 3.12, all dependencies)
uv venv --python 3.12 .venv
uv pip install --python .venv -e ".[dev]"

# 4. check
source .venv/bin/activate
signalseedbci --version
pytest -q            # optional: 58 tests, about 70 s
```

`.[dev]` also installs pyRiemann (Riemannian classifiers), pyxdf (reading XDF files)
and pytest. A minimal install without those is `uv pip install --python .venv -e .`.

**Every time you use it**, open Terminal in the `SignalSeedBCI` folder and run:

```bash
source .venv/bin/activate     # once per terminal window
signalseedbci gui             # the editor; or any other signalseedbci command
```

**Updating:** `git pull`, then `uv pip install --python .venv -e ".[dev]"`.

## Installation on Windows

Install once:

1. Install [Git for Windows](https://git-scm.com/download/win) and clone the repository
   (`git clone https://github.com/kbombeke/SignalSeedBCI.git`), or download it as a ZIP
   from GitHub and unpack it.
2. Open **PowerShell** in the `SignalSeedBCI` folder (in Explorer: Shift + right-click in
   the folder › *Open PowerShell window here*) and run:

   ```powershell
   powershell -ExecutionPolicy Bypass -File windows\install.ps1
   ```

   This installs uv and Python 3.12, installs SignalSeedBCI into `.venv` (with the
   Riemannian classifiers and XDF support), and opens the LSL network ports in the Windows
   firewall for private networks. Run PowerShell **as Administrator** for the firewall part,
   or allow Python when Windows asks. Options (add them after `install.ps1`):
   - `-AllowPublic`: also open the ports on networks Windows calls *Public*. A direct
     Ethernet cable to a Mac usually shows up as a Public "Unidentified network". Only
     needed for setup C.
   - `-BridgeOnly`: a minimal install for a laptop that only runs the bridge (setup C).
3. For the amplifier: install BioSemi's software and driver (ActiView / Developers kit),
   which provides `Labview_DLL.dll`. Not needed for synthetic data or recordings.
4. Check: `.venv\Scripts\signalseedbci --version`

**Every time you use it**, double-click in the `windows` folder:

| Launcher | Starts |
|---|---|
| `start-editor.cmd` | the editor in your browser (keep its window open; close it to quit) |
| `start-bridge.cmd` | the ActiveTwo → LSL bridge (edit it to match your cap, e.g. `--channels 64`) |

For other commands, open PowerShell in the `SignalSeedBCI` folder and activate the
environment once per window:

```powershell
.venv\Scripts\Activate.ps1    # Command Prompt: .venv\Scripts\activate
signalseedbci discover        # any signalseedbci command, as on macOS
```

If PowerShell says running scripts is disabled, run
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, or call commands without
activating: `.venv\Scripts\signalseedbci discover`.

**Updating:** `git pull` (or download the ZIP again), then
`uv pip install --python .venv -e ".[riemann,xdf]"`.

### macOS and Windows side by side

| | macOS (Terminal) | Windows (PowerShell) |
|---|---|---|
| Activate the environment | `source .venv/bin/activate` | `.venv\Scripts\Activate.ps1` |
| Start the editor | `signalseedbci gui` | `windows\start-editor.cmd` or `signalseedbci gui` |
| Start the bridge | (not available: no amplifier driver) | `windows\start-bridge.cmd` |
| Run a command without activating | `.venv/bin/signalseedbci …` | `.venv\Scripts\signalseedbci …` |
| Stop a running command | Ctrl+C | Ctrl+C (or close the window) |
| Paths in commands | `data/rec.fif` | `data\rec.fif` or `data/rec.fif` |

Pipelines (YAML), recordings and trained models are plain files that work on both: you can
build and train a pipeline on the Mac and copy the folder to the Windows laptop, or the
other way round.

## Quick start (no hardware needed)

Start the editor: on macOS `signalseedbci gui` (in the activated environment), on Windows
double-click `windows\start-editor.cmd`. Your browser opens the editor at <http://localhost:8765>. Then:

1. In the top bar, open the pipeline **`eyes_alpha.yaml`**.
2. Set **Data** to *Replay a recording*. In the second list, choose
   **＋ Create synthetic recording…**, pick *Alpha bursts*, 180 s, and click **Create**.
   This writes a test recording in which strong alpha appears for 10 s every 20 s.
3. Press **▶ Run**. Within about 20 s the eyes in the `eyes` node close during the
   alpha bursts and open between them. Click **⧉** on the node for a full-size view,
   or on the `alphaplot` node for the alpha trace.
4. Press **■ Stop** when done.

Prefer the terminal? The same in commands (identical on macOS and Windows, in the
activated environment):

```bash
signalseedbci synth --paradigm alpha-bursts --duration 180 --record data/alpha_bursts_raw.fif
signalseedbci replay data/alpha_bursts_raw.fif --loop     # terminal 1: plays the file as "the amplifier"
signalseedbci run pipelines/eyes_alpha.yaml               # terminal 2: runs the pipeline
```

## Using the editor

Start it with `signalseedbci gui` from the repository folder. It serves the project in the
current folder (`pipelines/`, `data/`, `models/`). Options: `--port 8766`,
`--project <folder>`, `--no-browser`.

**Top bar**

| Control | What it does |
|---|---|
| Pipeline list, New, Save, Save as… | Open, create and save pipelines (YAML in `pipelines/`). ⌘S saves. |
| Validation | ✓ valid, or the first problem (hover for all). Problem nodes get a red outline. |
| **Data** | Where the data comes from when you press Run (see below). |
| **Train…** | Fits the pipeline's `Classifier` nodes, and reports the cross-validated accuracy: on the recordings you tick, or on a **live session** from the current Data source (see [Training on live data](#training-on-live-data)). |
| **▶ Run / ■ Stop** | Starts the pipeline together with its data source; Stop ends both. |

**Data choices**

| Data | Second list | ▶ Run starts |
|---|---|---|
| Amplifier / bridge (live) | — | the pipeline on the live streams (e.g. from the Windows laptop) |
| Replay a recording | recording | the recording as live streams (looping) + the pipeline |
| Synthetic EEG | motor imagery / alpha bursts | a synthetic EEG stream + the pipeline |
| Simulated ActiveTwo | — | a simulated amplifier through the real BioSemi decoder + the pipeline |
| Recording, offline | recording | the pipeline over the file as fast as possible (evaluation) |

The recording list ends with **＋ Create synthetic recording…** to make labelled test data.
Your own recordings appear automatically when you put them in the project, e.g. in `data/`.

**Canvas**

- Drag nodes from the palette on the left, or select a node and click a palette item to
  add the next step and connect it automatically.
- Connect by dragging from an output dot (right) to an input dot (left). Colours show the
  kind of data (signal, markers, epochs, features, predictions); connections that don't
  fit are refused. Drag a connected input dot away to move or remove that connection.
- Select a node to edit its settings on the right; ● marks values that differ from the
  default, and each setting has a short explanation.
- **Resize** a node by dragging its bottom-right corner (it appears on hover). Nodes with a
  live view grow in width and height, and once large enough the view switches from a
  thumbnail to the full plot with axes, channel labels and legend. Other nodes change width
  only. Neighbouring nodes move aside; double-click the corner to reset the size. Sizes are
  saved with the pipeline.
- Keys: Delete removes, ⌘D duplicates, ⌘Z / ⇧⌘Z undo / redo. Scroll to pan; pinch or
  ⌘-scroll to zoom; **Fit** and **Arrange** tidy the view.

**Live views inside nodes**

- **LSLSignalOut**: a plot of what it publishes, with markers if connected.
- **SpectrumOut**: the live frequency spectrum with the peak frequency. Its window switches
  between spectrum and spectrogram.
- **EyesFeedback**: animated eyes (closed / open), with a full-screen window for a participant.
- **GazeFeedback**: animated eyes that look up / down / left / right as classified.
- LSLSignalOut has a **display** setting (1–40 Hz, high-pass, or none) for its plots only;
  use *none* for slow signals such as EOG.

Click **⧉** on such a node (or double-click the plot) to open it in its own window, or
enlarge the node itself by dragging its corner.

**Bottom panel**

- **Console**: output of runs, training and created recordings, with Stop buttons.
- **Monitor**: every LSL stream on the network, a plot of any of them (display filter,
  scale, window, channel pages, ⧉ window) and incoming markers and predictions.

## Example pipelines

| Pipeline | What it shows | Needs |
|---|---|---|
| `eyes_alpha.yaml` | Eyes close when posterior alpha is high and open when it is low (ThresholdClassifier, no training). Reports how often its decision matches trigger markers. | alpha-bursts data, or a real eyes-open/closed session |
| `alpha_neurofeedback.yaml` | Relative alpha power as a live stream (`signalseedbci-alpha`) plus a live spectrum (`signalseedbci-spectrum`). | any EEG |
| `motor_imagery.yaml` | Left- vs right-hand imagery with CSP + LDA; predictions on `signalseedbci-predictions`. | training first (below) |
| `inear_gaze.yaml` | Gaze direction (up / down / left / right) from in-ear EOG: animated eyes look where the classifier says, plus a live EOG plot. | training first (below) |

Motor imagery, trained and tested on synthetic data:

```bash
signalseedbci synth --record data/calib_raw.fif --duration 400 --seed 1
signalseedbci train pipelines/motor_imagery.yaml --data data/calib_raw.fif
signalseedbci synth --record data/test_raw.fif --duration 200 --seed 2
signalseedbci run pipelines/motor_imagery.yaml --offline data/test_raw.fif
```

In the editor this is Data › *Replay a recording* › ＋ Create synthetic recording
(motor imagery), then **Train…**, then **▶ Run**.

### Training on live data

With a participant connected you usually calibrate first: the participant follows cues
(e.g. left / right) while the classifier collects labelled epochs, and the model is fitted
at the end. In the editor:

1. Set **Data** to *Amplifier / bridge (live)* (or a simulation, to try it out).
2. **Train…** › **Live session** › Train. The status shows *● calibrating*.
3. Present the cues from your stimulus program (trigger port or LSL markers), as many trials
   as you want per class.
4. Press **■ Stop**. The pipeline fits every `Classifier` and reports the cross-validated
   accuracy, and saves the raw session as `data/calib_<pipeline>_<date-time>_raw.fif`.
5. Press **▶ Run** for online use with the new model.

The saved session can be used to retrain later (e.g. after changing the filter settings)
without a new calibration: tick it in **Train…** › Recordings. On the command line:

```bash
signalseedbci train pipelines/motor_imagery.yaml --live                 # until Ctrl+C
signalseedbci train pipelines/motor_imagery.yaml --live --duration 300  # or a fixed time
signalseedbci train pipelines/motor_imagery.yaml --data data/calib_motor_imagery_20261014-153000_raw.fif
```

### In-ear gaze direction

The synthetic *eye-movements* data simulates a 16-channel in-ear montage: 12 in-ear
electrodes (LC1-3 / RC1-3 on the concha, LE1-3 / RE1-3 in the ear canal of the left /
right ear) and references M1 / M2 (mastoids), Fpz (forehead) and Neck. Cues 1-4 ask to
look up / down / left / right; the eyes move ~0.25 s later and hold ~1.2 s. Eye movements
follow the corneo-retinal dipole: looking right makes the right ear more positive and the
left ear more negative; vertical movements mainly reach the forehead.

The pipeline references everything to the neck, low-passes at 10 Hz, cuts an epoch around
each cue and classifies the mean amplitude per electrode in two windows (**TimeWindowMeans**)
with shrinkage LDA. **GazeFeedback** shows eyes that look in the predicted direction.

```bash
signalseedbci synth --paradigm eye-movements --duration 300 --seed 1 --record data/eye_movements_300s_raw.fif
signalseedbci train pipelines/inear_gaze.yaml --data data/eye_movements_300s_raw.fif
signalseedbci run pipelines/inear_gaze.yaml --offline data/eye_movements_session2_raw.fif
```

Results on synthetic data: ~98% cross-validated; 100% on a second session of the same
simulated person, ~94% on a different simulated person. With **in-ear electrodes only**
(drop Fpz, or Fpz and the mastoids, with a PickChannels node) left/right stays reliable
but up/down gets confused (77% / 60%), because vertical eye movements barely reach the ear.

In the editor: open `inear_gaze.yaml`, set Data to *Replay a recording* ›
`data/eye_movements_session2_raw.fif` (or *Synthetic EEG* › *Eye movements*, which simulates
a new person each time), press ▶ Run, and click ⧉ on the `gaze` node.

## Recording with the BioSemi ActiveTwo

The amplifier connects by USB to a Windows computer, where the **bridge** turns it into
LSL streams (`BioSemi` and `BioSemi-Triggers`). Pipelines read those streams, on the same
laptop (setup B) or on a Mac on the network (setup C).

**Starting the bridge** (on Windows, in one of two ways):

- **USB driver**: double-click `windows\start-bridge.cmd`, or run
  `signalseedbci bridge biosemi --channels 64`. Close ActiView first; only one program can
  use the amplifier. The speed mode, sampling rate and channel layout are read from
  the amplifier, and CMS-out-of-range and low battery are reported. The driver is
  found in `C:\Program Files\BioSemi\Developers_kit\C-code`; otherwise use
  `--lib <path>` or set `BIOSEMI_DRIVER`.
- **ActiView TCP server**: ActiView keeps the amplifier (so you still see electrode
  offsets) and streams to the bridge. Enable the TCP server in ActiView, then:
  `signalseedbci bridge actiview --tcp-channels 73 --sfreq 2048`, using the channel count
  and rate shown in ActiView.

On first use, check that the bridge reports the right speed mode, channel count and
2048 Hz (see [Status and known limitations](#status-and-known-limitations)).

### Setup B: everything on the Windows laptop

1. **Install** on the laptop (see [Installation on Windows](#installation-on-windows)).
2. **Test without the amplifier**: start the editor (`start-editor.cmd`), open a pipeline,
   set Data to *Simulated ActiveTwo* and press ▶ Run.
3. **With the amplifier**: start the bridge (`start-bridge.cmd`), then in the editor set
   Data to *Amplifier / bridge (live)* and press ▶ Run. The bridge and the pipeline talk
   over LSL on the same machine; no network is needed.
4. Watch the Console for `tick took … ms` warnings: they mean the laptop can't keep up
   (close other programs, or decimate earlier in the pipeline).

### Setup C: Windows bridge, Mac for processing

1. **Install** on the laptop (see [Installation on Windows](#installation-on-windows),
   `-BridgeOnly` is enough) and on the Mac (see [Installation on macOS](#installation-on-macos)).
2. **Test the network without the amplifier.** On the laptop:
   `.venv\Scripts\signalseedbci bridge biosemi --fake`. On the Mac: `signalseedbci discover --check`.
   You should see `BioSemi` (EEG) and `BioSemi-Triggers`, with the clock offset and
   about 2048 Hz measured.
3. **If the Mac doesn't find the streams** (different subnets, Wi-Fi blocking multicast,
   university networks, a *Public* network on Windows), run on **both** machines:
   `signalseedbci lsl-config --peers <laptop-ip> <mac-ip> --session-id mylab`, and on
   Windows rerun `install.ps1 -AllowPublic` as Administrator. A direct Ethernet cable or a
   dedicated router is the most reliable setup.
4. **Start the bridge** on the laptop (above).
5. **On the Mac**: open a pipeline in the editor, set Data to *Amplifier / bridge
   (live)*, and press ▶ Run.

### Triggers and recording (both setups)

- **Triggers**: send event codes to the ActiveTwo trigger port from the stimulus
  computer. They are decoded with sample accuracy and published as `BioSemi-Triggers`.
- **Software markers** over LSL (e.g. from PsychoPy) also work: add an `LSLMarkerSource`
  node. They carry a few ms of network jitter, which is fine for motor imagery or alpha;
  use the trigger port for ERPs/P300.

- **Recording**: record whole sessions (all streams) with [LabRecorder](https://github.com/labstreaminglayer/App-LabRecorder)
  to XDF; SignalSeedBCI reads XDF files for replay and training. Alternatively, add a
  `Recorder` node to save a pipeline's signal and markers as a `.fif` file, or calibrate
  with [Train… › Live session](#training-on-live-data), which saves the raw session.

## Command line

All commands: `signalseedbci --help`; per command: `signalseedbci <command> --help`.
The commands are the same on macOS and Windows; run them in the activated environment
(see [macOS and Windows side by side](#macos-and-windows-side-by-side)).

| Command | Purpose |
|---|---|
| `signalseedbci gui` | Start the graphical editor |
| `signalseedbci run P.yaml` | Run a pipeline on live LSL streams |
| `signalseedbci run P.yaml --offline REC` | Run a pipeline over a recording as fast as possible |
| `signalseedbci run P.yaml --replay REC` | Run a pipeline over a recording at real-time pace (no LSL) |
| `signalseedbci train P.yaml --data REC [REC…]` | Fit the Classifier nodes; prints CV accuracy |
| `signalseedbci train P.yaml --live [--duration S]` | Calibrate on the live streams until stopped, then fit; saves the raw session in `data/` |
| `signalseedbci replay REC [--loop]` | Publish a recording as LSL streams (like an amplifier) |
| `signalseedbci synth [--paradigm alpha-bursts\|eye-movements] [--record FILE --duration S]` | Stream synthetic EEG, or write a synthetic recording |
| `signalseedbci bridge biosemi [--fake]` | BioSemi ActiveTwo (USB) → LSL; `--fake` simulates the amplifier |
| `signalseedbci bridge actiview --tcp-channels N --sfreq F` | ActiView TCP server → LSL |
| `signalseedbci discover [--check]` | List LSL streams on the network (with clock offsets and rates) |
| `signalseedbci lsl-config --peers IP…` | Configure LSL for networks where discovery doesn't work |
| `signalseedbci nodes [--json]` | List all pipeline node types and their settings |

Recordings (`REC`) can be `.bdf`, `.edf`, `.gdf`, `.fif`, `.vhdr`, `.set` or `.xdf`.
For BioSemi files the `Status` channel provides the triggers.

## Writing pipelines by hand

Pipelines are plain YAML. The editor reads and writes the same files, so you can switch
between them freely.

```yaml
name: My pipeline
nodes:
  src:    {type: LSLSource, params: {signal_name: BioSemi, marker_name: BioSemi-Triggers}}
  car:    {type: Rereference, params: {ref: average}}
  band:   {type: BandpassFilter, params: {l_freq: 8, h_freq: 30}}
  epochs: {type: Epocher, params: {events: {"1": left, "2": right}, tmin: 0.5, tmax: 2.5}}
  clf:    {type: Classifier, params: {estimator: csp_lda, model_path: ../models/mi.joblib}}
edges:
  - src.signal -> car.in
  - car.out -> band.in
  - band.out -> epochs.in
  - src.markers -> epochs.markers
  - epochs.out -> clf.in
```

Paths in a pipeline are relative to the pipeline file. All nodes and their settings are
described in the [Node reference](#node-reference) below.

## Node reference

Every node has **inputs** (left dots in the editor), **outputs** (right dots) and
**settings** (the inspector on the right, or `params:` in YAML). Settings you leave out use
the default. The editor shows the same descriptions; `signalseedbci nodes` lists them in
the terminal.

**Data kinds**: what flows along a connection, each with its own colour in the editor:

| Kind | What it is |
|---|---|
| `signal` | Continuous multichannel data in µV, with channel names and sampling rate |
| `markers` | Time-stamped events, e.g. trigger codes `1`, `2` or text markers |
| `epochs` | Fixed-length pieces of signal cut around events (or sliding windows), each with a label |
| `features` | A vector of numbers per epoch (e.g. band powers), with a label |
| `predictions` | A classifier decision: label + probability per class |

An input that accepts several kinds is written `epochs|features`. Inputs marked
*optional* may stay unconnected.

### Sources

**LSLSource**: live data from Lab Streaming Layer (the BioSemi bridge, a replay, the
synthetic generator, or any LSL program).
- Outputs: `signal`, `markers`
- `signal_name` (`BioSemi`): stream to connect to; empty = match by `signal_type` (`EEG`)
- `marker_name` (`BioSemi-Triggers`): marker stream; empty = no markers. It may start before
  or after the pipeline; the node keeps looking for it.
- `timeout` (15 s): how long to wait for the signal stream before giving up

**LSLMarkerSource**: an additional marker stream, e.g. software markers from PsychoPy.
- Output: `markers` (connect it to the same `markers` input as other marker sources; marker
  inputs accept several connections)
- `name`, or `type` (`Markers`) if no name is given

**FileSource**: plays a recording through the pipeline, at real-time pace (simulation) or
as fast as possible (training / evaluation). The editor's Data picker and `--offline`
swap it in automatically; you rarely add it by hand.
- Outputs: `signal`, `markers`
- `path` (required): BDF/EDF/GDF/FIF/BrainVision/EEGLAB/XDF file
- `stim_channel` (`auto` = `Status` or the first trigger channel), `event_mask` (255): which
  trigger bits count. BioSemi files often need 255 (8-bit triggers).

### Preprocessing

All filters are **causal** (they use only past samples) and keep their state between
chunks, so offline training and live use give identical results.

**PickChannels**: keep a subset of channels.
- `include` / `exclude`: names or regular expressions, e.g. `[O1, Oz, O2]` or `["EX.*"]`
- `types`: e.g. `[eeg]` to drop auxiliary channels

**RenameChannels**: rename channels, e.g. BioSemi `A1…B32` to 10-20 names, or a recording's own labels. Put it right after the source; later nodes use the new names. In the editor, pick under *Channels from* a recording in the project or a running LSL stream (such as the bridge). The editor then lists its channels with a box for each new name; leave a box empty to keep the name. It starts with the recording chosen under Data. You can also add names by hand.
- `preset`: `biosemi16/32/64/128/160/256` (MNE's BioSemi cap order; check your cap's layout)
- `mapping`: explicit `{old: new}` names, applied after the preset

**Rereference**: change the reference. BioSemi records reference-free, so always use it.
- `ref`: `average` (common average of the EEG channels) or a list, e.g. `[EX1, EX2]`
  (linked mastoids) or `[Neck]`
- `drop_ref` (false): remove the reference channels from the output

**BandpassFilter**: Butterworth band-pass, high-pass or low-pass (designed with MNE).
- `l_freq` (1 Hz) / `h_freq` (40 Hz): leave one empty (`null`) for a pure low-pass or high-pass
- `order` (4)

**NotchFilter**: removes mains interference.
- `freqs` (`[50]`): e.g. `[50, 100]`, or `[60]` in the Americas; `quality` (30) sets the width

**Decimate**: lowers the sampling rate after an anti-alias filter.
- `target_sfreq` (256 Hz): the input rate must be an integer multiple. The same pipeline then
  works for 2048 Hz live BioSemi data and 512 Hz synthetic data.

### Epoching

**Epocher**: cuts an epoch around each selected marker (cue-based paradigms, ERPs).
- Inputs: `in` (signal), `markers`
- `events`: marker → class label, e.g. `{'1': left, '2': right}`; only these markers make
  epochs. Empty = every marker, labelled with its value.
- `tmin` / `tmax` (0.5 / 2.5 s): epoch start and end relative to the marker
- `baseline`: `[start, end]` in s whose mean is subtracted per channel, e.g. `[-0.3, 0]`
- `buffer_s` (30 s): how much signal it keeps for markers that arrive late (e.g. over the network)

**SlidingWindow**: overlapping windows for continuous decoding (no markers needed).
- `window_s` (1 s), `step_s` (0.1 s): e.g. 1 s windows every 0.25 s for neurofeedback

### Features

**BandPower**: power per channel in frequency bands (Welch), per epoch.
- `bands` (`{mu: [8, 12], beta: [13, 30]}`): band name → `[fmin, fmax]` Hz
- `log` (true): log10 of the power; `relative` (false): divide by the total 1–40 Hz power
  (makes the value independent of overall signal level, as in the alpha pipelines)
- Feature names are `channel:band`, e.g. `Oz:alpha`.

**TimeWindowMeans**: mean amplitude per channel in one or more time windows of
each epoch. These are classic features for ERPs and for eye movements (EOG), where the
signal shifts and stays shifted while the gaze is held.
- Input: `epochs`; output: `features`
- `windows` (`[[0.3, 0.7], [0.7, 1.1]]`): `[start, end]` in seconds **relative to the event**.
  Every window must lie inside the epoch (`tmin`…`tmax` of the Epocher); otherwise the
  node reports which window is outside.
- Feature names are `channel:start-end s`, e.g. `LC1:0.3-0.7s`. With 15 channels and two
  windows that gives 30 features.
- Tip: combine with an Epocher `baseline`, so each mean is the *change* from before the cue,
  and with the `lda` estimator.

### Classification

**Classifier**: a trained machine-learning model (scikit-learn / pyRiemann / MNE).
- Input: `epochs` or `features`; output: `predictions`
- `estimator`:
  - `csp_lda`: CSP + LDA, for motor imagery (epochs)
  - `ts_lr` / `mdm`: Riemannian tangent space + logistic regression / minimum distance (epochs)
  - `xdawn_ts_lr`: xDAWN + tangent space, for ERPs / P300 (epochs)
  - `lda`: shrinkage LDA on feature vectors (BandPower, TimeWindowMeans)
  - `file.py:function`: your own estimator
- `model_path`: where **Train…** saves the model and Run loads it. The inspector shows whether
  it is trained and its cross-validated accuracy (`cv_folds`, 5).
- The model remembers the channels, sampling rate and epoch length it was trained on. If a
  pipeline feeds it something different, it refuses to run and explains why, rather than
  producing meaningless output.

**ThresholdClassifier**: a two-state decision on one feature, **high vs low**
(e.g. alpha high → `closed`, low → `open`). It needs no training.
- Inputs: `in` (features), `markers` (optional, for the accuracy check); output: `predictions`
- How it works, per update:
  1. takes the feature (`feature`: `mean` of all, or a name / regular expression such as
     `Oz:alpha`) and smooths it (`smooth_s`, 0.5 s);
  2. sets the threshold: in `adaptive` mode halfway between the typical low (10th
     percentile) and typical high (90th percentile) of the last `history_s` (60 s), so it
     calibrates itself to each person; in `fixed` mode it uses `threshold`;
  3. in adaptive mode, it waits until both levels have been seen: the spread must be at
     least `min_contrast` (0.5) × the median, and at least `min_history_s` (8 s) of data must
     be in. The console shows "calibrating" until then;
  4. decides with **hysteresis**: it only switches when the value clearly crosses the
     threshold, by a dead band of `hysteresis` (15%) of the high-low range, so it doesn't
     flicker;
  5. outputs the label (`high_label` / `low_label`) and a probability that grows smoothly
     with the distance to the threshold.
- `truth`: marker → true state, e.g. `{'1': closed, '2': open}`. The node then reports how
  often its decision matches ("matches markers 92%"); this doesn't affect the decision.
- Tips: the reaction time is about 1 s (analysis window + smoothing + hysteresis). Start a
  session with an eyes-open and an eyes-closed block so it calibrates immediately. Use
  `fixed` if one state is rare.

### Scripting

**SignalScript** / **EpochScript**: run your own Python function on every chunk of
signal / every epoch. See [Adding your own Python / MNE code](#adding-your-own-python--mne-code).
- `script` (required): the Python file; `params`: a dictionary passed to the script

### Outputs

Nodes marked ▣ show a **live view in their node box** while the pipeline runs. Click
**⧉** (or double-click the view) to open it in its own window, or drag the node's corner to
enlarge it.

**LSLSignalOut** ▣: publishes a signal or feature vectors as an LSL stream for other
programs, and plots it.
- Inputs: `in` (signal or features), `markers` (optional: republished as `<name>-markers` and
  drawn as event lines)
- `name` (`signalseedbci-out`), `type` (`EEG`)
- `display`: filter for the editor's plots only, the published data is unchanged:
  `bandpass` (1–40 Hz + notch, default), `highpass` (0.5 Hz) or `none`. Use `none` for slow
  signals such as EOG, which the 1–40 Hz view would hide.
- The view shows stacked channels for signals and overlaid lines for features (e.g. relative
  alpha per channel).

**SpectrumOut** ▣: the real-time frequency spectrum (Welch power, µV²/Hz) of a signal,
published as LSL (channels named `CHANNEL@FREQ`, e.g. `Oz@10`).
- Inputs: `in` (signal), `markers` (optional, as above)
- `window_s` (2 s) analysed every `step_s` (0.25 s), `resolution_hz` (1 Hz), `fmin` / `fmax`
  (1–40 Hz)
- `average_channels` (false): publish one averaged spectrum; needed for many channels (a
  stream holds at most 8192 values per update)
- The node shows the spectrum with the alpha band shaded and the peak frequency. Its window
  switches between **spectrum** (all channels + mean or one channel, dB or linear, averaging
  over frames, θ/α/β bands, peak marked) and a scrolling **spectrogram** with event lines.

**EyesFeedback** ▣: animated eyes that are **closed** when the prediction is
`closed_label` (default `closed`) and **open** otherwise. While open they blink now and then
and glance around.
- Input: `predictions` (e.g. from ThresholdClassifier); published again as LSL `name`
  (`signalseedbci-eyes`)
- `graded` (false): the eyelids follow the probability of `closed` instead of snapping
  open / closed
- The window is meant for a participant: **Full screen**, double-click hides the controls,
  and the "eyes open / eyes closed" label can be switched off.

**GazeFeedback** ▣: animated eyes that **look** in the predicted direction: up,
down, left or right. They hold that for `hold_s` (1.5 s), then look ahead again until the
next prediction. Looking down lowers the eyelids a little; looking up lifts the brows.
- Input: `predictions` (e.g. from a Classifier trained on cues `up/down/left/right`);
  published again as LSL `name` (`signalseedbci-gaze`)
- `up_label`, `down_label`, `left_label`, `right_label`: which prediction labels mean
  which direction, if your classes are named differently (e.g. `1`…`4`)
- `graded` (false): the gaze follows the probabilities as a weighted direction, so an
  uncertain decision looks only partway
- The node status shows the direction and its probability, or "looking ahead · last: left"
  between trials.

**PredictionOut**: publishes predictions as an LSL marker stream (JSON
`{"label": ..., "proba": {...}}`) for feedback programs such as PsychoPy or Unity.
- `name` (`signalseedbci-predictions`)

**Recorder**: saves the signal (and markers, if connected) to a `.fif` file when the
pipeline stops.
- `path` (`recording_raw.fif`). For whole sessions with all streams, LabRecorder (XDF) is
  usually the better choice.

**Logger**: prints what arrives in the Console. For predictions with a known true label
(from the Epocher's `events`, or ThresholdClassifier's `truth`) it shows the running accuracy.
- `every_s` (2 s): minimum time between summary lines for signal / feature input

## Adding your own Python / MNE code

- **`SignalScript`**: a Python file with `process(data, times, info, state)`, called on
  every chunk of continuous signal (µV, shape channels × samples). It must be causal,
  i.e. not use future samples.
- **`EpochScript`**: `process(epoch_data, meta, state)`, called per epoch. This is the
  natural place for MNE functions such as `ica.apply`, baseline correction or resampling.
  Return `None` to reject the epoch. Example: `scripts/mne_epoch_cleaning.py`.
- **Custom classifiers**: set `estimator: path/to/file.py:make_estimator`, a function
  returning any scikit-learn estimator.
- **Feedback programs** read predictions from LSL: `signalseedbci-predictions` (or the name set
  in `PredictionOut` / `EyesFeedback`), as JSON `{"label": ..., "proba": {...}}`.

## Troubleshooting

| Problem | What to do |
|---|---|
| "No LSL stream … found" | Is the bridge, replay or synthetic source running? `signalseedbci discover` lists what the Mac sees. Across machines, see step 3 of the BioSemi section. |
| Warning "A stream named 'BioSemi' is already on the network" | A real or simulated amplifier is still streaming. The pipeline may connect to that one; stop it, or rename a stream. |
| Editor: "Address already in use" | Another editor is running; use `signalseedbci gui --port 8766`. |
| "not trained yet" on a Classifier | Use **Train…** (or `signalseedbci train`) before running. |
| "model was trained on different input" | The channels, rate or epoch settings changed since training: retrain. |
| Eyes don't react at first | The ThresholdClassifier calibrates itself and starts once it has seen both low and high alpha. Begin with an eyes-open/eyes-closed block, or set a fixed threshold. |
| Windows: the editor or bridge can't see the other machine's streams | The network may be *Public*: rerun `install.ps1 -AllowPublic` as Administrator, or set the network to Private in Windows Settings. |
| `signalseedbci: command not found` / not recognized | Activate the environment: macOS `source .venv/bin/activate`, Windows `.venv\Scripts\Activate.ps1`. |
| Windows: "running scripts is disabled on this system" | Run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once in PowerShell, or use `.venv\Scripts\signalseedbci` without activating. |

## Development

```
SignalSeedBCI/
├── src/signalseedbci/
│   ├── bridge/          BioSemi USB + ActiView TCP → LSL (numpy + pylsl only)
│   ├── sources/         replay and synthetic EEG generators
│   ├── pipeline/        graph engine and all nodes (nodes/*.py)
│   ├── gui/             editor server + static web app (no build step)
│   ├── cli.py           the signalseedbci / ssbci command
│   └── models.py        built-in classifiers
├── pipelines/           example pipelines (YAML)
├── scripts/             example user scripts for Signal/EpochScript
├── tests/               pytest suite
├── docs/                logo
├── windows/             installer, editor and bridge launchers for Windows
├── data/                your recordings (not in git)
└── models/              trained models (not in git)
```

- Tests: `pytest -q`. GitHub Actions runs them on Windows and macOS for every push
  (`.github/workflows/tests.yml`).
- A new node is a class in `src/signalseedbci/pipeline/nodes/` that declares `inputs`,
  `outputs` and `params`. It appears automatically in the editor palette and in
  `signalseedbci nodes`.
- The editor is plain HTML/CSS/JavaScript in `src/signalseedbci/gui/static/`; reload the
  browser page after editing it.

## Status and known limitations

Tested: the BioSemi protocol decoder against a simulated ActiveTwo (buffer wrap-around,
odd read sizes, corruption and resynchronisation, 24-bit values, triggers); streaming
filters against one-shot filtering; epoching; LSL round trips; the editor; and training,
offline and live runs on synthetic data.

**Not yet verified with real hardware:**

- The USB driver calls (`Labview_DLL` via ctypes) follow BioSemi's documentation and the
  OpenViBE / LSL App-BioSemi drivers. On first use, check that the bridge reports the
  right speed mode, channel count and 2048 Hz.
- The CMS status bit polarity (`CMS_BIT_HIGH_MEANS_IN_RANGE` in `bridge/biosemi.py`):
  OpenViBE reports it is inverted relative to BioSemi's documentation.
- ActiView TCP scaling (31.25 nV per bit assumed): compare one signal with ActiView's display.
- 10-20 channel names assume the BioSemi A1… cap order of MNE's `biosemiNN` montages;
  check against your cap's layout sheet.
