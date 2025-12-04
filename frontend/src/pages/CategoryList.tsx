import { useEffect, useState } from 'react'
import { Button, Form, Input, Modal, Popconfirm, Space, Switch, Table, message } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { PlusOutlined } from '@ant-design/icons'
import { createCategory, deleteCategory, fetchCategories, updateCategory, type CategoryItem } from '../api'
import { useAuth } from '../auth'

type CategoryFormValues = {
  key: string
  label_zh: string
  enabled: boolean
  allow_parallel: boolean
}

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

export default function CategoryList() {
  const [list, setList] = useState<CategoryItem[]>([])
  const [tableLoading, setTableLoading] = useState(false)
  const [modalVisible, setModalVisible] = useState(false)
  const [saving, setSaving] = useState(false)
  const [editing, setEditing] = useState<CategoryItem | null>(null)
  const { user } = useAuth()
  const isAdmin = (user?.is_admin || 0) === 1
  const [form] = Form.useForm<CategoryFormValues>()

  const load = async () => {
    setTableLoading(true)
    try {
      const data = await fetchCategories()
      setList(data)
    } finally {
      setTableLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  const openCreate = () => {
    setEditing(null)
    setModalVisible(true)
    form.setFieldsValue({ key: '', label_zh: '', enabled: true, allow_parallel: true })
  }

  const openEdit = (item: CategoryItem) => {
    setEditing(item)
    setModalVisible(true)
    form.setFieldsValue({
      key: item.key,
      label_zh: item.label_zh,
      enabled: item.enabled === 1,
      allow_parallel: item.allow_parallel === 1
    })
  }

  const handleToggle = async (item: CategoryItem, enabled: boolean) => {
    try {
      await updateCategory(item.id, { enabled: enabled ? 1 : 0 })
      message.success('已更新启用状态')
      await load()
    } catch (err) {
      message.error(getErrorMessage(err))
    }
  }

  const handleParallelToggle = async (item: CategoryItem, allowed: boolean) => {
    try {
      await updateCategory(item.id, { allow_parallel: allowed ? 1 : 0 })
      message.success('已更新并行抓取设置')
      await load()
    } catch (err) {
      message.error(getErrorMessage(err))
    }
  }

  const handleDelete = async (item: CategoryItem) => {
    try {
      await deleteCategory(item.id)
      message.success('已删除')
      await load()
    } catch (err) {
      message.error(getErrorMessage(err))
    }
  }

  const handleFinish = async (values: CategoryFormValues) => {
    const payload = {
      key: values.key.trim(),
      label_zh: values.label_zh.trim(),
      enabled: values.enabled ? 1 : 0,
      allow_parallel: values.allow_parallel ? 1 : 0
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
        await updateCategory(editing.id, payload)
        message.success('类别已更新')
      } else {
        await createCategory(payload)
        message.success('类别已创建')
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

  const columns: ColumnsType<CategoryItem> = [
    { title: 'ID', dataIndex: 'id', width: 70 },
    { title: 'Key', dataIndex: 'key' },
    { title: '名称', dataIndex: 'label_zh' },
    {
      title: '启用',
      dataIndex: 'enabled',
      width: 120,
      render: (value, record) => (
        <Switch checked={value === 1} onChange={(checked) => handleToggle(record, checked)} disabled={!isAdmin} />
      )
    },
    {
      title: '并行抓取',
      dataIndex: 'allow_parallel',
      width: 140,
      render: (value, record) => (
        <Switch
          checked={value === 1}
          onChange={(checked) => handleParallelToggle(record, checked)}
          disabled={!isAdmin}
        />
      )
    },
    { title: '更新时间', dataIndex: 'updated_at' },
    {
      title: '操作',
      width: 200,
      render: (_, record) => (
        <Space>
          <Button type="link" onClick={() => openEdit(record)} disabled={!isAdmin}>
            编辑
          </Button>
          <Popconfirm title="确定删除该类别？" onConfirm={() => handleDelete(record)} disabled={!isAdmin}>
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
        <div style={{ fontSize: 18, fontWeight: 500 }}>所有类别</div>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate} disabled={!isAdmin}>
          新增类别
        </Button>
      </div>
      <Table<CategoryItem> rowKey="id" columns={columns} dataSource={list} loading={tableLoading} pagination={false} />
      <Modal
        destroyOnClose
        title={editing ? '编辑类别' : '新增类别'}
        open={modalVisible}
        onCancel={() => {
          setModalVisible(false)
          form.resetFields()
        }}
        onOk={() => form.submit()}
        confirmLoading={saving}
      >
        <Form<CategoryFormValues> form={form} layout="vertical" onFinish={handleFinish}>
          <Form.Item name="key" label="Key" rules={[{ required: true, message: '请输入唯一 Key' }]}>
            <Input placeholder="例如: game" />
          </Form.Item>
          <Form.Item name="label_zh" label="名称" rules={[{ required: true, message: '请输入中文名称' }]}>
            <Input placeholder="例如: 游戏" />
          </Form.Item>
          <Form.Item name="enabled" label="启用" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item name="allow_parallel" label="允许并行抓取" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
