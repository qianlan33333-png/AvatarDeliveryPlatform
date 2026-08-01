import { runtimeConfig } from './config/env'

App<IAppOption>({
  globalData: {
    apiBaseUrl: runtimeConfig.apiBaseUrl,
    envVersion: runtimeConfig.envVersion,
    sessionReady: false,
  },
})
