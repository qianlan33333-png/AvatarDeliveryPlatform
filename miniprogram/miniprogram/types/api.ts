export type HttpMethod = 'GET' | 'POST' | 'PUT' | 'DELETE'

export type RequestPayload =
  | string
  | WechatMiniprogram.IAnyObject
  | ArrayBuffer

export interface RequestOptions {
  path: string
  method?: HttpMethod
  data?: RequestPayload
  headers?: Record<string, string>
  auth?: boolean
  timeoutMs?: number
}
