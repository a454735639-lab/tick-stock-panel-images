"""删除公开包里"只有 index、实际拉不下来"的坏标签。

背景
----
多架构镜像在 registry 里有两层：index(manifest list) + 各自的子 manifest。
迁移时只把 index 推了上去，子 manifest 从未上传，
导致 `docker pull` 在"取子 manifest"这步 404 ——
标签"看起来在"，实际拉不下来。

这些标签留着只会误导人（以为能回滚，实际拉不动），
而对应的源码 commit 全部还在私有仓库里，需要时随时可以重建。

本脚本：
  1. 列出全部标签
  2. 对每个标签检查子 manifest 是否可达
  3. 删掉子 manifest 不可达的标签（保留可用的）
  4. 复查

用法
----
  GH_TOKEN=xxx python3 scripts/prune_broken_tags.py            # 干跑（只报告）
  GH_TOKEN=xxx python3 scripts/prune_broken_tags.py --apply    # 真删
"""

import json
import os
import subprocess
import sys

OWNER = os.environ.get("OWNER", "a454735639-lab")
PKG = os.environ.get("PKG", "tick-stock-panel-images")
REGISTRY = os.environ.get("REGISTRY", "ghcr.io")
GH_TOKEN = os.environ.get("GH_TOKEN", "")
APPLY = "--apply" in sys.argv

FULL = f"{OWNER}/{PKG}"
ACCEPT_INDEX = ("application/vnd.oci.image.index.v1+json,"
                "application/vnd.docker.distribution.manifest.list.v2+json")
ACCEPT_MAN = ("application/vnd.oci.image.manifest.v1+json,"
              "application/vnd.docker.distribution.manifest.v2+json")


def sh(cmd: list) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.stdout.strip()


def registry_token(scope: str, password: str = "") -> str:
    cmd = ["curl", "-sf"]
    if password:
        cmd += ["-u", f"probe:{password}"]
    cmd += [f"https://{REGISTRY}/token?scope={scope}&service={REGISTRY}"]
    out = sh(cmd)
    try:
        return json.loads(out).get("token", "")
    except Exception:
        return ""


def get_json(url: str, tok: str, accept: str):
    """返回 (http_code, parsed_json)。"""
    out = sh(["curl", "-s", "-w", "\n%{http_code}",
              "-H", f"Authorization: Bearer {tok}",
              "-H", f"Accept: {accept}", url])
    body, _, code = out.rpartition("\n")
    try:
        return code, json.loads(body)
    except Exception:
        return code, None


def list_tags(tok: str):
    code, d = get_json(f"https://{REGISTRY}/v2/{FULL}/tags/list?n=1000",
                       tok, "*/*")
    if d is None:
        return []
    return d.get("tags") or []


def children_ok(tok: str, tag: str):
    """子 manifest 全部可达才算好标签。"""
    code, idx = get_json(
        f"https://{REGISTRY}/v2/{FULL}/manifests/{tag}", tok, ACCEPT_INDEX)
    if code != "200" or not isinstance(idx, dict):
        return False, f"index HTTP {code}"
    kids = idx.get("manifests", [])
    if not kids:
        return True, "单架构"
    bad = []
    for ch in kids:
        ccode, cm = get_json(
            f"https://{REGISTRY}/v2/{FULL}/manifests/{ch['digest']}",
            tok, ACCEPT_MAN)
        if ccode != "200" or not isinstance(cm, dict):
            plat = ch.get("platform") or {}
            bad.append(f"{plat.get('os')}/{plat.get('architecture')}({ccode})")
    if bad:
        return False, "子manifest缺失: " + ",".join(bad)
    return True, f"{len(kids)}个子manifest全通"


def delete_tag(tok: str, tag: str) -> int:
    """删标签：先在 registry 层解绑，再删对应 version。"""
    # 1) 拿该标签当前 index 的 digest（registry 层删除必须按 digest）
    code, idx = get_json(
        f"https://{REGISTRY}/v2/{FULL}/manifests/{tag}", tok, ACCEPT_INDEX)
    digests = []
    if isinstance(idx, dict) and idx.get("manifests"):
        digests = [ch["digest"] for ch in idx["manifests"]]
    # 2) registry 层 DELETE manifest（GHCR 对此通常返回 405，删不掉也无妨）
    for d in digests:
        sh(["curl", "-s", "-o", "/dev/null", "-X", "DELETE",
            "-H", f"Authorization: Bearer {tok}",
            f"https://{REGISTRY}/v2/{FULL}/manifests/{d}"])
    # 3) 删 GitHub 侧 version（按 metadata.container.tags 匹配）
    code, vers = get_json(
        f"https://api.github.com/users/{OWNER}/packages/container/{PKG}"
        f"/versions?per_page=100", GH_TOKEN, "application/vnd.github+json")
    if not isinstance(vers, list):
        return 0
    n = 0
    for v in vers:
        tags = ((v.get("metadata") or {}).get("container", {}) or {}).get("tags") or []
        if tag in tags:
            rc = sh(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                     "-X", "DELETE",
                     "-H", f"Authorization: Bearer {GH_TOKEN}",
                     "-H", "Accept: application/vnd.github+json",
                     f"https://api.github.com/users/{OWNER}/packages/container/"
                     f"{PKG}/versions/{v['id']}"])
            n += 1
            print(f"    删除 version {v['id']} (tags={tags}) -> HTTP {rc}")
    return n


def main() -> int:
    if not GH_TOKEN:
        print("GH_TOKEN 为空", file=sys.stderr)
        return 2

    tok = registry_token(f"repository:{FULL}:pull,push", GH_TOKEN)
    if not tok:
        print("取 registry token 失败", file=sys.stderr)
        return 2

    tags = sorted(list_tags(tok))
    print(f"当前标签数: {len(tags)}")

    good, bad = [], []
    for t in tags:
        ok, why = children_ok(tok, t)
        print(f"  {'OK  ' if ok else 'BAD '} {t:52s} {why}")
        (good if ok else bad).append(t)

    print(f"\n可用: {len(good)} 个    坏标签: {len(bad)} 个")
    if not bad:
        print("没有坏标签，无需处理")
        return 0
    if not APPLY:
        print("\n干跑模式（加 --apply 才会真删）:")
        for t in bad:
            print(f"  将删除: {t}")
        return 0

    print("\n开始删除坏标签 ...")
    for t in bad:
        print(f"  删除 {t}")
        delete_tag(tok, t)

    print("\n--- 复查 ---")
    left = sorted(list_tags(tok))
    print(f"剩余标签: {len(left)}")
    for t in left:
        ok, why = children_ok(tok, t)
        print(f"  {'OK  ' if ok else 'BAD '} {t:52s} {why}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
