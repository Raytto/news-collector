import { useEffect, useMemo, useState } from 'react'
import { MinusCircleOutlined, PlusOutlined } from '@ant-design/icons'
import { AutoComplete, Button, Card, Form, Input, InputNumber, Radio, Select, Space, Tabs, Typography, message } from 'antd'
import { useNavigate, useParams } from 'react-router-dom'
import { createPipeline, fetchOptions, fetchPipeline, updatePipeline } from '../api'

type DeliveryKind = 'email' | 'feishu'

type LimitOverride = { category?: string; limit?: number }
type WeightEntry = { metric?: string; weight?: number }
type BonusEntry = { source?: string; bonus?: number }

const CATEGORY_LIMIT_DEFAULT = 10

const WEIGHT_FIELDS = [
  { key: 'timeliness', label: '时效性', defaultValue: 0.2 },
  { key: 'game_relevance', label: '游戏相关度', defaultValue: 0.4 },
  { key: 'mobile_game_relevance', label: '手游相关度', defaultValue: 0.2 },
  { key: 'ai_relevance', label: 'AI相关度', defaultValue: 0.1 },
  { key: 'tech_relevance', label: '科技相关度', defaultValue: 0.05 },
  { key: 'quality', label: '质量', defaultValue: 0.25 },
  { key: 'insight', label: '洞察', defaultValue: 0.35 },
  { key: 'depth', label: '深度', defaultValue: 0.25 },
  { key: 'novelty', label: '新颖度', defaultValue: 0.2 }
] as const

const DEFAULT_WEIGHT_VALUES: Record<string, number> = WEIGHT_FIELDS.reduce(
  (acc, cur) => ({ ...acc, [cur.key]: cur.defaultValue }),
  {}
)

const BONUS_SUGGESTIONS = [
  { key: 'openai.research', label: 'OpenAI Research', defaultValue: 3 },
  { key: 'deepmind', label: 'DeepMind', defaultValue: 1 },
  { key: 'qbitai-zhiku', label: '量子位智库', defaultValue: 2 }
] as const

const DEFAULT_BONUS_VALUES: Record<string, number> = BONUS_SUGGESTIONS.reduce(
  (acc, cur) => ({ ...acc, [cur.key]: cur.defaultValue }),
  {}
)

const filterAutoCompleteOption = (input: string, option?: { value: string; label?: string }) => {
  if (!option) return false
  const normalized = input.trim().toLowerCase()
  if (!normalized) return true
  const labelText = option.label ? option.label.toLowerCase() : ''
  return option.value.toLowerCase().includes(normalized) || labelText.includes(normalized)
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

function normalizeWeightsForForm(weights: unknown): WeightEntry[] {
  const result: WeightEntry[] = []
  const provided = weights && typeof weights === 'object' ? (weights as Record<string, unknown>) : {}
  const seen = new Set<string>()

  WEIGHT_FIELDS.forEach(({ key, defaultValue }) => {
    const numeric = toNumber(provided[key])
    result.push({ metric: key, weight: typeof numeric === 'number' ? numeric : defaultValue })
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

function normalizeBonusForForm(bonus: unknown): BonusEntry[] {
  const result: BonusEntry[] = []
  const provided = bonus && typeof bonus === 'object' ? (bonus as Record<string, unknown>) : {}
  const seen = new Set<string>()

  BONUS_SUGGESTIONS.forEach(({ key, defaultValue }) => {
    const numeric = toNumber(provided[key])
    result.push({ source: key, bonus: typeof numeric === 'number' ? numeric : defaultValue })
    seen.add(key)
  })

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

export default function PipelineForm() {
  const { id } = useParams()
  const editing = !!id
  const [form] = Form.useForm()
  const navigate = useNavigate()
  const [loading, setLoading] = useState(false)
  const [options, setOptions] = useState<{ categories: string[]; writer_types: string[]; delivery_kinds: string[] }>({
    categories: [],
    writer_types: [],
    delivery_kinds: []
  })
  const [deliveryKind, setDeliveryKind] = useState<DeliveryKind>('email')
  const limitOverrides = Form.useWatch<LimitOverride[]>(["writer", "limit_per_category_overrides"], form)

  const initialFormValues = useMemo(
    () => ({
      pipeline: { enabled: 1 },
      filters: { all_categories: 1 },
      writer: {
        hours: 24,
        limit_per_category_default: CATEGORY_LIMIT_DEFAULT,
        limit_per_category_overrides: [],
        per_source_cap: 3,
        weights_entries: normalizeWeightsForForm(DEFAULT_WEIGHT_VALUES),
        bonus_entries: normalizeBonusForForm(DEFAULT_BONUS_VALUES)
      }
    }),
    []
  )
  const weightSuggestions = useMemo(
    () =>
      WEIGHT_FIELDS.map((item) => ({
        value: item.key,
        label: `${item.label} (${item.key})`
      })),
    []
  )
  const bonusSuggestions = useMemo(
    () =>
      BONUS_SUGGESTIONS.map((item) => ({
        value: item.key,
        label: `${item.label} (${item.key})`
      })),
    []
  )

  useEffect(() => {
    fetchOptions().then(setOptions)
  }, [])

  useEffect(() => {
    if (!editing) return
    setLoading(true)
    fetchPipeline(Number(id))
      .then((data) => {
        const w = data.writer || {}
        const writerPatched: any = { ...w }
        const { defaultValue, overrides } = normalizeLimitForForm(w?.limit_per_category)
        writerPatched.limit_per_category_default = defaultValue
        writerPatched.limit_per_category_overrides = overrides
        writerPatched.weights_entries = normalizeWeightsForForm(w?.weights_json ?? DEFAULT_WEIGHT_VALUES)
        writerPatched.bonus_entries = normalizeBonusForForm(w?.bonus_json ?? DEFAULT_BONUS_VALUES)
        delete writerPatched.limit_per_category
        delete writerPatched.weights_json
        delete writerPatched.bonus_json
        if (typeof writerPatched.per_source_cap === 'undefined' || writerPatched.per_source_cap === null) {
          writerPatched.per_source_cap = 3
        }
        if (typeof writerPatched.hours === 'undefined' || writerPatched.hours === null) {
          writerPatched.hours = 24
        }
        form.setFieldsValue({
          pipeline: data.pipeline,
          filters: data.filters || { all_categories: 1 },
          writer: writerPatched,
          delivery: data.delivery
        })
        if (data.delivery?.kind) setDeliveryKind(data.delivery.kind)
      })
      .finally(() => setLoading(false))
  }, [id])

  const onSubmit = async () => {
    const values = await form.validateFields()
    setLoading(true)
    try {
      const writerValues = values.writer ? { ...values.writer } : undefined
      if (writerValues) {
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
      }

      const payload = {
        ...values,
        writer: writerValues,
        delivery: values.delivery ? { kind: deliveryKind, ...values.delivery } : undefined
      }
      if (editing) {
        await updatePipeline(Number(id), payload)
        message.success('已更新')
      } else {
        await createPipeline(payload)
        message.success('已创建')
      }
      navigate('/')
    } catch (e) {
      message.error('保存失败')
    } finally {
      setLoading(false)
    }
  }

  const deliveryFields = useMemo(() => {
    if (deliveryKind === 'email') {
      return (
        <Space direction="vertical" style={{ width: '100%' }}>
          <Form.Item name={["delivery", "email"]} label="收件邮箱" rules={[{ required: true }]}>
            <Input placeholder="example@domain.com" />
          </Form.Item>
          <Form.Item name={["delivery", "subject_tpl"]} label="主题模板">
            <Input placeholder="${date_zh}整合" />
          </Form.Item>
        </Space>
      )
    }
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
        <Form.Item name={["delivery", "chat_id"]} label="Chat ID">
          <Input placeholder="留空=所有所在群" />
        </Form.Item>
        <Form.Item name={["delivery", "title_tpl"]} label="标题模板">
          <Input placeholder="通知" />
        </Form.Item>
      </Space>
    )
  }, [deliveryKind])

  return (
    <Card loading={loading}>
      <Form form={form} layout="vertical" initialValues={initialFormValues}>
        <Tabs
          items={[
            {
              key: 'base',
              label: '基础',
              children: (
                <Space direction="vertical" style={{ width: '100%' }}>
                  <Form.Item name={["pipeline", "name"]} label="名称" rules={[{ required: true }]}>
                    <Input />
                  </Form.Item>
                  <Form.Item name={["pipeline", "enabled"]} label="启用">
                    <Radio.Group>
                      <Radio value={1}>启用</Radio>
                      <Radio value={0}>停用</Radio>
                    </Radio.Group>
                  </Form.Item>
                  <Form.Item name={["pipeline", "description"]} label="描述">
                    <Input.TextArea rows={3} />
                  </Form.Item>
                </Space>
              )
            },
            {
              key: 'filters',
              label: '过滤',
              children: (
                <Space direction="vertical" style={{ width: '100%' }}>
                  <Form.Item name={["filters", "all_categories"]} label="全部类别">
                    <Radio.Group>
                      <Radio value={1}>是</Radio>
                      <Radio value={0}>否</Radio>
                    </Radio.Group>
                  </Form.Item>
                  <Form.Item noStyle shouldUpdate>
                    {() =>
                      form.getFieldValue(["filters", "all_categories"]) === 0 && (
                        <Form.Item name={["filters", "categories_json"]} label="类别白名单">
                          <Select
                            mode="multiple"
                            options={options.categories.map((c) => ({ value: c, label: c }))}
                            placeholder="选择类别"
                          />
                        </Form.Item>
                      )
                    }
                  </Form.Item>
                </Space>
              )
            },
            {
              key: 'writer',
              label: 'Writer',
              children: (
                <Space direction="vertical" style={{ width: '100%' }}>
                  <Form.Item name={["writer", "type"]} label="类型" rules={[{ required: true }]}>
                    <Select
                      options={options.writer_types.map((w) => ({
                        value: w,
                        label: w === 'feishu_md' ? 'feishu' : w === 'info_html' ? 'email' : w
                      }))}
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
                        {(fields, { add, remove }) => (
                          <Space direction="vertical" style={{ width: '100%' }} size="small">
                            {fields.map((field) => {
                              const overridesValue = limitOverrides ?? []
                              const currentCategory = overridesValue?.[field.name]?.category
                              const usedCategories = new Set(
                                (overridesValue || [])
                                  .map((entry, idx) => (idx === field.name ? undefined : entry?.category))
                                  .filter((cat): cat is string => !!cat)
                              )
                              const categoryOptions = options.categories.map((category) => ({
                                value: category,
                                label: category,
                                disabled: usedCategories.has(category) && category !== currentCategory
                              }))
                              return (
                                <Space key={field.key} align="baseline" wrap>
                                  <Form.Item
                                    {...field}
                                    name={[field.name, 'category']}
                                    fieldKey={`${field.fieldKey}-category`}
                                    rules={[{ required: true, message: '请选择类别' }]}
                                  >
                                    <Select
                                      showSearch
                                      placeholder="选择类别"
                                      options={categoryOptions}
                                      style={{ minWidth: 200 }}
                                    />
                                  </Form.Item>
                                  <Form.Item
                                    {...field}
                                    name={[field.name, 'limit']}
                                    fieldKey={`${field.fieldKey}-limit`}
                                    rules={[{ required: true, message: '请输入上限' }]}
                                  >
                                    <InputNumber min={1} />
                                  </Form.Item>
                                  <Button
                                    type="text"
                                    danger
                                    icon={<MinusCircleOutlined />}
                                    onClick={() => remove(field.name)}
                                  />
                                </Space>
                              )
                            })}
                            <Button
                              type="dashed"
                              icon={<PlusOutlined />}
                              onClick={() =>
                                add({
                                  limit:
                                    form.getFieldValue(["writer", "limit_per_category_default"]) ||
                                    CATEGORY_LIMIT_DEFAULT
                                })
                              }
                              style={{ width: 'fit-content' }}
                            >
                              添加类别上限
                            </Button>
                          </Space>
                        )}
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
                        {(fields, { add, remove }) => (
                          <Space direction="vertical" style={{ width: '100%' }} size="small">
                            {fields.map((field) => (
                              <Space key={field.key} align="baseline" wrap>
                                <Form.Item
                                  {...field}
                                  name={[field.name, 'metric']}
                                  fieldKey={`${field.fieldKey}-metric`}
                                  rules={[{ required: true, message: '请输入指标名称' }]}
                                >
                                  <AutoComplete
                                    options={weightSuggestions}
                                    filterOption={filterAutoCompleteOption}
                                    placeholder="timeliness"
                                  >
                                    <Input placeholder="timeliness" style={{ minWidth: 200 }} />
                                  </AutoComplete>
                                </Form.Item>
                                <Form.Item
                                  {...field}
                                  name={[field.name, 'weight']}
                                  fieldKey={`${field.fieldKey}-weight`}
                                  rules={[{ required: true, message: '请输入权重' }]}
                                >
                                  <InputNumber min={0} step={0.05} />
                                </Form.Item>
                                <Button
                                  type="text"
                                  danger
                                  icon={<MinusCircleOutlined />}
                                  onClick={() => remove(field.name)}
                                />
                              </Space>
                            ))}
                            <Button
                              type="dashed"
                              icon={<PlusOutlined />}
                              onClick={() => add({ weight: 0.1 })}
                              style={{ width: 'fit-content' }}
                            >
                              添加指标
                            </Button>
                          </Space>
                        )}
                      </Form.List>
                    </Space>
                  </Form.Item>
                  <Form.Item label="来源加权 (可选)">
                    <Space direction="vertical" style={{ width: '100%' }}>
                      <Typography.Text type="secondary">
                        为特定来源加分，正数代表额外加权。
                      </Typography.Text>
                      <Form.List name={["writer", "bonus_entries"]}>
                        {(fields, { add, remove }) => (
                          <Space direction="vertical" style={{ width: '100%' }} size="small">
                            {fields.map((field) => (
                              <Space key={field.key} align="baseline" wrap>
                                <Form.Item
                                  {...field}
                                  name={[field.name, 'source']}
                                  fieldKey={`${field.fieldKey}-source`}
                                  rules={[{ required: true, message: '请输入来源标识' }]}
                                >
                                  <AutoComplete
                                    options={bonusSuggestions}
                                    filterOption={filterAutoCompleteOption}
                                    placeholder="openai.research"
                                  >
                                    <Input placeholder="openai.research" style={{ minWidth: 200 }} />
                                  </AutoComplete>
                                </Form.Item>
                                <Form.Item
                                  {...field}
                                  name={[field.name, 'bonus']}
                                  fieldKey={`${field.fieldKey}-bonus`}
                                  rules={[{ required: true, message: '请输入加权分值' }]}
                                >
                                  <InputNumber step={0.5} />
                                </Form.Item>
                                <Button
                                  type="text"
                                  danger
                                  icon={<MinusCircleOutlined />}
                                  onClick={() => remove(field.name)}
                                />
                              </Space>
                            ))}
                            <Button
                              type="dashed"
                              icon={<PlusOutlined />}
                              onClick={() => add({ bonus: 1 })}
                              style={{ width: 'fit-content' }}
                            >
                              添加来源
                            </Button>
                          </Space>
                        )}
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
                  {deliveryFields}
                </Space>
              )
            }
          ]}
        />
        <Space>
          <Button type="primary" onClick={onSubmit} loading={loading}>
            {editing ? '保存' : '创建'}
          </Button>
          <Button onClick={() => navigate('/')}>取消</Button>
        </Space>
      </Form>
    </Card>
  )
}
