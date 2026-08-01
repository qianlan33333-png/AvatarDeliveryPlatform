import { runtimeConfig } from './config/env'
import { loginWithWeChat } from './services/auth'
import { getAccessToken } from './services/session'

App<IAppOption>({
  globalData: {
    apiBaseUrl: runtimeConfig.apiBaseUrl,
    envVersion: runtimeConfig.envVersion,
    sessionReady: false,
  },

  onLaunch() {
    void this.ensureSession()
  },

  async ensureSession() {
    if (this.globalData.sessionReady && getAccessToken()) {
      return
    }
    this.globalData.sessionReady = false
    await loginWithWeChat()
    this.globalData.sessionReady = true
  },
})
