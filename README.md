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

## 生产约束

- 生产目标为独立 4C8G 腾讯云服务器，目录 `/srv/avatar-delivery`。
- 视频上传和播放仅使用腾讯云 VOD/CDN。
- 禁止部署到 `49.232.57.128`。

