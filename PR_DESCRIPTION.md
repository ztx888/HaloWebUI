# ✨ 积分系统 & 开屏通知 & 品牌自定义 & 数据库迁移

> 基于 `v0.7.3-7`，新增完整积分计费体系、管理员开屏通知、品牌自定义等企业级功能。
> **46 个文件变动，+8,411 行 / -1,107 行**

---

## 🎯 主要功能

### 1. 💰 积分计费系统

为 Open WebUI 增加了完整的积分（Credit）计费体系，支持按模型、按 Token 精细化定价。

#### 后端核心

| 模块         | 文件                                 | 说明                                                                                                        |
| ------------ | ------------------------------------ | ----------------------------------------------------------------------------------------------------------- |
| **数据模型** | `models/credits.py`                  | 4 张表：`credit`（余额）、`credit_log`（消费记录）、`trade_ticket`（交易工单）、`redemption_code`（兑换码） |
| **API 路由** | `routers/credit.py`                  | 积分查询、消费日志、统计分析、兑换码管理、支付回调等 API                                                    |
| **计费引擎** | `utils/credit/usage.py`              | Token 用量统计与积分扣费核心逻辑，支持 prompt/completion 分别定价                                           |
| **定价模型** | `utils/credit/models.py`             | 模型定价配置，支持全局默认价格 + 按模型自定义价格                                                           |
| **工具计费** | `utils/credit/utils.py`              | 功能调用（如 Web 搜索）、向量化（Embedding）计费                                                            |
| **支付接入** | `utils/credit/alipay.py`, `ezfp.py`  | 支付宝 & 易支付接入                                                                                         |
| **响应拦截** | `utils/response.py`, `utils/misc.py` | Token 统计中间件，在 OpenAI/Gemini 流式响应中自动计费                                                       |

#### 前端管理界面

| 组件                                           | 说明                                                                  |
| ---------------------------------------------- | --------------------------------------------------------------------- |
| `admin/Settings/Credit.svelte`                 | 全局积分设置：启用/禁用、注册赠送额度、Token 定价、功能定价、支付配置 |
| `admin/Users/Credit.svelte`                    | 用户积分管理：查看/修改余额、手动充值/扣费                            |
| `admin/Users/CreditLog.svelte`                 | 消费日志：按用户/时间查询、导出                                       |
| `admin/Users/RedemptionCodes.svelte`           | 兑换码管理：批量生成、编辑、删除、导出                                |
| `admin/Users/CreateRedemptionCodeModal.svelte` | 创建兑换码弹窗                                                        |
| `admin/Users/EditRedemptionCodeModal.svelte`   | 编辑兑换码弹窗                                                        |
| `admin/Users/DeleteCreditLogModal.svelte`      | 日志清理弹窗                                                          |
| `chat/Settings/Credit.svelte`                  | 用户侧积分面板：余额查看、消费记录、兑换码兑换                        |
| `workspace/Models/ModelEditor.svelte`          | 模型编辑器增加定价配置项                                              |

#### API 端点

```
GET    /api/v1/credit/config          # 获取积分配置（公开）
GET    /api/v1/credit/logs            # 当前用户消费日志
GET    /api/v1/credit/logs/all        # 全部消费日志（管理员）
DELETE /api/v1/credit/logs            # 清理历史日志（管理员）
POST   /api/v1/credit/statistics      # 消费统计（管理员）
GET    /api/v1/credit/model-price     # 模型定价配置
POST   /api/v1/credit/model-price     # 更新模型定价
POST   /api/v1/credit/ticket          # 创建支付工单
POST   /api/v1/credit/ticket/callback # 支付回调
GET    /api/v1/credit/codes           # 兑换码列表
POST   /api/v1/credit/codes           # 创建兑换码
PUT    /api/v1/credit/codes/{code}    # 更新兑换码
DELETE /api/v1/credit/codes/{code}    # 删除兑换码
GET    /api/v1/credit/codes/export    # 导出兑换码
POST   /api/v1/credit/codes/{code}/receive  # 兑换
```

#### 配置项（环境变量）

| 变量                                  | 默认值  | 说明                               |
| ------------------------------------- | ------- | ---------------------------------- |
| `CREDIT_ENABLED`                      | `false` | 是否启用积分系统                   |
| `CREDIT_AUTO_PRICE`                   | `true`  | 是否自动计算价格                   |
| `CREDIT_DEFAULT_BALANCE`              | `0`     | 新用户注册赠送积分                 |
| `CREDIT_PROMPT_PRICE_PER_MILLION`     | `0`     | Prompt Token 单价（每百万）        |
| `CREDIT_COMPLETION_PRICE_PER_MILLION` | `0`     | Completion Token 单价（每百万）    |
| `CREDIT_REQUEST_PRICE_PER_MILLION`    | `0`     | 请求单价（每百万次）               |
| `CREDIT_EMBEDDING_PRICE_PER_MILLION`  | `0`     | 向量化单价（每百万 Token）         |
| `CREDIT_FEATURE_PRICE_PER_MILLION`    | `0`     | 功能调用单价（每百万次）           |
| `CREDIT_CUSTOM_BILLING_PATTERNS`      | `[]`    | 自定义功能计费规则（JSON）         |
| `CREDIT_NO_CREDIT_MESSAGE`            | `""`    | 余额不足提示信息                   |
| `ALIPAY_*`                            | -       | 支付宝配置（AppID/私钥/公钥/网关） |
| `EZFP_*`                              | -       | 易支付配置                         |

---

### 2. 📢 开屏通知（Splash Notification）

管理员可配置 Markdown 格式的全站通知，用户打开首页时自动弹出。

#### 特性

- 🎨 **iOS Liquid Glass 风格** — 毛玻璃背景 + Shimmer 标题动画
- 📝 **Markdown 支持** — 标题、粗体、代码、链接、列表等
- 👁️ **实时预览** — 管理员编辑时即时看到渲染效果
- 🌙 **暗色模式适配** — 自动跟随系统主题
- 📱 **响应式设计** — 移动端/桌面端自适应
- � **每次刷新显示** — 每次刷新页面都会弹出通知（如果已启用）

#### 文件变动

| 文件                                       | 说明                                                 |
| ------------------------------------------ | ---------------------------------------------------- |
| `config.py`                                | `SPLASH_NOTIFICATION_ENABLED/TITLE/CONTENT` 配置变量 |
| `routers/configs.py`                       | GET/POST 端点（含公开端点）                          |
| `admin/Settings/SplashNotification.svelte` | 管理后台设置页                                       |
| `layout/SplashNotification.svelte`         | 首页弹窗组件                                         |
| `+layout.svelte`                           | 集成到主布局                                         |

#### API 端点

```
GET  /api/v1/configs/splash-notification        # 公开获取通知（无需登录）
GET  /api/v1/configs/splash-notification/admin   # 管理员获取配置
POST /api/v1/configs/splash-notification/admin   # 管理员保存配置
```

---

### 3. 🎨 品牌自定义

支持通过环境变量 `CUSTOM_NAME` 自定义网站名称，覆盖默认的 "Open WebUI"。

| 文件            | 说明                                   |
| --------------- | -------------------------------------- |
| `config.py`     | 读取 `CUSTOM_NAME` 并覆盖 `WEBUI_NAME` |
| `utils/auth.py` | 品牌相关认证逻辑优化                   |

---

### 4. 🗄️ 数据库迁移

新增 Alembic 迁移脚本，确保积分系统的 4 张表在所有环境下都能自动创建。

#### 迁移文件

`migrations/versions/a1b2c3d4e5f6_add_credit_tables.py`

#### 建表内容

| 表                | 主键   | 索引                                                            |
| ----------------- | ------ | --------------------------------------------------------------- |
| `credit`          | `id`   | `user_id` (UNIQUE)                                              |
| `credit_log`      | `id`   | `user_id`, `created_at`                                         |
| `trade_ticket`    | `id`   | `user_id`, `created_at`                                         |
| `redemption_code` | `code` | `purpose`, `user_id`, `created_at`, `expired_at`, `received_at` |

#### 兼容性保障

- ✅ **幂等性** — `if table not in existing_tables` 检查，已有表不会报错
- ✅ **索引补建** — 对已存在但缺少索引的表自动补建缺失索引
- ✅ **SQLite 兼容** — `Numeric(24,12)` / `JSON` 由 SQLAlchemy 自动映射
- ✅ **PostgreSQL 兼容** — 原生类型支持
- ✅ **升级安全** — 从任何旧版本升级均不会破坏现有数据

---

## 📦 新增依赖

```
# backend/requirements.txt
pycryptodome     # 支付签名加密
```

---

## 📁 文件变更总览

### 新增文件 (25)

**后端:**

- `backend/open_webui/models/credits.py` — 积分数据模型
- `backend/open_webui/routers/credit.py` — 积分 API 路由
- `backend/open_webui/utils/credit/__init__.py`
- `backend/open_webui/utils/credit/alipay.py` — 支付宝接入
- `backend/open_webui/utils/credit/ezfp.py` — 易支付接入
- `backend/open_webui/utils/credit/models.py` — 模型定价
- `backend/open_webui/utils/credit/usage.py` — Token 计费引擎
- `backend/open_webui/utils/credit/utils.py` — 计费工具
- `backend/open_webui/migrations/versions/a1b2c3d4e5f6_add_credit_tables.py` — 数据库迁移

**前端:**

- `src/lib/apis/credit/index.ts` — 积分前端 API
- `src/lib/components/admin/Settings/Credit.svelte` — 管理积分设置
- `src/lib/components/admin/Settings/SplashNotification.svelte` — 管理通知设置
- `src/lib/components/admin/Users/Credit.svelte` — 用户积分管理
- `src/lib/components/admin/Users/CreditLog.svelte` — 消费日志
- `src/lib/components/admin/Users/RedemptionCodes.svelte` — 兑换码管理
- `src/lib/components/admin/Users/CreateRedemptionCodeModal.svelte`
- `src/lib/components/admin/Users/EditRedemptionCodeModal.svelte`
- `src/lib/components/admin/Users/DeleteCreditLogModal.svelte`
- `src/lib/components/chat/Settings/Credit.svelte` — 用户积分面板
- `src/lib/components/layout/SplashNotification.svelte` — 首页通知弹窗

**其他:**

- `docker-compose.dev.yaml` — 开发环境 Docker 配置

### 修改文件 (25)

**后端:**

- `config.py` — 新增积分 / 通知 / 品牌配置变量 (+231)
- `main.py` — 注册积分路由，初始化配置 (+86)
- `functions.py` — 积分计费集成 (+120/-0)
- `routers/configs.py` — 通知 / 使用量配置端点 (+207)
- `routers/gemini.py` — Gemini 路由积分计费集成 (+359/-0)
- `routers/openai.py` — OpenAI 路由积分计费集成
- `routers/users.py` — 用户积分余额管理端点 (+90)
- `models/models.py` — 模型定价字段 (+40)
- `models/users.py` — 用户模型小调整
- `utils/auth.py` — 品牌自定义 + 认证优化 (+154)
- `utils/misc.py` — Token 统计工具函数 (+163)
- `utils/response.py` — 流式响应积分扣费 (+51)
- `utils/chat.py` — 聊天工具小调整
- `retrieval/utils.py` — 向量化积分计费 (+56)
- `requirements.txt` — 新增依赖

**前端:**

- `apis/configs/index.ts` — 通知 / 使用量配置 API (+142)
- `admin/Settings.svelte` — 新增积分/通知标签页 (+62)
- `admin/Users.svelte` — 用户管理增加积分标签 (+91)
- `admin/Users/UserList.svelte` — 用户列表增加积分列 (+26)
- `admin/Users/UserList/EditUserModal.svelte` — 编辑用户增加积分
- `workspace/Models/ModelEditor.svelte` — 模型编辑器增加定价 (+208)
- `i18n/locales/zh-CN/translation.json` — 中文翻译 (+115)
- `routes/(app)/+layout.svelte` — 集成开屏通知组件

---

## 🔄 升级指南

### 从 v0.7.3-7 升级

1. **拉取最新代码**
2. **重新构建 Docker 镜像** — 前端有新组件，需要重新构建
3. **重启容器** — 后端启动时自动执行数据库迁移，创建积分表
4. **（可选）配置积分系统** — 在管理后台 Settings > Credit 中启用并配置

### 环境变量

无需额外配置。所有新功能默认关闭，可通过管理后台 UI 或环境变量启用。

### 数据库

- **自动迁移** — 启动时 Alembic 自动检测并执行 `a1b2c3d4e5f6_add_credit_tables` 迁移
- **无破坏性** — 不修改任何现有表结构，仅新增 4 张表
- **兼容 SQLite / PostgreSQL** — 已在两种数据库上验证

---

## 🧪 测试建议

- [ ] 积分系统启用/禁用
- [ ] 新用户注册赠送积分
- [ ] OpenAI / Gemini 模型对话自动扣费
- [ ] 模型自定义定价
- [ ] 兑换码创建、兑换、过期
- [ ] 消费日志查询和清理
- [ ] 开屏通知创建、预览、关闭、不重复弹出
- [ ] 品牌名称自定义
- [ ] 全新安装（空数据库）能否正常启动
- [ ] 从旧版本升级是否数据库迁移正常
- [ ] SQLite 和 PostgreSQL 双环境验证
- [ ] 暗色/亮色模式 UI 适配
- [ ] 移动端响应式布局
