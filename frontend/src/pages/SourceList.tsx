import { useEffect, useMemo, useState } from 'react'
import { Button, Form, Input, Modal, Popconfirm, Select, Space, Switch, Table, Tag, message } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { MinusCircleOutlined, PlusOutlined } from '@ant-design/icons'
import {
  createSource,
  deleteSource,
  fetchCategories,
  fetchSources,
  updateSource,
  type CategoryItem,
  type SourceItem
} from '../api'
import { useAuth } from '../auth'

type SourceFormValues = {
  key: string
  label_zh: string
  category_key: string
  script_path: string
  enabled: boolean
  addresses: string[]
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

export default function SourceList() {
  const [list, setList] = useState<SourceItem[]>([])
  const [categories, setCategories] = useState<CategoryItem[]>([])
  const [tableLoading, setTableLoading] = useState(false)
  const [modalVisible, setModalVisible] = useState(false)
  const [saving, setSaving] = useState(false)
  const [editing, setEditing] = useState<SourceItem | null>(null)
  const [form] = Form.useForm<SourceFormValues>()
  const { user } = useAuth()
  const isAdmin = (user?.is_admin || 0) === 1

  const categoryOptions = useMemo(
    () =>
      categories.map((item) => ({
        value: item.key,
        label: `${item.label_zh} (${item.key})`
      })),
    [categories]
  )

  const load = async () => {
    setTableLoading(true)
    try {
      const [sourceData, categoryData] = await Promise.all([fetchSources(), fetchCategories()])
      setList(sourceData)
      setCategories(categoryData)
    } finally {
      setTableLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  const openCreate = () => {
    if (!categories.length) {
      message.warning('请先创建至少一个类别')
      return
    }
    setEditing(null)
    setModalVisible(true)
    form.setFieldsValue({
      key: '',
      label_zh: '',
      category_key: categories[0]?.key || '',
      script_path: '',
      enabled: true,
      addresses: ['']
    })
  }

  const openEdit = (item: SourceItem) => {
    setEditing(item)
    setModalVisible(true)
    form.setFieldsValue({
      key: item.key,
      label_zh: item.label_zh,
      category_key: item.category_key,
      script_path: item.script_path,
      enabled: item.enabled === 1,
      addresses: item.addresses && item.addresses.length ? [...item.addresses] : ['']
    })
  }

  const handleToggle = async (item: SourceItem, enabled: boolean) => {
    try {
      await updateSource(item.id, { enabled: enabled ? 1 : 0 })
      message.success('已更新启用状态')
      await load()
    } catch (err) {
      message.error(getErrorMessage(err))
    }
  }

  const handleDelete = async (item: SourceItem) => {
    try {
      await deleteSource(item.id)
      message.success('来源已删除')
      await load()
    } catch (err) {
      message.error(getErrorMessage(err))
    }
  }

  const handleFinish = async (values: SourceFormValues) => {
    const addresses = (values.addresses || []).map((addr) => addr.trim()).filter((addr) => !!addr)
    if (!addresses.length) {
      message.error('请至少填写一个源网址')
      return
    }
    const uniqueAddresses = Array.from(new Set(addresses))
    const payload = {
      key: values.key.trim(),
      label_zh: values.label_zh.trim(),
      category_key: values.category_key,
      script_path: values.script_path.trim(),
      enabled: values.enabled ? 1 : 0,
      addresses: uniqueAddresses
    }
    if (!payload.key) {
      message.error('请填写来源 key')
      return
    }
    if (!payload.label_zh) {
      message.error('请填写来源名称')
      return
    }
    if (!payload.category_key) {
      message.error('请选择所属类别')
      return
    }
    if (!payload.script_path) {
      message.error('请填写脚本路径')
      return
    }
    setSaving(true)
    try {
      if (editing) {
        await updateSource(editing.id, payload)
        message.success('来源已更新')
      } else {
        await createSource(payload)
        message.success('来源已创建')
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

  const columns: ColumnsType<SourceItem> = [
    { title: 'ID', dataIndex: 'id', width: 70 },
    { title: 'Key', dataIndex: 'key' },
    { title: '名称', dataIndex: 'label_zh' },
    {
      title: '类别',
      dataIndex: 'category_key',
      render: (_, record) => (
        <Tag color="blue">{record.category_label ? `${record.category_label} (${record.category_key})` : record.category_key}</Tag>
      )
    },
    {
      title: '源网址',
      dataIndex: 'addresses',
      render: (addresses: SourceItem['addresses']) =>
        addresses && addresses.length ? (
          <Space direction="vertical" size={0}>
            {addresses.map((addr) => (
              <a key={addr} href={addr} target="_blank" rel="noreferrer">
                {addr}
              </a>
            ))}
          </Space>
        ) : (
          '—'
        )
    },
    { title: '脚本路径', dataIndex: 'script_path' },
    {
      title: '启用',
      dataIndex: 'enabled',
      width: 120,
      render: (value, record) => (
        <Switch checked={value === 1} onChange={(checked) => handleToggle(record, checked)} disabled={!isAdmin} />
      )
    },
    { title: '更新时间', dataIndex: 'updated_at' },
    {
      title: '操作',
      width: 220,
      render: (_, record) => (
        <Space>
          <Button type="link" onClick={() => openEdit(record)} disabled={!isAdmin}>
            编辑
          </Button>
          <Popconfirm title="确定删除该来源？" onConfirm={() => handleDelete(record)} disabled={!isAdmin}>
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
        <div style={{ fontSize: 18, fontWeight: 500 }}>所有来源</div>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate} disabled={!isAdmin}>
          新增来源
        </Button>
      </div>
      <Table<SourceItem> rowKey="id" columns={columns} dataSource={list} loading={tableLoading} pagination={false} />
      <Modal
        destroyOnClose
        title={editing ? '编辑来源' : '新增来源'}
        open={modalVisible}
        onCancel={() => {
          setModalVisible(false)
          form.resetFields()
        }}
        onOk={() => form.submit()}
        confirmLoading={saving}
      >
        <Form<SourceFormValues> form={form} layout="vertical" onFinish={handleFinish}>
          <Form.Item name="key" label="Key" rules={[{ required: true, message: '请输入唯一 Key' }]}>
            <Input placeholder="例如: gamedeveloper" />
          </Form.Item>
          <Form.Item name="label_zh" label="名称" rules={[{ required: true, message: '请输入中文名称' }]}>
            <Input placeholder="例如: GameDeveloper" />
          </Form.Item>
        <Form.Item name="category_key" label="所属类别" rules={[{ required: true, message: '请选择类别' }]}>
          <Select options={categoryOptions} placeholder="选择类别" />
        </Form.Item>
        <Form.Item name="script_path" label="脚本路径" rules={[{ required: true, message: '请输入脚本路径' }]}>
          <Input placeholder="例如: news-collector/scraping/game/gamedeveloper.rss.py" />
        </Form.Item>
        <Form.List name="addresses">
          {(fields, { add, remove }) => (
            <>
              {fields.map((field, index) => (
                <Form.Item
                  {...field}
                  key={field.key}
                  label={index === 0 ? '源网址' : ''}
                  required={false}
                >
                  <Space align="baseline">
                    <Form.Item
                      {...field}
                      noStyle
                      rules={[
                        {
                          validator: (_, value) => {
                            if (!value || !value.trim()) {
                              return Promise.reject(new Error('请输入网址或删除此项'))
                            }
                            return Promise.resolve()
                          }
                        }
                      ]}
                    >
                      <Input placeholder="https://example.com/feed" />
                    </Form.Item>
                    {fields.length > 1 ? (
                      <MinusCircleOutlined onClick={() => remove(field.name)} />
                    ) : null}
                  </Space>
                </Form.Item>
              ))}
              <Form.Item>
                <Button type="dashed" onClick={() => add('')} block icon={<PlusOutlined />}>
                  添加源网址
                </Button>
              </Form.Item>
            </>
          )}
        </Form.List>
        <Form.Item name="enabled" label="启用" valuePropName="checked">
          <Switch />
        </Form.Item>
      </Form>
      </Modal>
    </div>
  )
}
