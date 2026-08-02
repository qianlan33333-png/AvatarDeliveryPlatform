import { bindPhone } from '../../services/auth'
import { getProfile } from '../../services/courses'
import type { UserProfile } from '../../types/domain'

interface CourseRightRow { course_id: string; period: string }
interface CapabilityRightRow { code: string; title: string; status: string; period: string }

function dateText(value: string | null): string {
  return value ? value.slice(0, 10).replace(/-/g, '.') : ''
}

function capabilityStatus(value: string): string {
  return ({ active: '有效', upcoming: '待生效', expired: '已到期', revoked: '已撤销', not_granted: '未开通' } as Record<string, string>)[value] || value
}

interface PhoneNumberEvent {
  detail: {
    code?: string
    errMsg?: string
  }
}

Page({
  data: {
    profile: null as UserProfile | null,
    courseRights: [] as CourseRightRow[],
    capabilityRights: [] as CapabilityRightRow[],
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
      this.setData({
        profile,
        courseRights: profile.entitlements.map((item) => ({
          course_id: item.course_id,
          period: item.expires_at ? `有效至 ${dateText(item.expires_at)}` : '长期有效',
        })),
        capabilityRights: profile.capabilities.map((item) => ({
          code: item.code,
          title: item.code === 'chat_qa' ? '聊一聊' : '帮我写话术',
          status: capabilityStatus(item.status),
          period: item.expires_at ? `有效至 ${dateText(item.expires_at)}` : (item.status === 'active' ? '长期有效' : '尚未开通'),
        })),
        loading: false,
      })
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
