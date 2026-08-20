---
name: comfyui-runpod-ops
description: Operational knowledge for running ComfyUI video/image generation (LTX-2.x, FLUX, Wan) on RunPod GPU pods with network volumes. Auto-invoke when deploying or debugging ComfyUI on RunPod, when renders come out black or "successful" jobs produce garbage, when converting UI workflows to API format, when managing a RunPod network volume, or when working with LTX-2.3/2.5 distilled vs dev checkpoints, frame counts, CFG/negative-prompt behavior, or first-last frame interpolation.
---

# ComfyUI on RunPod: operational skill

You are operating ComfyUI on disposable RunPod GPU pods backed by a
network volume. Apply these rules; consult PITFALLS.md in this folder
for the symptom-indexed catalog.

## Architecture rules

1. The network volume holds models and working files; pods are
   disposable; scripts/manifests live in git; finished outputs get
   pulled off the volume. Pod `/tmp` is already lost — stage anything
   reusable on the volume.
2. Terminate-and-redeploy beats debugging any pod older than 5 minutes
   of misbehavior. Never leave a pod idle: GPU-hours are the only real
   cost.
3. Pin ComfyUI core + custom-node commits in a lock file; make your
   bootstrap reset drift back to the pin. Update deliberately: update →
   render-validate → re-freeze the lock.

## Non-negotiable checks

- **Gate pixels, not status.** A job can report success and write a
  valid, pitch-black mp4 with no error anywhere. Run a content check
  (frame-extraction + size threshold; see scripts/check-output.py) on
  every new workflow before trusting it.
- After any UI→API workflow conversion, **lint the widgets**:
  `batch_size` > 8 is almost always a misfilled frame count (linked
  inputs leave stale widget values that shift later fields).
- Launch ComfyUI with `--disable-dynamic-vram` on RunPod's image
  (>16GB checkpoints truncate on load otherwise), and relaunch with
  your own flags on every boot — the image's default launch is not
  yours.
- Sweep for name collisions between the image's bundled models
  directory and your library: bundled files (including corrupt
  partials) shadow `extra_model_paths` copies.
- `pkill -f 'main[.]py'` (self-escaping pattern), and never combine
  kill + relaunch in one SSH command.

## Model-behavior rules (LTX-2.x)

- Distilled checkpoints run 8 steps at CFG 1 → **negative prompts are
  inert**. Content control = the conditioning image + positive prompt.
  Dev checkpoints want a real scheduler (2.5: 30-step LTXVScheduler,
  terminal 0.003) + CFG 5, where negatives work.
- Frames must be (multiple of 8) + 1; dimensions multiples of 32.
- The conditioning image's aspect ratio must match the render's, or
  the mismatch is letterboxed into every frame. Crop first.
- Multi-clip sequences: seed clip N+1 from clip N's last frame
  (`ffmpeg -sseof -0.1 -i clipN.mp4 -frames:v 1 last.png`).
- First-last interpolation between two stills: `LTXVAddGuide` at
  `frame_idx: 0` and `frame_idx: -1` (the official LTX-2.5 FLF2V
  template's wiring).
- Text-encoder repacks: the loader silently ignores unmatched tensor
  names (zero-match load = randomly initialized encoder = black
  output, no error). Diff the tensor-name census against a
  known-working file before trusting any repack; the sentencepiece
  tokenizer must be embedded as a `spiece_model` uint8 tensor.

## Throughput rules

- Group queued jobs by model family; each family swap reloads
  30–50 GB from the network volume.
- Expect: ~2-3 min pod boot on a provisioned volume; 2-4 min first
  model load; then seconds-scale renders (5s distilled clip ≈ 25s on
  H100; 1MP image ≈ 19s on a mid GPU).

## RunPod platform rules

- GraphQL deploys: `env` array is silently ignored (inject secrets
  post-boot); only `networkVolumeId` attaches a volume (verify
  `df -h /workspace` shows a network mount, not `overlay`); send a
  browser User-Agent or Cloudflare 403s you.
- The volume's S3-compatible API works from anywhere with SigV4 (see
  scripts/runpod-volume-s3.py): browser UA required, listing XML has no
  namespace, large DELETEs 524-then-complete (verify by re-listing).
- SSH host:port changes every boot; supply constraints are normal and
  the volume pins you to one datacenter — have fallbacks in that DC.
