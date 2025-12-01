import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { MinusCircleOutlined, PlusOutlined } from '@ant-design/icons'
import { Alert, AutoComplete, Button, Card, Checkbox, Form, Input, InputNumber, Radio, Select, Space, Table, Tabs, Tree, Typography, message } from 'antd'
import { useNavigate, useParams } from 'react-router-dom'
import { createPipeline, fetchFeishuChats, fetchOptions, fetchPipeline, fetchSources, updatePipeline } from '../api'
import type { Evaluator, FeishuChat, MetricOption, PipelineClassOption, SourceItem } from '../api'
import type { ColumnsType } from 'antd/es/table'
import type { FormListFieldData } from 'antd/es/form/FormList'

type DeliveryKind = 'email' | 'feishu'

function writerTypeForDelivery(kind?: DeliveryKind | null, fallback?: string, allowed?: string[]) {
  const allowedList = normalizeStringArray(allowed ?? [])
  const normalizedFallback = typeof fallback === 'string' ? fallback.trim() : ''
  if (normalizedFallback && (!allowedList.length || allowedList.includes(normalizedFallback))) {
    return normalizedFallback
  }
  const preferred = kind === 'feishu' ? 'feishu_md' : kind === 'email' ? 'info_html' : undefined
  if (preferred && (!allowedList.length || allowedList.includes(preferred))) {
    return preferred
  }
  if (allowedList.length) return allowedList[0]
  return normalizedFallback || preferred || 'info_html'
}

type LimitOverride = { category?: string; limit?: number }
type WeightEntry = { metric?: string; weight?: number }
type BonusEntry = { source?: string; bonus?: number }

const CATEGORY_LIMIT_DEFAULT = 10

const FALLBACK_METRICS: MetricOption[] = [
  { key: 'timeliness', label_zh: '时效性', default_weight: 0.14, sort_order: 10 },
  { key: 'game_relevance', label_zh: '游戏相关性', default_weight: 0.2, sort_order: 20 },
  { key: 'mobile_game_relevance', label_zh: '手游相关性', default_weight: 0.09, sort_order: 30 },
  { key: 'ai_relevance', label_zh: 'AI相关性', default_weight: 0.14, sort_order: 40 },
  { key: 'tech_relevance', label_zh: '科技相关性', default_weight: 0.11, sort_order: 50 },
  { key: 'quality', label_zh: '文章质量', default_weight: 0.13, sort_order: 60 },
  { key: 'insight', label_zh: '洞察力', default_weight: 0.08, sort_order: 70 },
  { key: 'depth', label_zh: '深度', default_weight: 0.06, sort_order: 80 },
  { key: 'novelty', label_zh: '新颖度', default_weight: 0.05, sort_order: 90 }
]

const BONUS_SUGGESTIONS = [
  { key: 'openai.research', label: 'OpenAI Research', defaultValue: 3 },
  { key: 'deepmind', label: 'DeepMind', defaultValue: 1 },
  { key: 'qbitai-zhiku', label: '量子位智库', defaultValue: 2 }
] as const

const DEFAULT_BONUS_VALUES: Record<string, number> = BONUS_SUGGESTIONS.reduce(
  (acc, cur) => ({ ...acc, [cur.key]: cur.defaultValue }),
  {}
)

const ROOT_NODE_KEY = '__ALL_CATEGORIES__'
const catKey = (cat: string) => `cat:${cat}`
const srcKey = (sourceKey: string) => `src:${sourceKey}`

const filterAutoCompleteOption = (input: string, option?: { value: string; label?: string }) => {
  if (!option) return false
  const normalized = input.trim().toLowerCase()
  if (!normalized) return true
  const labelText = option.label ? option.label.toLowerCase() : ''
  return option.value.toLowerCase().includes(normalized) || labelText.includes(normalized)
}

function normalizeStringArray(values: any): string[] {
  if (!Array.isArray(values)) return []
  const seen = new Set<string>()
  values.forEach((item) => {
    const str = typeof item === 'string' ? item.trim() : String(item ?? '').trim()
    if (str && !seen.has(str)) {
      seen.add(str)
    }
  })
  return Array.from(seen)
}

function toNumber(value: unknown): number | undefined {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string') {
    const trimmed = value.trim()
    if (!trimmed) return undefined
    const parsed = Number(trimmed)
    if (Number.isFinite(parsed)) return parsed
  }
  return undefined
}

function buildEmailSubject(hours: unknown): string {
  const numeric = toNumber(hours)
  const value = typeof numeric === 'number' && numeric > 0 ? numeric : 24
  const rounded = Math.round(value)
  return `情报鸭${rounded}小时精选`
}

function normalizeLimitForForm(limit: unknown): { defaultValue: number; overrides: LimitOverride[] } {
  let defaultValue = CATEGORY_LIMIT_DEFAULT
  const overrides: LimitOverride[] = []

  if (typeof limit === 'number') {
    const numeric = toNumber(limit)
    if (typeof numeric === 'number') {
      defaultValue = numeric
      return { defaultValue, overrides }
    }
  }

  if (limit && typeof limit === 'object') {
    Object.entries(limit as Record<string, unknown>).forEach(([key, value]) => {
      const numeric = toNumber(value)
      if (typeof numeric !== 'number') return
      if (key === 'default') {
        defaultValue = numeric
      } else {
        overrides.push({ category: key, limit: numeric })
      }
    })
    return { defaultValue, overrides }
  }

  return { defaultValue, overrides }
}

function normalizeWeightsForForm(weights: unknown, metrics: MetricOption[]): WeightEntry[] {
  const result: WeightEntry[] = []
  const provided = weights && typeof weights === 'object' ? (weights as Record<string, unknown>) : {}
  const seen = new Set<string>()

  metrics.forEach(({ key, default_weight }) => {
    const numeric = toNumber(provided[key])
    const fallback = typeof default_weight === 'number' ? default_weight : 0
    result.push({ metric: key, weight: typeof numeric === 'number' ? numeric : fallback })
    seen.add(key)
  })

  Object.entries(provided).forEach(([key, value]) => {
    if (seen.has(key)) return
    const numeric = toNumber(value)
    if (typeof numeric !== 'number') return
    result.push({ metric: key, weight: numeric })
  })

  return result
}

function normalizeBonusForForm(bonus: unknown, options?: { includeSuggestions?: boolean }): BonusEntry[] {
  const includeSuggestions = options?.includeSuggestions !== false
  const result: BonusEntry[] = []
  const provided =
    bonus && typeof bonus === 'object' && !Array.isArray(bonus) ? (bonus as Record<string, unknown>) : {}
  const seen = new Set<string>()

  if (includeSuggestions) {
    BONUS_SUGGESTIONS.forEach(({ key, defaultValue }) => {
      const numeric = toNumber(provided[key])
      result.push({ source: key, bonus: typeof numeric === 'number' ? numeric : defaultValue })
      seen.add(key)
    })
  }

  Object.entries(provided).forEach(([key, value]) => {
    if (seen.has(key)) return
    const numeric = toNumber(value)
    if (typeof numeric !== 'number') return
    result.push({ source: key, bonus: numeric })
  })

  return result
}

function buildLimitPayload(defaultValue?: number, overrides?: LimitOverride[]): Record<string, number> | undefined {
  const map: Record<string, number> = {}
  if (typeof defaultValue === 'number' && Number.isFinite(defaultValue) && defaultValue > 0) {
    map.default = Math.floor(defaultValue)
  }
  overrides
    ?.filter((entry): entry is { category: string; limit: number } => {
      const category = typeof entry?.category === 'string' ? entry.category.trim() : ''
      const limit = toNumber(entry?.limit)
      return !!category && typeof limit === 'number' && limit > 0
    })
    .forEach((entry) => {
      map[entry.category.trim()] = Math.floor(Number(entry.limit))
    })

  return Object.keys(map).length ? map : undefined
}

function buildNumericRecord(entries: { key?: string; value?: unknown }[]): Record<string, number> | undefined {
  const record: Record<string, number> = {}
  entries.forEach(({ key, value }) => {
    const name = typeof key === 'string' ? key.trim() : ''
    const numeric = toNumber(value)
    if (!name || typeof numeric !== 'number') return
    record[name] = numeric
  })
  return Object.keys(record).length ? record : undefined
}

type TreeNode = {
  title: string
  key: string
  children?: TreeNode[]
}

const TAB_ORDER = ['base', 'filters', 'writer', 'delivery'] as const
type TabKey = (typeof TAB_ORDER)[number]

function collectAllKeys(treeData: TreeNode[]): string[] {
  const keys: string[] = []
  const dfs = (nodes: TreeNode[]) => {
    nodes.forEach((node) => {
      keys.push(node.key)
      if (node.children?.length) {
        dfs(node.children)
      }
    })
  }
  dfs(treeData)
  return keys
}

export default function PipelineForm() {
  const { id } = useParams()
  const editing = !!id
  const [form] = Form.useForm()
  const navigate = useNavigate()
  const [loading, setLoading] = useState(editing)
  const [options, setOptions] = useState<{
    categories: string[]
    delivery_kinds: string[]
    metrics: MetricOption[]
    pipeline_classes: PipelineClassOption[]
    evaluators: Evaluator[]
    writer_types: string[]
  }>({
    categories: [],
    delivery_kinds: ['email', 'feishu'],
    metrics: FALLBACK_METRICS,
    pipeline_classes: [],
    evaluators: [],
    writer_types: ['feishu_md', 'info_html']
  })
  const [sources, setSources] = useState<SourceItem[]>([])
  const [deliveryKind, setDeliveryKind] = useState<DeliveryKind>('email')
  // 保持当前选中的分页标签，避免保存后回到“基础”
  const [activeTab, setActiveTab] = useState<TabKey>('base')
  const [feishuChats, setFeishuChats] = useState<FeishuChat[]>([])
  const [fetchingFeishuChats, setFetchingFeishuChats] = useState(false)
  const [optionsReady, setOptionsReady] = useState(false)
  const [sourcesReady, setSourcesReady] = useState(false)
  const writerSnapshotRef = useRef<any>(null)
  const [fetchError, setFetchError] = useState<string | null>(null)
  const [reloadKey, setReloadKey] = useState(0)
  const [treeCheckedKeys, setTreeCheckedKeys] = useState<string[]>([])
  const limitOverrides = Form.useWatch<LimitOverride[]>(["writer", "limit_per_category_overrides"], form)
  const pipelineClassIdValue = Form.useWatch<number | undefined>(["pipeline", "pipeline_class_id"], form)
  const toAllChatValue = Form.useWatch<number | undefined>(["delivery", "to_all_chat"], form)
  const chatIdValue = Form.useWatch<string | undefined>(["delivery", "chat_id"], form)
  const allowedMetricKeys = useMemo(() => {
    if (!pipelineClassIdValue) return null
    const cls = options.pipeline_classes.find((item) => Number(item.id) === Number(pipelineClassIdValue))
    if (!cls || !Array.isArray(cls.evaluators) || !cls.evaluators.length) return null
    const evaluatorKeys = new Set(cls.evaluators.map((k) => k.trim()).filter(Boolean))
    if (!evaluatorKeys.size) return null
    const union = new Set<string>()
    options.evaluators.forEach((ev) => {
      if (!evaluatorKeys.has(ev.key.trim())) return
      ev.metrics?.forEach((m) => {
        if (typeof m === 'string' && m.trim()) {
          union.add(m.trim())
        }
      })
    })
    return union.size ? union : null
  }, [options.evaluators, options.pipeline_classes, pipelineClassIdValue])
  const metrics = useMemo(() => {
    const base = options.metrics.length ? options.metrics : FALLBACK_METRICS
    if (!allowedMetricKeys || !allowedMetricKeys.size) return base
    const filtered = base.filter((item) => allowedMetricKeys.has(item.key))
    if (filtered.length) return filtered
    // 当允许指标未在 base 列表时，回退生成最小定义
    return Array.from(allowedMetricKeys).map((key, idx) => ({
      key,
      label_zh: key,
      default_weight: 1,
      sort_order: idx * 10
    }))
  }, [allowedMetricKeys, options.metrics])
  useEffect(() => {
    const allowed = new Set(metrics.map((m) => m.key))
    const existing = (form.getFieldValue(["writer", "weights_entries"]) as WeightEntry[] | undefined) ?? []
    const cleaned: WeightEntry[] = []
    const seen = new Set<string>()
    existing.forEach((entry) => {
      const key = typeof entry?.metric === 'string' ? entry.metric.trim() : ''
      if (!key || !allowed.has(key) || seen.has(key)) return
      seen.add(key)
      cleaned.push({ metric: key, weight: entry?.weight })
    })
    metrics.forEach((m) => {
      if (seen.has(m.key)) return
      cleaned.push({
        metric: m.key,
        weight: typeof m.default_weight === 'number' ? m.default_weight : undefined
      })
      seen.add(m.key)
    })
    const changed = JSON.stringify(cleaned) !== JSON.stringify(existing)
    if (changed) {
      form.setFieldValue(["writer", "weights_entries"], cleaned)
    }
  }, [form, metrics])
  const [weekdaySelection, setWeekdaySelection] = useState<number[] | null>(null)
  const weekdayOptions = useMemo(
    () => [
      { label: '周一', value: 1 },
      { label: '周二', value: 2 },
      { label: '周三', value: 3 },
      { label: '周四', value: 4 },
      { label: '周五', value: 5 },
      { label: '周六', value: 6 },
      { label: '周日', value: 7 }
    ],
    []
  )

  const pageReady = optionsReady && sourcesReady
  const pageLoading = loading || !pageReady
  const actionsDisabled = pageLoading

  const initialFormValues = useMemo(
    () => ({
      pipeline: { enabled: 1, name: '我的推送管线' },
      filters: { all_categories: 1 },
      writer: {
        type: writerTypeForDelivery(deliveryKind),
        hours: 24,
        limit_per_category_default: CATEGORY_LIMIT_DEFAULT,
        limit_per_category_overrides: [],
        per_source_cap: 3,
        weights_entries: normalizeWeightsForForm(undefined, metrics),
        bonus_entries: normalizeBonusForForm(DEFAULT_BONUS_VALUES)
      },
      delivery: {
        to_all_chat: 1
      }
    }),
    [deliveryKind, metrics]
  )
  const pipelineClassMap = useMemo(() => {
    const map = new Map<number, PipelineClassOption>()
    options.pipeline_classes.forEach((cls) => {
      if (Number.isFinite(cls.id)) {
        map.set(Number(cls.id), cls)
      }
    })
    return map
  }, [options.pipeline_classes])
  const selectedPipelineClass = useMemo(() => {
    if (pipelineClassIdValue == null) return undefined
    const numeric = Number(pipelineClassIdValue)
    if (!Number.isFinite(numeric)) return undefined
    return pipelineClassMap.get(numeric)
  }, [pipelineClassIdValue, pipelineClassMap])
  const isLegouMinigameClass = useMemo(
    () => selectedPipelineClass?.key === 'legou_minigame',
    [selectedPipelineClass]
  )
  const allowedWriterTypes = useMemo(() => {
    const explicit = normalizeStringArray(selectedPipelineClass?.writers || [])
    if (explicit.length) return explicit
    return normalizeStringArray(options.writer_types || [])
  }, [options.writer_types, selectedPipelineClass])
  const writerTypeOptions = useMemo(
    () => allowedWriterTypes.map((type) => ({ value: type, label: type })),
    [allowedWriterTypes]
  )
  const getAllowedWriterTypes = useCallback(
    (classId?: number | null) => {
      if (classId != null) {
        const cls = pipelineClassMap.get(Number(classId))
        const explicit = normalizeStringArray(cls?.writers || [])
        if (explicit.length) return explicit
      }
      return allowedWriterTypes
    },
    [allowedWriterTypes, pipelineClassMap]
  )
  const defaultBonusEntries = useMemo(
    () =>
      isLegouMinigameClass
        ? normalizeBonusForForm({}, { includeSuggestions: false })
        : normalizeBonusForForm(DEFAULT_BONUS_VALUES),
    [isLegouMinigameClass]
  )
  const allowedCategorySet = useMemo(() => {
    const set = new Set<string>()
    ;(selectedPipelineClass?.categories || []).forEach((cat) => {
      if (cat) set.add(cat)
    })
    return set
  }, [selectedPipelineClass])
  const mergedCategories = useMemo(() => {
    const ordered: string[] = []
    const pushUnique = (value?: string | null) => {
      const key = typeof value === 'string' ? value.trim() : ''
      if (!key || ordered.includes(key)) return
      ordered.push(key)
    }
    options.categories.forEach(pushUnique)
    sources.forEach((src) => pushUnique(src.category_key))
    options.pipeline_classes.forEach((cls) => (cls.categories || []).forEach(pushUnique))
    return ordered
  }, [options.categories, options.pipeline_classes, sources])
  const visibleCategories = useMemo(
    () => (allowedCategorySet.size ? mergedCategories.filter((cat) => allowedCategorySet.has(cat)) : mergedCategories),
    [allowedCategorySet, mergedCategories]
  )
  const visibleSources = useMemo(
    () => (allowedCategorySet.size ? sources.filter((item) => allowedCategorySet.has(item.category_key)) : sources),
    [allowedCategorySet, sources]
  )
  const treeData = useMemo<TreeNode[]>(() => {
    const categoryNodes = visibleCategories.map((cat) => {
      const children = visibleSources
        .filter((item) => item.category_key === cat)
        .map((item) => ({
          title: item.label_zh ? `${item.label_zh} (${item.key})` : item.key,
          key: srcKey(item.key),
        }))
      return {
        title: cat,
        key: catKey(cat),
        children,
      }
    })
    return [
      {
        title: '全部类别/来源',
        key: ROOT_NODE_KEY,
        children: categoryNodes,
      },
    ]
  }, [visibleCategories, visibleSources])
  const allTreeKeys = useMemo(() => collectAllKeys(treeData), [treeData])
  useEffect(() => {
    setTreeCheckedKeys((prev) => prev.filter((key) => allTreeKeys.includes(key)))
  }, [allTreeKeys])
  const deriveFiltersFromChecked = useCallback(
    (checked: string[]) => {
      const keySet = new Set<string>(checked)
      const categoryKeys = visibleCategories.map((c) => catKey(c))
      const sourceKeys = visibleSources.map((s) => srcKey(s.key))
      const hasAllCategories = categoryKeys.length > 0 && categoryKeys.every((k) => keySet.has(k))
      const hasAllSources = sourceKeys.length > 0 ? sourceKeys.every((k) => keySet.has(k)) : hasAllCategories
      if (keySet.has(ROOT_NODE_KEY) || (hasAllCategories && hasAllSources)) {
        // Normalize to full selection including root
        setTreeCheckedKeys(allTreeKeys)
        return { all_categories: 1, categories_json: [], include_src_json: [] }
      }
      const categoriesSelected: string[] = []
      visibleCategories.forEach((cat) => {
        if (keySet.has(catKey(cat))) {
          categoriesSelected.push(cat)
        }
      })
      const categorySet = new Set(categoriesSelected)
      const includeSrc: string[] = []
      visibleSources.forEach((src) => {
        const skey = srcKey(src.key)
        if (keySet.has(skey) && !categorySet.has(src.category_key)) {
          includeSrc.push(src.key)
        }
      })
      return {
        all_categories: 0,
        categories_json: categoriesSelected,
        include_src_json: includeSrc,
      }
    },
    [allTreeKeys, visibleCategories, visibleSources]
  )
  const buildCheckedKeysFromFilters = useCallback(
    (filters: any) => {
      const keys = new Set<string>()
      if (filters?.all_categories === 1) {
        allTreeKeys.forEach((k) => keys.add(k))
        return Array.from(keys)
      }
      const categories = Array.isArray(filters?.categories_json) ? filters.categories_json : []
      categories.forEach((cat: any) => {
        const catStr = String(cat)
        if (!visibleCategories.includes(catStr)) return
        keys.add(catKey(catStr))
        visibleSources
          .filter((s) => s.category_key === catStr)
          .forEach((s) => {
            keys.add(srcKey(s.key))
          })
      })
      const includeSrc = Array.isArray(filters?.include_src_json) ? filters.include_src_json : []
      const visibleSourceKeys = new Set(visibleSources.map((s) => s.key))
      includeSrc.forEach((src: any) => {
        const skey = String(src)
        if (visibleSourceKeys.has(skey)) {
          keys.add(srcKey(skey))
        }
      })
      return Array.from(keys)
    },
    [allTreeKeys, visibleCategories, visibleSources]
  )
  useEffect(() => {
    if (!optionsReady || !sourcesReady) return
    const normalizedKeys = treeCheckedKeys.filter((key) => allTreeKeys.includes(key))
    const filtersPayload = deriveFiltersFromChecked(normalizedKeys)
    form.setFieldsValue({ filters: filtersPayload })
  }, [allTreeKeys, deriveFiltersFromChecked, form, optionsReady, sourcesReady, treeCheckedKeys])
  const weightSuggestions = useMemo(
    () =>
      metrics.map((item) => ({
        value: item.key,
        label: `${item.label_zh || item.key} (${item.key})`
      })),
    [metrics]
  )
  const bonusSuggestions = useMemo(
    () =>
      BONUS_SUGGESTIONS.map((item) => ({
        value: item.key,
        label: `${item.label} (${item.key})`
      })),
    []
  )
  const feishuChatColumns = useMemo<ColumnsType<FeishuChat>>(
    () => [
      {
        title: '群名称',
        dataIndex: 'name',
        key: 'name',
        render: (_value, record) => (
          <Space direction="vertical" size={0}>
            <Typography.Text strong>{record.name || record.chat_id}</Typography.Text>
            {record.description ? (
              <Typography.Text
                type="secondary"
                ellipsis={{ tooltip: record.description }}
                style={{ maxWidth: 360 }}
              >
                {record.description}
              </Typography.Text>
            ) : null}
          </Space>
        )
      },
      {
        title: 'Chat ID',
        dataIndex: 'chat_id',
        key: 'chat_id',
        render: (value: string) => (
          <Typography.Text code copyable>
            {value}
          </Typography.Text>
        )
      },
      {
        title: '成员数',
        dataIndex: 'member_count',
        key: 'member_count',
        align: 'right',
        width: 90,
        render: (value: number | null | undefined) => (typeof value === 'number' ? value : '—')
      }
    ],
    []
  )
  const selectedFeishuChat = useMemo(
    () => feishuChats.find((chat) => chat.chat_id === chatIdValue),
    [feishuChats, chatIdValue]
  )

  useEffect(() => {
    let mounted = true
    fetchOptions()
      .then((data) => {
        if (!mounted) return
        const metricList = Array.isArray(data.metrics) ? data.metrics : []
        const sanitizedMetrics = metricList
          .filter((item): item is MetricOption => !!item && typeof item === 'object' && typeof item.key === 'string')
          .map((item, idx) => ({
            key: item.key.trim(),
            label_zh: item.label_zh || item.key.trim(),
            default_weight: typeof item.default_weight === 'number' ? item.default_weight : undefined,
            sort_order: typeof item.sort_order === 'number' ? item.sort_order : idx
          }))
        const sortedMetrics = sanitizedMetrics.length
          ? [...sanitizedMetrics].sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0))
          : FALLBACK_METRICS
        const classes = Array.isArray(data.pipeline_classes) ? data.pipeline_classes : []
        const evaluatorsRaw = Array.isArray(data.evaluators) ? data.evaluators : []
        const sanitizedEvaluators: Evaluator[] = evaluatorsRaw
          .filter((item: any) => item && typeof item === 'object')
          .map((item: any) => ({
            id: Number(item.id),
            key: typeof item.key === 'string' ? item.key.trim() : '',
            label_zh: typeof item.label_zh === 'string' ? item.label_zh : item.key,
            description: typeof item.description === 'string' ? item.description : null,
            prompt: typeof item.prompt === 'string' ? item.prompt : null,
            active: typeof item.active === 'number' ? item.active : 1,
            metrics: normalizeStringArray(item.metrics),
            created_at: item.created_at,
            updated_at: item.updated_at
          }))
          .filter((ev) => ev.id && ev.key)
        const sanitizedClasses: PipelineClassOption[] = classes
          .filter((item: any) => item && typeof item === 'object')
          .map((item: any) => ({
            id: Number(item.id),
            key: typeof item.key === 'string' ? item.key : '',
            label_zh: typeof item.label_zh === 'string' ? item.label_zh : item.key,
            description: typeof item.description === 'string' ? item.description : null,
            enabled: typeof item.enabled === 'number' ? item.enabled : 1,
            categories: normalizeStringArray(item.categories),
            evaluators: normalizeStringArray(item.evaluators),
            writers: normalizeStringArray(item.writers)
          }))
          .filter((item) => Number.isFinite(item.id) && item.key)
        const writerTypes = Array.isArray(data.writer_types) ? normalizeStringArray(data.writer_types) : []
        setOptions({
          categories: Array.isArray(data.categories) ? data.categories : [],
          delivery_kinds: Array.isArray(data.delivery_kinds) ? data.delivery_kinds : [],
          metrics: sortedMetrics,
          pipeline_classes: sanitizedClasses,
          evaluators: sanitizedEvaluators,
          writer_types: writerTypes.length ? writerTypes : ['feishu_md', 'info_html']
        })
        if (!editing && !form.isFieldsTouched(['writer', 'weights_entries'])) {
          form.setFieldsValue({
            writer: {
              weights_entries: normalizeWeightsForForm(undefined, sortedMetrics)
            }
          })
        }
      })
      .catch(() => {
        if (!mounted) return
        setOptions({
          categories: [],
          delivery_kinds: ['email', 'feishu'],
          metrics: FALLBACK_METRICS,
          pipeline_classes: [],
          evaluators: [],
          writer_types: ['feishu_md', 'info_html']
        })
        if (!editing && !form.isFieldsTouched(['writer', 'weights_entries'])) {
          form.setFieldsValue({
            writer: {
              weights_entries: normalizeWeightsForForm(undefined, FALLBACK_METRICS)
            }
          })
        }
      })
      .finally(() => {
        if (mounted) {
          setOptionsReady(true)
        }
      })
    return () => {
      mounted = false
    }
  }, [editing, form])

  useEffect(() => {
    let mounted = true
    fetchSources()
      .then((items) => {
        if (!mounted) return
        setSources(Array.isArray(items) ? items : [])
      })
      .catch(() => {
        if (mounted) {
          setSources([])
        }
      })
      .finally(() => {
        if (mounted) {
          setSourcesReady(true)
        }
      })
    return () => {
      mounted = false
    }
  }, [])

  useEffect(() => {
    const currentWriterType = form.getFieldValue(["writer", "type"])
    const nextWriterType = writerTypeForDelivery(
      deliveryKind,
      currentWriterType,
      allowedWriterTypes
    )
    if (nextWriterType !== currentWriterType) {
      form.setFieldValue(["writer", "type"], nextWriterType)
    }
  }, [allowedWriterTypes, deliveryKind, form])

  const applyPipelineData = (data: any) => {
    // Normalize pipeline for form consumption (esp. weekdays array)
    const pipelinePatched: any = { ...(data.pipeline || {}) }
    if (pipelinePatched?.pipeline_class_id !== undefined && pipelinePatched?.pipeline_class_id !== null) {
      const parsed = Number(pipelinePatched.pipeline_class_id)
      pipelinePatched.pipeline_class_id = Number.isFinite(parsed) ? parsed : undefined
    }
    const wdRaw = pipelinePatched?.weekdays_json
    if (Array.isArray(wdRaw)) {
      pipelinePatched.weekdays_json = wdRaw
        .map((n: any) => Number(n))
        .filter((n: number) => Number.isFinite(n) && n >= 1 && n <= 7)
    } else if (typeof wdRaw === 'string') {
      try {
        const parsed = JSON.parse(wdRaw)
        if (Array.isArray(parsed)) {
          pipelinePatched.weekdays_json = parsed
            .map((n: any) => Number(n))
            .filter((n: number) => Number.isFinite(n) && n >= 1 && n <= 7)
        } else {
          pipelinePatched.weekdays_json = undefined
        }
      } catch {
        pipelinePatched.weekdays_json = undefined
      }
    } else if (wdRaw == null || (typeof wdRaw === 'string' && wdRaw.trim() === '')) {
      pipelinePatched.weekdays_json = undefined
    }
    const w = data.writer || {}
    const writerPatched: any = { ...w }
    const { defaultValue, overrides } = normalizeLimitForForm(w?.limit_per_category)
    writerPatched.limit_per_category_default = defaultValue
    writerPatched.limit_per_category_overrides = overrides
    writerPatched.weights_entries = normalizeWeightsForForm(w?.weights_json ?? {}, metrics)
    const pipelineClassFromData =
      pipelinePatched.pipeline_class_id != null
        ? pipelineClassMap.get(Number(pipelinePatched.pipeline_class_id))
        : undefined
    const includeSuggestedBonus = pipelineClassFromData?.key !== 'legou_minigame'
    const bonusSeed = includeSuggestedBonus ? w?.bonus_json ?? DEFAULT_BONUS_VALUES : w?.bonus_json ?? {}
    writerPatched.bonus_entries = normalizeBonusForForm(bonusSeed, {
      includeSuggestions: includeSuggestedBonus
    })
    delete writerPatched.limit_per_category
    delete writerPatched.weights_json
    delete writerPatched.bonus_json
    if (typeof writerPatched.per_source_cap === 'undefined' || writerPatched.per_source_cap === null) {
      writerPatched.per_source_cap = 3
    }
    if (typeof writerPatched.hours === 'undefined' || writerPatched.hours === null) {
      writerPatched.hours = 24
    }
    const deliveryForForm = data.delivery ? { ...data.delivery } : undefined
    const patchedDelivery =
      deliveryForForm?.kind === 'feishu'
        ? {
            ...deliveryForForm,
            to_all_chat:
              typeof deliveryForForm.to_all_chat === 'number'
                ? deliveryForForm.to_all_chat
                : Number(deliveryForForm.to_all_chat ?? 0),
            chat_id: deliveryForForm.chat_id || undefined
          }
        : deliveryForForm
    const writerType = writerTypeForDelivery(
      patchedDelivery?.kind as DeliveryKind | undefined,
      writerPatched.type,
      getAllowedWriterTypes(pipelinePatched?.pipeline_class_id)
    )
    writerPatched.type = writerType
    form.setFieldsValue({
      pipeline: pipelinePatched,
      filters: data.filters || { all_categories: 1 },
      writer: writerPatched,
      delivery: patchedDelivery
    })
    writerSnapshotRef.current = writerPatched
    const computedKeys = buildCheckedKeysFromFilters(data.filters || {})
    setTreeCheckedKeys(computedKeys)
    if (deliveryForForm?.kind === 'feishu') {
      form.setFieldValue(["delivery", "to_all_chat"], patchedDelivery?.to_all_chat ?? 0)
      form.setFieldValue(["delivery", "chat_id"], patchedDelivery?.chat_id)
    }
    if (deliveryForForm?.kind) setDeliveryKind(deliveryForForm.kind as DeliveryKind)

    if (pipelinePatched.weekdays_json === undefined) {
      setWeekdaySelection(null)
      form.setFieldValue(["pipeline", "weekdays_json"], null)
    } else if (pipelinePatched.weekdays_json === null) {
      setWeekdaySelection(null)
      form.setFieldValue(["pipeline", "weekdays_json"], null)
    } else if (Array.isArray(pipelinePatched.weekdays_json)) {
      const normalized = pipelinePatched.weekdays_json
        .map((n: any) => Number(n))
        .filter((n: number) => Number.isFinite(n) && n >= 1 && n <= 7)
      setWeekdaySelection(normalized)
      form.setFieldValue(["pipeline", "weekdays_json"], normalized)
    }
  }

  useEffect(() => {
    if (!editing || !optionsReady || !sourcesReady) return
    let cancelled = false
    setFetchError(null)
    setLoading(true)
    fetchPipeline(Number(id))
      .then((data) => {
        if (cancelled) return
        applyPipelineData(data)
      })
      .catch((err: any) => {
        if (cancelled) return
        const detail = err?.response?.data?.detail
        const fallback = err?.message || '加载管线数据失败'
        const msg = typeof detail === 'string' && detail.trim() ? detail : fallback
        setFetchError(msg)
        message.error(msg)
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false)
        }
      })
    return () => {
      cancelled = true
    }
  }, [editing, id, optionsReady, sourcesReady, reloadKey])

  useEffect(() => {
    if (editing || !optionsReady) return
    const existing = form.getFieldValue(["pipeline", "pipeline_class_id"])
    if (existing) return
    const enabledClasses = options.pipeline_classes.filter((item) => item.enabled !== 0)
    const first = enabledClasses[0] || options.pipeline_classes[0]
    if (first && Number.isFinite(first.id)) {
      form.setFieldValue(["pipeline", "pipeline_class_id"], first.id)
    }
  }, [editing, form, options.pipeline_classes, optionsReady])

  useEffect(() => {
    if (editing) return
    if (!optionsReady) return
    if (form.isFieldsTouched(['writer', 'bonus_entries'])) return
    const current = form.getFieldValue(["writer", "bonus_entries"])
    const normalizedCurrent = Array.isArray(current) ? current : []
    const desired = defaultBonusEntries
    if (JSON.stringify(normalizedCurrent) !== JSON.stringify(desired)) {
      form.setFieldValue(["writer", "bonus_entries"], desired)
    }
  }, [defaultBonusEntries, editing, form, optionsReady])

  useEffect(() => {
    if (!editing) {
      const preset = [1, 2, 3, 4, 5, 6, 7]
      setWeekdaySelection(preset)
      form.setFieldValue(["pipeline", "weekdays_json"], preset)
    }
  }, [editing, form])

  useEffect(() => {
    if (editing) return
    if (!optionsReady || !sourcesReady) return
    setTreeCheckedKeys(allTreeKeys)
    form.setFieldsValue({
      filters: { all_categories: 1, categories_json: [], include_src_json: [] }
    })
  }, [allTreeKeys, editing, form, optionsReady, sourcesReady])

  useEffect(() => {
    if (writerSnapshotRef.current == null && pageReady) {
      const current = form.getFieldValue(["writer"]) ?? form.getFieldsValue(true)?.writer
      if (current) {
        writerSnapshotRef.current = current
      }
    }
  }, [form, pageReady, optionsReady, sourcesReady, editing])

  useEffect(() => {
    if (deliveryKind !== 'feishu') {
      setFeishuChats([])
    }
  }, [deliveryKind])

  const handleFetchFeishuChats = useCallback(async () => {
    const appId = (form.getFieldValue(["delivery", "app_id"]) as string | undefined)?.trim() || ''
    const appSecret = (form.getFieldValue(["delivery", "app_secret"]) as string | undefined)?.trim() || ''
    if (!appId || !appSecret) {
      message.warning('请先填写 App ID 和 App Secret')
      return
    }
    setFetchingFeishuChats(true)
    try {
      const chats = await fetchFeishuChats({ app_id: appId, app_secret: appSecret })
      setFeishuChats(chats)
      if (!chats.length) {
        message.info('未获取到群信息')
      }
      const current = form.getFieldValue(["delivery", "chat_id"])
      if (current && !chats.some((chat) => chat.chat_id === current)) {
        form.setFieldValue(["delivery", "chat_id"], undefined)
      } else if (!current && chats.length === 1) {
        form.setFieldValue(["delivery", "chat_id"], chats[0].chat_id)
      }
    } catch (error: any) {
      const detail = error?.response?.data?.detail
      message.error(detail || '获取群信息失败，请检查凭证')
    } finally {
      setFetchingFeishuChats(false)
    }
  }, [form])

  const normalizeCheckedKeys = (checked: any): string[] => {
    if (Array.isArray(checked)) return checked.map((k) => String(k))
    if (checked && Array.isArray(checked.checked)) return checked.checked.map((k) => String(k))
    return []
  }

  const handleTreeCheck = (checked: any) => {
    const normalized = normalizeCheckedKeys(checked)
    const filtersPayload = deriveFiltersFromChecked(normalized)
    const nextChecked = filtersPayload.all_categories === 1 ? allTreeKeys : normalized
    setTreeCheckedKeys(nextChecked)
    form.setFieldsValue({ filters: filtersPayload })
  }

  const onSubmit = async () => {
    const values = await form.validateFields()
    const allValues = form.getFieldsValue(true)
    const writerSnapshot = writerSnapshotRef.current || {}
    const writerFromForm = (allValues?.writer || {}) as any
    const writerFromValues = (values?.writer || {}) as any
    // Debug log to inspect weekday values
    // eslint-disable-next-line no-console
    console.log('debug weekdays', values?.pipeline?.weekdays_json, form.getFieldValue(["pipeline", "weekdays_json"]))
    setLoading(true)
    try {
      // Normalize weekdays for payload (source of truth = component state)
      const pipelinePayload: any = { ...(values?.pipeline || {}) }
      if (Array.isArray(weekdaySelection)) {
        const sorted = [...weekdaySelection]
          .map((n: any) => Number(n))
          .filter((n: number) => Number.isFinite(n) && n >= 1 && n <= 7)
          .sort((a: number, b: number) => a - b)
        pipelinePayload.weekdays_json = sorted
      } else if (weekdaySelection === null) {
        pipelinePayload.weekdays_json = null
      } else {
        delete pipelinePayload.weekdays_json
      }
      // Merge with snapshot to ensure untouched writer fields persist; prefer snapshot values when editing
      const baseWriter = editing ? writerSnapshot : {}
      const writerValues = { ...baseWriter, ...writerFromForm, ...writerFromValues } as any
      writerValues.weights_entries =
        writerFromForm?.weights_entries ??
        writerFromValues?.weights_entries ??
        baseWriter?.weights_entries ??
        writerValues.weights_entries
      writerValues.bonus_entries =
        writerFromForm?.bonus_entries ??
        writerFromValues?.bonus_entries ??
        baseWriter?.bonus_entries ??
        writerValues.bonus_entries
      writerValues.limit_per_category_overrides =
        writerFromForm?.limit_per_category_overrides ??
        writerFromValues?.limit_per_category_overrides ??
        baseWriter?.limit_per_category_overrides ??
        writerValues.limit_per_category_overrides
      const fallbackLimitDefault = editing ? baseWriter?.limit_per_category_default : CATEGORY_LIMIT_DEFAULT
      writerValues.limit_per_category_default =
        toNumber(writerFromForm?.limit_per_category_default) ??
        toNumber(writerFromValues?.limit_per_category_default) ??
        toNumber(fallbackLimitDefault) ??
        CATEGORY_LIMIT_DEFAULT
      writerValues.per_source_cap =
        toNumber(writerFromForm?.per_source_cap) ??
        toNumber(writerFromValues?.per_source_cap) ??
        toNumber(baseWriter?.per_source_cap) ??
        writerValues.per_source_cap
      const fallbackHours = editing ? toNumber(baseWriter?.hours) : 24
      writerValues.hours =
        toNumber(writerFromForm?.hours) ??
        toNumber(writerFromValues?.hours) ??
        fallbackHours ??
        24
      writerValues.type = writerTypeForDelivery(deliveryKind, writerValues.type, allowedWriterTypes)
      const limitMap = buildLimitPayload(
        toNumber(writerValues.limit_per_category_default),
        writerValues.limit_per_category_overrides
      )
      if (limitMap) {
        writerValues.limit_per_category = limitMap
      }
      delete writerValues.limit_per_category_default
      delete writerValues.limit_per_category_overrides

      const weightRecord = buildNumericRecord(
        (writerValues.weights_entries || []).map((entry: WeightEntry) => ({
          key: entry.metric,
          value: entry.weight
        }))
      )
      if (weightRecord) {
        writerValues.weights_json = weightRecord
      }
      delete writerValues.weights_entries

      const bonusRecord = buildNumericRecord(
        (writerValues.bonus_entries || []).map((entry: BonusEntry) => ({
          key: entry.source,
          value: entry.bonus
        }))
      )
      if (bonusRecord) {
        writerValues.bonus_json = bonusRecord
      }
      delete writerValues.bonus_entries

      let deliveryValues = values.delivery ? { ...values.delivery } : undefined
      if (deliveryKind === 'email') {
        const subject = buildEmailSubject(writerValues.hours)
        if (deliveryValues) {
          deliveryValues.subject_tpl = subject
        } else {
          deliveryValues = { subject_tpl: subject }
        }
      }

      const payload = {
        ...values,
        pipeline: pipelinePayload,
        writer: writerValues,
        delivery: deliveryValues ? { kind: deliveryKind, ...deliveryValues } : undefined
      }
      if (editing) {
        // 在编辑模式下，保存后刷新一次表单，确保与数据库一致
        await updatePipeline(Number(id), payload)
        const latest = await fetchPipeline(Number(id))
        applyPipelineData(latest)
        message.success('已更新')
      } else {
        // 新建完成后仍返回列表页（保留原行为）
        await createPipeline(payload)
        message.success('已创建')
        navigate('/')
      }
    } catch (e: any) {
      const detail = e?.response?.data?.detail
      if (detail) {
        if (typeof detail === 'string') {
          message.error(`保存失败：${detail}`)
        } else if (Array.isArray(detail)) {
          // FastAPI validation errors array
          const first = detail[0]
          const loc = Array.isArray(first?.loc) ? first.loc.join('.') : ''
          const msg = first?.msg || JSON.stringify(detail)
          message.error(`保存失败：${loc ? loc + ' - ' : ''}${msg}`)
        } else {
          message.error('保存失败')
        }
      } else {
        message.error('保存失败')
      }
    } finally {
      setLoading(false)
    }
  }

  const renderDeliveryFields = () => {
    if (deliveryKind === 'email') {
      return (
        <Space direction="vertical" style={{ width: '100%' }}>
          <Form.Item name={["delivery", "email"]} label="收件邮箱" rules={[{ required: true }]}>
            <Input placeholder="example@domain.com" />
          </Form.Item>
        </Space>
      )
    }

    const sendToAll = Number(toAllChatValue) === 1
    const currentChatId = chatIdValue

    return (
      <Space direction="vertical" style={{ width: '100%' }}>
        <Form.Item name={["delivery", "app_id"]} label="App ID" rules={[{ required: true }]}>
          <Input />
        </Form.Item>
        <Form.Item name={["delivery", "app_secret"]} label="App Secret" rules={[{ required: true }]}>
          <Input.Password />
        </Form.Item>
        <Form.Item name={["delivery", "to_all_chat"]} label="群发到所有">
          <Radio.Group>
            <Radio value={1}>是</Radio>
            <Radio value={0}>否</Radio>
          </Radio.Group>
        </Form.Item>
        {!sendToAll && (
          <Form.Item
            label="群聊"
            required
            extra={
              feishuChats.length
                ? '点击下方表格中的任意行即可选择目标群聊'
                : '点击下方按钮拉取机器人所在群'
            }
          >
            <Space direction="vertical" style={{ width: '100%' }} size="small">
              <Form.Item
                noStyle
                name={["delivery", "chat_id"]}
                rules={[{ required: true, message: '请选择群聊' }]}
              >
                <Input readOnly placeholder="请从下方表格选择群聊" />
              </Form.Item>
              {currentChatId ? (
                <Space size="small" wrap align="baseline">
                  <Typography.Text>当前记录：</Typography.Text>
                  <Typography.Text code copyable>
                    {currentChatId}
                  </Typography.Text>
                  <Typography.Text type="secondary">
                    {selectedFeishuChat?.name
                      ? `（${selectedFeishuChat.name}）`
                      : '（尚未加载群名称）'}
                  </Typography.Text>
                </Space>
              ) : (
                <Typography.Text type="secondary">暂无记录，请先选择群聊。</Typography.Text>
              )}
              <Space wrap>
                <Button type="default" onClick={handleFetchFeishuChats} loading={fetchingFeishuChats}>
                  获取机器人所在群信息
                </Button>
                {currentChatId ? (
                  <Button
                    type="link"
                    onClick={() => {
                      form.setFieldValue(["delivery", "chat_id"], undefined)
                    }}
                  >
                    清除选择
                  </Button>
                ) : null}
              </Space>
              <Table
                size="small"
                bordered
                rowKey="chat_id"
                columns={feishuChatColumns}
                dataSource={feishuChats}
                loading={fetchingFeishuChats}
                pagination={{ pageSize: 6, hideOnSinglePage: true }}
                rowSelection={{
                  type: 'radio',
                  selectedRowKeys: currentChatId ? [currentChatId] : [],
                  onChange: (selectedRowKeys) => {
                    const [first] = selectedRowKeys
                    const normalized =
                      typeof first === 'string'
                        ? first
                        : typeof first === 'number'
                        ? String(first)
                        : first
                        ? String(first)
                        : undefined
                    form.setFieldValue(["delivery", "chat_id"], normalized)
                  }
                }}
                locale={{
                  emptyText: fetchingFeishuChats
                    ? '正在加载群信息...'
                    : '尚无数据，请点击“获取机器人所在群信息”'
                }}
              />
            </Space>
          </Form.Item>
        )}
        <Form.Item name={["delivery", "title_tpl"]} label="卡片标题">
          <Input placeholder="通知" />
        </Form.Item>
      </Space>
    )
  }

  const currentTabIndex = useMemo(() => TAB_ORDER.indexOf(activeTab), [activeTab])
  const prevTab = currentTabIndex > 0 ? TAB_ORDER[currentTabIndex - 1] : null
  const nextTab =
    currentTabIndex >= 0 && currentTabIndex < TAB_ORDER.length - 1
      ? TAB_ORDER[currentTabIndex + 1]
      : null

  return (
    <Form
      form={form}
      layout="vertical"
      initialValues={initialFormValues}
      onValuesChange={(_, all) => {
        if (all?.writer) {
          writerSnapshotRef.current = all.writer
        }
      }}
    >
      <Card loading={pageLoading}>
        {fetchError ? (
          <Alert
            type="error"
            showIcon
            style={{ marginBottom: 12 }}
            message="加载管线数据失败"
            description={fetchError}
            action={
              <Button size="small" onClick={() => setReloadKey((key) => key + 1)} loading={loading}>
                重新加载
              </Button>
            }
          />
        ) : null}
        <Tabs
          activeKey={activeTab}
          onChange={(k) => setActiveTab(k as TabKey)}
          items={[
            {
              key: 'base',
              label: '基础',
              children: (
                <Space direction="vertical" style={{ width: '100%' }}>
                  <Form.Item name={["pipeline", "name"]} label="名称">
                    <Input placeholder="可留空，名称仅用于展示" />
                  </Form.Item>
                  <Form.Item
                    name={["pipeline", "pipeline_class_id"]}
                    label="管线类别"
                    rules={[{ required: true, message: '请选择管线类别' }]}
                  >
                    <Select
                      placeholder="请选择管线类别"
                      options={options.pipeline_classes
                        .filter((item) => item.enabled !== 0)
                        .map((item) => ({
                          value: item.id,
                          label: item.label_zh || item.key
                        }))}
                    />
                  </Form.Item>
                  <Form.Item name={["pipeline", "enabled"]} label="启用">
                    <Radio.Group>
                      <Radio value={1}>启用</Radio>
                      <Radio value={0}>停用</Radio>
                    </Radio.Group>
                  </Form.Item>
                  <Form.Item
                    name={["pipeline", "weekdays_json"]}
                    label="在以下星期早9:30左右投放"
                    tooltip="留空=不限制；全选=等价于不限制；选择为空数组=永不按星期触发（常用于临时停发）"
                  >
                    <Space direction="vertical" style={{ width: '100%' }}>
                      <Checkbox.Group
                        options={weekdayOptions}
                        value={Array.isArray(weekdaySelection) ? weekdaySelection : []}
                        onChange={(vals) => {
                          const normalized = (vals as Array<number | string>).map((v) => Number(v))
                          setWeekdaySelection(normalized)
                          form.setFieldValue(["pipeline", "weekdays_json"], normalized)
                        }}
                      />
                      <Space size="small">
                        <Button
                          size="small"
                          onClick={() => {
                            const preset = [1, 2, 3, 4, 5]
                            setWeekdaySelection(preset)
                            form.setFieldValue(["pipeline", "weekdays_json"], preset)
                          }}
                        >
                          仅工作日
                        </Button>
                        <Button
                          size="small"
                          onClick={() => {
                            const preset = [6, 7]
                            setWeekdaySelection(preset)
                            form.setFieldValue(["pipeline", "weekdays_json"], preset)
                          }}
                        >
                          仅周末
                        </Button>
                        <Button
                          size="small"
                          onClick={() => {
                            const preset = [1, 2, 3, 4, 5, 6, 7]
                            setWeekdaySelection(preset)
                            form.setFieldValue(["pipeline", "weekdays_json"], preset)
                          }}
                        >
                          全选
                        </Button>
                        <Button
                          size="small"
                          danger
                          onClick={() => {
                            const preset: number[] = []
                            setWeekdaySelection(preset)
                            form.setFieldValue(["pipeline", "weekdays_json"], preset)
                          }}
                        >
                          清空
                        </Button>
                      </Space>
                      <Typography.Text type="secondary">
                        说明：系统按北京时区判断今天的星期；Runner 提供 --ignore-weekday 或 FORCE_RUN=1 覆盖。
                      </Typography.Text>
                    </Space>
                  </Form.Item>
                  <Form.Item name={["pipeline", "description"]} label="描述">
                    <Input.TextArea rows={3} />
                  </Form.Item>
                </Space>
              )
            },
            {
              key: 'filters',
              label: '来源',
              children: (
                <Space direction="vertical" style={{ width: '100%' }}>
                  <Typography.Text type="secondary">
                    勾选来源树：一级为“全部”，二级为类别，三级为来源。全选=all_categories=1；勾选某类别=记录到
                    categories_json；仅勾选具体来源=include_src_json。
                  </Typography.Text>
                  <Tree
                    checkable
                    selectable={false}
                    defaultExpandAll
                    checkedKeys={treeCheckedKeys}
                    onCheck={handleTreeCheck}
                    treeData={treeData}
                  />
                </Space>
              )
            },
            {
              key: 'writer',
              label: 'Writer',
              children: (
                <Space direction="vertical" style={{ width: '100%' }}>
                  <Form.Item
                    name={["writer", "type"]}
                    label="Writer 类型"
                    rules={[{ required: true, message: '请选择 Writer 类型' }]}
                  >
                    <Select
                      showSearch
                      placeholder="选择 Writer 类型"
                      options={writerTypeOptions}
                      optionFilterProp="label"
                    />
                  </Form.Item>
                  <Form.Item name={["writer", "hours"]} label="回溯小时" initialValue={24}>
                    <InputNumber min={1} />
                  </Form.Item>
                  <Form.Item
                    name={["writer", "limit_per_category_default"]}
                    label="每类默认上限"
                    rules={[{ required: true, message: '请输入默认每类上限' }]}
                  >
                    <InputNumber min={1} placeholder={CATEGORY_LIMIT_DEFAULT} />
                  </Form.Item>
                  <Form.Item label="每类单独上限 (可选)">
                    <Space direction="vertical" style={{ width: '100%' }}>
                      <Typography.Text type="secondary">覆盖默认值，为特定类别设置不同上限。</Typography.Text>
                      <Form.List name={["writer", "limit_per_category_overrides"]}>
                        {(fields, { add, remove }) => {
                          const overridesValue = (limitOverrides ?? []) as LimitOverride[]
                          const selectedCategories = new Set(
                            overridesValue
                              .map((entry) => (entry?.category ? String(entry.category).trim() : undefined))
                              .filter((v): v is string => !!v)
                          )

                          const dataSource = fields.map((field) => ({ key: field.key, field }))

                          const columns: ColumnsType<{ field: FormListFieldData }> = [
                            {
                              title: '类别',
                              dataIndex: 'field',
                              key: 'category',
                              render: (_, { field }) => {
                                const currentCategory =
                                  overridesValue?.[field.name] && overridesValue?.[field.name]?.category
                                    ? String(overridesValue?.[field.name]?.category).trim()
                                    : undefined
                                const usedByOthers = new Set<string>()
                                overridesValue.forEach((entry, idx) => {
                                  if (idx === field.name) return
                                  const cat = entry?.category ? String(entry.category).trim() : ''
                                  if (cat) usedByOthers.add(cat)
                                })
                                const categoryOptions = options.categories
                                  .map((cat) => ({ value: cat, label: cat }))
                                  .filter((opt) => opt.value === currentCategory || !usedByOthers.has(opt.value))
                                if (
                                  currentCategory &&
                                  !categoryOptions.some((opt) => opt.value === currentCategory)
                                ) {
                                  categoryOptions.unshift({ value: currentCategory, label: currentCategory })
                                }
                                return (
                                  <Form.Item
                                    name={[field.name, 'category']}
                                    fieldKey={`${field.key}-category`}
                                    rules={[{ required: true, message: '请选择类别' }]}
                                    style={{ marginBottom: 0 }}
                                  >
                                    <Select
                                      showSearch
                                      placeholder="选择类别"
                                      options={categoryOptions}
                                      style={{ minWidth: 200 }}
                                    />
                                  </Form.Item>
                                )
                              }
                            },
                            {
                              title: '上限',
                              dataIndex: 'field',
                              key: 'limit',
                              width: 140,
                              render: (_, { field }) => (
                                <Form.Item
                                  name={[field.name, 'limit']}
                                  fieldKey={`${field.key}-limit`}
                                  rules={[{ required: true, message: '请输入上限' }]}
                                  style={{ marginBottom: 0 }}
                                >
                                  <InputNumber min={1} />
                                </Form.Item>
                              )
                            },
                            {
                              title: '操作',
                              dataIndex: 'actions',
                              key: 'actions',
                              align: 'center',
                              width: 80,
                              render: (_, { field }) => (
                                <Button
                                  type="text"
                                  danger
                                  icon={<MinusCircleOutlined />}
                                  onClick={() => remove(field.name)}
                                />
                              )
                            }
                          ]

                          const handleAddOverride = () => {
                            const next = options.categories.find((c) => !selectedCategories.has(c))
                            add({
                              category: next,
                              limit:
                                form.getFieldValue(["writer", "limit_per_category_default"]) || CATEGORY_LIMIT_DEFAULT
                            })
                          }

                          return (
                            <Space direction="vertical" style={{ width: '100%' }} size="middle">
                              <Table
                                size="small"
                                pagination={false}
                                rowKey="key"
                                dataSource={dataSource}
                                columns={columns}
                                tableLayout="fixed"
                                locale={{ emptyText: '暂无类别上限' }}
                              />
                              <Button
                                type="dashed"
                                icon={<PlusOutlined />}
                                onClick={handleAddOverride}
                                style={{ width: 'fit-content' }}
                              >
                                添加类别上限
                              </Button>
                            </Space>
                          )
                        }}
                      </Form.List>
                    </Space>
                  </Form.Item>
                  <Form.Item name={["writer", "per_source_cap"]} label="每来源上限">
                    <InputNumber placeholder={3} min={1} />
                  </Form.Item>
                  <Form.Item label="维度权重">
                    <Space direction="vertical" style={{ width: '100%' }}>
                      <Typography.Text type="secondary">
                        调整取稿时的排序权重，可修改默认值或新增自定义指标。
                      </Typography.Text>
                      <Form.List name={["writer", "weights_entries"]}>
                        {(fields, { add, remove }) => {
                          const entries =
                            (form.getFieldValue(["writer", "weights_entries"]) as WeightEntry[] | undefined) ?? []
                          const selectedMetrics = new Set(
                            entries
                              .map((entry) =>
                                entry && typeof entry.metric === 'string' && entry.metric.trim()
                                  ? entry.metric.trim()
                                  : undefined
                              )
                              .filter((key): key is string => !!key)
                          )
                          const availableMetrics = metrics.filter((item) => !selectedMetrics.has(item.key.trim()))
                          const dataSource = fields.map((field) => ({ key: field.key, field }))
                          const columns: ColumnsType<{ field: FormListFieldData }> = [
                            {
                              title: '指标',
                              dataIndex: 'field',
                              key: 'metric',
                              render: (_, { field }) => {
                                const rawMetric =
                                  entries?.[field.name] && typeof entries[field.name]?.metric === 'string'
                                    ? entries[field.name]?.metric
                                    : undefined
                                const currentMetric =
                                  typeof rawMetric === 'string' && rawMetric.trim() ? rawMetric.trim() : undefined
                                const usedByOthers = new Set<string>()
                                entries.forEach((entry, idx) => {
                                  if (idx === field.name) return
                                  if (entry && typeof entry.metric === 'string' && entry.metric.trim()) {
                                    usedByOthers.add(entry.metric.trim())
                                  }
                                })
                                const baseOptions = weightSuggestions.filter(
                                  (option) => option.value === currentMetric || !usedByOthers.has(option.value)
                                )
                                const options = baseOptions.map((option) => ({
                                  value: option.value,
                                  label: option.label
                                }))
                                if (
                                  typeof currentMetric === 'string' &&
                                  currentMetric &&
                                  !options.some((opt) => opt.value === currentMetric)
                                ) {
                                  options.unshift({ value: currentMetric, label: currentMetric })
                                }
                                return (
                                  <Form.Item
                                    name={[field.name, 'metric']}
                                    fieldKey={`${field.key}-metric`}
                                    rules={[{ required: true, message: '请选择指标' }]}
                                    style={{ marginBottom: 0 }}
                                  >
                                    <Select
                                      showSearch
                                      optionFilterProp="label"
                                      placeholder="请选择指标"
                                      options={options}
                                      style={{ minWidth: 220 }}
                                    />
                                  </Form.Item>
                                )
                              }
                            },
                            {
                              title: '权重',
                              dataIndex: 'field',
                              key: 'weight',
                              render: (_, { field }) => (
                                <Form.Item
                                  name={[field.name, 'weight']}
                                  fieldKey={`${field.key}-weight`}
                                  rules={[{ required: true, message: '请输入权重' }]}
                                  style={{ marginBottom: 0 }}
                                >
                                  <InputNumber min={0} step={0.05} style={{ width: '100%' }} />
                                </Form.Item>
                              )
                            },
                            {
                              title: '操作',
                              dataIndex: 'actions',
                              key: 'actions',
                              align: 'center',
                              width: 80,
                              render: (_, { field }) => (
                                <Button
                                  type="text"
                                  danger
                                  icon={<MinusCircleOutlined />}
                                  onClick={() => remove(field.name)}
                                />
                              )
                            }
                          ]
                          const handleAddMetric = () => {
                            const next = availableMetrics[0]
                            if (!next) return
                            const metricKey = typeof next.key === 'string' ? next.key.trim() : next.key
                            add({
                              metric: metricKey,
                              weight: typeof next.default_weight === 'number' ? next.default_weight : 0.1
                            })
                          }
                          return (
                            <Space direction="vertical" style={{ width: '100%' }} size="middle">
                              <Table
                                size="small"
                                pagination={false}
                                rowKey="key"
                                dataSource={dataSource}
                                columns={columns}
                                tableLayout="fixed"
                                locale={{ emptyText: '暂无指标' }}
                              />
                              <Button
                                type="dashed"
                                icon={<PlusOutlined />}
                                onClick={handleAddMetric}
                                disabled={!availableMetrics.length}
                                style={{ width: 'fit-content' }}
                              >
                                添加指标
                              </Button>
                            </Space>
                          )
                        }}
                      </Form.List>
                    </Space>
                  </Form.Item>
                  <Form.Item label="来源加权 (可选)">
                    <Space direction="vertical" style={{ width: '100%' }}>
                      <Typography.Text type="secondary">
                        为特定来源加分，正数代表额外加分。
                      </Typography.Text>
                      <Form.List name={["writer", "bonus_entries"]}>
                        {(fields, { add, remove }) => {
                          const entries =
                            (form.getFieldValue(["writer", "bonus_entries"]) as BonusEntry[] | undefined) ?? []
                          const selectedSources = new Set(
                            entries
                              .map((entry) => (entry && typeof entry.source === 'string' ? entry.source.trim() : undefined))
                              .filter((key): key is string => !!key)
                          )
                          const dataSource = fields.map((field) => ({ key: field.key, field }))
                          const columns: ColumnsType<{ field: FormListFieldData }> = [
                            {
                              title: '来源',
                              dataIndex: 'field',
                              key: 'source',
                              render: (_, { field }) => {
                                const currentSource =
                                  (entries?.[field.name] && typeof entries[field.name]?.source === 'string'
                                    ? entries[field.name]?.source
                                    : undefined) ?? undefined
                                const usedByOthers = new Set<string>()
                                entries.forEach((entry, idx) => {
                                  if (idx === field.name) return
                                  if (entry && typeof entry.source === 'string' && entry.source.trim()) {
                                    usedByOthers.add(entry.source.trim())
                                  }
                                })
                                const baseOptions = bonusSuggestions
                                  .filter((option) => option.value === currentSource || !usedByOthers.has(option.value))
                                  .map((option) => ({ value: option.value, label: option.label }))
                                if (
                                  typeof currentSource === 'string' &&
                                  currentSource &&
                                  !baseOptions.some((opt) => opt.value === currentSource)
                                ) {
                                  baseOptions.unshift({ value: currentSource, label: currentSource })
                                }
                                return (
                                  <Form.Item
                                    name={[field.name, 'source']}
                                    fieldKey={`${field.key}-source`}
                                    rules={[{ required: true, message: '请输入来源标识' }]}
                                    style={{ marginBottom: 0 }}
                                  >
                                    <AutoComplete
                                      options={baseOptions}
                                      filterOption={filterAutoCompleteOption}
                                      placeholder="openai.research"
                                    >
                                      <Input placeholder="openai.research" style={{ minWidth: 200 }} />
                                    </AutoComplete>
                                  </Form.Item>
                                )
                              }
                            },
                            {
                              title: '加权',
                              dataIndex: 'field',
                              key: 'bonus',
                              render: (_, { field }) => (
                                <Form.Item
                                  name={[field.name, 'bonus']}
                                  fieldKey={`${field.key}-bonus`}
                                  rules={[{ required: true, message: '请输入加权分值' }]}
                                  style={{ marginBottom: 0 }}
                                >
                                  <InputNumber step={0.5} />
                                </Form.Item>
                              )
                            },
                            {
                              title: '操作',
                              dataIndex: 'actions',
                              key: 'actions',
                              align: 'center',
                              width: 80,
                              render: (_, { field }) => (
                                <Button
                                  type="text"
                                  danger
                                  icon={<MinusCircleOutlined />}
                                  onClick={() => remove(field.name)}
                                />
                              )
                            }
                          ]
                          const handleAddSource = () => {
                            const suggestion = BONUS_SUGGESTIONS.find((item) => !selectedSources.has(item.key))
                            if (suggestion) {
                              add({ source: suggestion.key, bonus: suggestion.defaultValue })
                            } else {
                              add({ bonus: 1 })
                            }
                          }
                          return (
                            <Space direction="vertical" style={{ width: '100%' }} size="middle">
                              <Table
                                size="small"
                                pagination={false}
                                rowKey="key"
                                dataSource={dataSource}
                                columns={columns}
                                tableLayout="fixed"
                                locale={{ emptyText: '暂无来源' }}
                              />
                              <Button
                                type="dashed"
                                icon={<PlusOutlined />}
                                onClick={handleAddSource}
                                style={{ width: 'fit-content' }}
                              >
                                添加来源
                              </Button>
                            </Space>
                          )
                        }}
                      </Form.List>
                    </Space>
                  </Form.Item>
                </Space>
              )
            },
            {
              key: 'delivery',
              label: '投递',
              children: (
                <Space direction="vertical" style={{ width: '100%' }}>
                  <Radio.Group value={deliveryKind} onChange={(e) => setDeliveryKind(e.target.value)}>
                    <Radio value="email">Email</Radio>
                    <Radio value="feishu">Feishu</Radio>
                  </Radio.Group>
                  {renderDeliveryFields()}
                </Space>
              )
            }
          ]}
        />
      </Card>
      <Space style={{ marginTop: 16 }}>
        {prevTab ? (
          <Button onClick={() => setActiveTab(prevTab)} disabled={actionsDisabled}>
            上一项
          </Button>
        ) : null}
        {editing ? (
          <Button type="primary" onClick={onSubmit} loading={loading} disabled={actionsDisabled}>
            保存
          </Button>
        ) : nextTab ? (
          <Button type="primary" onClick={() => setActiveTab(nextTab)} disabled={actionsDisabled}>
            下一项
          </Button>
        ) : (
          <Button type="primary" onClick={onSubmit} loading={loading} disabled={actionsDisabled}>
            创建
          </Button>
        )}
        <Button onClick={() => navigate('/')}>取消</Button>
      </Space>
    </Form>
  )
}
