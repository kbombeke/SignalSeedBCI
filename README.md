# signalseeder

Build, simulate and run brain–computer interface (BCI) experiments on macOS, with a
**BioSemi ActiveTwo** amplifier or without any hardware.

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

> The Python package and command are called **`bciflow`**; this repository is
> signalseeder.

---

## Contents

1. [How it fits together](#how-it-fits-together)
2. [Installation](#installation)
3. [Quick start (no hardware needed)](#quick-start-no-hardware-needed)
4. [Using the editor](#using-the-editor)
5. [Example pipelines](#example-pipelines)
6. [Recording with the BioSemi ActiveTwo](#recording-with-the-biosemi-activetwo)
7. [Command line](#command-line)
8. [Writing pipelines by hand](#writing-pipelines-by-hand)
9. [Adding your own Python / MNE code](#adding-your-own-python--mne-code)
10. [Troubleshooting](#troubleshooting)
11. [Development](#development)
12. [Status and known limitations](#status-and-known-limitations)

---

## How it fits together

```
   Windows laptop (next to the amplifier)            Mac (processing + editor)
 ┌──────────────────────────────────────┐  network  ┌────────────────────────────────────────┐
 │ ActiveTwo ─USB─► bciflow bridge      │── LSL ───►│ bciflow gui / bciflow run pipeline.yaml │
 │   (or ActiView TCP ─► bridge)        │ "BioSemi" │  source → re-reference → filter →        │
 │ trigger port ─► "BioSemi-Triggers"   │ +triggers │  features → classifier → feedback        │
 └──────────────────────────────────────┘           │                                          │
                                                    │  replay / synthetic / simulated ActiveTwo│
                                                    │  publish the *same* streams (no amp)     │
                                                    └────────────────────────────────────────┘
```

A **pipeline** is a graph of nodes saved as a YAML file in `pipelines/`. The same file
runs live, on a replayed recording, or offline at full speed for training and evaluation.
That way training and online use exactly the same (causal) preprocessing.

## Installation

### Requirements

- macOS on Apple Silicon or Intel (Linux works too), or Windows 10/11 for the bridge laptop
- [uv](https://docs.astral.sh/uv/) (it installs the right Python, 3.12, for you)
- A modern browser (Chrome, Safari, Firefox, Edge) for the editor

### macOS

```bash
# 1. install uv once (or: brew install uv)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. get the code
git clone https://github.com/kbombeke/signalseeder.git
cd signalseeder

# 3. create the environment and install (Python 3.12, all dependencies)
uv venv --python 3.12 .venv
uv pip install --python .venv -e ".[dev]"

# 4. activate it (do this in every new terminal)
source .venv/bin/activate

# 5. check
bciflow --version
pytest -q            # optional: 48 tests, about 30 s
```

`.[dev]` also installs pyRiemann (Riemannian classifiers), pyxdf (reading XDF files)
and pytest. A minimal install without those is `uv pip install --python .venv -e .`.

**Updating:** `git pull`, then rerun step 3 (the `uv pip install` line).

### Windows (the bridge laptop next to the amplifier)

1. Install [Git for Windows](https://git-scm.com/download/win) and clone the repository,
   or download it as a ZIP from GitHub and unpack it.
2. Open PowerShell in the `signalseeder` folder and run:

   ```powershell
   powershell -ExecutionPolicy Bypass -File windows\install.ps1
   ```

   This installs uv and Python 3.12, installs bciflow into `.venv`, and opens the LSL
   network ports in the Windows firewall for private networks (run PowerShell as
   Administrator for that part, or allow Python when Windows asks).
3. For the USB connection, install BioSemi's software and driver (ActiView /
   Developers kit), which provides `Labview_DLL.dll`.

## Quick start (no hardware needed)

```bash
source .venv/bin/activate
bciflow gui
```

Your browser opens the editor at <http://localhost:8765>. Then:

1. In the top bar, open the pipeline **`eyes_alpha.yaml`**.
2. Set **Data** to *Replay a recording*. In the second list, choose
   **＋ Create synthetic recording…**, pick *Alpha bursts*, 180 s, and click **Create**.
   This writes a test recording in which strong alpha appears for 10 s every 20 s.
3. Press **▶ Run**. Within about 20 s the eyes in the `eyes` node close during the
   alpha bursts and open between them. Click **⧉** on the node for a full-size view,
   or on the `alphaplot` node for the alpha trace.
4. Press **■ Stop** when done.

Prefer the terminal? The same in commands:

```bash
bciflow synth --paradigm alpha-bursts --duration 180 --record data/alpha_bursts_raw.fif
bciflow replay data/alpha_bursts_raw.fif --loop     # terminal 1: plays the file as "the amplifier"
bciflow run pipelines/eyes_alpha.yaml               # terminal 2: runs the pipeline
```

## Using the editor

Start it with `bciflow gui` from the repository folder. It serves the project in the
current folder (`pipelines/`, `data/`, `models/`). Options: `--port 8766`,
`--project <folder>`, `--no-browser`.

**Top bar**

| Control | What it does |
|---|---|
| Pipeline list, New, Save, Save as… | Open, create and save pipelines (YAML in `pipelines/`). ⌘S saves. |
| Validation | ✓ valid, or the first problem (hover for all). Problem nodes get a red outline. |
| **Data** | Where the data comes from when you press Run (see below). |
| **Train…** | Fits the pipeline's `Classifier` nodes on the recordings you tick, and reports the cross-validated accuracy. |
| **▶ Run / ■ Stop** | Starts the pipeline together with its data source; Stop ends both. |

**Data choices**

| Data | Second list | ▶ Run starts |
|---|---|---|
| Amplifier / bridge (live LSL) | — | the pipeline on the live streams (e.g. from the Windows laptop) |
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
- Keys: Delete removes, ⌘D duplicates, ⌘Z / ⇧⌘Z undo / redo. Scroll to pan; pinch or
  ⌘-scroll to zoom; **Fit** and **Arrange** tidy the view.

**Live views inside nodes**

- **LSLSignalOut**: a plot of what it publishes, with markers if connected.
- **SpectrumOut**: the live frequency spectrum with the peak frequency. Its window switches
  between spectrum and spectrogram.
- **EyesFeedback**: animated eyes (closed / open), with a full-screen window for a participant.

Click **⧉** on such a node (or double-click the plot) to open it in its own window.

**Bottom panel**

- **Console**: output of runs, training and created recordings, with Stop buttons.
- **Monitor**: every LSL stream on the network, a plot of any of them (display filter,
  scale, window, channel pages, ⧉ window) and incoming markers and predictions.

## Example pipelines

| Pipeline | What it shows | Needs |
|---|---|---|
| `eyes_alpha.yaml` | Eyes close when posterior alpha is high and open when it is low (ThresholdClassifier, no training). Reports how often its decision matches trigger markers. | alpha-bursts data, or a real eyes-open/closed session |
| `alpha_neurofeedback.yaml` | Relative alpha power as a live stream (`bciflow-alpha`) plus a live spectrum (`bciflow-spectrum`). | any EEG |
| `motor_imagery.yaml` | Left- vs right-hand imagery with CSP + LDA; predictions on `bciflow-predictions`. | training first (below) |

Motor imagery, trained and tested on synthetic data:

```bash
bciflow synth --record data/calib_raw.fif --duration 400 --seed 1
bciflow train pipelines/motor_imagery.yaml --data data/calib_raw.fif
bciflow synth --record data/test_raw.fif --duration 200 --seed 2
bciflow run pipelines/motor_imagery.yaml --offline data/test_raw.fif
```

In the editor this is Data › *Replay a recording* › ＋ Create synthetic recording
(motor imagery), then **Train…**, then **▶ Run**.

## Recording with the BioSemi ActiveTwo

The amplifier is connected to a Windows laptop. The bridge on that laptop streams it to
the network, where the Mac picks it up.

1. **Install** on the laptop (see [Windows](#windows-the-bridge-laptop-next-to-the-amplifier)).
2. **Test the network without the amplifier.** On the laptop:
   `.venv\Scripts\bciflow bridge biosemi --fake`. On the Mac: `bciflow discover --check`.
   You should see `BioSemi` (EEG) and `BioSemi-Triggers`, with the clock offset and
   about 2048 Hz measured.
3. **If the Mac doesn't find the streams** (different subnets, Wi-Fi blocking multicast,
   university networks), run on **both** machines:
   `bciflow lsl-config --peers <laptop-ip> <mac-ip> --session-id mylab`.
   A direct Ethernet cable or a dedicated router is the most reliable setup.
4. **Start the bridge**, in one of two ways:
   - **USB driver**: `windows\start-bridge.cmd`, or
     `bciflow bridge biosemi --channels 64`. Close ActiView first; only one program can
     use the amplifier. The speed mode, sampling rate and channel layout are read from
     the amplifier, and CMS-out-of-range and low battery are reported. The driver is
     found in `C:\Program Files\BioSemi\Developers_kit\C-code`; otherwise use
     `--lib <path>` or set `BIOSEMI_DRIVER`.
   - **ActiView TCP server**: ActiView keeps the amplifier (so you still see electrode
     offsets) and streams to the bridge. Enable the TCP server in ActiView, then:
     `bciflow bridge actiview --tcp-channels 73 --sfreq 2048`, using the channel count
     and rate shown in ActiView.
5. **On the Mac**: open a pipeline in the editor, set Data to *Amplifier / bridge
   (live LSL)*, and press ▶ Run.
6. **Triggers**: send event codes to the ActiveTwo trigger port from the stimulus
   computer. They are decoded with sample accuracy and published as `BioSemi-Triggers`.
   Software markers over LSL (e.g. from PsychoPy) also work: add an `LSLMarkerSource`
   node. They carry a few ms of network jitter, which is fine for motor imagery or alpha;
   use the trigger port for ERPs/P300.

Record whole sessions (all streams) with [LabRecorder](https://github.com/labstreaminglayer/App-LabRecorder)
to XDF; signalseeder reads XDF files for replay and training. Alternatively, add a
`Recorder` node to save a pipeline's signal and markers as a `.fif` file.

## Command line

All commands: `bciflow --help`; per command: `bciflow <command> --help`.

| Command | Purpose |
|---|---|
| `bciflow gui` | Start the graphical editor |
| `bciflow run P.yaml` | Run a pipeline on live LSL streams |
| `bciflow run P.yaml --offline REC` | Run a pipeline over a recording as fast as possible |
| `bciflow run P.yaml --replay REC` | Run a pipeline over a recording at real-time pace (no LSL) |
| `bciflow train P.yaml --data REC [REC…]` | Fit the Classifier nodes; prints CV accuracy |
| `bciflow replay REC [--loop]` | Publish a recording as LSL streams (like an amplifier) |
| `bciflow synth [--paradigm alpha-bursts] [--record FILE --duration S]` | Stream synthetic EEG, or write a synthetic recording |
| `bciflow bridge biosemi [--fake]` | BioSemi ActiveTwo (USB) → LSL; `--fake` simulates the amplifier |
| `bciflow bridge actiview --tcp-channels N --sfreq F` | ActiView TCP server → LSL |
| `bciflow discover [--check]` | List LSL streams on the network (with clock offsets and rates) |
| `bciflow lsl-config --peers IP…` | Configure LSL for networks where discovery doesn't work |
| `bciflow nodes [--json]` | List all pipeline node types and their settings |

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

Paths in a pipeline are relative to the pipeline file. Available nodes (`bciflow nodes`
shows all settings):

| Category | Nodes |
|---|---|
| Sources | `LSLSource`, `LSLMarkerSource`, `FileSource` |
| Preprocessing | `PickChannels`, `RenameChannels` (BioSemi A1… → 10-20), `Rereference`, `BandpassFilter`, `NotchFilter`, `Decimate` |
| Epoching | `Epocher` (marker-locked), `SlidingWindow` (continuous) |
| Features | `BandPower` |
| Classification | `Classifier` (csp_lda, ts_lr, mdm, xdawn_ts_lr, lda, or your own), `ThresholdClassifier` |
| Scripting | `SignalScript`, `EpochScript` |
| Outputs | `LSLSignalOut`, `SpectrumOut`, `EyesFeedback`, `PredictionOut`, `Recorder`, `Logger` |

A trained model remembers the channels, sampling rate and epoch length it was trained on.
If a pipeline feeds it something different, it refuses to run and explains why, rather
than producing meaningless output.

## Adding your own Python / MNE code

- **`SignalScript`**: a Python file with `process(data, times, info, state)`, called on
  every chunk of continuous signal (µV, shape channels × samples). It must be causal,
  i.e. not use future samples.
- **`EpochScript`**: `process(epoch_data, meta, state)`, called per epoch. This is the
  natural place for MNE functions such as `ica.apply`, baseline correction or resampling.
  Return `None` to reject the epoch. Example: `scripts/mne_epoch_cleaning.py`.
- **Custom classifiers**: set `estimator: path/to/file.py:make_estimator`, a function
  returning any scikit-learn estimator.
- **Feedback programs** read predictions from LSL: `bciflow-predictions` (or the name set
  in `PredictionOut` / `EyesFeedback`), as JSON `{"label": ..., "proba": {...}}`.

## Troubleshooting

| Problem | What to do |
|---|---|
| "No LSL stream … found" | Is the bridge, replay or synthetic source running? `bciflow discover` lists what the Mac sees. Across machines, see step 3 of the BioSemi section. |
| Warning "A stream named 'BioSemi' is already on the network" | A real or simulated amplifier is still streaming. The pipeline may connect to that one; stop it, or rename a stream. |
| Editor: "Address already in use" | Another editor is running; use `bciflow gui --port 8766`. |
| "not trained yet" on a Classifier | Use **Train…** (or `bciflow train`) before running. |
| "model was trained on different input" | The channels, rate or epoch settings changed since training: retrain. |
| Eyes don't react at first | The ThresholdClassifier calibrates itself and starts once it has seen both low and high alpha. Begin with an eyes-open/eyes-closed block, or set a fixed threshold. |
| `bciflow: command not found` | Activate the environment: `source .venv/bin/activate` (Windows: `.venv\Scripts\activate`). |

## Development

```
signalseeder/
├── src/bciflow/
│   ├── bridge/          BioSemi USB + ActiView TCP → LSL (numpy + pylsl only)
│   ├── sources/         replay and synthetic EEG generators
│   ├── pipeline/        graph engine and all nodes (nodes/*.py)
│   ├── gui/             editor server + static web app (no build step)
│   ├── cli.py           the bciflow command
│   └── models.py        built-in classifiers
├── pipelines/           example pipelines (YAML)
├── scripts/             example user scripts for Signal/EpochScript
├── tests/               pytest suite
├── windows/             installer and bridge launcher for the Windows laptop
├── data/                your recordings (not in git)
└── models/              trained models (not in git)
```

- Tests: `pytest -q`.
- A new node is a class in `src/bciflow/pipeline/nodes/` that declares `inputs`,
  `outputs` and `params`. It appears automatically in the editor palette and in
  `bciflow nodes`.
- The editor is plain HTML/CSS/JavaScript in `src/bciflow/gui/static/`; reload the
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
