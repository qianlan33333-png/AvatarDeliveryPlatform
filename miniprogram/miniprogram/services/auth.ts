import { request } from './request'
import { setAccessToken } from './session'
import type { AuthResponse } from '../types/domain'

let loginPromise: Promise<AuthResponse> | null = null

function getLoginCode(): Promise<string> {
  return new Promise((resolve, reject) => {
    wx.login({
      success: (result) => {
        if (result.code) {
          resolve(result.code)
          return
        }
        reject(new Error('微信登录未返回临时凭证'))
      },
      fail: (error) => reject(new Error(error.errMsg || '微信登录失败')),
    })
  })
}

export function loginWithWeChat(): Promise<AuthResponse> {
  if (loginPromise !== null) {
    return loginPromise
  }
  const pending = getLoginCode()
    .then((code) =>
      request<AuthResponse>({
        path: '/auth/wechat/login',
        method: 'POST',
        data: { code },
        auth: false,
      }),
    )
    .then((response) => {
      setAccessToken(response.access_token)
      return response
    })
  loginPromise = pending
  void pending.then(
    () => {
      loginPromise = null
    },
    () => {
      loginPromise = null
    },
  )
  return pending
}

export async function bindPhone(code: string): Promise<AuthResponse> {
  const response = await request<AuthResponse>({
    path: '/auth/phone/bind',
    method: 'POST',
    data: { code },
  })
  setAccessToken(response.access_token)
  return response
}
