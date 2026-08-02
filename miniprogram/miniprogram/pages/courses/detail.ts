import { getCourse } from '../../services/courses'
import type { CourseDetail, LessonSummary } from '../../types/domain'
import {
  courseLearningText,
  filterIncompleteLessons,
  normalizeCourseProgress,
} from '../../utils/course-progress'

function formatDuration(seconds: number): string {
  if (seconds <= 0) {
    return '时长待更新'
  }
  const minutes = Math.floor(seconds / 60)
  const remainder = seconds % 60
  if (minutes === 0) {
    return `${remainder}秒`
  }
  return `${minutes}分${remainder.toString().padStart(2, '0')}秒`
}

type DisplayLesson = LessonSummary & {
  duration_text: string
  lesson_number: number
  study_text: string
}

type DisplayCourse = Omit<CourseDetail, 'lessons'> & {
  lessons: DisplayLesson[]
  learning_text: string
}

function buildDisplayLessons(lessons: LessonSummary[]): DisplayLesson[] {
  return lessons.map((lesson, index) => {
    const durationText = formatDuration(lesson.duration_seconds)
    let studyText = `未学习 · ${durationText}`
    if (lesson.locked) {
      studyText = '未开通'
    } else if (lesson.completed) {
      studyText = `已学完 · ${durationText}`
    } else if (lesson.position_seconds > 0) {
      if (lesson.duration_seconds > 0) {
        const watchedPercent = Math.min(
          99,
          Math.max(1, Math.floor(lesson.position_seconds * 100 / lesson.duration_seconds)),
        )
        studyText = `已学 ${watchedPercent}% · ${durationText}`
      } else {
        studyText = `学习中 · ${durationText}`
      }
    }
    return {
      ...lesson,
      duration_text: durationText,
      lesson_number: index + 1,
      study_text: studyText,
    }
  })
}

Page({
  data: {
    courseId: '',
    course: null as DisplayCourse | null,
    visibleLessons: [] as DisplayLesson[],
    showIncompleteOnly: false,
    loadedOnce: false,
    loading: true,
    error: '',
  },

  onLoad(options: Record<string, string | undefined>) {
    const courseId = options.id || ''
    this.setData({ courseId })
    void this.loadCourse()
  },

  onShow() {
    if (this.data.loadedOnce) {
      void this.loadCourse()
    }
  },

  async loadCourse() {
    if (!this.data.courseId) {
      this.setData({ loading: false, error: '课程参数缺失' })
      return
    }
    this.setData({
      loading: !this.data.course,
      error: '',
    })
    try {
      await getApp<IAppOption>().ensureSession()
      const course = await getCourse(this.data.courseId)
      const lessons = buildDisplayLessons(course.lessons)
      const normalizedCourse = normalizeCourseProgress(course, lessons)
      const displayCourse: DisplayCourse = {
        ...normalizedCourse,
        lessons,
        learning_text: courseLearningText(normalizedCourse),
      }
      this.setData({
        course: displayCourse,
        visibleLessons: filterIncompleteLessons(
          lessons,
          this.data.showIncompleteOnly,
        ),
        loadedOnce: true,
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

  toggleIncomplete(event: { detail: { value: boolean } }) {
    const showIncompleteOnly = event.detail.value
    const lessons = this.data.course?.lessons || []
    this.setData({
      showIncompleteOnly,
      visibleLessons: filterIncompleteLessons(lessons, showIncompleteOnly),
    })
  },

  showAllLessons() {
    const lessons = this.data.course?.lessons || []
    this.setData({
      showIncompleteOnly: false,
      visibleLessons: lessons,
    })
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
