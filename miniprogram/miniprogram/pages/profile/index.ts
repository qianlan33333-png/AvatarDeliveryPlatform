import { bindPhone } from '../../services/auth'
import { getProfile } from '../../services/courses'
import type { UserProfile } from '../../types/domain'

interface PhoneNumberEvent {
  detail: {
    code?: string
    errMsg?: string
  }
}

Page({
  data: {
    profile: null as UserProfile | null,
    loading: true,
    bindingPhone: false,
    error: '',
  },

  onShow() {
    void this.loadProfile()
  },

  onPullDownRefresh() {
    void this.loadProfile().finally(() => wx.stopPullDownRefresh())
  },

  async loadProfile() {
    this.setData({ loading: true, error: '' })
    try {
      await getApp<IAppOption>().ensureSession()
      const profile = await getProfile()
      this.setData({ profile, loading: false })
    } catch (error: unknown) {
      this.setData({
        loading: false,
        error: error instanceof Error ? error.message : '用户信息加载失败',
      })
    }
  },

  async onGetPhoneNumber(event: PhoneNumberEvent) {
    const code = event.detail.code
    if (!code) {
      wx.showToast({ title: '未获得手机号授权', icon: 'none' })
      return
    }
    this.setData({ bindingPhone: true })
    try {
      const response = await bindPhone(code)
      wx.showToast({
        title: response.claimed_entitlements
          ? `已认领 ${response.claimed_entitlements} 门课程`
          : '手机号绑定成功',
        icon: 'none',
      })
      await this.loadProfile()
    } catch (error: unknown) {
      wx.showToast({
        title: error instanceof Error ? error.message : '手机号绑定失败',
        icon: 'none',
      })
    } finally {
      this.setData({ bindingPhone: false })
    }
  },
})
