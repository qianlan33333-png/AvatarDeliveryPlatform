import type { CourseSummary, LessonSummary } from '../types/domain'

function finiteNonNegative(value: number | undefined): number | null {
  return Number.isFinite(value) && (value as number) >= 0 ? (value as number) : null
}

export function normalizeCourseProgress<T extends CourseSummary>(
  course: T,
  lessons: LessonSummary[] = [],
): T {
  const lessonCount = finiteNonNegative(course.lesson_count) ?? lessons.length
  const inferredCompletedCount = lessons.filter((lesson) => lesson.completed).length
  const completedLessonCount = Math.min(
    lessonCount,
    finiteNonNegative(course.completed_lesson_count) ?? inferredCompletedCount,
  )
  const inferredPercent = lessonCount > 0
    ? Math.floor(completedLessonCount * 100 / lessonCount)
    : 0
  const progressPercent = Math.min(
    100,
    finiteNonNegative(course.progress_percent) ?? inferredPercent,
  )
  const inferredCurrentIndex = lessons.reduce(
    (latestIndex, lesson, index) => (
      lesson.completed || lesson.position_seconds > 0 ? index : latestIndex
    ),
    -1,
  )

  return {
    ...course,
    lesson_count: lessonCount,
    completed_lesson_count: completedLessonCount,
    progress_percent: progressPercent,
    current_lesson_id:
      course.current_lesson_id ?? lessons[inferredCurrentIndex]?.id ?? null,
    current_lesson_title:
      course.current_lesson_title || lessons[inferredCurrentIndex]?.title || '',
    current_lesson_number:
      course.current_lesson_number ?? (inferredCurrentIndex >= 0 ? inferredCurrentIndex + 1 : null),
    has_started:
      course.has_started ?? lessons.some(
        (lesson) => lesson.completed || lesson.position_seconds > 0,
      ),
  }
}

export function courseLearningText(course: CourseSummary): string {
  if (course.lesson_count === 0) {
    return '暂未开放课节'
  }
  if (course.progress_percent >= 100) {
    return '已全部学完'
  }
  if (
    course.has_started
    && course.current_lesson_number
    && course.current_lesson_title
  ) {
    return `学到：第 ${course.current_lesson_number} 讲 · ${course.current_lesson_title}`
  }
  if (!course.has_access && course.preview_lesson_count > 0) {
    return '尚未开始，可先观看试听课节'
  }
  return '尚未开始学习'
}

export function filterIncompleteLessons<T extends Pick<LessonSummary, 'completed'>>(
  lessons: T[],
  incompleteOnly: boolean,
): T[] {
  return incompleteOnly ? lessons.filter((lesson) => !lesson.completed) : lessons
}
