import { useEffect, useMemo, useState } from 'react'
import { Button, Form, Input, Modal, Popconfirm, Select, Space, Switch, Table, Tag, Typography, message } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { PlusOutlined } from '@ant-design/icons'
import {
  createEvaluator,
  deleteEvaluator,
  fetchAiMetrics,
  fetchEvaluators,
  updateEvaluator,
  type AiMetric,
  type Evaluator
} from '../api'
import { useAuth } from '../auth'

type EvaluatorFormValues = {
  key: string
  label_zh: string
  description?: string
  prompt?: string
  metrics: string[]
  active: boolean
}

function getErrorMessage(err: unknown): string {
  if (err && typeof err === 'object') {
    const anyErr = err as { response?: { data?: { detail?: string } }; message?: string }
    const detail = anyErr?.response?.data?.detail
    if (typeof detail === 'string') return detail
    if (typeof anyErr?.message === 'string' && anyErr.message) return anyErr.message
  }
  return '操作失败'
}

export default function Evaluators() {
  const [list, setList] = useState<Evaluator[]>([])
  const [metrics, setMetrics] = useState<AiMetric[]>([])
  const [loading, setLoading] = useState(false)
  const [modalVisible, setModalVisible] = useState(false)
  const [saving, setSaving] = useState(false)
  const [editing, setEditing] = useState<Evaluator | null>(null)
  const [form] = Form.useForm<EvaluatorFormValues>()
  const { user } = useAuth()
  const isAdmin = (user?.is_admin || 0) === 1

  const metricOptions = useMemo(
    () =>
      metrics.map((m) => ({
        value: m.key,
        label: `${m.label_zh || m.key} (${m.key})`
      })),
    [metrics]
  )

  const load = async () => {
    setLoading(true)
    try {
      const [evs, mets] = await Promise.all([fetchEvaluators(), fetchAiMetrics()])
      setList(Array.isArray(evs) ? evs : [])
      setMetrics(Array.isArray(mets) ? mets : [])
    } catch (err) {
      message.error(getErrorMessage(err))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  const openCreate = () => {
    setEditing(null)
    setModalVisible(true)
    form.setFieldsValue({
      key: '',
      label_zh: '',
      description: '',
      prompt: '',
      metrics: metrics.map((m) => m.key),
      active: true
    })
  }

  const openEdit = (item: Evaluator) => {
    setEditing(item)
    setModalVisible(true)
    form.setFieldsValue({
      key: item.key,
      label_zh: item.label_zh,
      description: item.description || '',
      prompt: item.prompt || '',
      metrics: item.metrics || [],
      active: item.active === 1
    })
  }

  const handleToggle = async (item: Evaluator, enabled: boolean) => {
    try {
      await updateEvaluator(item.id, { active: enabled ? 1 : 0 })
      message.success('已更新启用状态')
      load()
    } catch (err) {
      message.error(getErrorMessage(err))
    }
  }

  const handleDelete = async (item: Evaluator) => {
    try {
      await deleteEvaluator(item.id)
      message.success('已删除')
      load()
    } catch (err) {
      message.error(getErrorMessage(err))
    }
  }

  const handleFinish = async (values: EvaluatorFormValues) => {
    const payload = {
      key: values.key.trim(),
      label_zh: values.label_zh.trim(),
      description: values.description?.trim() || null,
      prompt: values.prompt?.trim() || null,
      metrics: values.metrics || [],
      active: values.active ? 1 : 0
    }

    if (!payload.key) {
      message.error('请填写评估器 key')
      return
    }
    if (!payload.label_zh) {
      message.error('请填写评估器名称')
      return
    }

    setSaving(true)
    try {
      if (editing) {
        await updateEvaluator(editing.id, {
          label_zh: payload.label_zh,
          description: payload.description,
          prompt: payload.prompt,
          metrics: payload.metrics,
          active: payload.active
        })
        message.success('评估器已更新')
      } else {
        await createEvaluator(payload)
        message.success('评估器已创建')
      }
      setModalVisible(false)
      form.resetFields()
      load()
    } catch (err) {
      message.error(getErrorMessage(err))
    } finally {
      setSaving(false)
    }
  }

  const columns: ColumnsType<Evaluator> = [
    { title: 'ID', dataIndex: 'id', width: 70 },
    { title: 'Key', dataIndex: 'key' },
    { title: '名称', dataIndex: 'label_zh' },
    {
      title: '描述',
      dataIndex: 'description',
      render: (v) => v || '—',
      ellipsis: true
    },
    {
      title: '允许指标',
      dataIndex: 'metrics',
      render: (items: string[]) =>
        (items || []).length ? (
          <Space size="small" wrap>
            {items.map((m) => (
              <Tag key={m}>{m}</Tag>
            ))}
          </Space>
        ) : (
          <span>—</span>
        )
    },
    {
      title: '启用',
      dataIndex: 'active',
      width: 120,
      render: (value, record) => (
        <Switch checked={value === 1} onChange={(checked) => handleToggle(record, checked)} disabled={!isAdmin} />
      )
    },
    { title: '更新时间', dataIndex: 'updated_at', width: 180 },
    {
      title: '操作',
      width: 220,
      render: (_, record) => (
        <Space>
          <Button type="link" onClick={() => openEdit(record)} disabled={!isAdmin}>
            编辑
          </Button>
          <Popconfirm
            title="确定删除该评估器？"
            onConfirm={() => handleDelete(record)}
            disabled={!isAdmin}
            description="删除前请先解除管线引用"
          >
            <Button type="link" danger disabled={!isAdmin}>
              删除
            </Button>
          </Popconfirm>
        </Space>
      )
    }
  ]

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div style={{ fontSize: 18, fontWeight: 500 }}>评估器</div>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate} disabled={!isAdmin}>
          新增评估器
        </Button>
      </div>
      <Table<Evaluator>
        rowKey="id"
        columns={columns}
        dataSource={list}
        loading={loading}
        pagination={false}
        size="middle"
      />
      <Modal
        destroyOnClose
        title={editing ? '编辑评估器' : '新增评估器'}
        open={modalVisible}
        onCancel={() => {
          setModalVisible(false)
          form.resetFields()
        }}
        onOk={() => form.submit()}
        confirmLoading={saving}
        width={800}
      >
        <Form<EvaluatorFormValues> form={form} layout="vertical" onFinish={handleFinish}>
          <Form.Item name="key" label="Key" rules={[{ required: true, message: '请输入唯一 Key' }]}>
            <Input placeholder="例如: news_evaluator" disabled={!!editing} />
          </Form.Item>
          <Form.Item name="label_zh" label="名称" rules={[{ required: true, message: '请输入名称' }]}>
            <Input placeholder="例如: 资讯评估器" />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={3} placeholder="可选，说明该评估器用途" />
          </Form.Item>
          <Form.Item name="prompt" label="Prompt">
            <Input.TextArea rows={6} placeholder="粘贴提示词，支持 <<SYS>> / <<USER>> 分隔" />
          </Form.Item>
          <Form.Item name="metrics" label="允许的指标">
            <Select
              mode="multiple"
              allowClear
              placeholder="选择指标"
              options={metricOptions}
              notFoundContent={<Typography.Text type="secondary">请先在“AI指标”里配置指标</Typography.Text>}
            />
          </Form.Item>
          <Form.Item name="active" label="启用" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
