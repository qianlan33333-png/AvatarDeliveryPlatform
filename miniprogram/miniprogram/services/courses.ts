import { request } from './request'
import type {
  CourseDetail,
  CourseSummary,
  LessonDetail,
  UserProfile,
} from '../types/domain'

export async function listCourses(): Promise<CourseSummary[]> {
  const response = await request<{ items: CourseSummary[] }>({ path: '/courses' })
  return response.items
}

export function getCourse(courseId: string): Promise<CourseDetail> {
  return request<CourseDetail>({ path: `/courses/${courseId}` })
}

export function getLesson(lessonId: string): Promise<LessonDetail> {
  return request<LessonDetail>({ path: `/lessons/${lessonId}` })
}

export function getProfile(): Promise<UserProfile> {
  return request<UserProfile>({ path: '/me' })
}
