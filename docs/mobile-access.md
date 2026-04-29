# Halo 安卓访问与接入说明

这份说明把 HaloWebUI 的两条移动端路径分开讲清楚：

- `PWA`：Halo 自己的正式安装方案，适合作为默认推荐入口
- `Conduit`：第三方原生客户端，适合作为兼容方案，不是 Halo 官方客户端

## 1. Halo PWA

### 适用场景

- 想让用户直接从手机桌面打开 Halo
- 想保留 Halo 自己的页面、品牌和交互
- 想要“像 App 一样启动”，但不做原生打包

### 当前能力边界

- 支持安装到安卓桌面，优先推荐 Android Chrome 或 Samsung Internet
- 首次联网访问后，可以离线打开应用外壳和设置页
- 聊天发送、模型列表、历史同步、上传附件仍然必须联网
- 不缓存聊天结果，不把旧接口结果当成离线数据继续展示

### 安装方式

#### 安卓

- Android Chrome / Samsung Internet
  - 打开 Halo
  - 进入 `设置 -> 关于`
  - 如果浏览器支持直接安装，会看到“安装 Halo WebUI”按钮
  - 如果没有直接按钮，请打开浏览器菜单，选择“安装应用”或“添加到主屏幕”

#### iPhone / iPad

- Safari
  - 打开 Halo
  - 点“分享”
  - 选择“添加到主屏幕”

### 使用前提

- 推荐使用 `HTTPS`
- 域名、反向代理和证书要对手机浏览器可访问
- 如果是内网地址，手机必须能直接访问到该地址

## 2. Conduit 兼容支持

`Conduit` 是独立第三方客户端，不隶属于 Open WebUI，也不是 Halo 官方客户端。

Halo 当前提供的是“兼容支持”：

- 不改 Halo 品牌归属
- 不新增 Halo 专属客户端协议
- 保持 Open WebUI 常见登录、聊天、文件和流式接口对它可用

### 适用场景

- 想要更像原生 App 的移动端体验
- 需要语音输入、分享面板、桌面小组件这类原生能力
- 能接受它是第三方客户端，而不是 Halo 官方 Android App

### 接入前提

- Halo 对外提供稳定的基础 URL
- 推荐使用 `HTTPS`
- WebSocket 可用
- 如果你依赖反向代理登录，还要能传递自定义请求头或 Cookie
- 如果要走 API Key，需要后端已经开启 API Key 能力

### 重点链路

- 登录：用户名密码、LDAP、JWT / 反向代理头
- API Key
- 文件上传
- WebSocket 流式响应

### 常见失败点

#### 1. 反向代理没有转发 WebSocket

现象：

- 能登录，但发消息后一直转圈
- 流式输出卡住

重点检查：

- `/ws/socket.io`
- `Upgrade`
- `Connection`

Nginx 类代理至少要保证类似下面这类转发：

```nginx
location /ws/socket.io {
    proxy_pass http://127.0.0.1:8080/ws/socket.io;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
}
```

#### 2. API Key 没开，或客户端填了错误的认证方式

现象：

- 连接能保存，但请求直接 401 / 403

重点检查：

- 是否真的启用了 API Key
- 当前连接到底应该走用户名密码、代理登录，还是 API Key

#### 3. 代理登录依赖自定义请求头，但客户端没带上

现象：

- 浏览器里能进，Conduit 里不行

重点检查：

- 是否依赖 `X-API-Key`、`Authorization`、组织路由头，或受信任邮箱头
- Conduit 连接配置里是否已经补上同样的请求头

#### 4. 基础地址或路径不对

现象：

- 登录页能打开，但接口报 404
- 某些功能正常，流式或上传不正常

重点检查：

- 填的是 Halo 的实际基础地址，不是上游模型地址
- 代理没有把 `/api`、`/ws`、上传相关路径拆坏

#### 5. 手机不信任当前证书

现象：

- 浏览器勉强能打开，原生客户端连接失败

重点检查：

- 证书是否是公开受信任证书
- 如果用了自签证书，手机系统是否已经信任对应 CA

## 3. 推荐顺序

如果你要给普通用户一个最稳的移动端入口，优先顺序建议是：

1. 先推荐 Halo 自己的 PWA
2. 对需要更强原生体验的用户，再提供 Conduit 兼容接入说明

这样用户看到的仍然是 Halo 自己的产品入口，而不是把第三方客户端误当成 Halo 官方 App。
