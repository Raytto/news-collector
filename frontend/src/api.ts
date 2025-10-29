import axios from 'axios'

// Default to '/api' so Vite dev proxy can forward to backend.
export const API_BASE = import.meta.env.VITE_API_BASE || '/api'

export const http = axios.create({ baseURL: API_BASE })

export type PipelineListItem = {
  id: number
  name: string
  enabled: number
  description?: string
  updated_at?: string
  writer_type?: string
  writer_hours?: number
  delivery_kind?: 'email' | 'feishu' | null
}

export async function fetchPipelines(): Promise<PipelineListItem[]> {
  const { data } = await http.get('/pipelines')
  return data
}

export async function fetchOptions(): Promise<{
  categories: string[]
  writer_types: string[]
  delivery_kinds: string[]
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

export async function deletePipeline(id: number) {
  const { data } = await http.delete(`/pipelines/${id}`)
  return data
}
