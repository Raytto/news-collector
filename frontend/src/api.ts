import axios from 'axios'

// Default to '/api' so Vite dev proxy can forward to backend.
export const API_BASE = import.meta.env.VITE_API_BASE || '/api'

export const http = axios.create({ baseURL: API_BASE })

// 401 handler: emit a global event for login modal
http.interceptors.response.use(
  (resp) => resp,
  (error) => {
    const status = error?.response?.status
    if (status === 401) {
      try {
        window.dispatchEvent(new CustomEvent('auth:required'))
      } catch {}
    }
    return Promise.reject(error)
  }
)

export type Me = { id: number; email: string; name: string; is_admin: number }

export async function getMe(): Promise<Me> {
  const { data } = await http.get('/me')
  return data
}

export async function logout(): Promise<void> {
  await http.post('/auth/logout')
}

export async function requestLoginCode(email: string): Promise<void> {
  await http.post('/auth/login/code', { email })
}

export async function verifyLogin(email: string, code: string): Promise<Me> {
  const { data } = await http.post('/auth/login/verify', { email, code })
  return data
}

export async function requestSignupCode(email: string, name: string): Promise<void> {
  await http.post('/auth/signup/code', { email, name })
}

export async function verifySignup(email: string, code: string, name: string): Promise<Me> {
  const { data } = await http.post('/auth/signup/verify', { email, code, name })
  return data
}

export type PipelineListItem = {
  id: number
  name: string
  enabled: number
  debug_enabled?: number
  description?: string
  updated_at?: string
  writer_type?: string
  writer_hours?: number
  delivery_kind?: 'email' | 'feishu' | null
  owner_user_id?: number | null
  owner_user_name?: string | null
}

// --- Users (admin) ---
export type UserItem = {
  id: number
  email: string
  name: string
  is_admin: number
  enabled: number
  avatar_url?: string | null
  created_at?: string | null
  verified_at?: string | null
  last_login_at?: string | null
}

export type UserListResponse = {
  items: UserItem[]
  total: number
}

export type InfoListItem = {
  id: number
  title: string
  source: string
  source_label?: string | null
  category?: string | null
  category_label?: string | null
  publish?: string | null
  link: string
  final_score?: number | null
  review_updated_at?: string | null
}

export type InfoListResponse = {
  items: InfoListItem[]
  total: number
}

export type CategoryItem = {
  id: number
  key: string
  label_zh: string
  enabled: number
  created_at?: string
  updated_at?: string
}

export type SourceItem = {
  id: number
  key: string
  label_zh: string
  enabled: number
  category_key: string
  category_label?: string | null
  script_path: string
  addresses: string[]
  created_at?: string
  updated_at?: string
}

export type MetricOption = {
  key: string
  label_zh: string
  default_weight?: number | null
  sort_order?: number
}

export type FeishuChat = {
  chat_id: string
  name?: string | null
  description?: string | null
  member_count?: number | null
}

export type InfoDetail = {
  id: number
  title: string
  source: string
  source_label?: string | null
  category?: string | null
  category_label?: string | null
  publish?: string | null
  link: string
  detail?: string | null
}

export type InfoReviewMetric = {
  metric_key: string
  metric_label: string
  score: number
}

export type InfoReview = {
  final_score: number | null
  ai_comment?: string | null
  ai_summary?: string | null
  ai_key_concepts?: string[] | null
  ai_summary_long?: string | null
  raw_response?: string | null
  updated_at?: string | null
  created_at?: string | null
  has_review?: boolean
  scores: InfoReviewMetric[]
}

export type AiMetric = {
  id: number
  key: string
  label_zh: string
  rate_guide_zh?: string | null
  default_weight?: number | null
  active: number
  sort_order: number
  created_at?: string | null
  updated_at?: string | null
}

export async function fetchPipelines(): Promise<PipelineListItem[]> {
  const { data } = await http.get('/pipelines')
  return data
}

export async function fetchUsers(params: {
  page?: number
  pageSize?: number
  q?: string | null
}): Promise<UserListResponse> {
  const { data } = await http.get('/admin/users', {
    params: {
      page: params.page,
      page_size: params.pageSize,
      q: params.q || undefined
    }
  })
  return data
}

export async function fetchUserDetail(id: number): Promise<{ user: UserItem; pipelines: PipelineListItem[] }> {
  const { data } = await http.get(`/admin/users/${id}`)
  return data
}

export async function updateUser(
  id: number,
  payload: Partial<{ name: string; is_admin: number; enabled: number }>
): Promise<UserItem> {
  const { data } = await http.patch(`/admin/users/${id}`, payload)
  return data
}

export async function fetchOptions(): Promise<{
  categories: string[]
  writer_types: string[]
  delivery_kinds: string[]
  metrics: MetricOption[]
}> {
  const { data } = await http.get('/options')
  return data
}

export async function fetchPipeline(id: number) {
  const { data } = await http.get(`/pipelines/${id}`)
  return data
}

export async function createPipeline(payload: any) {
  const { data } = await http.post('/pipelines', payload)
  return data
}

export async function updatePipeline(id: number, payload: any) {
  const { data } = await http.put(`/pipelines/${id}`, payload)
  return data
}

export async function fetchFeishuChats(payload: { app_id: string; app_secret: string }): Promise<FeishuChat[]> {
  const { data } = await http.post('/feishu/chats', payload)
  const items = Array.isArray(data?.items) ? data.items : []
  return items
    .map((item: any) => {
      const rawCount = item?.member_count
      let memberCount: number | null = null
      if (typeof rawCount === 'number' && Number.isFinite(rawCount)) {
        memberCount = rawCount
      } else {
        const numeric = Number(rawCount)
        memberCount = Number.isFinite(numeric) ? numeric : null
      }
      return {
        chat_id: typeof item?.chat_id === 'string' ? item.chat_id : String(item?.chat_id || ''),
        name: typeof item?.name === 'string' ? item.name : item?.chat_id,
        description: typeof item?.description === 'string' ? item.description : null,
        member_count: memberCount
      }
    })
    .filter((item) => !!item.chat_id)
}

export async function deletePipeline(id: number) {
  const { data } = await http.delete(`/pipelines/${id}`)
  return data
}

export async function fetchInfos(params: {
  page?: number
  pageSize?: number
  category?: string | null
  source?: string | null
  q?: string | null
}): Promise<InfoListResponse> {
  const { data } = await http.get('/infos', {
    params: {
      page: params.page,
      page_size: params.pageSize,
      category: params.category || undefined,
      source: params.source || undefined,
      q: params.q || undefined
    }
  })
  return data
}

export async function fetchInfoDetail(id: number): Promise<InfoDetail> {
  const { data } = await http.get(`/infos/${id}`)
  return data
}

export async function fetchInfoReview(id: number): Promise<InfoReview> {
  const { data } = await http.get(`/infos/${id}/ai_review`)
  return data
}

export async function fetchCategories(): Promise<CategoryItem[]> {
  const { data } = await http.get('/categories')
  return data
}

export async function createCategory(payload: { key: string; label_zh: string; enabled?: number }) {
  const { data } = await http.post('/categories', payload)
  return data
}

export async function updateCategory(
  id: number,
  payload: Partial<{ key: string; label_zh: string; enabled: number }>
) {
  const { data } = await http.put(`/categories/${id}`, payload)
  return data
}

export async function deleteCategory(id: number) {
  const { data } = await http.delete(`/categories/${id}`)
  return data
}

export async function fetchSources(): Promise<SourceItem[]> {
  const { data } = await http.get('/sources')
  return data
}

export async function createSource(payload: {
  key: string
  label_zh: string
  enabled?: number
  category_key: string
  script_path: string
  addresses?: string[]
}) {
  const { data } = await http.post('/sources', payload)
  return data
}

export async function updateSource(
  id: number,
  payload: Partial<{
    key: string
    label_zh: string
    enabled: number
    category_key: string
    script_path: string
    addresses: string[]
  }>
) {
  const { data } = await http.put(`/sources/${id}`, payload)
  return data
}

export async function deleteSource(id: number) {
  const { data } = await http.delete(`/sources/${id}`)
  return data
}

export async function fetchAiMetrics(): Promise<AiMetric[]> {
  const { data } = await http.get('/ai-metrics')
  return data
}

export async function createAiMetric(payload: {
  key: string
  label_zh: string
  rate_guide_zh?: string | null
  default_weight?: number | null
  sort_order?: number
  active?: number
}) {
  const { data } = await http.post('/ai-metrics', payload)
  return data
}

export async function updateAiMetric(
  id: number,
  payload: Partial<{
    label_zh: string
    rate_guide_zh: string | null
    default_weight: number | null
    sort_order: number
    active: number
  }>
) {
  const { data } = await http.put(`/ai-metrics/${id}`, payload)
  return data
}

export async function deleteAiMetric(id: number) {
  const { data } = await http.delete(`/ai-metrics/${id}`)
  return data
}
