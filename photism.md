# Photism — Design Document

## 1. Identity

**Photism** is a tool in The Atelier suite of generative image tools within the A-U.SUPPLY admin panel.

The name is a reference to **synesthesia** — specifically the phenomenon of perceiving sounds as colors and visual forms. It sits alongside Punctum and Spectralize as part of The Atelier's naming convention.

**Subtitle:** `audio → image translation`

---

## 2. Purpose and Context

Photism is a **spectral audio editor**: it transcribes audio as a spectrogram, lets you visually edit that spectrogram with drawing tools and effects, and then reconverts the edited spectrogram back into audio.

It is designed for **A-U.SUPPLY admins** as a workflow tool for augmenting sound clips that will be used as elements in larger projects. It is not a final-product tool — its output feeds downstream into compositions and releases.

Key capabilities:
- Import preexisting images into the spectrogram (Stamp)
- Edit the spectrogram freehand with drawing implements (Paint)
- Preview edits as audio before exporting
- Export both the visual spectrogram (image/video) and the re-synthesized audio

---

## 3. Technical Pipeline

```
Audio file (MP3/WAV/OGG/FLAC/M4A)
    ↓
Decode to PCM (mono, via OfflineAudioContext)
    ↓
STFT — Short-Time Fourier Transform
  · Hann window
  · Configurable FFT size: 512 / 1024 / 2048 / 4096
  · Hop = FFT size / 4 (75% overlap)
  · Produces: magnitude frames + phase frames
    ↓
Log-magnitude normalization
  · v = log1p(raw/maxMag × 100) / log(101)
  · Maps [0, maxMag] → [0, 1] on a logarithmic curve
  · maxMag preserved for inversion
  · Mel-scale y-axis mapping for perceptual display accuracy
    ↓
Render as spectrogram image (Canvas 2D)
    ↓
[User edits magnitude data]
    ↓
ISTFT — Inverse STFT with Overlap-Add (OLA)
  · Inverts log-normalization: raw = maxMag × (101^v − 1) / 100
  · Reconstructs complex spectrum from edited magnitudes + original phases
  · Hann-windowed overlap-add
  · RMS-normalized against original audio to preserve audible balance
    ↓
Synthesized WAV audio
```

### FFT Implementation

The FFT is implemented from scratch in pure JavaScript (Cooley-Tukey radix-2, in-place). This was a deliberate choice to avoid external dependencies and is also a constraint of the admin panel environment. A WASM-based FFT (or WebAssembly port of FFTW) is a noted future consideration for performance at high FFT sizes on long audio files.

### Mel-Scale Display

The spectrogram y-axis maps linear frequency bins to a mel-scaled display for perceptual accuracy (equal visual spacing ≈ equal perceived pitch difference). The underlying magnitude data is stored in linear frequency space. Whether brush edits should operate in mel-space vs. linear-space is an open design question — currently edits operate on raw linear bins.

### Phase Handling

Original phase values are stored per-bin per-frame at analysis time and reused during ISTFT synthesis. Edits modify only magnitude data; phase is not editable directly. This means drawn/painted regions use whatever phase existed in the original recording at that time/frequency position.

---

## 4. Visualization Modes

All modes are **cosmetic only** — they affect how the spectrogram is rendered to the canvas (and thus what is exported as image/video), but all edits always operate on the same underlying linear magnitude data regardless of which mode is active.

| Mode | Description | Parameters |
|---|---|---|
| **Waterfall** | Standard left→right spectrogram. Time on x-axis, frequency (mel-scaled) on y-axis, magnitude as color. | Max Freq |
| **Radial** | Same spectrogram data mapped onto a circle, time progressing around the circumference. | Rotation |
| **Glitch** | Waterfall with pixel-level warp displacement and noise injection. | Warp, Noise |
| **Hybrid** | Split canvas: RMS waveform on top, spectrogram on bottom, with a seam interference effect at the boundary. | Split ratio |

---

## 5. Color Palettes

Four palettes, applied via a 256-entry lookup table (LUT):

| Palette | Character |
|---|---|
| **Hot** | Black → deep red → orange → yellow → white. Classic thermal. |
| **Viridis** | Purple → blue → teal → green → yellow. Perceptually uniform. |
| **Mono** | Black → white. Grayscale. |
| **Acid** | High-contrast neon. |

---

## 6. Paint Tools

Entering Paint mode overlays an editing layer (`editedFrames`) on top of the original spectrogram data. Edits are non-destructive relative to the original analysis — `editedFrames` is a separate copy.

### Brush Tools

| Tool | Behavior |
|---|---|
| **Draw** | Writes energy to selected bins. Currently writes absolute maximum (1.0). A per-brush intensity/opacity slider is planned so 100% = max energy and lower values blend with existing content. |
| **Erase** | Sets bins to zero (silence). |
| **Gain** | Multiplies existing bin values by a configurable factor (default 1.5×). |
| **Blur** | Applies a Gaussian kernel across frequency bins, softening detail. |
| **Smear** | Copies content from a nearby position — a spectral smudge tool. |

### Brush Parameters

- **Size** — radius in pixels (2–30px)
- **Gain amount** — multiplier for the Gain tool (shown only when Gain is active)

### Undo

Edits are tracked in a per-stroke undo stack (max 20 entries). Each stroke records only the changed frames as a patch diff for memory efficiency. Full-swap entries are used for operations that replace all of `editedFrames` (FX apply, stamp, reset).

### Reset Edits

Discards all edits and reverts to the original spectrogram data.

---

## 7. Stamp Tool

Stamp lets you imprint an image's luminance values onto the spectrogram as magnitude data — bright pixels become high spectral energy at the corresponding time/frequency position.

### Controls

| Control | Description |
|---|---|
| Image source | Upload a file or pick from the media library |
| Time range | Where in the audio the stamp is applied (dual-range slider, 0–100%) |
| Opacity | Scales stamp contribution (0–100%) |
| Blend mode | Add, Screen, Multiply, Replace |

### Current Behavior

The stamp is applied across the full frequency range and the selected time range. The image is stretched to fill the target region. Luminance is computed as `0.299R + 0.587G + 0.114B` (standard luma).

---

## 8. FX Panel

Global spectral effects applied to `editedFrames`. All effects have a preview step before being committed — applying creates a new undo entry.

| Effect | Description | Strength param |
|---|---|---|
| **Blur** | Gaussian blur in both time and frequency axes | Yes |
| **Sharpen** | Unsharp-mask style contrast boost | Yes |
| **Pitch ▲** | Shifts all frequency content upward by remapping bins | Yes |
| **Pitch ▼** | Shifts all frequency content downward | Yes |
| **Reverse** | Flips the time axis of the spectrogram | No |
| **Pixelate** | Block-averages the spectrogram into coarse chunks | Yes |

Currently all effects apply to the **entire spectrogram**. Selection-based application (apply only to a time/frequency region) is a planned feature.

---

## 9. Export

| Action | Output | Destination |
|---|---|---|
| **Save Image** | Current canvas as PNG | The Stacks (media library) |
| **Record WebM** | Real-time video capture of spectrogram animation synced to audio | Browser download |
| **Save Frames** | Each animation frame as PNG, bundled in a ZIP | Browser download |
| **Send to Stacks** | Synthesized WAV from edited (or original) spectrogram | The Stacks (media library) |
| **Download WAV** | Same synthesis as above | Browser download |

WebM recording is intended as a **preview/scratch format**, not a finished artifact.

Audio synthesis uses `editedFrames` if edits exist, otherwise falls back to the original spectrogram frames. The trimmed time window is respected for both playback and export.

---

## 10. Playback

| Control | Description |
|---|---|
| Play/Stop | Plays the current audio; shows synthesis progress when in Edit mode |
| Loop | Toggles looped playback within the trim window |
| Orig / Edit toggle | Switches between the original decoded audio and a re-synthesized version from `editedFrames`. Only visible after edits have been made. The Edit pill dims when the cached synthesis is stale. |
| Playhead | Vertical line on the spectrogram canvas tracking playback position |
| Waveform cursor | Position indicator on the mini waveform strip |
| Time display | Elapsed time readout |

**Edit mode synthesis** uses the same ISTFT pipeline as audio export. The synthesis is trimmed to the current view window and RMS-normalized against the original audio so unedited regions remain audible alongside painted regions. Synthesis result is cached and only recomputed when edits are made or the trim range changes.

---

## 11. Status

### Working

- Audio loading — all formats (MP3, WAV, OGG, FLAC, M4A), upload and from media library
- Spectrogram analysis and all four visualization modes
- All four color palettes
- Paint tools — draw, erase, gain, blur, smear
- Undo (20-step per-stroke patch stack)
- Stamp tool — upload or from library, blend modes, opacity, time range
- FX panel — all seven effects with preview
- Save Image → Stacks
- Save Frames (ZIP)
- Send to Stacks (audio synthesis + upload)
- Download WAV
- Original audio playback with progress indicator and loop
- Orig/Edit playback toggle with synthesis progress display

### Needs Attention / Known Issues

- **Edited audio playback** — recently fixed (2026-05-18) for two bugs: trim exclusion of painted regions and level imbalance (original audio being inaudible alongside drawn regions). Needs further testing across a variety of audio and edit types to confirm stable.
- **FFT size change mid-session** — changing the Detail selector after edits have been made is known to cause problems. The edit state (`editedFrames`), undo stack, and cached synthesis buffers are all tied to the original FFT size and may produce incorrect results or be silently invalidated after a size change. Needs investigation and a proper re-analysis + state-reset flow.
- **Trim sliders** — interaction between trim range, paint edits, and export needs testing; behavior at edge cases (trim range very narrow, trim after editing) may be unreliable.
- **Draw brush intensity** — currently draws at absolute maximum energy (1.0), which creates a large level disparity between drawn and undrawn regions. An opacity/intensity control is needed.

### Known Limitations

- Mono only — audio is downmixed to a single channel on load
- Phase is not editable; synthesis reuses original phases which can produce artifacts when magnitude is heavily modified in regions where original phase was near-zero
- Synthesis can be slow on long files at high FFT sizes (pure JS FFT)
- WebM recording requires real-time playback (no faster-than-real-time render)

---

## 12. Non-Goals

Photism is not:

- A **sequencer** — no timeline arrangement of multiple clips
- A **DAW (Digital Audio Workstation)** — no mixer, routing, MIDI input, plugin host, or multi-track editing
- A **real-time effects processor** — all processing is offline/batch (though a real-time mode is a long-term aspiration)
- A **pitch correction or transcription tool** — no note detection, alignment, or correction workflow
- A **final mastering tool** — output is intended as raw material for larger projects, not finished product

---

## 13. Goals

**Near-term:** A reliable, expressive spectral editor for augmenting audio clips. The primary workflow is: load a clip, visually reshape its spectral content using paint tools, stamp images, and effects, then export the transformed audio back to The Stacks for use in larger projects.

**Long-term north star:** A tool for making unique and bizarre audio clips — pushing audio into genuinely strange territory through visual/spectral manipulation. The ultimate aspiration is a **real-time spectral effects pedal**: live audio in, live spectrogram manipulation, live audio out.

---

## 14. Wishlist

### Paint Tools
- Draw tool opacity/intensity slider (100% = max energy, lower values blend with existing content)
- Clone brush — sample from one region and paint it elsewhere
- Sharpen brush — boost contrast between adjacent bins
- Harmonics brush — paint a fundamental and its overtone series auto-populates
- Noise fill — flood a region with broadband or band-limited noise
- Smooth brush — gentle neighbor-blending, softer than Blur
- Warp/liquify — drag frequency content up or down in pitch
- Frequency lock — snaps brush to nearest harmonic or musical note
- Pitch shift brush — lift content from one frequency band and re-deposit at a different pitch
- Envelope brush — draw an amplitude shape over time across a frequency band
- Invert brush — flip magnitude values in a region
- Symmetry brush — mirror strokes across the frequency axis
- Scatter — randomly distribute energy within a stroke region

### Stamp Tool
- Axis orientation control — map image onto time axis vs. frequency axis
- Frequency band targeting — stamp only into a specified frequency range (e.g. bass only, upper mids only)

### FX
- Selection-based FX — apply effects to a user-defined time/frequency region rather than the whole spectrogram
- Isolated section preview for FX — preview and adjust the selected region in isolation before applying
- Stretch/compress — time-stretch without pitch change
- Formant shift — move resonant peaks while preserving pitch
- Convolution — multiply spectrogram by another audio file's spectrum
- Phase randomize — scramble phase while keeping magnitudes (washy/diffuse textures)
- Comb filter — periodic notches across the frequency axis
- Chorus/detune — duplicate and slightly shift frequency content
- Fade in/out — ramp magnitude to/from zero over a time range
- Tangential: spectral gate, harmonic enhance, band isolate, spectral freeze, echo, wavefold, normalize

### Export
- Export selected region only (time/frequency selection → WAV/PNG)
- MIDI export — derive note events from detected pitches
- Session export — save raw spectrogram data (frames + phases + metadata) to a file for re-import and continued editing

### Visualization
- Additional display modes (open)

### Infrastructure
- WASM-based FFT for performance at high FFT sizes / long files
- Proper mid-session FFT size change — re-analyze and migrate or reset edit state cleanly
- Stereo support (currently downmixes to mono)
- Editable phase (advanced — enables more accurate resynthesis of heavily modified regions)
- Real-time processing mode (long-term: live audio in → spectrogram manipulation → live audio out)
