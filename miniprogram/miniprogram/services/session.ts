const ACCESS_TOKEN_STORAGE_KEY = 'avatar_delivery_access_token'

export function getAccessToken(): string | null {
  const value: unknown = wx.getStorageSync(ACCESS_TOKEN_STORAGE_KEY)
  return typeof value === 'string' && value.length > 0 ? value : null
}

export function setAccessToken(accessToken: string): void {
  wx.setStorageSync(ACCESS_TOKEN_STORAGE_KEY, accessToken)
}

export function clearAccessToken(): void {
  wx.removeStorageSync(ACCESS_TOKEN_STORAGE_KEY)
}
