# tick-stock-panel-release

**公开发布流水线** —— 构建并发布 [`ghcr.io/a454735639-lab/tick-stock-panel`](https://github.com/a454735639-lab/tick-stock-panel)。

## 这个仓库有什么、没有什么

| | 说明 |
|---|---|
| ✅ 有 | 构建配方（Dockerfile 引用方式）、Actions 工作流、CI 配置 |
| ❌ 没有 | **任何私有源码**——一行都没有 |

源码始终留在私有仓库 [`a454735639-lab/tick-stock-panel`](https://github.com/a454735639-lab/tick-stock-panel)（private）。
本仓库通过一个**只读 deploy key** 在 CI 运行时把它拉下来，构建完成后随即丢弃。

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

所以：**要让镜像能被匿名拉取，必须由公开仓库来创建并推送这个包。**
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
docker pull ghcr.io/a454735639-lab/tick-stock-panel:fused-v0.3.29
```

## 手动触发发布

Actions → Release Images → Run workflow：

- `fuse`：构建融合镜像（生产用）
- `base`：构建纯应用镜像

推送前建议先在私有源仓库跑一次完整构建，确认无误再在这里发布。

## 维护要点

- 私有源仓库的 deploy key 标题是 `workbuddy-release-pipeline`（只读）。
- 换密钥时：删除旧 key → 本仓库 `DEPLOY_KEY` secret 同步更新 → 私有源仓库重新加 key。
- 源仓库改名或转移 owner，需同步改本工作流里的 `SOURCE_REPO` 与 deploy key。
