# 🐳 Docker 镜像发布工作流说明

## 📋 概述

本仓库配置了自动化 Docker 镜像构建和发布到 GitHub Container Registry (GHCR) 的工作流。

## 🚀 触发条件

Docker 镜像**仅在以下情况下**构建和推送：

### 1️⃣ 创建新的 GitHub Release（自动触发）

当您在 GitHub 上发布新版本时：

1. 进入仓库页面 → **Releases** → **Create a new release**
2. 创建新标签（如 `v0.7.3-8`）
3. 填写 Release 标题和说明
4. 点击 **Publish release**

**触发的工作流：**

- ✅ `docker-publish.yml` — 构建 AMD64 镜像
- ✅ `docker-publish-arm.yml` — 构建 ARM64 镜像

### 2️⃣ 手动触发

在 GitHub Actions 页面手动运行：

1. 进入仓库 → **Actions** 标签页
2. 选择工作流：
   - `Build and Push Docker Image` (AMD64)
   - `Build and Push Docker Image (ARM64)` (ARM64)
3. 点击 **Run workflow** → 选择分支 → **Run workflow**

## 🏷️ 镜像标签

### AMD64 镜像

发布版本 `v0.7.3-8` 时，会创建以下标签：

```
ghcr.io/ztx888/openwebui:latest
ghcr.io/ztx888/openwebui:0.7.3-8
ghcr.io/ztx888/openwebui:v0.7.3-8
```

### ARM64 镜像

发布版本 `v0.7.3-8` 时，会创建以下标签：

```
ghcr.io/ztx888/openwebui:latest-arm64
ghcr.io/ztx888/openwebui:0.7.3-8-arm64
ghcr.io/ztx888/openwebui:v0.7.3-8-arm64
```

## 📦 使用镜像

### 拉取最新版本

```bash
# AMD64
docker pull ghcr.io/ztx888/openwebui:latest

# ARM64
docker pull ghcr.io/ztx888/openwebui:latest-arm64
```

### 拉取特定版本

```bash
# AMD64
docker pull ghcr.io/ztx888/openwebui:0.7.3-8

# ARM64
docker pull ghcr.io/ztx888/openwebui:0.7.3-8-arm64
```

## 🔄 发布新版本的完整流程

### 方式一：通过 GitHub UI（推荐）

1. **准备代码**

   ```bash
   git add .
   git commit -m "feat: 新功能描述"
   git push origin main
   ```

2. **创建 Release**
   - 访问 https://github.com/zhizinan1997/open-webui-xinban/releases/new
   - **Choose a tag**: 输入新版本号（如 `v0.7.3-8`）
   - **Release title**: 输入版本标题（如 `v0.7.3-8: 积分系统 & 开屏通知`）
   - **Description**: 粘贴 `PR_DESCRIPTION.md` 的内容或自定义说明
   - 点击 **Publish release**

3. **自动构建**
   - GitHub Actions 自动触发两个工作流
   - 约 10-15 分钟后，镜像推送到 GHCR
   - 可在 **Actions** 标签页查看构建进度

### 方式二：通过命令行

```bash
# 1. 创建并推送标签
git tag -a v0.7.3-8 -m "v0.7.3-8: 积分系统 & 开屏通知"
git push origin v0.7.3-8

# 2. 使用 GitHub CLI 创建 Release
gh release create v0.7.3-8 \
  --title "v0.7.3-8: 积分系统 & 开屏通知" \
  --notes-file PR_DESCRIPTION.md
```

## ⚠️ 重要说明

### ❌ 不再自动构建的情况

- **Push 到 main 分支** — 不会触发 Docker 构建
- **合并 PR** — 不会触发 Docker 构建
- **普通提交** — 不会触发 Docker 构建

### ✅ 优势

- **节省资源** — 避免每次提交都构建镜像
- **版本控制** — 镜像与 Release 版本一一对应
- **清晰追溯** — 通过标签快速定位代码版本

## 🔍 查看构建状态

- **Actions 页面**: https://github.com/zhizinan1997/open-webui-xinban/actions
- **Packages 页面**: https://github.com/zhizinan1997?tab=packages

## 📝 相关文件

- `.github/workflows/docker-publish.yml` — AMD64 构建工作流
- `.github/workflows/docker-publish-arm.yml` — ARM64 构建工作流
- `.github/workflows/build-release.yml` — GitHub Release 创建工作流
