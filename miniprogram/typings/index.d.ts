/// <reference types="miniprogram-api-typings" />

interface IAppOption {
  globalData: {
    apiBaseUrl: string
    envVersion: 'develop' | 'trial' | 'release'
    sessionReady: boolean
  }
}
