import { runtimeConfig } from '../config/env'
import { getAccessToken } from './session'
import { request } from './request'

export interface RecommendedCourseCard {
  id: string
  title: string
  subtitle: string
  description: string
  cover_url: string
  locked: boolean
}

export interface ChatStreamEvent {
  event: 'ready' | 'delta' | 'recommendations' | 'done' | 'error'
  data: Record<string, unknown>
}

export interface ChatStreamCallbacks {
  onEvent(event: ChatStreamEvent): void
  onError(message: string): void
  onComplete(): void
}

interface ChunkResult {
  data: ArrayBuffer
}

interface ChunkedRequestTask {
  abort(): void
  onChunkReceived(callback: (result: ChunkResult) => void): void
}

interface ChunkedResponse {
  statusCode: number
  data: unknown
}

interface ChunkedRequestOptions {
  url: string
  method: 'GET'
  header: Record<string, string>
  enableChunked: boolean
  timeout: number
  success(response: ChunkedResponse): void
  fail(error: { errMsg?: string }): void
}

class Utf8StreamDecoder {
  private pending: number[] = []

  decode(buffer: ArrayBuffer, flush = false): string {
    const incoming = Array.from(new Uint8Array(buffer))
    const bytes = this.pending.concat(incoming)
    let output = ''
    let index = 0
    while (index < bytes.length) {
      const first = bytes[index]
      let needed = 1
      let codePoint = first
      if (first >= 0xc2 && first <= 0xdf) {
        needed = 2
        codePoint = first & 0x1f
      } else if (first >= 0xe0 && first <= 0xef) {
        needed = 3
        codePoint = first & 0x0f
      } else if (first >= 0xf0 && first <= 0xf4) {
        needed = 4
        codePoint = first & 0x07
      } else if (first >= 0x80) {
        output += '\ufffd'
        index += 1
        continue
      }
      if (index + needed > bytes.length) {
        break
      }
      let valid = true
      for (let offset = 1; offset < needed; offset += 1) {
        const continuation = bytes[index + offset]
        if ((continuation & 0xc0) !== 0x80) {
          valid = false
          break
        }
        codePoint = (codePoint << 6) | (continuation & 0x3f)
      }
      if (!valid) {
        output += '\ufffd'
        index += 1
        continue
      }
      output += String.fromCodePoint(codePoint)
      index += needed
    }
    this.pending = bytes.slice(index)
    if (flush && this.pending.length) {
      output += '\ufffd'
      this.pending = []
    }
    return output
  }
}

class SSEParser {
  private buffer = ''

  constructor(private readonly callback: (event: ChatStreamEvent) => void) {}

  push(chunk: string, flush = false): void {
    this.buffer += chunk.replace(/\r\n/g, '\n')
    if (flush && this.buffer && !this.buffer.endsWith('\n\n')) {
      this.buffer += '\n\n'
    }
    let boundary = this.buffer.indexOf('\n\n')
    while (boundary >= 0) {
      const block = this.buffer.slice(0, boundary)
      this.buffer = this.buffer.slice(boundary + 2)
      this.parseBlock(block)
      boundary = this.buffer.indexOf('\n\n')
    }
  }

  private parseBlock(block: string): void {
    let eventName = 'delta'
    const dataLines: string[] = []
    for (const line of block.split('\n')) {
      if (line.startsWith('event:')) {
        eventName = line.slice(6).trim()
      } else if (line.startsWith('data:')) {
        dataLines.push(line.slice(5).trim())
      }
    }
    if (!dataLines.length) {
      return
    }
    try {
      const data = JSON.parse(dataLines.join('\n')) as Record<string, unknown>
      if (
        eventName === 'ready' ||
        eventName === 'delta' ||
        eventName === 'recommendations' ||
        eventName === 'done' ||
        eventName === 'error'
      ) {
        this.callback({ event: eventName, data })
      }
    } catch {
      return
    }
  }
}

export async function reserveChat(
  prompt: string,
  conversationId: string,
): Promise<{ ticket: string; expires_at: string; candidate_count: number }> {
  return request({
    path: '/chat/reservations',
    method: 'POST',
    data: {
      prompt,
      conversation_id: conversationId || null,
    },
  })
}

export function streamChat(
  ticket: string,
  callbacks: ChatStreamCallbacks,
): ChunkedRequestTask | null {
  const accessToken = getAccessToken()
  if (!accessToken) {
    callbacks.onError('登录状态已失效，请重新进入')
    return null
  }
  const decoder = new Utf8StreamDecoder()
  const parser = new SSEParser(callbacks.onEvent)
  let receivedChunk = false
  const chunkedRequest = wx.request as unknown as (
    options: ChunkedRequestOptions,
  ) => ChunkedRequestTask
  const task = chunkedRequest({
    url: `${runtimeConfig.apiBaseUrl}/chat/stream/${encodeURIComponent(ticket)}`,
    method: 'GET',
    header: { Authorization: `Bearer ${accessToken}` },
    enableChunked: true,
    timeout: 60000,
    success: (response) => {
      if (response.statusCode < 200 || response.statusCode >= 300) {
        callbacks.onError('问答请求失败，请稍后重试')
        return
      }
      if (!receivedChunk && typeof response.data === 'string') {
        parser.push(response.data, true)
      } else {
        parser.push(decoder.decode(new ArrayBuffer(0), true), true)
      }
      callbacks.onComplete()
    },
    fail: (error) => callbacks.onError(error.errMsg || '问答连接中断'),
  })
  task.onChunkReceived((result) => {
    receivedChunk = true
    parser.push(decoder.decode(result.data))
  })
  return task
}
