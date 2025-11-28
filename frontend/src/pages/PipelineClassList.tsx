import { useEffect, useState } from 'react'
import { Button, Form, Input, Modal, Popconfirm, Select, Space, Switch, Table, Tag, message } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { PlusOutlined } from '@ant-design/icons'
import {
  fetchPipelineClasses,
  createPipelineClass,
  updatePipelineClass,
  deletePipelineClass,
  fetchCategories,
  fetchEvaluators,
  type PipelineClass,
  type CategoryItem,
  type Evaluator
} from '../api'
import { useAuth } from '../auth'

type PipelineClassFormValues = {
  key: string
  label_zh: string
  description?: string
  enabled: boolean
  categories: string[]
  evaluators: string[]
  writers: string[]
}

const WRITER_OPTIONS = [
  { value: 'email_news', label: 'email_news' },
  { value: 'feishu_news', label: 'feishu_news' },
  { value: 'feishu_legou_game', label: 'feishu_legou_game' },
  { value: 'feishu_md', label: 'feishu_md' },
  { value: 'info_html', label: 'info_html' }
]

function getErrorMessage(err: unknown): string {
  if (err && typeof err === 'object') {
    const anyErr = err as { response?: { data?: { detail?: string } } }
    const detail = anyErr?.response?.data?.detail
    if (typeof detail === 'string') return detail
    if ('message' in anyErr && typeof (anyErr as { message?: string }).message === 'string') {
      return (anyErr as { message: string }).message
    }
  }
  return '操作失败'
}

export default function PipelineClassList() {
  const [list, setList] = useState<PipelineClass[]>([])
  const [categories, setCategories] = useState<CategoryItem[]>([])
  const [evaluators, setEvaluators] = useState<Evaluator[]>([])
  const [loading, setLoading] = useState(false)
  const [modalVisible, setModalVisible] = useState(false)
  const [saving, setSaving] = useState(false)
  const [editing, setEditing] = useState<PipelineClass | null>(null)
  const [form] = Form.useForm<PipelineClassFormValues>()
  const { user } = useAuth()
  const isAdmin = (user?.is_admin || 0) === 1

  const load = async () => {
    setLoading(true)
    try {
      const [cls, cats, evs] = await Promise.all([fetchPipelineClasses(), fetchCategories(), fetchEvaluators()])
      setList(Array.isArray(cls) ? cls : [])
      setCategories(Array.isArray(cats) ? cats : [])
      setEvaluators(Array.isArray(evs) ? evs : [])
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
      enabled: true,
      categories: [],
      evaluators: [],
      writers: []
    })
  }

  const openEdit = (item: PipelineClass) => {
    setEditing(item)
    setModalVisible(true)
    form.setFieldsValue({
      key: item.key,
      label_zh: item.label_zh,
      description: item.description || '',
      enabled: item.enabled === 1,
      categories: item.categories || [],
      evaluators: item.evaluators || [],
      writers: item.writers || []
    })
  }

  const handleToggle = async (item: PipelineClass, enabled: boolean) => {
    try {
      await updatePipelineClass(item.id, { enabled: enabled ? 1 : 0 })
      message.success('已更新启用状态')
      await load()
    } catch (err) {
      message.error(getErrorMessage(err))
    }
  }

  const handleDelete = async (item: PipelineClass) => {
    try {
      await deletePipelineClass(item.id)
      message.success('已删除')
      await load()
    } catch (err) {
      message.error(getErrorMessage(err))
    }
  }

  const handleFinish = async (values: PipelineClassFormValues) => {
    const payload = {
      key: values.key.trim(),
      label_zh: values.label_zh.trim(),
      description: values.description?.trim() || null,
      enabled: values.enabled ? 1 : 0,
      categories: values.categories || [],
      evaluators: values.evaluators || [],
      writers: values.writers || []
    }
    if (!payload.key) {
      message.error('请填写类别 key')
      return
    }
    if (!payload.label_zh) {
      message.error('请填写类别名称')
      return
    }
    setSaving(true)
    try {
      if (editing) {
        await updatePipelineClass(editing.id, payload)
        message.success('管线类别已更新')
      } else {
        await createPipelineClass(payload)
        message.success('管线类别已创建')
      }
      setModalVisible(false)
      form.resetFields()
      await load()
    } catch (err) {
      message.error(getErrorMessage(err))
    } finally {
      setSaving(false)
    }
  }

  const columns: ColumnsType<PipelineClass> = [
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
      title: '启用',
      dataIndex: 'enabled',
      width: 120,
      render: (value, record) => (
        <Switch checked={value === 1} onChange={(checked) => handleToggle(record, checked)} disabled={!isAdmin} />
      )
    },
    {
      title: '允许来源类别',
      dataIndex: 'categories',
      render: (cats: string[]) =>
        (cats || []).length ? (
          <Space size="small" wrap>
            {cats.map((c) => (
              <Tag key={c}>{c}</Tag>
            ))}
          </Space>
        ) : (
          <span>—</span>
        )
    },
    {
      title: '评估器',
      dataIndex: 'evaluators',
      render: (evs: string[]) =>
        (evs || []).length ? (
          <Space size="small" wrap>
            {evs.map((e) => (
              <Tag color="geekblue" key={e}>
                {e}
              </Tag>
            ))}
          </Space>
        ) : (
          <span>—</span>
        )
    },
    {
      title: 'Writer',
      dataIndex: 'writers',
      render: (ws: string[]) =>
        (ws || []).length ? (
          <Space size="small" wrap>
            {ws.map((w) => (
              <Tag color="green" key={w}>
                {w}
              </Tag>
            ))}
          </Space>
        ) : (
          <span>—</span>
        )
    },
    { title: '更新时间', dataIndex: 'updated_at', width: 180 },
    {
      title: '操作',
      width: 200,
      render: (_, record) => (
        <Space>
          <Button type="link" onClick={() => openEdit(record)} disabled={!isAdmin}>
            编辑
          </Button>
          <Popconfirm
            title="确定删除该管线类别？"
            onConfirm={() => handleDelete(record)}
            disabled={!isAdmin}
            description="删除前请确保没有关联的管线"
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
        <div style={{ fontSize: 18, fontWeight: 500 }}>管线类别</div>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate} disabled={!isAdmin}>
          新增管线类别
        </Button>
      </div>
      <Table<PipelineClass>
        rowKey="id"
        columns={columns}
        dataSource={list}
        loading={loading}
        pagination={false}
        size="middle"
      />
      <Modal
        destroyOnClose
        title={editing ? '编辑管线类别' : '新增管线类别'}
        open={modalVisible}
        onCancel={() => {
          setModalVisible(false)
          form.resetFields()
        }}
        onOk={() => form.submit()}
        confirmLoading={saving}
        width={720}
      >
        <Form<PipelineClassFormValues> form={form} layout="vertical" onFinish={handleFinish}>
          <Form.Item name="key" label="Key" rules={[{ required: true, message: '请输入唯一 Key' }]}>
            <Input placeholder="例如: general_news" disabled={!!editing} />
          </Form.Item>
          <Form.Item name="label_zh" label="名称" rules={[{ required: true, message: '请输入名称' }]}>
            <Input placeholder="例如: 综合资讯" />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={3} placeholder="可选，说明该管线类别的用途" />
          </Form.Item>
          <Form.Item name="enabled" label="启用" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item name="categories" label="允许来源类别">
            <Select
              mode="multiple"
              allowClear
              placeholder="选择允许的来源类别"
              options={categories.map((cat) => ({ value: cat.key, label: `${cat.label_zh} (${cat.key})` }))}
            />
          </Form.Item>
          <Form.Item name="evaluators" label="允许的评估器">
            <Select
              mode="multiple"
              allowClear
              placeholder="选择评估器"
              options={evaluators.map((ev) => ({
                value: ev.key,
                label: `${ev.label_zh || ev.key} (${ev.key})`
              }))}
            />
          </Form.Item>
          <Form.Item name="writers" label="允许的 Writer 类型">
            <Select mode="multiple" allowClear placeholder="选择 Writer 类型" options={WRITER_OPTIONS} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
