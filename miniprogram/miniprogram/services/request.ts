import { runtimeConfig } from '../config/env'
import type { RequestOptions } from '../types/api'
import { clearAccessToken, getAccessToken } from './session'

export class ApiRequestError extends Error {
  readonly statusCode: number
  readonly payload: unknown

  constructor(message: string, statusCode = 0, payload?: unknown) {
    super(message)
    this.name = 'ApiRequestError'
    this.statusCode = statusCode
    this.payload = payload
  }
}

function buildUrl(path: string): string {
  const normalizedPath = path.startsWith('/') ? path : `/${path}`
  return `${runtimeConfig.apiBaseUrl}${normalizedPath}`
}

function getErrorMessage(payload: unknown): string {
  if (typeof payload !== 'object' || payload === null || !('detail' in payload)) {
    return '请求失败，请稍后重试'
  }

  const detail = (payload as { detail?: unknown }).detail
  if (typeof detail === 'string' && detail.length > 0) {
    return detail
  }
  if (typeof detail === 'object' && detail !== null && 'message' in detail) {
    const message = (detail as { message?: unknown }).message
    if (typeof message === 'string' && message.length > 0) {
      return message
    }
  }
  if (typeof detail === 'object' && detail !== null && 'code' in detail) {
    const code = (detail as { code?: unknown }).code
    if (code === 'capability_locked') {
      return '该能力尚未开通，请联系运营人员'
    }
    if (code === 'ai_capacity_full') {
      return '当前使用人数较多，请稍后再试'
    }
  }
  return '请求失败，请稍后重试'
}

export function request<TResponse>(options: RequestOptions): Promise<TResponse> {
  const headers: Record<string, string> = {
    'content-type': 'application/json',
    ...options.headers,
  }
  const accessToken = options.auth === false ? null : getAccessToken()

  if (accessToken !== null) {
    headers.Authorization = `Bearer ${accessToken}`
  }

  return new Promise<TResponse>((resolve, reject) => {
    wx.request({
      url: buildUrl(options.path),
      method: options.method === undefined ? 'GET' : options.method,
      data: options.data,
      header: headers,
      timeout:
        options.timeoutMs === undefined
          ? runtimeConfig.requestTimeoutMs
          : options.timeoutMs,
      success: (response) => {
        if (response.statusCode >= 200 && response.statusCode < 300) {
          resolve(response.data as TResponse)
          return
        }

        if (response.statusCode === 401) {
          clearAccessToken()
        }

        reject(
          new ApiRequestError(
            getErrorMessage(response.data),
            response.statusCode,
            response.data,
          ),
        )
      },
      fail: (error) => {
        reject(new ApiRequestError(error.errMsg || '网络连接失败'))
      },
    })
  })
}
