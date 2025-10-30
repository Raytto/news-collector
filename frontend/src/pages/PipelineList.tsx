import { useEffect, useState } from 'react'
import { Button, Popconfirm, Space, Switch, Table, Tag, message } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { PlusOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { deletePipeline, fetchPipelines, updatePipeline, type PipelineListItem } from '../api'

export default function PipelineList() {
  const [list, setList] = useState<PipelineListItem[]>([])
  const [loading, setLoading] = useState(false)
  const navigate = useNavigate()

  const load = async () => {
    setLoading(true)
    try {
      const data = await fetchPipelines()
      setList(data)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  const onToggle = async (item: PipelineListItem, enabled: boolean) => {
    try {
      await updatePipeline(item.id, {
        pipeline: { name: item.name, enabled: enabled ? 1 : 0, description: item.description || '' }
      })
      message.success('已更新启用状态')
      load()
    } catch (e) {
      message.error('更新失败')
    }
  }

  const onDelete = async (id: number) => {
    try {
      await deletePipeline(id)
      message.success('已删除')
      load()
    } catch {
      message.error('删除失败')
    }
  }

  const columns: ColumnsType<PipelineListItem> = [
    { title: 'ID', dataIndex: 'id', width: 80 },
    { title: '名称', dataIndex: 'name' },
    {
      title: '启用',
      dataIndex: 'enabled',
      width: 100,
      render: (v, record) => <Switch checked={v === 1} onChange={(val) => onToggle(record, val)} />
    },
    {
      title: 'Writer',
      render: (_, r) => {
        const label = r.writer_type === 'feishu_md' ? 'feishu' : r.writer_type === 'info_html' ? 'email' : r.writer_type
        return (
          <Space size={4}>
            {label && <Tag>{label}</Tag>}
            {r.writer_hours != null && <span>{r.writer_hours}h</span>}
          </Space>
        )
      }
    },
    {
      title: '投递方式',
      dataIndex: 'delivery_kind',
      render: (v) => (v ? <Tag color={v === 'email' ? 'blue' : 'green'}>{v}</Tag> : <Tag>未配置</Tag>)
    },
    {
      title: '操作',
      width: 200,
      render: (_, r) => (
        <Space>
          <Button type="link" onClick={() => navigate(`/edit/${r.id}`)}>
            编辑
          </Button>
          <Popconfirm title="确定删除?" onConfirm={() => onDelete(r.id)}>
            <Button type="link" danger>
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
        <div style={{ fontSize: 18, fontWeight: 500 }}>投递管理</div>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => navigate('/new')}>
          新建投递
        </Button>
      </div>
      <Table
        rowKey="id"
        columns={columns}
        dataSource={list}
        loading={loading}
        pagination={{ pageSize: 10 }}
      />
    </div>
  )
}
