export interface AuthUser {
  id: string
  phone_bound: boolean
  phone_last4: string
}

export type CapabilityCode = 'chat_qa' | 'copywriting'

export type CapabilityStatus =
  | 'active'
  | 'upcoming'
  | 'expired'
  | 'revoked'
  | 'not_granted'

export interface CapabilityEntitlement {
  code: CapabilityCode
  status: CapabilityStatus
  effective_at: string | null
  expires_at: string | null
}

export interface AuthResponse {
  access_token: string
  token_type: 'bearer'
  claimed_entitlements?: number
  claimed_capabilities?: number
  user: AuthUser
}

export interface CourseSummary {
  id: string
  title: string
  subtitle: string
  description: string
  cover_url: string
  keywords: string[]
  lesson_count: number
  preview_lesson_count: number
  has_access: boolean
  locked: boolean
}

export interface LessonSummary {
  id: string
  title: string
  description: string
  sort_order: number
  is_preview: boolean
  locked: boolean
  duration_seconds: number
  position_seconds: number
  completed: boolean
}

export interface CourseDetail extends CourseSummary {
  lessons: LessonSummary[]
}

export interface LessonDetail {
  id: string
  course_id: string
  title: string
  description: string
  is_preview: boolean
  duration_seconds: number
  position_seconds: number
  completed: boolean
  can_play: boolean
}

export interface PlaybackAdmission {
  lease_id: string
  playback_url: string
  expires_at: string
  heartbeat_interval_seconds: number
  online_count: number
}

export interface UserProfile {
  id: string
  nickname: string
  avatar_url: string
  phone_bound: boolean
  phone_last4: string
  active_course_count: number
  learning_lesson_count: number
  entitlements: Array<{
    course_id: string
    effective_at: string
    expires_at: string | null
  }>
  capabilities: CapabilityEntitlement[]
}
