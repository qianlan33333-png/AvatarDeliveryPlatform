Page({
  data: {
    draft: '',
    maxLength: 500,
  },

  onDraftInput(event: WechatMiniprogram.InputEvent) {
    this.setData({
      draft: event.detail.value,
    })
  },

  onSend() {
    if (this.data.draft.trim().length === 0) {
      wx.showToast({
        title: '先输入你想问的问题',
        icon: 'none',
      })
      return
    }

    wx.showToast({
      title: '问答接口将在下一批接入',
      icon: 'none',
    })
  },
})
