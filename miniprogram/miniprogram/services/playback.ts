import { request } from './request'
import type { PlaybackAdmission } from '../types/domain'

const DEVICE_ID_STORAGE_KEY = 'avatar_delivery_device_id'

export function getDeviceId(): string {
  const stored: unknown = wx.getStorageSync(DEVICE_ID_STORAGE_KEY)
  if (typeof stored === 'string' && stored.length >= 8) {
    return stored
  }
  const deviceId = `wx-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`
  wx.setStorageSync(DEVICE_ID_STORAGE_KEY, deviceId)
  return deviceId
}

export function admitPlayback(
  lessonId: string,
  deviceId: string,
): Promise<PlaybackAdmission> {
  return request<PlaybackAdmission>({
    path: '/playback/admit',
    method: 'POST',
    data: { lesson_id: lessonId, device_id: deviceId },
  })
}

export function heartbeatPlayback(
  leaseId: string,
  deviceId: string,
): Promise<{ ok: boolean; expires_at: string }> {
  return request({
    path: '/playback/heartbeat',
    method: 'POST',
    data: { lease_id: leaseId, device_id: deviceId },
  })
}

export function saveProgress(
  lessonId: string,
  positionSeconds: number,
  completed: boolean,
): Promise<{ position_seconds: number; completed: boolean }> {
  return request({
    path: `/learning-progress/${lessonId}`,
    method: 'PUT',
    data: {
      position_seconds: Math.max(0, Math.floor(positionSeconds)),
      completed,
    },
  })
}
