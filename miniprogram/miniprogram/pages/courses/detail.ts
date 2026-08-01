import { getCourse } from '../../services/courses'
import type { CourseDetail, LessonSummary } from '../../types/domain'

function formatDuration(seconds: number): string {
  if (seconds <= 0) {
    return '--:--'
  }
  const minutes = Math.floor(seconds / 60)
  const remainder = seconds % 60
  return `${minutes}:${remainder.toString().padStart(2, '0')}`
}

type DisplayLesson = LessonSummary & { duration_text: string }
type DisplayCourse = Omit<CourseDetail, 'lessons'> & { lessons: DisplayLesson[] }

Page({
  data: {
    courseId: '',
    course: null as DisplayCourse | null,
    loading: true,
    error: '',
  },

  onLoad(options: Record<string, string | undefined>) {
    const courseId = options.id || ''
    this.setData({ courseId })
    void this.loadCourse()
  },

  async loadCourse() {
    if (!this.data.courseId) {
      this.setData({ loading: false, error: '课程参数缺失' })
      return
    }
    this.setData({ loading: true, error: '' })
    try {
      await getApp<IAppOption>().ensureSession()
      const course = await getCourse(this.data.courseId)
      this.setData({
        course: {
          ...course,
          lessons: course.lessons.map((lesson) => ({
            ...lesson,
            duration_text: formatDuration(lesson.duration_seconds),
          })),
        },
        loading: false,
      })
      wx.setNavigationBarTitle({ title: course.title })
    } catch (error: unknown) {
      this.setData({
        loading: false,
        error: error instanceof Error ? error.message : '课程加载失败',
      })
    }
  },

  openLesson(event: WechatMiniprogram.TapEvent) {
    const lessonId = event.currentTarget.dataset.id
    const course = this.data.course
    if (!lessonId || !course) {
      return
    }
    const lesson = course.lessons.find((item) => item.id === lessonId)
    if (!lesson) {
      return
    }
    if (lesson.locked) {
      wx.showModal({
        title: '该课节尚未开通',
        content: '你可以先观看试听课节。开通课程后，完整课节会自动解锁。',
        confirmText: '我知道了',
        showCancel: false,
      })
      return
    }
    wx.navigateTo({ url: `/pages/player/index?lesson_id=${lesson.id}` })
  },
})
