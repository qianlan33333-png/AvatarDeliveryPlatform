# AvatarDeliveryPlatform

独立的“分身 + 课程交付”平台，首版提供：

- 微信身份和手机号权益绑定；
- 课程、课节及腾讯云 VOD 视频素材管理；
- 外部 webhook 幂等开通课程；
- 微信原生小程序看课；
- 基础问答和关键词课程推荐。

本仓库与 AI-CRM、HuangYouCanAI 的代码、数据库、队列和部署完全隔离。

## 本地启动

```bash
cp .env.example .env
docker compose up --build
```

服务启动后：

- 健康检查：`http://127.0.0.1:8080/health`
- 管理后台：`http://127.0.0.1:8080/admin`

## 开发检查

```bash
python -m pytest
ruff check backend tests
```

## 正式发布

小程序正式版不使用本地的 `127.0.0.1` 接口。发布顺序固定为：

1. 将 `avatar.youcangogogo.com` 解析到业务服务器，完成 HTTPS；
2. 在服务器安全配置微信、VOD、模型、webhook 和飞书密钥；
3. 部署精确代码版本，执行数据库迁移并通过健康检查；
4. 在微信公众平台配置服务器合法域名和隐私合规信息；
5. 使用微信开发者工具上传体验版，通过真机验收后提交审核；
6. 审核通过后由小程序管理员点击发布。

详细清单见 [生产与小程序发布手册](docs/operations/release.md)。在手册的生产前置项没有全部就绪前，不要把开发者工具的“普通编译成功”视为已上线。

## 生产约束

- 当前部署目标为用户已重置的 `49.232.57.128`，生产目录 `/srv/avatar-delivery`。
- 当前机器为 2C2G，只作为首版 MVP 宿主；100 个播放租约必须在 VOD 接通后经混合压测验收，不能仅凭配置宣称达标。
- 视频上传和播放仅使用腾讯云 VOD/CDN。
- 业务服务器不接收视频文件，也不执行 FFmpeg 转码。

## 权益 webhook

外部订单系统的签名方式、载荷和幂等规则见 [课程权益 webhook 契约](docs/integrations/course-entitlement-webhook.md)。

## 腾讯云 VOD 接入

配置 `.env` 中的 `TENCENT_VOD_SECRET_ID`、`TENCENT_VOD_SECRET_KEY`、转码任务流和回调令牌。后台先创建素材记录，再由浏览器使用一次性签名直传 VOD。

VOD 普通回调地址使用：

```text
https://<domain>/api/v1/media/vod/callback?token=<TENCENT_VOD_CALLBACK_TOKEN>
```

课程发布前，服务端会检查每个课节均绑定状态为 `ready` 的视频；试听必须创建独立试听课节。
