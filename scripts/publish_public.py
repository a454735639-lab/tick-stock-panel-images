#!/usr/bin/env python3
"""把备份的 manifest 发布到公开容器包，并做**完整拉取链路**验证。

⚠️ 重要限制（实测教训，动手前务必读完）
-----------------------------------------
这个脚本**只能发布多架构 index（manifest list）这一层**，
备份文件里不含子 manifest 的 body（只有 digest 引用），所以它**无法**
把一个多架构镜像还原成可拉取的状态。

真实事故：曾用本脚本把 27 个标签的 index 原样 PUT 到新包，
`GET manifest by tag` 全部返回 200，看着一切正常，
但 `docker pull` 必然失败 —— docker 的流程是
「取 index → 按平台挑子 manifest → 取子 manifest → 取 config → 取层」，
第 3 步 404 MANIFEST_UNKNOWN。子 manifest 从未上传过。

因此：
  - **发布镜像请用 rebuild-images.yml**（从私有源码重建，
    buildx 会把 index、子 manifest、config、各层 blob 一并推上去）。
  - 本脚本仅保留作为"标签/index 占位与清单核对"的辅助工具，
    且**绝不可**用它的 200 结果来判断镜像可拉。

其它实测结论（GHCR）：
  - 包的可见性在"首次创建"时继承创建它的仓库：
    私有仓库首推 = PRIVATE，公开仓库首推 = PUBLIC。
  - 已存在的包不会跟随仓库可见性翻转；REST / GraphQL 都没有改可见性的端点。
  - 包的推送权跟随"创建它的仓库"：别的仓库的令牌推不进去
    （write_package denied）。
  - 包名一旦被用过，即使删除，名字仍被占用（最长 30 天可 restore），
    期间别的仓库无法接管 —— 所以本次用新包名 tick-stock-panel-images。

字节序列化（digest 一致性）
---------------------------
manifest 的 digest 是**原始字节**的 sha256。用 `json.dumps(body)` 的默认
分隔符（", " / ": "）会改变字节，从而改变 digest —— 即使语义完全等价。
要保持 digest 不变必须用紧凑分隔符 `(",", ":")`。
（本次迁移中线上 digest 与备份 digest 全部不一致，就是这个原因。）

本脚本独立于 workflow 的 YAML，避开 block scalar 的缩进陷阱；
所有 HTTP 用 urllib 实现，不依赖 curl 的引号转义。

环境变量：
  OWNER       仓库拥有者，默认 a454735639-lab
  PKG         包名（不含 owner），默认 tick-stock-panel-images
  REGISTRY    镜像仓库，默认 ghcr.io
  GH_TOKEN    GitHub token（CI 里用 secrets.GITHUB_TOKEN）
  BACKUP_FILE manifest 备份路径，默认 <repo>/backup/manifests.json
  RESULT_FILE 结果输出路径，默认 <repo>/publish_result.json
"""
import base64
import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
# 脚本位于 <repo>/scripts/，仓库根目录就是它的上一级。
# 用脚本自身位置定位备份文件，避免受 workflow 的 working-directory 影响。
REPO_ROOT = os.path.dirname(HERE)

OWNER = os.environ.get("OWNER", "a454735639-lab")
PKG = os.environ.get("PKG", "tick-stock-panel-images")
REGISTRY = os.environ.get("REGISTRY", "ghcr.io")
GH_TOKEN = os.environ.get("GH_TOKEN", "")
BACKUP_FILE = os.environ.get(
    "BACKUP_FILE", os.path.join(REPO_ROOT, "backup", "manifests.json")
)
RESULT_FILE = os.environ.get(
    "RESULT_FILE", os.path.join(REPO_ROOT, "publish_result.json")
)

FULL_PKG = f"{OWNER}/{PKG}"
OCI_INDEX = "application/vnd.oci.image.index.v1+json"


def http(url, method="GET", headers=None, data=None):
    """返回 (status, body_text)。非 2xx 不抛异常，便于逐条记录失败原因。"""
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # 网络异常等
        return 0, f"{type(e).__name__}: {e}"


def registry_token(scope, password=None):
    """向 ghcr.io 的 token 端点换一个 registry token。

    password 为 None 时走匿名（不带 Authorization），用于判断包是否公开。
    """
    url = (
        f"https://{REGISTRY}/token?scope={scope}&service={REGISTRY}"
    )
    headers = {}
    if password is not None:
        raw = f"probe:{password}".encode("utf-8")
        headers["Authorization"] = "Basic " + base64.b64encode(raw).decode()
    status, body = http(url, headers=headers)
    if status != 200:
        return ""
    try:
        return json.loads(body).get("token", "") or ""
    except Exception:
        return ""


def publish(mans, tok):
    """把每个 manifest 原样 PUT 回目标包。返回 (ok, failures)。"""
    ok = 0
    failures = []
    for rec in mans:
        tag = rec.get("tag")
        body = rec.get("manifest")
        mt = rec.get("mediaType") or OCI_INDEX
        if not tag or not body:
            failures.append({"tag": tag, "error": "missing tag or manifest body"})
            print(f"  SKIP {tag}: missing tag/body")
            continue
        # 用紧凑分隔符保持与备份一致的原始字节，
        # 否则 digest 会变（语义等价但字节不同）。
        payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
        status, text = http(
            f"https://{REGISTRY}/v2/{FULL_PKG}/manifests/{tag}",
            method="PUT",
            headers={
                "Authorization": f"Bearer {tok}",
                "Content-Type": mt,
                "Content-Length": str(len(payload)),
            },
            data=payload,
        )
        if status in (200, 201, 202):
            ok += 1
            print(f"  OK   {tag} (HTTP {status})")
        else:
            failures.append({"tag": tag, "error": f"HTTP {status} {text[:240]}"})
            print(f"  FAIL {tag} HTTP {status} {text[:240]}")
    return ok, failures


def verify_anonymous():
    """匿名验证：拿 token + 列标签 + 拉关键标签的 manifest。"""
    result = {"anon_token": False, "tags": [], "critical": {}}

    tok = registry_token(f"repository:{FULL_PKG}:pull")
    if not tok:
        print("  ANON_TOKEN_DENIED -- package is NOT public")
        return result
    print("  ANON_TOKEN_OK -> package is PUBLIC")
    result["anon_token"] = True

    status, body = http(
        f"https://{REGISTRY}/v2/{FULL_PKG}/tags/list?n=1000",
        headers={"Authorization": f"Bearer {tok}"},
    )
    tags = []
    if status == 200:
        try:
            tags = json.loads(body).get("tags") or []
        except Exception:
            tags = []
    result["tags"] = sorted(tags)
    print(f"  tags visible anonymously: {len(tags)}")

    for tag in ("fused-v0.3.29", "fused-latest", "latest"):
        code, _ = http(
            f"https://{REGISTRY}/v2/{FULL_PKG}/manifests/{tag}",
            headers={
                "Authorization": f"Bearer {tok}",
                "Accept": (
                    "application/vnd.oci.image.index.v1+json,"
                    "application/vnd.docker.distribution.manifest.list.v2+json,"
                    "application/vnd.docker.distribution.manifest.v2+json"
                ),
            },
        )
        result["critical"][tag] = code
        print(f"  manifest {tag}: HTTP {code}")
    return result


def main() -> int:
    if not GH_TOKEN:
        print("GH_TOKEN is empty", file=sys.stderr)
        return 1

    with open(BACKUP_FILE, encoding="utf-8") as f:
        mans = json.load(f)
    print(f"manifests to publish: {len(mans)}")
    if not mans:
        print("empty backup", file=sys.stderr)
        return 1

    push_tok = registry_token(
        f"repository:{FULL_PKG}:pull,push", password=GH_TOKEN
    )
    if not push_tok:
        print("NO_PUSH_TOKEN -- cannot obtain registry push token", file=sys.stderr)
        return 1
    print(f"push token acquired (len={len(push_tok)})")

    ok, failures = publish(mans, push_tok)
    print(f"\nPUBLISHED {ok}/{len(mans)}")
    for f_ in failures[:15]:
        print("  -", f_.get("tag"), f_.get("error"))

    print("\n--- anonymous verification ---")
    verify = verify_anonymous()
    verify["published"] = ok
    verify["total"] = len(mans)
    verify["failures"] = failures

    with open(RESULT_FILE, "w", encoding="utf-8") as f:
        json.dump(verify, f, ensure_ascii=False, indent=1)

    problems = []
    if failures:
        problems.append(f"{len(failures)} manifest(s) failed to publish")
    if not verify["anon_token"]:
        problems.append("package is not anonymously pullable")
    else:
        if verify["published"] and len(verify["tags"]) < verify["published"]:
            problems.append(
                f"tag count mismatch: {len(verify['tags'])} < {verify['published']}"
            )
        for tag, code in verify["critical"].items():
            if code != 200:
                problems.append(f"critical tag {tag} -> HTTP {code}")

    if problems:
        print("\nPROBLEMS:")
        for p in problems:
            print("  !", p)
        return 1
    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
