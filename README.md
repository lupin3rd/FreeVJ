<div align="center">

# FreeVJ

**A curated collection of ideas, patches and tools to make live visuals with free and open-source software.**

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Made with Pure Data](https://img.shields.io/badge/Made%20with-Pure%20Data-4b4b4b.svg)](https://puredata.info)
[![OSC](https://img.shields.io/badge/protocol-OSC-ff69b4.svg)](https://opensoundcontrol.stanford.edu/)
[![Free Software](https://img.shields.io/badge/Free%20Software-%E2%9C%8A-green.svg)](https://www.gnu.org/philosophy/free-sw.html)

</div>

---

## What is FreeVJ?

**FreeVJ is not a single program — it is an umbrella project.** It gathers notes, Pure Data patches, scripts, experiments and links to other projects that let you build a **VJ set using only free software**, from a laptop and a cheap MIDI controller up to a full multi-projector show.

The goal is simple: **no licensing fees, no locked file formats, no vendor lock-in.** Everything here runs on GNU/Linux (mostly) and speaks open protocols — **OSC**, **MIDI**, **JACK/PulseAudio** — so the pieces can be recombined freely.

The sibling projects listed below all grew out of this one and share the same spirit: small, hackable, protocol-first tools that talk to each other.

---

## What's in this repository

This repository is the **Pure Data side** of FreeVJ: the patches that glue together video sources, audio analysis, controllers and lights.

```
puredata/
├── FreeVJ.pd            # main patch — video + audio + MIDI/OSC control surface
├── FreeVJ-talk.pd       # talk / live-setup variant
├── FreeVJ-xponent.pd    # M-Audio Xponent controller mapping
├── pozzo.pd             # "well" — media pool / launcher
├── abs/                 # abstractions
│   ├── vimix*.pd        # Vimix control over OSC (osc, move, monitor)
│   ├── mediagrid/       # file picker grid (video / image / audio) with thumbnails
│   ├── pd-mkmr-gui/     # custom knobs, sliders, LEDs and Pd "next" themes
│   ├── launchpad.pd     # Novation Launchpad mapping
│   ├── bb_learn.pd      # MIDI learn helper
│   ├── qlc-osc.pd       # QLC+ lighting control over OSC
│   ├── SpectrumA.pd     # audio spectrum input
│   └── ...
└── scripts/             # helper scripts (thumbnails, blob → file …)
```

**Highlights**

- 🎛️ **Control surface** — MIDI controllers (UC-33, Xponent, Launchpad…) mapped to video parameters.
- 🎬 **Vimix integration** — send/receive OSC to drive the live video mixer, including a moving grid and a source monitor.
- 🖼️ **MediaGrid** — pick video, image or audio files from a visual grid, with generated thumbnails (`ffmpegthumbnailer`).
- 💡 **Lighting** — QLC+ over OSC, so sound, video and lights follow the same clock.
- 🎚️ **Audio reactivity** — spectrum analysis and tempo/beat detection feeding the visuals.

---

## My projects

Tools born from FreeVJ — each one is standalone and usable on its own.

| Project | Description | Stack |
| --- | --- | --- |
| **[VJmix](https://github.com/lupin3rd/VJmix)** | VJ controller for Vimix driven by viOSC: 8×8 step sequencer, thumbnail grid, mapper and MIDI/OSC I/O monitor, with Launchpad support. Fires OSC in sync with the music (VU/BPM analysis, MIDI clock, band peaks). | Python |
| **[viosc](https://github.com/lupin3rd/viosc)** | OSC router, state mirror and monitoring layer for Vimix, running on the same machine. Exposes a small OSC API to an external UI/controller, with a source cache, FFmpeg thumbnails and on-demand property monitoring. | Python |
| **[spectrum-analyzer](https://github.com/lupin3rd/spectrum-analyzer)** | Real-time audio spectrum analyzer for Linux with OSC output. A modernized fork of *luz*: GTK3 + OpenGL, low-latency PulseAudio/ALSA capture, log-spaced analysis over 20 Hz–20 kHz. | C++ |
| **[vimix-chataigne-module](https://github.com/lupin3rd/vimix-chataigne-module)** | A [Chataigne](https://benjamin.kuperberg.fr/chataigne) module to interact with Vimix, installable from the community modules list. | JavaScript |
| **[vlc-vj](https://github.com/lupin3rd/vlc-vj)** | VLC 3.x Lua extension: mark points of interest in a video you are watching, then cut them into clips with `ffmpeg`. Markers are timestamped notes stored as JSON. | Lua |

---

## Other software I work with

FreeVJ would not exist without these projects. Go and support them.

| Software | What it is | Link |
| --- | --- | --- |
| **Vimix** | The live video mixer at the heart of the setup — GLSL-based, real-time, multi-source video mixing with a full OSC API. | [github.com/brunoherbelin/vimix](https://github.com/brunoherbelin/vimix) |
| **Pure Data (Pd)** | The visual programming language used for all the patches in this repo: audio, MIDI, OSC and video (Gem) in one canvas. | [puredata.info](https://puredata.info/) |
| **PlugData** | A modern Pure Data distribution as a plugin (VST3/CLAP/LV2) and standalone app, with a much nicer editor — great to run these patches inside a DAW. | [plugdata.org](https://plugdata.org/) |
| **Chataigne** | Modular tool for show control and prototyping: maps MIDI, OSC, DMX, serial, gamepads and more to each other, with a timeline. | [benjamin.kuperberg.fr/chataigne](https://benjamin.kuperberg.fr/chataigne) |

---

## Getting started

1. **Install the basics** — [Pure Data](https://puredata.info/) (Vanilla or [PlugData](https://plugdata.org/)), [Vimix](https://github.com/brunoherbelin/vimix), and `ffmpeg` + `ffmpegthumbnailer` for media thumbnails.
2. **Clone this repository**

   ```bash
   git clone https://github.com/lupin3rd/FreeVJ.git
   cd FreeVJ
   ```

3. **Open a patch** — start with `puredata/FreeVJ.pd` in Pure Data. The `declare -path abs;` lines at the top load the abstractions automatically.
4. **Wire up your gear** — set the MIDI input device in the patch, then point OSC at Vimix (`/vimix/...`, default ports **7000** in / **7001** out).
5. **Play.**

> 💡 Missing externals? Some abstractions need `mrpeach`/`osc`, `pd-lua` and Gem. Install them through **Deken** (Pd's built-in package manager) or your distro packages.

---

## Contributing

Ideas, patches, and corrections are welcome — open an issue or a pull request. If a patch works for you, a short note describing your setup is just as valuable as code.

---

## License

Released under the **GNU General Public License v3.0** — see [LICENSE](LICENSE).

---

<div align="center">

### ☕ Support the projects

If FreeVJ or any of these tools saved your set, you can buy me a coffee.
Small donations keep the patches coming.

[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-lupin3rd-FFDD00?style=for-the-badge&logo=buymeacoffee&logoColor=black)](https://buymeacoffee.com/lupin3rd)

**[buymeacoffee.com/lupin3rd](https://buymeacoffee.com/lupin3rd)**

<sub>Made with ❤️ and free software.</sub>

</div>
