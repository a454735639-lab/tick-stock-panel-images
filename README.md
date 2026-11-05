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
用于一次性把旧包的全部标签原样恢复到新包；**不含源码，也不含任何密钥**。

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

所以：**要让镜像能被匿名拉取，必须由公开仓库来创建并推送一个全新的包名。**
这也是本仓库存在的唯一理由。

## 镜像标签

| 标签 | 内容 |
|---|---|
| `fused-latest` | 融合镜像：上游 TSP + akshare 适配器 + 财务调度 + Streamlit 面板 |
| `fused-v0.3.29` | 同上，固定版本（生产在用） |
| `latest` | 纯上游应用镜像 |
| `sha-<commit>` / `fused-<commit>` | 按提交可追溯 |

共 27 个标签，均为 `linux/amd64` + `linux/arm64` 双架构。

## 怎么用

匿名拉取，**不需要任何 token**：

```bash
docker pull ghcr.io/a454735639-lab/tick-stock-panel-images:fused-v0.3.29
```

## 工作流

| 工作流 | 作用 | 触发方式 |
|---|---|---|
| `Release Images` | 从私有源仓库拉源码 → 构建 → 推到公开包 → 匿名验证 | 手动 dispatch / push |
| `Publish Public Package` | 把 `backup/manifests.json` 里的 manifest 原样 PUT 回公开包（幂等） | 手动 dispatch / push |

> `Publish Public Package` 存在的意义：GHCR 的 blob 按 digest 全局去重，
> 所以可以把 manifest 原样 PUT 回去而不必重新构建（实测 27/27 恢复成功）。
> 迁移期间用它做一次性恢复；日常发布走 `Release Images` 即可。

## 维护要点

- 私有源仓库的 deploy key 标题是 `workbuddy-release-pipeline`（只读）。
- 换密钥时：删除旧 key → 本仓库 `DEPLOY_KEY` secret 同步更新 → 私有源仓库重新加 key。
- 源仓库改名或转移 owner，需同步改 `release.yml` 里的 `SOURCE_REPO` 与 deploy key。
- **包名不能改**：GHCR 没有包改名 API，且旧名会被占用。任何涉及包的改动前先确认包名。
- YAML 里的步骤名若含 `: ` 必须加引号（`mapping values are not allowed here`）；
  多行 Python 一律抽到 `scripts/` 下独立文件，不要内嵌在 block scalar 里。
