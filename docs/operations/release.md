# 生产与小程序发布手册

## 1. 发布门槛

以下条件必须同时满足：

- `www.qianlan333.cloud` 已备案并解析到 `49.232.57.128`；
- 服务器经只读检查确认不存在 AI-CRM 或 HuangYouCanAI 代码、数据库、队列和网关配置；
- HTTPS 证书有效，`https://www.qianlan333.cloud/health` 正常；
- 微信小程序 AppID 为 `wx318698f4c753111e`，AppSecret 只保存在服务器密钥环境；
- 腾讯云 VOD 已开通，自适应 HLS 任务流、回调令牌和播放域名已配置；
- 生产 `.env` 已替换所有 `replace-*` 值，且没有提交到 Git；
- 全量测试、小程序类型检查、数据库迁移回滚和镜像构建均通过。

## 2. 需要安全提供的生产配置

不要把下列值发到 GitHub Issue、PR 或小程序代码中：

- `WECHAT_APP_SECRET`
- `TENCENT_VOD_SECRET_ID` / `TENCENT_VOD_SECRET_KEY` / `TENCENT_VOD_SUB_APP_ID`
- `ENTITLEMENT_WEBHOOK_SECRET`
- `APP_SECRET_KEY` / `PHONE_ENCRYPTION_KEY` / `PHONE_LOOKUP_PEPPER`
- `LLM_ENCRYPTION_KEY`
- `ADMIN_BOOTSTRAP_PASSWORD`
- `FEISHU_ALERT_WEBHOOK`

大模型 API Key 在后台“模型配置”中录入，服务端加密保存。

## 3. 服务器发布

1. 在 `/srv/avatar-delivery/releases/<git-sha>` 解压或检出对应版本。
2. 首次部署运行 `deploy/bootstrap-runtime-env.sh /srv/avatar-delivery/shared/runtime.env <git-sha>` 生成内部密钥；密钥文件权限必须为 `600`。
3. 构建标记为精确 Git SHA 的镜像。
4. 运行 `deploy/release.sh <absolute-release-dir> <git-sha>`；脚本会先启动 PostgreSQL/Redis、执行 Alembic，再替换 API/Worker。
5. 验证 `/health`、后台登录、微信登录、课程列表、播放准入、webhook 幂等和飞书告警。
6. 只有新版本健康时才切换 Nginx；保留上一个镜像 SHA 和当前数据库备份。

当前 2C2G 服务器只是 MVP 宿主。视频流量由 VOD/CDN 承担，但“100 人并发”仍必须通过 125 播放租约 + 15 AI 流 + 后台 + webhook 的混合压测后才能宣称达标。

## 4. 微信公众平台配置

登录微信公众平台小程序后：

1. 在开发管理中确认 AppID 和开发者权限。
2. 将 `https://www.qianlan333.cloud` 加入 `request` 合法域名。
3. 按实际 VOD/CDN 播放链接，将其 HTTPS 域名加入平台要求的媒体或下载域名白名单。
4. 配置服务类目、小程序名称、图标、简介、客服方式、用户隐私保护指引和小程序备案。
5. 在隐私保护指引中如实声明手机号、学习进度、对话内容和必要设备标识的处理目的。

## 5. 小程序上传、体验、审核与发布

1. 微信开发者工具保持右上角“普通编译”，先点“预览”做一次真机检查。
2. 点右上角“上传”，版本号按 `1.0.0`、`1.0.1` 递增，项目备注写清当次功能和对应 Git SHA。
3. 进入微信公众平台的版本管理，先将该开发版设为体验版。
4. 用 iOS 和 Android 真机验证：微信登录、手机号授权、未购锁定、试听、正课播放、横屏、倍速、拖动、断点续播、切换前后台、弱网、AI 问答和课程推荐。
5. 准备一个审核可用的测试课程及试听路径，在提审说明里说清如何进入。
6. 提交审核；审核通过后点击“发布”。正式发布前不要下架审核用课程或撤销测试权益。

## 6. 发布后观察

- 首批先开放 10 人，然后依次放量到 30、60、100 人，每档观察至少 30 分钟。
- 核心观察项：API p95、5xx、CPU、内存、播放租约数、AI 并发、VOD 首帧和拖动恢复。
- 80 人预警、90 人高优告警、100 人拒绝新播放；已经进入的有效租约仍允许续约。
- 异常时回滚到上一个镜像 SHA，不回退已提交的权益事件。
