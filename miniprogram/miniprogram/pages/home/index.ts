import { getProfile } from '../../services/courses'
import type {
  CapabilityCode,
  CapabilityEntitlement,
} from '../../types/domain'

interface AiEntryDefinition {
  code: CapabilityCode
  mark: string
  title: string
  description: string
  route: string
  tone: 'light' | 'yellow'
}

interface AiEntry extends AiEntryDefinition {
  active: boolean
  statusLabel: string
  periodLabel: string
  actionLabel: string
}

const ENTRY_DEFINITIONS: AiEntryDefinition[] = [
  {
    code: 'chat_qa',
    mark: '聊',
    title: '聊一聊',
    description: '问问题、理思路，也可以找到更适合你的课程。',
    route: '/pages/chat/index',
    tone: 'light',
  },
  {
    code: 'copywriting',
    mark: '写',
    title: '帮我写话术',
    description: '说清对象、渠道和目标，连续调整到可以直接使用。',
    route: '/pages/copywriting/index',
    tone: 'yellow',
  },
]

function displayDate(value: string | null): string {
  if (!value) {
    return ''
  }
  return value.slice(0, 10).replace(/-/g, '.')
}

function capabilityPeriod(capability?: CapabilityEntitlement): string {
  if (!capability) {
    return '尚未开通'
  }
  const effectiveAt = displayDate(capability.effective_at)
  const expiresAt = displayDate(capability.expires_at)
  if (effectiveAt && expiresAt) {
    return `有效期 ${effectiveAt} - ${expiresAt}`
  }
  if (expiresAt) {
    return `有效至 ${expiresAt}`
  }
  if (effectiveAt) {
    return capability.status === 'upcoming'
      ? `${effectiveAt} 生效`
      : `${effectiveAt} 起长期有效`
  }
  return capability.status === 'active' ? '长期有效' : '尚未开通'
}

function statusLabel(capability?: CapabilityEntitlement): string {
  if (!capability) {
    return '未开通'
  }
  switch (capability.status) {
    case 'active':
      return '已开通'
    case 'upcoming':
      return '即将生效'
    case 'expired':
      return '已到期'
    case 'revoked':
      return '已撤销'
    case 'not_granted':
      return '未开通'
  }
}

function buildEntries(capabilities: CapabilityEntitlement[] = []): AiEntry[] {
  return ENTRY_DEFINITIONS.map((definition) => {
    const capability = capabilities.find((item) => item.code === definition.code)
    const active = capability?.status === 'active'
    return {
      ...definition,
      active,
      statusLabel: statusLabel(capability),
      periodLabel: capabilityPeriod(capability),
      actionLabel: active ? '进入对话' : '联系运营开通',
    }
  })
}

Page({
  data: {
    entries: buildEntries(),
    loading: true,
    error: '',
  },

  onShow() {
    void this.loadCapabilities()
  },

  onPullDownRefresh() {
    void this.loadCapabilities().finally(() => wx.stopPullDownRefresh())
  },

  async loadCapabilities() {
    this.setData({ loading: true, error: '' })
    try {
      await getApp<IAppOption>().ensureSession()
      const profile = await getProfile()
      this.setData({
        entries: buildEntries(profile.capabilities || []),
        loading: false,
      })
    } catch (error: unknown) {
      this.setData({
        entries: buildEntries(),
        loading: false,
        error:
          error instanceof Error ? error.message : '权益状态暂时无法加载',
      })
    }
  },

  openCapability(event: WechatMiniprogram.TapEvent) {
    if (this.data.loading) {
      wx.showToast({ title: '正在确认权益', icon: 'none' })
      return
    }
    const code = event.currentTarget.dataset.code as CapabilityCode | undefined
    const entry = this.data.entries.find((item) => item.code === code)
    if (!entry) {
      return
    }
    if (!entry.active) {
      wx.showModal({
        title: `${entry.title}尚未开通`,
        content: '请联系运营人员开通对应会员，开通后即可使用。',
        showCancel: false,
        confirmText: '我知道了',
      })
      return
    }
    wx.navigateTo({ url: entry.route })
  },
})
