import { useData } from './data'
import { useUi } from './ui'
import { useAuth } from './auth'
import { useServer } from './server'
import { useUppy } from './uppy'

export const useApp = () => ({
  data: useData(),
  ui: useUi(),
  auth: useAuth(),
  // Created with the app, before anyone signs in: it reads the server at
  // sign-in, and a store created later would miss that.
  server: useServer(),
  uppy: useUppy()
})
