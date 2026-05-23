import axios from 'axios'

export const api = axios.create({ baseURL: '/api/v1/admin' })

api.interceptors.request.use(c => {
  const t = localStorage.getItem('nanobot_token')
  if (t) c.headers.Authorization = `Bearer ${t}`
  return c
})
