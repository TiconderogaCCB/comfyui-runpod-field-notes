# ComfyUI on RunPod: field notes

Hard-won operational knowledge for running **ComfyUI video and image
generation on RunPod GPU pods with network volumes** — LTX-2 family
(2.3 / 2.5), FLUX.1/FLUX.2, and Wan 2.2. Distilled from building the
generation pipeline for a Civil War documentary short, including one
long night in which **seven consecutive renders reported "success" and
every one of them was pitch black.**

If you are an AI agent operating this stack: start with
[`PITFALLS.md`](PITFALLS.md) (symptom-indexed), or install
[`SKILL.md`](SKILL.md) as a skill. If you are a human: read on.

## The architecture that survives disposable pods

Four layers, one job each:

| Layer | Job | Rule |
|---|---|---|
| **Network volume** | model library + working files | Operational storage, not an archive. Verify by size/hash; never re-download what verifies. |
| **GPU pods** | disposable compute | Terminate before you debug a pod for more than a few minutes. Never leave one idle. |
| **Git repo** | scripts, manifests, workflow JSONs | A setting that exists only as a pod-side edit does not exist. |
| **Off-cloud archive** | finished outputs | Pull outputs off the volume at every session close and delete them there. |

Pods can and do vanish mid-session (spot reclaim). The split above makes
that a non-event: everything durable lives on the volume or in git, and
pod `/tmp` is treated as already lost.

Two supporting practices that earn their keep:

- **A manifest drives all model downloads** (exact repo, filename, byte
  size, destination). The downloader is idempotent — present + correct
  size = skip; partials resume; atomic rename only on success. Boot
  never downloads; provisioning is an explicit operator step.
- **Pin the software.** Record the ComfyUI core commit, custom-node
  commits, and launch flags in a lock file, and have your bootstrap
  reset drift back to the pin. Vendor images update underneath you;
  custom-node repos churn weekly. Updates should be deliberate events:
  update → validate with renders → re-freeze the lock.

## The rule that saves you a night: gate pixels, not status

A ComfyUI job can complete, return `"status": "success"`, and write a
valid, playable, pitch-black mp4. No error is raised anywhere. Our root
cause was a text encoder whose weights silently failed to load (see
PITFALLS #1–2), but the lesson is general:

> **"Job succeeded" is a statement about the graph, not about the
> pixels.** Gate every new workflow with a content check before
> trusting it.

[`scripts/check-output.py`](scripts/check-output.py) is the ~90-line
dependency-free gate we use: it ffprobes the container, extracts
first/middle/last frames, and flags the output BLACK when all frames
compress below a size threshold (a solid-color PNG is ~1KB; any real
photographic frame is >30KB).

## Recipes that are true but not written down anywhere

**LTX-2.3 distilled ("FAST", 8 steps, CFG 1):**

- **Negative prompts do nothing at CFG 1.** Not "less" — nothing.
  Guidance scale 1 means the negative branch never mixes in. On the
  fast tier, content accuracy comes from the *seed image*; save your
  negative-prompt effort for CFG ≥ 3 pipelines.
- **Frame count must be (multiple of 8) + 1**: 121 = 5 s, 193 = 8 s,
  289 = 12 s at 24 fps. Dimensions must be multiples of 32. Wrong
  values fail late, after the model loads.
- **The seed image's aspect ratio must match the render's**, or the
  model paints the mismatch into every frame as letterbox bars. Crop
  first: `ffmpeg -i in.png -vf 'crop=in_w:in_w*9/16' out.png`.
- **Frame-lock chains** (multi-clip sequences): seed clip N+1 with clip
  N's last frame — `ffmpeg -sseof -0.1 -i clipN.mp4 -frames:v 1 -update 1 last.png`
  — instead of rolling fresh text-to-video per clip.
- **First-last interpolation** between two stills: `LTXVAddGuide` at
  `frame_idx: 0` and `frame_idx: -1` (this is exactly how the official
  LTX-2.5 FLF2V template is wired). Works beautifully for "bring these two period
  photographs to life and fill the gap."

**LTX-2.5 (dev):** 30-step `LTXVScheduler` (max_shift 2.05, base_shift
0.95, stretch true, terminal 0.003) + CFG 5. Running the distilled
8-step sigma schedule against the dev checkpoint produces a slow
crossfade instead of motion.

**Throughput:** group jobs by model family. The first job per model
pays a multi-minute load from the network volume; after that the model
is resident and a 5-second distilled clip renders in ~25 seconds on an
H100. Alternating model families reloads 30–50 GB per swap.

## The pitfall catalog

[`PITFALLS.md`](PITFALLS.md) — 20 entries, symptom-first, ranked by
severity (silent wrong results → wasted hours → friction). The top
tier, briefly:

1. "Success" with black frames — gate pixels, not status.
2. A tokenizer-accepted text-encoder load is **not** a weight-matched
   load: mismatched tensor names are silently ignored, and you get a
   randomly-initialized encoder. (`language_model.model.*` vs `model.*`
   prefixes, if you're repacking Gemma for LTX.)
3. UI→API workflow conversion can misfill widgets — a linked input's
   stale widget value shifts every later value one slot. Ours put the
   frame count into `batch_size`: 121 videos per batch, presenting as
   OOM.
4. RunPod's ComfyUI image auto-populates the volume with bundled
   models — and an interrupted download can leave a **corrupt multi-GB partial**
   in that bundled directory (ours: 11.5 GB of a 29.5 GB checkpoint)
   that permanently shadows your correct copy, because ComfyUI resolves
   its own models directory before `extra_model_paths`.

## Scripts

All dependency-free (Python stdlib), MIT, small enough to audit:

| Script | Does |
|---|---|
| [`check-output.py`](scripts/check-output.py) | content gate: PASS / BLACK / BAD per output file |
| [`runpod-volume-s3.py`](scripts/runpod-volume-s3.py) | inspect/manage a RunPod network volume from anywhere via its S3-compatible API — no pod, no boto3 (quirks handled: browser UA, namespace-less XML, 524-on-large-DELETE) |
| [`ui2api.py`](scripts/ui2api.py) | ComfyUI UI-graph → API-prompt conversion with a lint pass for the widget-misfill class of bugs |
| [`patch-ltxvideo-pad-shim.py`](scripts/patch-ltxvideo-pad-shim.py) | fixes ComfyUI-LTXVideo's `pad` import (kornia git-main only) with a behavior-identical `F.pad` shim |

## Provenance

Extracted from the production tooling behind the documentary short
mentioned above (the reason every example prompt is wagons and
limestone). The numbers
(load times, render cadences, VRAM figures) were measured on H100 80GB
pods in August 2026.

MIT license. Corrections and additions welcome — especially newer
ComfyUI cores changing any behavior documented here.
