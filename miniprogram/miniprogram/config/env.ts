export type RuntimeEnvironment = 'develop' | 'trial' | 'release'

interface RuntimeConfig {
  apiBaseUrl: string
  envVersion: RuntimeEnvironment
  requestTimeoutMs: number
}

const API_BASE_URLS: Record<RuntimeEnvironment, string> = {
  develop: 'https://www.qianlan333.cloud/api/v1',
  trial: 'https://www.qianlan333.cloud/api/v1',
  release: 'https://www.qianlan333.cloud/api/v1',
}

function getRuntimeEnvironment(): RuntimeEnvironment {
  const envVersion = wx.getAccountInfoSync().miniProgram.envVersion

  if (envVersion === 'trial' || envVersion === 'release') {
    return envVersion
  }

  return 'develop'
}

const envVersion = getRuntimeEnvironment()

export const runtimeConfig: Readonly<RuntimeConfig> = Object.freeze({
  apiBaseUrl: API_BASE_URLS[envVersion],
  envVersion,
  requestTimeoutMs: 10000,
})
