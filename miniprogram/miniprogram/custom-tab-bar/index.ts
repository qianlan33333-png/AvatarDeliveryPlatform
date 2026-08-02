const ITEMS = [
  { path: '/pages/home/index', text: '首页' },
  { path: '/pages/courses/index', text: '课程' },
  { path: '/pages/profile/index', text: '我的' },
]

Component({
  data: { items: ITEMS, selected: 0 },
  lifetimes: {
    attached() { this.syncSelected() },
  },
  pageLifetimes: {
    show() { this.syncSelected() },
  },
  methods: {
    syncSelected() {
      const pages = getCurrentPages()
      const route = pages[pages.length - 1]?.route || ''
      const selected = ITEMS.findIndex((item) => item.path.slice(1) === route)
      this.setData({ selected: selected < 0 ? 0 : selected })
    },
    switchTab(event: WechatMiniprogram.TapEvent) {
      const path = event.currentTarget.dataset.path
      if (typeof path === 'string') wx.switchTab({ url: path })
    },
  },
})
