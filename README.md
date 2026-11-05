# tick-stock-panel-images

**公开发布流水线** —— 构建并发布 [`ghcr.io/a454735639-lab/tick-stock-panel-images`](https://github.com/a454735639-lab/tick-stock-panel-images)。

## 这个仓库有什么、没有什么

| | 说明 |
|---|---|
| ✅ 有 | 构建配方（Dockerfile 引用方式）、Actions 工作流、镜像 manifest 元数据备份 |
| ❌ 没有 | **任何私有源码**——一行都没有 |

源码始终留在私有仓库 [`a454735639-lab/tick-stock-panel-src`](https://github.com/a454735639-lab/tick-stock-panel-src)（private）。
本仓库通过一个**只读 deploy key** 在 CI 运行时把它拉下来，构建完成后随即丢弃。

`backup/manifests.json` 里是镜像的 **manifest 元数据**（tag / digest / 架构 / mediaType），
用作历史清单留档；**不含源码，也不含任何密钥**。

## 为什么需要这么绕？

这是实测摸清的 GHCR 机制，不是想当然：

1. **包的可见性在"首次创建"时继承创建它的仓库。**
   私有仓库首推 → 包是 PRIVATE；公开仓库首推 → 包是 PUBLIC。
2. **已存在的包不会跟随关联仓库的可见性翻转。**
   把私有仓库翻成 public，包**仍然是 PRIVATE**。
3. **REST API 没有"修改包可见性"的端点**，GraphQL 也没有任何 Package mutation。
   （OpenAPI 里 packages 相关路径只有 `get` / `delete` / `post .../restore`。）
4. **包的推送权跟随"创建它的仓库"。**
   别的仓库的令牌推不进去，报 `permission_denied: write_package` / `read_package`。
5. **包名一旦被用过即被占用。**
   旧私有包 `tick-stock-panel` 删除后，包名仍被原仓库上下文占用
   （最长 30 天可 restore 保留期），期间别的仓库即使新建同名仓库也无法接管
   （PUT 仍报 `permission_denied: write_package`）。**所以必须换新包名。**
6. **仓库改名不影响包归属。**
   repo id 不变，包的推送权跟着 repo id 走，所以把私有仓库改名成
   `tick-stock-panel-src` 不会让包失联。

所以：**要让镜像能被匿名拉取，必须由公开仓库来创建并推送一个全新的包名。**
这也是本仓库存在的唯一理由。

## 镜像标签

| 标签 | 内容 |
|---|---|
| `fused-latest` | 融合镜像：上游 TSP + akshare 适配器 + 财务调度 + Streamlit 面板 |
| `fused-v0.3.29` | 同上，固定版本（生产在用） |
| `latest` | 纯上游应用镜像 |
| `sha-<commit>` / `fused-<commit>` | 按提交可追溯 |

均为 `linux/amd64` + `linux/arm64` 双架构。

## 怎么用

匿名拉取，**不需要任何 token**：

```bash
docker pull ghcr.io/a454735639-lab/tick-stock-panel-images:fused-v0.3.29
```

## 工作流

| 工作流 | 作用 | 触发方式 |
|---|---|---|
| `Rebuild Images` | **发布镜像的唯一正确方式**：从私有源仓库拉源码 → 构建 → 推公开包 → 完整拉取链路校验 | 手动 dispatch |
| `Smoke Test Release Pipeline` | 冒烟：验证整条链路，但只推临时标签 `smoke-<sha>`，不碰生产标签 | 手动 dispatch / push |
| `Publish Public Package` | 只发布 `backup/manifests.json` 里的 **index 层**（见下方警告） | 手动 dispatch / push |

### ⚠️ 为什么必须"重建"而不是"搬 manifest"

多架构镜像在 registry 里有两层：

```
index (manifest list)          ← 一个标签指向它
  ├── 子 manifest (amd64)      ← docker 按平台挑它，再取它
  ├── 子 manifest (arm64)
  └── 子 manifest (attestation × 2)
```

`docker pull` 的真实链路是
「取 index → 选平台 → **取子 manifest** → 取 config → 取各层 blob」。

**只把 index 推上去，第 3 步会 404（`MANIFEST_UNKNOWN`）——
标签看起来在，实际 pull 必然失败。**
这不是理论推测：本仓库曾经把 27 个标签的 index 原样 PUT 到新包，
`GET manifest by tag` 全部返回 200，看起来一切正常，
但子 manifest 从未上传，`docker pull` 100% 失败。

而且 `backup/manifests.json` 里**只有 index 层**，
子 manifest 的 body 不在其中（只有 digest 引用），
所以靠搬移**在物理上就不可能**还原出可拉取的镜像 —— 只能重建。

`Rebuild Images` 用 buildx 构建，push 时会把 index、子 manifest、config、
每一层 blob 一并推上去，天然完整。

### 校验镜像能不能拉

`scripts/verify_pullable.py` 走完 docker 的完整解析链路
（index → 子 manifest → config → 各层 blob），而不只看"标签能不能解析"：

```bash
GH_TOKEN=xxx python3 scripts/verify_pullable.py fused-v0.3.29 fused-latest latest
```

`GH_TOKEN` 留空则走**匿名**链路 —— 匿名能走通，才叫"公开可拉"。

## 维护要点

- 私有源仓库的 deploy key 标题是 `workbuddy-release-pipeline`（只读）。
- 换密钥时：删除旧 key → 本仓库 `DEPLOY_KEY` secret 同步更新 → 私有源仓库重新加 key。
- 源仓库改名或转移 owner，需同步改工作流里的 `SOURCE_REPO` 与 deploy key。
- **包名不能改**：GHCR 没有包改名 API，且旧名会被占用。任何涉及包的改动前先确认包名。
- **发布一律走重建**，不要试图搬 manifest —— 见上方「为什么必须重建」。
- **验证一律走完整链路**，不要只看 `GET manifest by tag` 是否 200 —— 见上方事故说明。
- manifest 的 digest 是**原始字节**的 sha256。用 `json.dumps(body)` 的默认分隔符
  （`", "` / `": "`）会改变字节从而改变 digest，即使语义完全等价。
  要保持 digest 不变必须用紧凑分隔符 `(",", ":")`。
- YAML 里的步骤名若含 `: ` 必须加引号（`mapping values are not allowed here`）；
  多行 Python 一律抽到 `scripts/` 下独立文件，不要内嵌在 block scalar 里。
