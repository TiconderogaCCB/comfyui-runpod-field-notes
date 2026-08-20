# Seven successful renders, all black: field notes from debugging LTX-2 on RunPod

*(Draft announcement post — for r/comfyui, r/StableDiffusion, or the
RunPod Discord. Personal account, links to the repo.)*

Last week I had seven consecutive LTX-2.3 video jobs return
`"status": "success"` — valid, playable mp4s, correct frame counts,
correct duration. Every single frame was pitch black. No error in any
log, at any layer.

The debugging ladder that followed ruled out, one variable at a time:
fp8 vs bf16 checkpoints, dev-with-LoRA vs distilled, multiple
sampler/scheduler combos, image conditioning vs pure T2V, and two
ComfyUI core versions. The actual cause: a repacked Gemma text encoder
whose tensor names kept the HuggingFace repo prefixes
(`language_model.model.*`) where the loader wanted stripped names
(`model.*`). ComfyUI's loader validates the tokenizer, then **silently
ignores every unmatched weight tensor** — so the model ran with a
randomly-initialized text encoder and dutifully denoised garbage
conditioning into black frames. Zero warnings, seven "successes."

Along the way we also hit (and root-caused): a UI→API workflow
converter bug that put the frame count into `batch_size` (the graph was
requesting 121 videos per batch — presents as OOM, was misdiagnosed
twice); a corrupt 11.5GB partial checkpoint stranded in the image's bundled
models directory by an interrupted download, silently shadowing our
verified copy (ComfyUI resolves its own models dir before
`extra_model_paths`); the fact that **negative
prompts do literally nothing at CFG 1** on distilled checkpoints; and
four undocumented quirks of RunPod's S3-compatible volume API.

I've written all of it up — 20 symptom-indexed pitfalls, the recipes
that turned out to be true but undocumented (frame math, first-last
interpolation with LTXVAddGuide, frame-lock chaining, seed-aspect
letterboxing), and four small dependency-free scripts, including the
content gate that makes "success with black frames" hard to miss
again:

**[LINK TO REPO]**

Context: this all came out of building the generation pipeline for a
family-history documentary short set in 1860s Texas, which is why every
example prompt involves wagons and limestone. Numbers were measured on
H100 80GB pods, August 2026, ComfyUI 0.33.x. Corrections welcome —
especially if newer cores change any of this.
