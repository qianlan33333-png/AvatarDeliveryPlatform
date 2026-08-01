# 课程权益 webhook 契约

## 请求

```text
POST /api/v1/webhooks/course-entitlements
Content-Type: application/json
X-Avatar-Timestamp: <Unix 秒>
X-Avatar-Nonce: <每次投递唯一的随机字符串>
X-Avatar-Signature: <hex HMAC-SHA256>
```

签名原文为：

```text
<timestamp>\n<nonce>\n<原始 HTTP body 字节>
```

使用 `ENTITLEMENT_WEBHOOK_SECRET` 计算 HMAC-SHA256，结果以小写十六进制放入 `X-Avatar-Signature`。不要对 JSON 重新排序后再计算；签名的必须是实际发送的原始 body。

## 载荷

```json
{
  "event_id": "evt_20260801_0001",
  "order_id": "order_10001",
  "product_code": "COURSE-001",
  "phone": "13800138000",
  "action": "grant",
  "effective_at": "2026-08-01T12:00:00+08:00",
  "expires_at": "2027-08-01T12:00:00+08:00"
}
```

`action` 可选：

- `grant`：首次开通；
- `renew`：续期，有效期按载荷更新；
- `revoke`：退款或人工撤销。

一个 `product_code` 可在后台映射到一门或多门课程。用户尚未绑定手机号时，系统会先记录待认领权益；后续绑定同一手机号时在数据库事务内认领。

## 幂等与重试

- 同一 `event_id` 和同一载荷重复投递：返回成功，不重复开通。
- 同一 `event_id` 但载荷不同：返回 `409 Conflict` 并发送告警。
- 时间戳超出允许偏差、签名错误或 nonce 重放：拒绝请求。
- 商品码未配置或业务失败：事件保留在后台，运营可在修正映射后人工重放。

调用方对超时或 5xx 使用指数退避重试，不要在未收到响应时更换 `event_id`。
