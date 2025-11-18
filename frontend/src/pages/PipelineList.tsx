import { useCallback, useEffect, useState } from 'react'
import { Button, Popconfirm, Space, Switch, Table, Tag, message } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { PlusOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { deletePipeline, fetchPipelines, updatePipeline, type PipelineListItem } from '../api'
import { useAuth } from '../auth'

export default function PipelineList() {
  const { user } = useAuth()
  const isAdmin = (user?.is_admin || 0) === 1
  const [list, setList] = useState<PipelineListItem[]>([])
  const [loading, setLoading] = useState(false)
  const navigate = useNavigate()

  const load = useCallback(async () => {
    // 只有在已登录时才拉取数据
    if (!user) return
    setLoading(true)
    try {
      const data = await fetchPipelines()
      setList(data)
    } finally {
      setLoading(false)
    }
  }, [user])

  useEffect(() => {
    // 用户变化（例如刚登录成功）时自动刷新列表
    load()
  }, [load])

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
    ...(isAdmin
      ? ([
          {
            title: '创建者',
            dataIndex: 'owner_user_name',
            width: 160,
            render: (v, r) =>
              r.owner_user_id ? (
                <Button type="link" onClick={() => navigate(`/users?id=${r.owner_user_id}`)}>
                  {v || `用户#${r.owner_user_id}`}
                </Button>
              ) : (
                <span>-</span>
              )
          }
        ] as ColumnsType<PipelineListItem>)
      : ([] as ColumnsType<PipelineListItem>)),
    {
      title: '启用',
      dataIndex: 'enabled',
      width: 100,
      render: (v, record) => <Switch checked={v === 1} onChange={(val) => onToggle(record, val)} />
    },
    ...(isAdmin
      ? ([
          {
            title: 'Debug',
            dataIndex: 'debug_enabled',
            width: 100,
            render: (v) => (v === 1 ? <Tag color="gold">ON</Tag> : <Tag>OFF</Tag>)
          }
        ] as ColumnsType<PipelineListItem>)
      : ([] as ColumnsType<PipelineListItem>)),
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
      title: '星期',
      dataIndex: 'weekday_tag',
      width: 110,
      render: (v) => {
        const map: Record<string, { color: string; text: string }> = {
          '每天': { color: 'green', text: '每天' },
          '工作日': { color: 'blue', text: '工作日' },
          '周末': { color: 'purple', text: '周末' },
          '不限制': { color: 'default', text: '不限制' },
          '不按星期': { color: 'default', text: '不按星期' },
          '自定义': { color: 'orange', text: '自定义' }
        }
        const tag = v && map[v] ? map[v] : { color: 'default', text: v || '—' }
        return <Tag color={tag.color}>{tag.text}</Tag>
      }
    },
    {
      title: '推送方式',
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
        <div style={{ fontSize: 18, fontWeight: 500 }}>我的推送</div>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => navigate('/new')}>
          新建推送
        </Button>
      </div>
      <Table
        rowKey="id"
        columns={columns}
        dataSource={list}
        loading={loading}
        pagination={{ pageSize: 10 }}
        locale={{
          emptyText: (
            <div style={{ textAlign: 'center', padding: '48px 0' }}>
              <Button type="primary" size="large" icon={<PlusOutlined />} onClick={() => navigate('/new')}>
                创建你的第一个信息推送
              </Button>
            </div>
          )
        }}
      />
    </div>
  )
}
