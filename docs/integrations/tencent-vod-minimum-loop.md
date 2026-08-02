# 腾讯云 VOD 最小闭环配置

目标链路：后台创建素材 -> 浏览器直传 VOD -> VOD 自动生成 HLS -> 回调将素材标记为已就绪 -> 绑定试听课节 -> 小程序播放。

## 1. 开通应用并记录应用 ID

1. 在腾讯云控制台开通“云点播 VOD”。
2. 进入“应用管理”，创建或选中本项目独占的应用。
3. 记录该应用的“应用 ID（SubAppId）”。生产配置不要继续使用占位值 `0`。
4. 第一次联调可以使用 VOD 默认分发域名；正式对外前改为已备案的自定义播放域名。

## 2. 创建 720p 自适应 HLS 任务流

在“媒体处理设置”中创建自适应码流模板：

- 封装：HLS
- 视频：H.264
- 音频：AAC，64kbps
- 加密：关闭
- 不放大低分辨率源文件
- 子流：360p / 400kbps、480p / 700kbps、720p / 1200kbps

如果控制台首轮只允许快速选择 480p/720p，先用这两档跑通，再补 360p。

随后创建任务流，名称必须精确为：

```text
avatarDeliveryHLS
```

任务流中只需加入上述自适应码流模板。这个名称对应服务器的 `TENCENT_VOD_PROCEDURE`。

## 3. 创建专用 CAM 密钥

1. 在访问管理 CAM 创建仅供本项目使用的子用户，例如 `avatar-delivery-vod`。
2. 访问方式选择“编程访问”。不要使用腾讯云主账号永久密钥。
3. 首轮联调可授予预设策略 `QcloudVODFullAccess`；闭环通过后，再按 VOD 应用和实际 API 动作收窄为自定义策略。
4. 生成并安全保存 `SecretId`、`SecretKey`。`SecretKey` 只进入服务器的 `/srv/avatar-delivery/shared/runtime.env`，禁止写入 Git、小程序或浏览器代码。

## 4. 配置回调

在 VOD 应用的“回调设置”中选择普通回调，并勾选：

- 视频上传完成（`NewFileUpload`）
- 任务流状态变更（`ProcedureStateChanged`）

回调 URL：

```text
https://www.qianlan333.cloud/api/v1/media/vod/callback?token=<服务器中的 TENCENT_VOD_CALLBACK_TOKEN>
```

回调方式选择 HTTP POST。令牌只保存在腾讯云回调配置和服务器环境变量中。

## 5. 服务器环境变量

在生产服务器安全配置以下值：

```dotenv
TENCENT_VOD_SECRET_ID=<CAM SecretId>
TENCENT_VOD_SECRET_KEY=<CAM SecretKey>
TENCENT_VOD_SUB_APP_ID=<VOD 应用 ID>
TENCENT_VOD_PROCEDURE=avatarDeliveryHLS
TENCENT_VOD_STORAGE_REGION=
```

存储地域留空时使用该 VOD 应用的默认地域，避免指定了尚未启用的地域而上传失败。修改后重建 API/Worker，并确认 `/health` 中 VOD 集成状态为正常。

## 6. 后台闭环

1. 打开 `https://www.qianlan333.cloud/admin/media`。
2. 创建素材，进入上传页并选择一个短 MP4 测试文件。
3. 上传完成后等待状态变成“已就绪”。
4. 在课程管理中新建课程和独立试听课节，将该课节绑定到已就绪素材并发布。
5. 不要通过前端截断正课文件来实现试听；试听使用独立视频。

## 7. 小程序闭环

1. 服务端配置真实 `WECHAT_APP_SECRET`，否则 `wx.login` 无法换取用户身份。
2. 微信公众平台将 `https://www.qianlan333.cloud` 加入 `request` 合法域名。
3. 将测试视频实际返回的 VOD HTTPS 域名加入小程序所要求的媒体/下载域名白名单。
4. 微信开发者工具使用“普通编译”，点击“预览”，用真机进入已发布课程的试听课节。
5. 验证首帧、横屏、全屏、倍速、拖动和断点续播。

首轮只使用一个 1～3 分钟的小视频。闭环通过后再配置自定义播放域名 `video.qianlan333.cloud`、批量上传和容量压测。
