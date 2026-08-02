# 微信小程序本地开发

## 打开工程

微信开发者工具选择当前 `miniprogram/` 目录。真正的小程序源码位于
`miniprogram/miniprogram/`，这是由 `project.config.json` 中的
`miniprogramRoot` 指定的官方目录结构。

## 本地联调

1. 在仓库根目录启动后端：`docker compose up --build`。
2. 开发版 API 使用 `http://127.0.0.1:8080/api/v1`。
3. 当前电脑的 `project.private.config.json` 已关闭合法域名校验，仅供开发者工具本地联调；该文件不会提交 Git。
4. 真机调试不能访问电脑的 `127.0.0.1`，接入真机链路时应改用已备案 HTTPS 域名或同网段开发地址。

## 环境与密钥

- 正式 AppID 保存在 `project.config.json`。
- AppSecret、VOD SecretKey、模型 API Key 等服务端密钥禁止写入小程序或提交 Git。
- 体验版和正式版 API 均使用 `https://www.qianlan333.cloud/api/v1`。

## 校验

```bash
npm install
npm run typecheck
```
