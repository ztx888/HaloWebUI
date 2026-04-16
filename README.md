<div align="center">
  <img src="./static/favicon.png" alt="HaloWebUI" width="120" height="120" />
  <h1>HaloWebUI</h1>
  <p><strong>自托管 AI 平台 · 多模型路由 · 知识检索增强 · 全链路可控</strong></p>
  <p>
    基于 Open WebUI 深度定制，原生集成 Anthropic Claude / Google Gemini / xAI Grok，<br/>
    内置 HaloClaw 消息网关，一站式管理你的所有大模型。
  </p>

  <br/>

  <a href="https://github.com/ztx888/HaloWebUI/stargazers">
    <img src="https://img.shields.io/github/stars/ztx888/HaloWebUI?style=for-the-badge&logo=github&color=f4c542" alt="Stars" />
  </a>
  <a href="https://github.com/ztx888/HaloWebUI/network/members">
    <img src="https://img.shields.io/github/forks/ztx888/HaloWebUI?style=for-the-badge&logo=github&color=8ac926" alt="Forks" />
  </a>
  <a href="https://github.com/ztx888/HaloWebUI/commits/main">
    <img src="https://img.shields.io/github/last-commit/ztx888/HaloWebUI/main?style=for-the-badge&logo=git&color=ff595e" alt="Last Commit" />
  </a>
  <a href="https://github.com/ztx888/HaloWebUI/blob/main/LICENSE">
    <img src="https://img.shields.io/github/license/ztx888/HaloWebUI?style=for-the-badge&color=6a4c93" alt="License" />
  </a>

  <br/><br/>

  <img src="https://img.shields.io/badge/Svelte_4-FF3E00?style=flat-square&logo=svelte&logoColor=white" />
  <img src="https://img.shields.io/badge/TypeScript-3178C6?style=flat-square&logo=typescript&logoColor=white" />
  <img src="https://img.shields.io/badge/Tailwind_CSS-06B6D4?style=flat-square&logo=tailwindcss&logoColor=white" />
  <img src="https://img.shields.io/badge/Python_3.11+-3776AB?style=flat-square&logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white" />
  <img src="https://img.shields.io/badge/Docker-2496ED?style=flat-square&logo=docker&logoColor=white" />
</div>

<br/>

<!-- TODO: 替换为实际界面截图（推荐 16:9、1920×1080），放到 static/screenshot.png -->
<!--
<div align="center">
  <img src="./static/screenshot.png" alt="HaloWebUI 界面预览" width="800" />
</div>
<br/>
-->

## ✨ 核心能力

<!-- TODO: 待补充真实特性网格 -->

> 特性列表整理中，敬请期待。

## 🚀 快速开始

> [!IMPORTANT]
> 必须挂载 `-v open-webui:/app/backend/data` 以持久化数据库与上传文件。

### Docker 运行

```bash
docker run -d -p 3000:8080 \
  --add-host=host.docker.internal:host-gateway \
  -v open-webui:/app/backend/data \
  --name halowebui \
  --restart always \
  ghcr.io/ztx888/halowebui:main
```

### Docker Compose（默认推荐）

```bash
docker compose up -d
```

启动完成后访问 **http://localhost:3000** ，首次注册的用户自动成为管理员。

### MCP stdio 说明

- 官方 `main` 镜像是默认推荐版，内置了 `uv/uvx`、`node/npx` 与 `git`，可直接体验当前内置的常见 stdio MCP 预设，也兼容一部分通过 `uvx --from git+...` 安装的 MCP。
- 官方 `slim` 镜像是极简版，不内置 `uv/uvx`、`node/npx`、`git` 等 stdio MCP 常用运行时，适合更在意镜像体积和依赖面的部署场景。
- stdio MCP 命令运行在 HaloWebUI 服务端容器内，不是在浏览器或你的本机 shell 里执行。
- 某些自定义 stdio MCP 会通过 Git 源安装（例如 `uvx --from git+https://...`）；这类配置除了 `uv/uvx` 之外还依赖 `git`。
- MCP 配置保存后不会自动验证，需要进入对应配置并手动点击 `验证连接` / `重新验证`。
- `docker exec` 进入容器后能运行某个命令，不代表临时 shell 路径一定适合长期配置为 MCP command；请优先使用镜像内稳定安装路径或服务主进程可见的常规 `PATH`，避免依赖 `fnm_multishells/...` 这类临时路径。
- stdio MCP 本身不会长期常驻占用额外内存；额外内存主要来自实际启动的 MCP 子进程，空闲后会按系统配置自动回收。

### Docker 运行（轻量版 slim）

```bash
docker run -d -p 3000:8080 \
  --add-host=host.docker.internal:host-gateway \
  -v open-webui:/app/backend/data \
  --name halowebui \
  --restart always \
  ghcr.io/ztx888/halowebui:slim
```

### Docker Compose（轻量版 slim）

```bash
docker compose -f docker-compose.yaml -f docker-compose.slim.yaml up -d
```

`slim` 适合：

- 追求更小镜像
- 不需要 stdio MCP 开箱体验
- 愿意自行补充 Node.js / uv 等运行时

<details>
<summary><strong>⚙️ 常用环境变量</strong></summary>

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `OPENAI_API_KEY` | OpenAI 兼容 API 密钥 | — |
| `OPENAI_API_BASE_URL` | OpenAI 兼容 API 地址 | `https://api.openai.com/v1` |
| `REQUESTS_VERIFY` | 后端通过 `requests` 发起 HTTPS 请求时是否校验证书 | `true` |
| `AIOHTTP_CLIENT_SESSION_SSL` | 后端通过 `aiohttp` 发起 HTTPS 请求时是否校验证书 | `true` |
| `ANTHROPIC_API_KEY` | Anthropic Claude API 密钥 | — |
| `GEMINI_API_KEY` | Google Gemini API 密钥 | — |
| `OLLAMA_BASE_URL` | Ollama 服务地址 | `http://host.docker.internal:11434` |
| `WEBUI_SECRET_KEY` | JWT 签名密钥（生产环境必须设置） | 随机生成 |
| `DATABASE_URL` | 数据库连接串（PostgreSQL） | SQLite 本地文件 |
| `REDIS_URL` | Redis 缓存地址 | — |

使用自签证书时，优先把 CA 证书导入容器信任链；只有临时排障时才建议把上面两个开关设为 `false`。

</details>

## 🏗️ 技术架构

```
┌─────────────────────────────────────────────┐
│                  浏览器 / PWA                │
│         Svelte 4 · TypeScript · Tailwind    │
├─────────────────────────────────────────────┤
│               FastAPI 后端                   │
│   AnyRouter · HaloClaw · RAG · Pipeline     │
├──────────┬──────────┬──────────┬────────────┤
│  Claude  │  Gemini  │  OpenAI  │   Ollama   │
│  (原生)  │  (原生)  │ (兼容层) │   (本地)   │
├──────────┴──────────┴──────────┴────────────┤
│  SQLite / PostgreSQL  ·  Redis  ·  向量 DB  │
└─────────────────────────────────────────────┘
```

## 🙏 致谢

HaloWebUI 基于 [Open WebUI](https://github.com/open-webui/open-webui) 深度定制开发。感谢 Open WebUI 社区的卓越贡献。

## 📄 许可证

本项目遵循 [BSD-3-Clause](LICENSE) 许可协议。

---

<div align="center">

### ⭐ Star History

<a href="https://star-history.com/#ztx888/HaloWebUI&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=ztx888/HaloWebUI&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=ztx888/HaloWebUI&type=Date" />
    <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=ztx888/HaloWebUI&type=Date" width="680" />
  </picture>
</a>

<br/><br/>

<sub>如果 HaloWebUI 对你有帮助，请点亮一颗 ⭐ 支持我们！</sub>

</div>
