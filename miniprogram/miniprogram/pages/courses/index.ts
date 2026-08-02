import { listCourses } from '../../services/courses'
import type { CourseSummary } from '../../types/domain'
import {
  courseLearningText,
  normalizeCourseProgress,
} from '../../utils/course-progress'

type DisplayCourse = CourseSummary & {
  learning_text: string
  progress_text: string
}

function buildDisplayCourse(course: CourseSummary): DisplayCourse {
  const normalizedCourse = normalizeCourseProgress(course)
  const progressText = normalizedCourse.has_access || normalizedCourse.has_started
    ? `已学 ${normalizedCourse.progress_percent}%`
    : normalizedCourse.preview_lesson_count > 0
      ? `${normalizedCourse.preview_lesson_count} 讲试听`
      : '查看课程介绍'
  return {
    ...normalizedCourse,
    learning_text: courseLearningText(normalizedCourse),
    progress_text: progressText,
  }
}

Page({
  data: {
    courses: [] as DisplayCourse[],
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
      this.setData({
        courses: courses.map(buildDisplayCourse),
        loading: false,
      })
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
