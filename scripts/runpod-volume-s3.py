#!/usr/bin/env python3
"""runpod-volume-s3.py - inspect and manage a RunPod network volume from
anywhere via its S3-compatible API. No pod, no GPU spend, no boto3/awscli:
pure Python stdlib (urllib + hmac SigV4).

Configuration (environment variables):
  RUNPOD_S3_ENDPOINT    e.g. https://s3api-us-xx-1.runpod.io  (match your DC)
  RUNPOD_S3_REGION      e.g. us-xx-1
  RUNPOD_S3_BUCKET      your network volume id (the bucket name IS the volume id)
  RUNPOD_S3_ACCESS_KEY  from RunPod console -> Settings -> S3 API keys
  RUNPOD_S3_SECRET_KEY

Commands:
  ls [prefix]              list keys (size, date, key)
  du [prefix] [--depth N]  aggregate usage per directory (default depth 2)
  get <key> <local>        download one object
  put <local> <key>        upload one small file (<100MB guard)
  rm <key> --yes           delete one object

Live-verified quirks of RunPod's S3 API this client handles (2026-08):
  - Cloudflare 403s non-browser User-Agents ("error code: 1010") -> send
    a browser UA.
  - ListObjectsV2 XML has NO namespace (AWS proper namespaces it) -> a
    namespace-qualified parser silently sees zero results; parse
    namespace-agnostically.
  - DELETE of a multi-GB object returns HTTP 524 (Cloudflare's ~100s
    timeout) while the origin completes the delete anyway -> treat 524
    as pending and confirm by re-listing.
  - Listing is slow (~1-3s per 1000 keys) -> prefix-scope everything.

MIT license.
"""
import hashlib
import hmac
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict

ENDPOINT = os.environ.get("RUNPOD_S3_ENDPOINT", "").rstrip("/")
REGION = os.environ.get("RUNPOD_S3_REGION", "")
BUCKET = os.environ.get("RUNPOD_S3_BUCKET", "")
ACCESS = os.environ.get("RUNPOD_S3_ACCESS_KEY", "")
SECRET = os.environ.get("RUNPOD_S3_SECRET_KEY", "")
EMPTY_SHA = hashlib.sha256(b"").hexdigest()
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def _require_env():
    missing = [n for n, v in [("RUNPOD_S3_ENDPOINT", ENDPOINT), ("RUNPOD_S3_REGION", REGION),
                              ("RUNPOD_S3_BUCKET", BUCKET), ("RUNPOD_S3_ACCESS_KEY", ACCESS),
                              ("RUNPOD_S3_SECRET_KEY", SECRET)] if not v]
    if missing:
        sys.exit("Missing environment variables: " + ", ".join(missing))


def _hsign(key, msg):
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def s3_request(method, key_path, params, body=None, timeout=300):
    host = ENDPOINT.split("://", 1)[1]
    payload_hash = hashlib.sha256(body or b"").hexdigest()
    t = time.gmtime()
    amz_date = time.strftime("%Y%m%dT%H%M%SZ", t)
    datestamp = time.strftime("%Y%m%d", t)
    path = "/" + BUCKET + (("/" + key_path) if key_path else "")
    q = "&".join("%s=%s" % (urllib.parse.quote(k, safe="-_.~"), urllib.parse.quote(str(v), safe="-_.~"))
                 for k, v in sorted(params.items()))
    canonical_headers = "host:%s\nx-amz-content-sha256:%s\nx-amz-date:%s\n" % (host, payload_hash, amz_date)
    signed = "host;x-amz-content-sha256;x-amz-date"
    creq = "%s\n%s\n%s\n%s\n%s\n%s" % (method, urllib.parse.quote(path, safe="/-_.~"), q,
                                       canonical_headers, signed, payload_hash)
    scope = "%s/%s/s3/aws4_request" % (datestamp, REGION)
    sts = "AWS4-HMAC-SHA256\n%s\n%s\n%s" % (amz_date, scope, hashlib.sha256(creq.encode()).hexdigest())
    k = _hsign(_hsign(_hsign(_hsign(("AWS4" + SECRET).encode(), datestamp), REGION), "s3"), "aws4_request")
    sig = hmac.new(k, sts.encode(), hashlib.sha256).hexdigest()
    url = ENDPOINT + path + (("?" + q) if q else "")
    req = urllib.request.Request(url, data=body, method=method, headers={
        "User-Agent": UA, "x-amz-date": amz_date, "x-amz-content-sha256": payload_hash,
        "Authorization": "AWS4-HMAC-SHA256 Credential=%s/%s, SignedHeaders=%s, Signature=%s"
                         % (ACCESS, scope, signed, sig)})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(), resp.status


def _ns_of(root):
    return root.tag.split("}")[0] + "}" if root.tag.startswith("{") else ""


def list_all(prefix="", max_pages=2000):
    token, pages = None, 0
    while True:
        params = {"list-type": "2", "max-keys": "1000"}
        if prefix:
            params["prefix"] = prefix
        if token:
            params["continuation-token"] = token
        data, _ = s3_request("GET", "", params)
        root = ET.fromstring(data)
        ns = _ns_of(root)
        pages += 1
        for c in root.findall(ns + "Contents"):
            yield (c.findtext(ns + "Key"), int(c.findtext(ns + "Size") or 0),
                   (c.findtext(ns + "LastModified") or "")[:16])
        if (root.findtext(ns + "IsTruncated") or "false") != "true":
            return
        token = root.findtext(ns + "NextContinuationToken")
        if pages >= max_pages:
            print("WARNING: page cap hit; listing truncated")
            return


def gib(n):
    return "%.2f GiB" % (n / (1 << 30))


def cmd_ls(prefix):
    n = tot = 0
    for key, size, lm in list_all(prefix):
        print("%13d  %s  %s" % (size, lm, key))
        n += 1
        tot += size
    print("-- %d objects, %s" % (n, gib(tot)))


def cmd_du(prefix, depth):
    agg = defaultdict(lambda: [0, 0])
    n = tot = 0
    for key, size, _ in list_all(prefix):
        parts = key.split("/")
        d = "/".join(parts[:depth]) if len(parts) > depth else key
        agg[d][0] += 1
        agg[d][1] += size
        n += 1
        tot += size
    for d in sorted(agg, key=lambda x: -agg[x][1]):
        print("%10s  %7d  %s" % (gib(agg[d][1]), agg[d][0], d))
    print("-- TOTAL: %d objects, %s" % (n, gib(tot)))


def cmd_get(key, local):
    data, _ = s3_request("GET", key, {}, timeout=1800)
    with open(local, "wb") as f:
        f.write(data)
    print("wrote %s (%d bytes)" % (local, len(data)))


def cmd_put(local, key):
    size = os.path.getsize(local)
    if size > 100 * 1024 * 1024:
        sys.exit("File >100MB - push big files from inside the datacenter, not over this API.")
    with open(local, "rb") as f:
        body = f.read()
    _, status = s3_request("PUT", key, {}, body=body, timeout=1800)
    print("PUT %s -> %s (HTTP %d, %d bytes)" % (local, key, status, size))


def cmd_rm(key, yes):
    found = [(k, s) for k, s, _ in list_all(key) if k == key]
    if not found:
        sys.exit("No such key: %s" % key)
    size = found[0][1]
    if not yes:
        sys.exit("Would delete %s (%d bytes). Re-run with --yes." % (key, size))
    try:
        _, status = s3_request("DELETE", key, {})
        print("DELETED %s (%d bytes, HTTP %d)" % (key, size, status))
        return
    except urllib.error.HTTPError as e:
        if e.code != 524:
            raise
        print("HTTP 524 (Cloudflare timeout) - the delete still runs; verifying...")
    for _ in range(9):
        time.sleep(20)
        if not any(k == key for k, _, _ in list_all(key)):
            print("DELETED %s (%d bytes, verified by listing)" % (key, size))
            return
    sys.exit("Key still present after 3 minutes - re-check with: ls %s" % key)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    if not args:
        print(__doc__)
        return
    _require_env()
    cmd = args[0]
    if cmd == "ls":
        cmd_ls(args[1] if len(args) > 1 else "")
    elif cmd == "du":
        depth = 2
        raw = sys.argv[1:]
        for i, f in enumerate(raw):
            if f.startswith("--depth="):
                depth = int(f.split("=")[1])
            elif f == "--depth" and i + 1 < len(raw):
                depth = int(raw[i + 1])
                if raw[i + 1] in args:
                    args.remove(raw[i + 1])
        cmd_du(args[1] if len(args) > 1 else "", depth)
    elif cmd == "get":
        cmd_get(args[1], args[2])
    elif cmd == "put":
        cmd_put(args[1], args[2])
    elif cmd == "rm":
        cmd_rm(args[1], "--yes" in flags)
    else:
        sys.exit("Unknown command %r - run with no args for help." % cmd)


if __name__ == "__main__":
    main()
