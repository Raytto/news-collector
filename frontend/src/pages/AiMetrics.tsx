import { useEffect, useState } from 'react'
import {
  Button,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Space,
  Switch,
  Table,
  Typography,
  message
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  createAiMetric,
  deleteAiMetric,
  fetchAiMetrics,
  updateAiMetric,
  type AiMetric
} from '../api'
import { useAuth } from '../auth'

type MetricFormValues = {
  key: string
  label_zh: string
  rate_guide_zh?: string
  default_weight?: number | null
  sort_order?: number
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

export default function AiMetrics() {
  const [list, setList] = useState<AiMetric[]>([])
  const [loading, setLoading] = useState(false)
  const [modalVisible, setModalVisible] = useState(false)
  const [saving, setSaving] = useState(false)
  const [editing, setEditing] = useState<AiMetric | null>(null)
  const [form] = Form.useForm<MetricFormValues>()
  const { user } = useAuth()
  const isAdmin = (user?.is_admin || 0) === 1

  const load = async () => {
    setLoading(true)
    try {
      const data = await fetchAiMetrics()
      setList(data)
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
      rate_guide_zh: '',
      default_weight: undefined,
      sort_order: list.length ? Math.max(...list.map((item) => item.sort_order)) + 10 : 0,
      active: true
    })
  }

  const openEdit = (item: AiMetric) => {
    setEditing(item)
    setModalVisible(true)
    form.setFieldsValue({
      key: item.key,
      label_zh: item.label_zh,
      rate_guide_zh: item.rate_guide_zh || '',
      default_weight: item.default_weight ?? undefined,
      sort_order: item.sort_order,
      active: item.active === 1
    })
  }

  const handleToggle = async (item: AiMetric, enabled: boolean) => {
    try {
      await updateAiMetric(item.id, { active: enabled ? 1 : 0 })
      message.success('已更新启用状态')
      load()
    } catch (err) {
      message.error(getErrorMessage(err))
    }
  }

  const handleDelete = async (item: AiMetric) => {
    try {
      await deleteAiMetric(item.id)
      message.success('已删除')
      load()
    } catch (err) {
      message.error(getErrorMessage(err))
    }
  }

  const handleFinish = async (values: MetricFormValues) => {
    const payload = {
      key: values.key.trim(),
      label_zh: values.label_zh.trim(),
      rate_guide_zh: values.rate_guide_zh?.trim() ? values.rate_guide_zh.trim() : null,
      default_weight:
        typeof values.default_weight === 'number' && Number.isFinite(values.default_weight)
          ? values.default_weight
          : null,
      sort_order:
        typeof values.sort_order === 'number' && Number.isFinite(values.sort_order)
          ? Math.round(values.sort_order)
          : 0,
      active: values.active ? 1 : 0
    }

    if (!payload.key) {
      message.error('请填写指标 key')
      return
    }
    if (!payload.label_zh) {
      message.error('请填写指标名称')
      return
    }

    setSaving(true)
    try {
      if (editing) {
        await updateAiMetric(editing.id, {
          label_zh: payload.label_zh,
          rate_guide_zh: payload.rate_guide_zh,
          default_weight: payload.default_weight,
          sort_order: payload.sort_order,
          active: payload.active
        })
        message.success('指标已更新')
      } else {
        await createAiMetric(payload)
        message.success('指标已创建')
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

  const columns: ColumnsType<AiMetric> = [
    { title: 'ID', dataIndex: 'id', width: 70 },
    { title: 'Key', dataIndex: 'key' },
    { title: '名称', dataIndex: 'label_zh' },
    {
      title: '评分提示',
      dataIndex: 'rate_guide_zh',
      render: (value: string | null | undefined) =>
        value ? (
          <Typography.Paragraph ellipsis={{ rows: 2, expandable: true }}>
            {value}
          </Typography.Paragraph>
        ) : (
          '—'
        )
    },
    {
      title: '默认权重',
      dataIndex: 'default_weight',
      width: 120,
      render: (value: number | null | undefined) =>
        typeof value === 'number' ? value.toFixed(2) : '—'
    },
    { title: '排序', dataIndex: 'sort_order', width: 100 },
    {
      title: '启用',
      dataIndex: 'active',
      width: 120,
      render: (value: number, record) => (
        <Switch checked={value === 1} onChange={(checked) => handleToggle(record, checked)} disabled={!isAdmin} />
      )
    },
    {
      title: '操作',
      width: 200,
      render: (_, record) => (
        <Space>
          <Button type="link" onClick={() => openEdit(record)} disabled={!isAdmin}>
            编辑
          </Button>
          <Popconfirm title="确定删除该指标？" onConfirm={() => handleDelete(record)} disabled={!isAdmin}>
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
        <div style={{ fontSize: 18, fontWeight: 500 }}>AI指标</div>
        <Button type="primary" onClick={openCreate} disabled={!isAdmin}>
          新增维度
        </Button>
      </div>
      <Table<AiMetric> rowKey="id" columns={columns} dataSource={list} loading={loading} pagination={false} />
      <Modal
        destroyOnClose
        title={editing ? '编辑维度' : '新增维度'}
        open={modalVisible}
        onCancel={() => {
          setModalVisible(false)
          form.resetFields()
        }}
        onOk={() => form.submit()}
        confirmLoading={saving}
        width={600}
      >
        <Form<MetricFormValues> form={form} layout="vertical" onFinish={handleFinish}>
          <Form.Item name="key" label="Key" rules={[{ required: true, message: '请输入唯一 Key' }]}>
            <Input placeholder="例如: timeliness" disabled={!!editing} />
          </Form.Item>
          <Form.Item name="label_zh" label="名称" rules={[{ required: true, message: '请输入名称' }]}>
            <Input placeholder="例如: 时效性" />
          </Form.Item>
          <Form.Item name="rate_guide_zh" label="评分提示">
            <Input.TextArea placeholder="给评估模型的提示/备注" rows={4} />
          </Form.Item>
          <Form.Item name="default_weight" label="默认权重">
            <InputNumber style={{ width: '100%' }} step={0.01} placeholder="可选，例如 0.1" />
          </Form.Item>
          <Form.Item name="sort_order" label="排序">
            <InputNumber style={{ width: '100%' }} step={1} placeholder="用于前端排序，数值越小越靠前" />
          </Form.Item>
          <Form.Item name="active" label="启用" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
