# Pitfalls: ComfyUI + RunPod + LTX-2 / FLUX / Wan

Symptom-indexed. Each entry: what you see → what it actually is → the fix.
Severity: 🔴 silent wrong results · 🟠 wasted hours · 🟡 friction.

Environment these were caught in: RunPod `runpod/comfyui` pod image,
ComfyUI core 0.33.x (and current master as of 2026-08), ComfyUI-LTXVideo,
network volumes, H100/L40S pods. Newer versions may change details —
test, don't assume.

---

## 🔴 1. Every render is pitch black, but every job reports "success"

**Symptom:** jobs complete normally, mp4s are valid and playable, ~20 KB
for 5 seconds, every frame solid black. No error in the log. Persists
across checkpoints (fp8/bf16), samplers, schedulers, with
and without image conditioning, across ComfyUI core versions.

**Cause (ours):** the text encoder loaded with **zero matched weight
tensors** (see #2) — a randomly-initialized encoder produces garbage
conditioning, and the model denoises to black. Nothing raises an error.

**Fix:** gate outputs on pixels, not status (`check-output.py` here).
Then isolate with single-variable probes: dtype → sampler → conditioning
→ core version → **text encoder**. The TE is the last suspect everyone
checks and the first one that should be.

## 🔴 2. A tokenizer-accepted text-encoder load is not a weight-matched load

**Symptom:** your repacked/merged text-encoder safetensors loads without
any error (tokenizer validates fine) — but produces black/garbage output.
Meanwhile the *folder* layout of the same model fails loudly with
`ValueError: invalid tokenizer`.

**Cause:** ComfyUI's loaders match state-dict tensors by name and
**silently ignore every unmatched tensor**. A Gemma repack that keeps
the HuggingFace repo's prefixes (`language_model.model.embed_tokens.weight`,
`vision_tower.vision_model.*`) matches nothing; the loader expects
stripped names (`model.embed_tokens.weight`, `vision_model.*`). Zero
matches → random weights → no warning.

**Fix:** mirror the tensor-name census of a known-working file of the
same class before trusting any repack (read both safetensors headers and
diff the name sets — 20 lines of stdlib). And note the loader wants the
sentencepiece tokenizer embedded as a `spiece_model` uint8 tensor inside
the file; the separate `tokenizer.model` file is not read.

## 🔴 3. UI→API workflow conversion misfills widgets (the batch_size=121 bug)

**Symptom:** a converted workflow OOMs at the sampler requesting
absurd memory, or numeric widgets carry obviously-wrong values (a frame
count in `batch_size`, an fps in `bit_depth`, a pixel size in a combo
field).

**Cause:** in ComfyUI UI-graph JSON, an input that is **linked** but
still has a widget keeps its stale value in `widgets_values`. A
converter that walks the input spec and doesn't consume that stale slot
shifts every subsequent widget by one position. Ours put 121 (frames)
into `batch_size` — the graph was requesting 121 videos per batch, which
presented as VRAM exhaustion and was repeatedly misdiagnosed.

**Fix:** when converting, consume (and discard) the widget slot of any
linked input that has one. Lint every conversion: `batch_size > 8` is
almost always a misfill. `scripts/ui2api.py` here implements both.

## 🔴 4. Negative prompts do nothing at CFG 1

**Symptom:** distilled/turbo pipelines (LTX-2.3 distilled, 8-step, CFG 1)
ignore everything in the negative prompt — anachronisms, watermarks,
letterboxes sail through.

**Cause:** not a bug. At guidance scale 1 the negative branch never
mixes into the prediction. This is inherent to CFG-distilled models.

**Fix:** on distilled tiers, control content with the conditioning
image (image-to-video) and the positive prompt. Save negative-prompt
effort for CFG ≥ 3 pipelines, where it actually bites.

## 🔴 5. Bundled models shadow your library (and one of them may be corrupt)

**Symptom:** a model that verifies perfectly on disk fails to load
("file not fully covered", truncated-buffer errors) — or a *different*
file loads than the one you provisioned.

**Cause:** ComfyUI resolves filenames against its **own** `models/`
directory before `extra_model_paths.yaml` entries. The RunPod ComfyUI
image auto-populates a fresh network volume with bundled models, and an
interrupted download into that directory strands corrupt partials there
(ours: an **11.5 GB partial of a 29.5 GB fp8 checkpoint**) that
permanently shadow the correct library copy by name. An image-version bump can re-sync the
bundle and resurrect files you deleted.

**Fix:** sweep for name collisions between the bundled models directory
and your library; delete bundled files whose size mismatches the
authoritative source. Re-check after any image update.

## 🔴 6. Large safetensors truncate on load with dynamic-VRAM extensions

**Symptom:** deterministic load failure for >16 GB checkpoints —
"buffer length … must be a multiple of element size" with the same byte
count every retry — while hashes verify and `safe_open` works.

**Cause:** the image's dynamic-VRAM loader path rebases an mmap view
but slices with absolute offsets, truncating the last tensor.
Size-dependent, so smaller models hide it.

**Fix:** launch ComfyUI with `--disable-dynamic-vram`.

---

## 🟠 7. The distilled sigma schedule crossfades on the dev checkpoint

Running an 8-step distillation schedule (ManualSigmas) against a
**dev/full** checkpoint at CFG 1 produces a slow crossfade instead of
motion. Dev checkpoints want a real scheduler (LTX-2.5: 30-step
LTXVScheduler, terminal 0.003) and real CFG (5.0). Distilled schedules
belong to distilled checkpoints (or dev + the official distillation
LoRA).

## 🟠 8. Frame count and dimensions have hard constraints that fail late

LTX-2: frames = (multiple of 8) + 1 (121, 193, 289, 481…); width and
height = multiples of 32. Violations error only after the multi-minute
model load. Validate before submitting.

## 🟠 9. Seed-image aspect ratio paints itself into the video

Conditioning a 16:9 latent with a 4:3 image letterboxes every frame —
the model treats the padding as content. Crop the conditioning image to
the render's aspect first.

## 🟠 10. `pkill` kills your own SSH session

`pkill -f "python main.py"` matches the remote shell running your own
ssh command (the pattern text is in its command line). Use a
self-escaping pattern (`pkill -f 'main[.]py'`) and never put the kill
and the relaunch in the same command.

## 🟠 11. ComfyUI-LTXVideo imports a `pad` that PyPI kornia doesn't have

`pyramid_blending.py` imports `pad` from
`kornia.geometry.transform.pyramid`, which exists only in kornia
git-main. The whole node pack fails to import ("cannot import name
'pad'"), taking every LTXV node with it. The shim here
(`patch-ltxvideo-pad-shim.py`) replaces it with the behavior-identical
`torch.nn.functional.pad`. **Any `git reset` of the package wipes the
shim — re-apply after every package update.** Still true on master as
of 2026-08.

## 🟠 12. Custom-node managers can't install packages on some images

The Manager CLI reports "Neither pip nor uv are available" even with a
working venv pip on PATH. Manual `git clone` into `custom_nodes/` +
`venv/bin/pip install -r requirements.txt` works fine.

## 🟠 13. RunPod GraphQL deploys: three silent traps

- The `env` array on `podFindAndDeployOnDemand` is **silently ignored**
  — inject secrets post-boot over SSH, or set env through the
  dashboard/template flow, which does apply.
- Only `networkVolumeId` attaches a network volume. `volumeKey` is
  accepted and does nothing; `volumeInGb` mints a NEW volume. Verify
  with `df -h /workspace` — a network mount, not `overlay`.
- Requests without a browser-style User-Agent get intermittent
  Cloudflare 403 ("error code: 1010").

## 🟠 14. GPU type ids are strings that lie

"NVIDIA H100 SXM" is not a valid id (INVALID_INPUT); "NVIDIA H100 80GB
HBM3" and "NVIDIA H100 NVL" are. Supply constraints are normal and can
last hours; your network volume pins you to its datacenter, so
fallbacks must exist *in that DC*. Queueing image jobs behind video jobs
on one big GPU is a fine fallback — ComfyUI's queue is sequential.

## 🟠 15. The image's default ComfyUI launch is not your launch

Every pod boot starts ComfyUI with the image's own flags (and its own
output directory). If you need `--disable-dynamic-vram`, custom output
dirs, or temp dirs: kill and relaunch on every boot, or persist flags
the image's launcher reads.

---

## 🟡 16. SSH ports change on every pod start

Always re-read host:port after boot/resume; the runtime ports populate
up to a minute after the pod reports RUNNING.

## 🟡 17. CRLF from Windows kills scp'd shell scripts

"`set: pipefail: invalid option name`" = carriage returns. Strip after
any copy from a Windows working tree: `sed -i 's/\r$//' *.sh`, or
transfer through git.

## 🟡 18. RunPod's S3-compatible volume API has four quirks

(1) Cloudflare 403s non-browser User-Agents. (2) `ListObjectsV2` XML
carries **no namespace** (AWS proper namespaces it) — a namespaced
parser silently sees zero results. (3) DELETE of a multi-GB object
returns **HTTP 524** (Cloudflare timeout) while the origin completes
the delete anyway — treat 524 as "verify by re-listing". (4) Listing is
slow; prefix-scope everything. `runpod-volume-s3.py` here handles all
four with zero dependencies.

## 🟡 19. Pod /tmp is not storage

Spot reclaim can EXIT your pod any time. Job JSONs, staged inputs, and
anything you'll need again belong on the network volume or in git.

## 🟡 20. Model-swap thrash

Network-volume model loads are minutes, not seconds. Alternating model
families in one queue reloads tens of GB per swap. Sort the queue by
model; pay each load once.
