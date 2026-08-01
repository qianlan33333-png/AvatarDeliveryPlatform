import { getCourse, getLesson } from '../../services/courses'
import {
  admitPlayback,
  getDeviceId,
  heartbeatPlayback,
  saveProgress,
} from '../../services/playback'
import type { LessonDetail } from '../../types/domain'

interface TimeUpdateEvent {
  detail: {
    currentTime: number
    duration: number
  }
}

let heartbeatTimer: number | null = null
let lastProgressSaveAt = 0
const playbackRates = [0.75, 1, 1.25, 1.5, 2]

function clearHeartbeatTimer(): void {
  if (heartbeatTimer !== null) {
    clearInterval(heartbeatTimer)
    heartbeatTimer = null
  }
}

Page({
  data: {
    lessonId: '',
    lesson: null as LessonDetail | null,
    sourceUrl: '',
    leaseId: '',
    deviceId: '',
    heartbeatSeconds: 30,
    playbackRates,
    playbackRate: 1,
    currentPosition: 0,
    previousLessonId: '',
    nextLessonId: '',
    loading: true,
    error: '',
  },

  onLoad(options: Record<string, string | undefined>) {
    this.setData({ lessonId: options.lesson_id || '' })
    void this.loadPlayer()
  },

  onShow() {
    if (this.data.leaseId) {
      this.startHeartbeat()
    }
  },

  onHide() {
    clearHeartbeatTimer()
    this.saveCurrentProgress(false)
  },

  onUnload() {
    clearHeartbeatTimer()
    this.saveCurrentProgress(false)
  },

  async loadPlayer() {
    if (!this.data.lessonId) {
      this.setData({ loading: false, error: '课节参数缺失' })
      return
    }
    this.setData({ loading: true, error: '' })
    try {
      await getApp<IAppOption>().ensureSession()
      const lesson = await getLesson(this.data.lessonId)
      if (!lesson.can_play) {
        throw new Error('视频正在处理中，请稍后再试')
      }
      const course = await getCourse(lesson.course_id)
      const playableLessons = course.lessons.filter((item) => !item.locked)
      const currentIndex = playableLessons.findIndex((item) => item.id === lesson.id)
      const admission = await admitPlayback(lesson.id, getDeviceId())
      this.setData({
        lesson,
        sourceUrl: admission.playback_url,
        leaseId: admission.lease_id,
        deviceId: getDeviceId(),
        heartbeatSeconds: admission.heartbeat_interval_seconds,
        currentPosition: lesson.position_seconds,
        previousLessonId:
          currentIndex > 0 ? playableLessons[currentIndex - 1].id : '',
        nextLessonId:
          currentIndex >= 0 && currentIndex < playableLessons.length - 1
            ? playableLessons[currentIndex + 1].id
            : '',
        loading: false,
      })
      wx.setNavigationBarTitle({ title: lesson.title })
      this.startHeartbeat()
    } catch (error: unknown) {
      this.setData({
        loading: false,
        error: error instanceof Error ? error.message : '视频加载失败',
      })
    }
  },

  startHeartbeat() {
    clearHeartbeatTimer()
    if (!this.data.leaseId || !this.data.deviceId) {
      return
    }
    heartbeatTimer = setInterval(() => {
      void heartbeatPlayback(this.data.leaseId, this.data.deviceId).catch(
        (error: unknown) => {
          clearHeartbeatTimer()
          this.setData({
            error: error instanceof Error ? error.message : '播放会话已中断',
          })
        },
      )
    }, this.data.heartbeatSeconds * 1000)
  },

  onTimeUpdate(event: TimeUpdateEvent) {
    const currentPosition = Math.max(0, event.detail.currentTime)
    this.setData({ currentPosition })
    const now = Date.now()
    if (now - lastProgressSaveAt >= 10000) {
      lastProgressSaveAt = now
      this.saveCurrentProgress(false)
    }
  },

  onEnded() {
    const duration = this.data.lesson?.duration_seconds || this.data.currentPosition
    this.setData({ currentPosition: duration })
    this.saveCurrentProgress(true)
  },

  saveCurrentProgress(completed: boolean) {
    if (!this.data.lessonId) {
      return
    }
    void saveProgress(
      this.data.lessonId,
      this.data.currentPosition,
      completed,
    ).catch(() => undefined)
  },

  switchLesson(event: WechatMiniprogram.TapEvent) {
    const lessonId = event.currentTarget.dataset.id
    if (!lessonId) {
      return
    }
    this.saveCurrentProgress(false)
    wx.redirectTo({ url: `/pages/player/index?lesson_id=${lessonId}` })
  },

  onPlaybackError() {
    wx.showToast({ title: '视频播放失败，请检查网络后重试', icon: 'none' })
  },

  changePlaybackRate(event: WechatMiniprogram.TapEvent) {
    const rawRate = Number(event.currentTarget.dataset.rate)
    if (!playbackRates.includes(rawRate)) {
      return
    }
    wx.createVideoContext('course-video', this).playbackRate(rawRate)
    this.setData({ playbackRate: rawRate })
  },
})
