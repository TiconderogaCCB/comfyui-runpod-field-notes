#!/usr/bin/env python3
"""Patch ComfyUI-LTXVideo pyramid_blending.py: replace the kornia-git
`pad` import with a behavior-identical F.pad shim (PyPI kornia 0.8.3
has no pyramid.pad). See PITFALLS #11."""
import sys

path = sys.argv[1]
src = open(path, encoding="utf-8").read()

if "_pad_compat" in src:
    print("already patched:", path)
    raise SystemExit(0)
_before = src

src = src.replace("    is_powerof_two,\n    pad,\n)", "    is_powerof_two,\n)")

shim = '''

def _pad_compat(input, padding, mode="constant", value=0.0):
    # kornia.pad shim: the package imports pad from kornia git-main
    # (unreleased); PyPI kornia 0.8.3 has no pyramid.pad. kornia.pad is a
    # thin wrapper over F.pad with identical mode names, so this is
    # behavior-identical. Remove if kornia>=0.9 ships pad.
    return F.pad(input, padding, mode=mode, value=value)

'''

src = src.replace("from torch import Tensor\n", "from torch import Tensor" + shim, 1)
src = src.replace("pad(image, ", "_pad_compat(image, ")
src = src.replace("pad(images, ", "_pad_compat(images, ")

open(path, "w", encoding="utf-8").write(src)
print("patched", path)
