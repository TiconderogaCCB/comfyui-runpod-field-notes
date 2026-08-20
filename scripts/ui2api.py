#!/usr/bin/env python3
"""ui2api.py <ui_workflow.json> <out.json> [--oi <object_info.json>] [--dump]
Convert a FLAT ComfyUI UI-graph JSON (nodes/links format, no subgraph
nodes) to API-prompt format, mirroring the frontend's graphToPrompt.
With --oi, widget filling follows the class input spec (required then
optional) positionally against widgets_values - the frontend's own rule -
which correctly fills nodes whose widgets are NOT serialized as input
entries (PrimitiveInt/Float/String, RandomNoise, LTXVEmptyLatentAudio,
etc.). Nodes with no outputs (annotations, SaveVideo terminals) are
skipped. --dump prints the converted node map.
"""
import json
import sys


def convert(wf, oi=None):
    nodes = {str(n["id"]): n for n in wf.get("nodes", [])}
    links = {l[0]: l for l in wf.get("links", [])}
    prompt = {}
    for nid, node in nodes.items():
        if not node.get("outputs"):
            continue  # annotation + terminal side-effect nodes
        if node.get("mode", 0) == 4:
            continue  # bypassed
        cls = node["type"]
        wv = list(node.get("widgets_values", []))
        spec_keys = []
        if oi and cls in oi:
            spec = oi[cls].get("input", {})
            spec_keys = list(spec.get("required", {}).keys()) + list(spec.get("optional", {}).keys())
        serialized = {i.get("name"): i for i in node.get("inputs", [])}
        inputs = {}
        wi = 0
        if oi and spec_keys:
            # spec-order walk: linked inputs take their links, everything
            # else consumes widgets_values positionally (graphToPrompt rule).
            # CRITICAL (PITFALLS #3): an input that is LINKED but
            # still has a widget slot (entry["widget"] set - e.g. `length`
            # fed by a PrimitiveInt) leaves its STALE value in
            # widgets_values. That slot must be CONSUMED and discarded, or
            # every later widget shifts one position (this is how 121
            # frames landed in batch_size and produced 121-video batches -
            # the OOM/"121-vs-97" family of failures).
            for key in spec_keys:
                entry = serialized.get(key)
                if entry is not None and entry.get("link") is not None and entry["link"] in links:
                    _, orig, slot, _, _, _ = links[entry["link"]]
                    inputs[key] = [str(orig), slot]
                    if entry.get("widget") is not None and wi < len(wv):
                        wi += 1  # discard the stale widget value
                elif entry is not None and entry.get("widget") is None and entry.get("link") is None:
                    pass  # unconnected pure socket - no widget slot
                elif wi < len(wv):
                    inputs[key] = wv[wi]
                    wi += 1
        else:
            for inp in node.get("inputs", []):
                link_id = inp.get("link")
                if link_id is not None and link_id in links:
                    _, orig, slot, _, _, _ = links[link_id]
                    inputs[inp["name"]] = [str(orig), slot]
                elif inp.get("widget") is not None:
                    if wi < len(wv):
                        inputs[inp["name"]] = wv[wi]
                    wi += 1
        prompt[nid] = {"class_type": cls, "inputs": inputs}
    return prompt


def lint(prompt, oi=None):
    """Pre-submit sanity pass. Returns a list of warning strings.
    Catches the widget-misfill failure family (PITFALLS #3): orphan
    widget values landing in the wrong key."""
    warns = []
    for nid, node in prompt.items():
        ins = node.get("inputs", {})
        bs = ins.get("batch_size")
        if isinstance(bs, (int, float)) and bs > 8:
            warns.append("%s %s: batch_size=%s (misfilled frame count? real batches are 1-8)"
                         % (nid, node["class_type"], bs))
        if oi and node["class_type"] in oi:
            spec = oi[node["class_type"]].get("input", {})
            allspec = dict(spec.get("required", {}))
            allspec.update(spec.get("optional", {}))
            for k, v in ins.items():
                s = allspec.get(k)
                if not s or not isinstance(s, list) or not s[0]:
                    continue
                if isinstance(s[0], list) and s[0] and isinstance(s[0][0], str) \
                        and isinstance(v, (int, float)):
                    warns.append("%s %s: %s=%r but spec is a string COMBO %s..."
                                 % (nid, node["class_type"], k, v, s[0][:2]))
    return warns


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        raise SystemExit(2)
    src, dst = sys.argv[1], sys.argv[2]
    oi = None
    if "--oi" in sys.argv:
        i = sys.argv.index("--oi")
        oi = json.load(open(sys.argv[i + 1], encoding="utf-8"))
    wf = json.load(open(src, encoding="utf-8"))
    prompt = convert(wf, oi)
    for w in lint(prompt, oi):
        print("LINT:", w)
    if "--dump" in sys.argv:
        for nid, n in sorted(prompt.items(), key=lambda kv: int(kv[0].split(":")[-1])):
            print(nid, n["class_type"], json.dumps(n["inputs"])[:200])
    json.dump(prompt, open(dst, "w", encoding="utf-8"), indent=2)
    print("wrote", dst, "-", len(prompt), "nodes")


if __name__ == "__main__":
    main()
