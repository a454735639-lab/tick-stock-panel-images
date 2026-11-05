"""尝试恢复 30 天内被删除的旧私有包。

背景
----
迁移到公开包 tick-stock-panel-images 时，只 PUT 了多架构 index（manifest list），
4 个子 manifest（amd64/arm64 + 2 个 attestation）从未上传，
导致 `docker pull` 在"取子 manifest"这一步失败。

GitHub 的 restore 端点可以在删除后 30 天内把整个包恢复回来
（前提：命名空间未被复用）。旧包是 2 小时前才删的，还有救。

用法
----
  GH_TOKEN=xxx python3 scripts/restore_old_pkg.py

只读诊断 + 尝试 restore，不做破坏性操作。
"""

import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"
OLD_PKG = os.environ.get("OLD_PKG", "tick-stock-panel")
NEW_PKG = os.environ.get("NEW_PKG", "tick-stock-panel-images")
OWNER = os.environ.get("OWNER", "a454735639-lab")
TOKEN = os.environ.get("GH_TOKEN", "")


def api(method: str, path: str, body=None):
    """调用 GitHub REST API，返回 (status, parsed_body)。"""
    url = path if path.startswith("http") else API + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            try:
                return resp.status, json.loads(raw)
            except Exception:
                return resp.status, raw[:400]
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw[:400]
    except Exception as e:
        return 0, str(e)


def main() -> int:
    if not TOKEN:
        print("GH_TOKEN 为空，无法操作")
        return 2

    print("=" * 66)
    print(f"目标：恢复被删除的旧包 {OWNER}/{OLD_PKG}")
    print("=" * 66)

    # ── 1. 恢复前诊断：旧包还在不在 ──────────────────────────────────
    st, pkg = api("GET", f"/user/packages/container/{OLD_PKG}")
    print(f"[1] GET 旧包元数据 -> HTTP {st}")
    if st == 200 and isinstance(pkg, dict):
        print(f"    visibility={pkg.get('visibility')} "
              f"version_count={pkg.get('version_count')} "
              f"repository={(pkg.get('repository') or {}).get('full_name')}")
    else:
        print(f"    {str(pkg)[:200]}")

    # ── 2. 尝试 restore 整个包 ────────────────────────────────────────
    st, res = api("POST", f"/user/packages/container/{OLD_PKG}/restore")
    print(f"[2] POST restore -> HTTP {st}")
    if st in (200, 201, 202):
        print("    ✅ restore 请求已受理")
    else:
        print(f"    {str(res)[:300]}")

    # ── 3. 恢复后复查：版本数与标签 ────────────────────────────────────
    st, versions = api(
        "GET", f"/user/packages/container/{OLD_PKG}/versions?per_page=100")
    print(f"[3] GET 旧包 versions -> HTTP {st}")
    if st == 200 and isinstance(versions, list):
        print(f"    版本数: {len(versions)}")
        tags = set()
        for v in versions:
            for t in ((v.get("metadata") or {}).get("container", {}) or {}).get("tags") or []:
                tags.add(t)
        print(f"    标签数: {len(tags)}")
        for t in sorted(tags):
            print(f"      - {t}")

    # ── 4. 新包现状（对照）────────────────────────────────────────────
    st, newpkg = api("GET", f"/user/packages/container/{NEW_PKG}")
    print(f"[4] GET 新包 {NEW_PKG} -> HTTP {st}")
    if st == 200 and isinstance(newpkg, dict):
        print(f"    visibility={newpkg.get('visibility')} "
              f"version_count={newpkg.get('version_count')}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
