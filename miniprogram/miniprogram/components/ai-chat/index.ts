import {
  reserveChat,
  streamChat,
  type ChatMode,
  type ChatAnswerImage,
  type ChatStreamEvent,
  type ChatStreamTask,
  type RecommendedCourseCard,
} from '../../services/chat'

interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  cards: RecommendedCourseCard[]
  images: ChatAnswerImage[]
}

const activeStreams = new WeakMap<object, ChatStreamTask>()

Component({
  properties: {
    mode: {
      type: String,
      value: 'qa',
    },
    welcomeMessage: {
      type: String,
      value: '',
    },
    inputPlaceholder: {
      type: String,
      value: '输入你想说的内容',
    },
    emptyPrompt: {
      type: String,
      value: '先输入内容',
    },
    errorFallback: {
      type: String,
      value: 'AI 暂时不可用',
    },
    prompts: {
      type: Array,
      value: [],
    },
    maxLength: {
      type: Number,
      value: 500,
    },
  },

  data: {
    draft: '',
    sending: false,
    conversationId: '',
    scrollIntoView: 'message-welcome',
    messages: [] as ChatMessage[],
  },

  lifetimes: {
    attached() {
      this.setData({
        messages: [
          {
            id: 'welcome',
            role: 'assistant',
            content: this.properties.welcomeMessage,
            cards: [],
            images: [],
          },
        ],
      })
    },

    detached() {
      const stream = activeStreams.get(this)
      if (stream) {
        activeStreams.delete(this)
        stream.abort()
      }
    },
  },

  methods: {
    onDraftInput(event: WechatMiniprogram.InputEvent) {
      this.setData({ draft: event.detail.value })
    },

    choosePrompt(event: WechatMiniprogram.TapEvent) {
      const prompt = event.currentTarget.dataset.prompt
      if (typeof prompt === 'string' && prompt) {
        this.setData({ draft: prompt })
      }
    },

    async onSend() {
      const prompt = this.data.draft.trim()
      if (!prompt || this.data.sending) {
        if (!prompt) {
          wx.showToast({ title: this.properties.emptyPrompt, icon: 'none' })
        }
        return
      }
      const mode: ChatMode =
        this.properties.mode === 'copywriting' ? 'copywriting' : 'qa'
      const sequence = Date.now().toString()
      const assistantId = `assistant-${sequence}`
      const messages: ChatMessage[] = [
        ...this.data.messages,
        {
          id: `user-${sequence}`,
          role: 'user',
          content: prompt,
          cards: [],
          images: [],
        },
        {
          id: assistantId,
          role: 'assistant',
          content: '',
          cards: [],
          images: [],
        },
      ]
      this.setData({
        messages,
        draft: '',
        sending: true,
        scrollIntoView: `message-${assistantId}`,
      })
      try {
        await getApp<IAppOption>().ensureSession()
        const reservation = await reserveChat(
          prompt,
          this.data.conversationId,
          mode,
        )
        const stream = streamChat(
          reservation.ticket,
          {
            onEvent: (event) => this.handleStreamEvent(assistantId, event, mode),
            onError: (message) => this.finishWithError(assistantId, message),
            onComplete: () => {
              if (this.data.sending) {
                this.setData({ sending: false })
              }
              activeStreams.delete(this)
            },
          },
          mode,
        )
        if (stream) {
          activeStreams.set(this, stream)
        }
      } catch (error: unknown) {
        this.finishWithError(
          assistantId,
          error instanceof Error ? error.message : this.properties.errorFallback,
        )
      }
    },

    handleStreamEvent(
      assistantId: string,
      event: ChatStreamEvent,
      mode: ChatMode,
    ) {
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
        if (mode === 'qa' && Array.isArray(items)) {
          this.updateAssistant(assistantId, (message) => ({
            ...message,
            cards: items as RecommendedCourseCard[],
          }))
        }
        return
      }
      if (event.event === 'images') {
        const items = event.data.items
        if (mode === 'qa' && Array.isArray(items)) {
          const images = items.filter(
            (item): item is ChatAnswerImage =>
              typeof item === 'object' &&
              item !== null &&
              typeof (item as ChatAnswerImage).url === 'string' &&
              typeof (item as ChatAnswerImage).alt_text === 'string',
          )
          this.updateAssistant(assistantId, (message) => ({
            ...message,
            images,
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
          typeof message === 'string' ? message : this.properties.errorFallback,
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
      activeStreams.delete(this)
    },

    openRecommendedCourse(event: WechatMiniprogram.TapEvent) {
      const courseId = event.currentTarget.dataset.id
      if (courseId) {
        wx.navigateTo({ url: `/pages/courses/detail?id=${courseId}` })
      }
    },

    previewAnswerImage(event: WechatMiniprogram.TapEvent) {
      const messageId = event.currentTarget.dataset.messageId
      const current = event.currentTarget.dataset.url
      const message = this.data.messages.find((item) => item.id === messageId)
      if (message && typeof current === 'string') {
        wx.previewImage({
          current,
          urls: message.images.map((item) => item.url),
        })
      }
    },
  },
})
