"""校验一个容器镜像标签能否被 docker 真正拉下来。

为什么需要这个脚本
------------------
之前只用 "GET manifest by tag → HTTP 200" 判断镜像可用，是**不够的**。
docker pull 的真实链路是：

    1. GET /v2/{pkg}/manifests/{tag}      → 多架构 index (manifest list)
    2. 按运行平台挑一个子 manifest
    3. GET /v2/{pkg}/manifests/{child}    → 单架构 manifest   ← 少了会 404
    4. GET /v2/{pkg}/blobs/{config}       → 镜像配置
    5. GET /v2/{pkg}/blobs/{layer}        → 各层内容

历史上出过一次事故：只把 index PUT 上去，子 manifest 从未上传，
第 1 步 200、第 3 步 404 —— 标签"看起来在"，实际 pull 必然失败。
本脚本把第 2~5 步全部走一遍。

用法
----
  GH_TOKEN=xxx python3 scripts/verify_pullable.py            # 验默认关键标签
  GH_TOKEN=xxx python3 scripts/verify_pullable.py tag1 tag2   # 验指定标签
"""

import json
import os
import sys
import urllib.error
import urllib.request

REGISTRY = os.environ.get("REGISTRY", "ghcr.io")
OWNER = os.environ.get("OWNER", "a454735639-lab")
PKG = os.environ.get("PKG", "tick-stock-panel-images")
TOKEN = os.environ.get("GH_TOKEN", "")
DEFAULT_TAGS = ["fused-v0.3.29", "fused-latest", "latest"]

ACCEPT_INDEX = ("application/vnd.oci.image.index.v1+json,"
                "application/vnd.docker.distribution.manifest.list.v2+json")
ACCEPT_MANIFEST = ("application/vnd.oci.image.manifest.v1+json,"
                   "application/vnd.docker.distribution.manifest.v2+json")


def http_get(url: str, accept: str, token: str = ""):
    """返回 (status, body_bytes)。不抛异常。"""
    req = urllib.request.Request(url)
    req.add_header("Accept", accept)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        return 0, str(e).encode()


def get_registry_token(repo: str, gh_token: str = "") -> str:
    """取 ghcr.io 的 pull token。给了 gh_token 就用它认证（CI 场景）。"""
    url = (f"https://{REGISTRY}/token"
           f"?scope=repository:{repo}:pull&service={REGISTRY}")
    req = urllib.request.Request(url)
    if gh_token:
        # ghcr.io 要求用户名非空
        import base64
        cred = base64.b64encode(f"probe:{gh_token}".encode()).decode()
        req.add_header("Authorization", f"Basic {cred}")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read()).get("token", "")
    except Exception:
        return ""


def verify_tag(repo: str, tag: str, token: str) -> tuple:
    """完整走一遍 docker 的解析链路。返回 (ok, 说明列表)。"""
    notes = []
    base = f"https://{REGISTRY}/v2/{repo}"

    # ── 步骤 1：取 index ────────────────────────────────────────────
    st, body = http_get(f"{base}/manifests/{tag}", ACCEPT_INDEX, token)
    if st != 200:
        return False, [f"步骤1 取 index: HTTP {st}（标签不可读）"]

    try:
        idx = json.loads(body)
    except Exception:
        return False, ["步骤1 解析 index 失败"]

    media = idx.get("mediaType", "")
    children = idx.get("manifests", [])
    notes.append(f"index mediaType={media} 子manifest数={len(children)}")

    # 单架构镜像（没有 index 层）直接验 config
    if not children:
        if "config" not in idx:
            return False, notes + ["步骤3 无 config 字段，manifest 不完整"]
        children = [None]

    # ── 步骤 2~5：每个子 manifest 都要能取到，且 config/layer 可达 ──
    ok_all = True
    for child in children:
        if child is None:
            plat, cdig = "single", None
        else:
            plat = (child.get("platform") or {})
            plat = f"{plat.get('os')}/{plat.get('architecture')}"
            cdig = child.get("digest")

        if cdig is None:
            notes.append(f"  [{plat}] 无 digest")
            ok_all = False
            continue

        # 步骤 3：取子 manifest
        st, body = http_get(f"{base}/manifests/{cdig}", ACCEPT_MANIFEST, token)
        if st != 200:
            notes.append(f"  [{plat}] 取子manifest失败 HTTP {st}  <== 致命")
            ok_all = False
            continue

        try:
            cm = json.loads(body)
        except Exception:
            notes.append(f"  [{plat}] 子manifest解析失败")
            ok_all = False
            continue

        cfg = cm.get("config") or {}
        layers = cm.get("layers") or []

        # 步骤 4：config blob 可达
        cst, _ = http_get(f"{base}/blobs/{cfg.get('digest')}",
                           "*/*", token)
        if cst != 200:
            notes.append(f"  [{plat}] config blob HTTP {cst}  <== 致命")
            ok_all = False
            continue

        # 步骤 5：层 blob 可达（只探 HEAD 语义，取全部层可能很大）
        bad = 0
        for lay in layers:
            lst, _ = http_get(f"{base}/blobs/{lay.get('digest')}", "*/*", token)
            if lst != 200:
                bad += 1
        if bad:
            notes.append(f"  [{plat}] {bad}/{len(layers)} 层 blob 不可达  <== 致命")
            ok_all = False
        else:
            notes.append(f"  [{plat}] 子manifest OK, config OK, "
                         f"{len(layers)} 层全部可达")

    return ok_all, notes


def main() -> int:
    tags = sys.argv[1:] or DEFAULT_TAGS
    repo = f"{OWNER}/{PKG}"

    # 优先用环境里的 GH_TOKEN（CI 的 GITHUB_TOKEN），
    # 没有就用匿名 token —— 匿名能通才叫"公开可拉"。
    token = get_registry_token(repo, TOKEN)
    mode = "带凭据"
    if not token:
        token = get_registry_token(repo)
        mode = "匿名"
    if not token:
        print(f"❌ 拿不到 {REGISTRY} 的 pull token（包可能不存在或非公开）")
        return 1

    print("=" * 68)
    print(f"包: {repo}    令牌模式: {mode}")
    print("=" * 68)

    all_ok = True
    for tag in tags:
        ok, notes = verify_tag(repo, tag, token)
        print(f"\n▶ {tag}")
        for n in notes:
            print(f"    {n}")
        if ok:
            print(f"  ✅ {tag} 可完整拉取（index→子manifest→config→层 全通）")
        else:
            print(f"  ❌ {tag} 不可拉取")
            all_ok = False

    print()
    if all_ok:
        print(f"✅ 全部 {len(tags)} 个标签通过完整拉取链路验证")
        return 0
    print(f"❌ 有标签未通过验证")
    return 1


if __name__ == "__main__":
    sys.exit(main())
