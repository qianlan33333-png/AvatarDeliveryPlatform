import { listCourses } from '../../services/courses'
import type { CourseSummary } from '../../types/domain'

Page({
  data: {
    courses: [] as CourseSummary[],
    loading: true,
    error: '',
  },

  onShow() {
    void this.loadCourses()
  },

  onPullDownRefresh() {
    void this.loadCourses().finally(() => wx.stopPullDownRefresh())
  },

  async loadCourses() {
    this.setData({ loading: true, error: '' })
    try {
      await getApp<IAppOption>().ensureSession()
      const courses = await listCourses()
      this.setData({ courses, loading: false })
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : '课程加载失败'
      this.setData({ error: message, loading: false })
    }
  },

  openCourse(event: WechatMiniprogram.TapEvent) {
    const courseId = event.currentTarget.dataset.id
    if (!courseId) {
      return
    }
    wx.navigateTo({ url: `/pages/courses/detail?id=${courseId}` })
  },
})
