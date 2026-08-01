import {
  reserveChat,
  streamChat,
  type ChatStreamEvent,
  type RecommendedCourseCard,
} from '../../services/chat'

interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  cards: RecommendedCourseCard[]
}

let activeStream: { abort(): void } | null = null

Page({
  data: {
    draft: '',
    maxLength: 500,
    sending: false,
    conversationId: '',
    scrollIntoView: 'message-welcome',
    messages: [
      {
        id: 'welcome',
        role: 'assistant',
        content: '你好，我可以回答基础问题，也可以根据你的需求推荐已经发布的课程。',
        cards: [],
      },
    ] as ChatMessage[],
  },

  onUnload() {
    activeStream?.abort()
    activeStream = null
  },

  onDraftInput(event: WechatMiniprogram.InputEvent) {
    this.setData({ draft: event.detail.value })
  },

  choosePrompt(event: WechatMiniprogram.TapEvent) {
    const prompt = event.currentTarget.dataset.prompt
    if (prompt) {
      this.setData({ draft: prompt })
    }
  },

  async onSend() {
    const prompt = this.data.draft.trim()
    if (!prompt || this.data.sending) {
      if (!prompt) {
        wx.showToast({ title: '先输入你想问的问题', icon: 'none' })
      }
      return
    }
    const sequence = Date.now().toString()
    const assistantId = `assistant-${sequence}`
    const messages: ChatMessage[] = [
      ...this.data.messages,
      { id: `user-${sequence}`, role: 'user', content: prompt, cards: [] },
      { id: assistantId, role: 'assistant', content: '', cards: [] },
    ]
    this.setData({
      messages,
      draft: '',
      sending: true,
      scrollIntoView: `message-${assistantId}`,
    })
    try {
      await getApp<IAppOption>().ensureSession()
      const reservation = await reserveChat(prompt, this.data.conversationId)
      activeStream = streamChat(reservation.ticket, {
        onEvent: (event) => this.handleStreamEvent(assistantId, event),
        onError: (message) => this.finishWithError(assistantId, message),
        onComplete: () => {
          if (this.data.sending) {
            this.setData({ sending: false })
          }
          activeStream = null
        },
      })
    } catch (error: unknown) {
      this.finishWithError(
        assistantId,
        error instanceof Error ? error.message : '问答暂时不可用',
      )
    }
  },

  handleStreamEvent(assistantId: string, event: ChatStreamEvent) {
    if (event.event === 'ready') {
      const conversationId = event.data.conversation_id
      if (typeof conversationId === 'string') {
        this.setData({ conversationId })
      }
      return
    }
    if (event.event === 'delta') {
      const text = event.data.text
      if (typeof text === 'string') {
        this.updateAssistant(assistantId, (message) => ({
          ...message,
          content: message.content + text,
        }))
      }
      return
    }
    if (event.event === 'recommendations') {
      const items = event.data.items
      if (Array.isArray(items)) {
        this.updateAssistant(assistantId, (message) => ({
          ...message,
          cards: items as RecommendedCourseCard[],
        }))
      }
      return
    }
    if (event.event === 'done') {
      this.setData({ sending: false })
      return
    }
    if (event.event === 'error') {
      const message = event.data.message
      this.finishWithError(
        assistantId,
        typeof message === 'string' ? message : '问答暂时不可用',
      )
    }
  },

  updateAssistant(
    assistantId: string,
    update: (message: ChatMessage) => ChatMessage,
  ) {
    const messages = this.data.messages.map((message) =>
      message.id === assistantId ? update(message) : message,
    )
    this.setData({ messages, scrollIntoView: `message-${assistantId}` })
  },

  finishWithError(assistantId: string, errorMessage: string) {
    this.updateAssistant(assistantId, (message) => ({
      ...message,
      content: message.content || errorMessage,
    }))
    this.setData({ sending: false })
    activeStream = null
  },

  openRecommendedCourse(event: WechatMiniprogram.TapEvent) {
    const courseId = event.currentTarget.dataset.id
    if (courseId) {
      wx.navigateTo({ url: `/pages/courses/detail?id=${courseId}` })
    }
  },
})
