import { useEffect, useMemo, useState } from 'react'
import { Button, Drawer, Form, Input, Space, Switch, Table, Tag, message } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { fetchUserDetail, fetchUsers, type PipelineListItem, type UserItem, updateUser } from '../api'
import { useAuth } from '../auth'
import { useLocation, useNavigate } from 'react-router-dom'

export default function Users() {
  const { user: me } = useAuth()
  const location = useLocation()
  const navigate = useNavigate()
  const isAdmin = (me?.is_admin || 0) === 1
  const [q, setQ] = useState('')
  const [loading, setLoading] = useState(false)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [total, setTotal] = useState(0)
  const [items, setItems] = useState<UserItem[]>([])

  const [detailId, setDetailId] = useState<number | null>(null)
  const [detail, setDetail] = useState<{ user: UserItem; pipelines: PipelineListItem[] } | null>(null)

  const columns: ColumnsType<UserItem> = useMemo(
    () => [
      { title: 'ID', dataIndex: 'id', width: 80 },
      { title: '邮箱', dataIndex: 'email' },
      { title: '昵称', dataIndex: 'name' },
      {
        title: '管理员',
        dataIndex: 'is_admin',
        width: 120,
        render: (v, record) => (
          <Switch
            checked={v === 1}
            disabled={!isAdmin}
            onChange={async (checked) => {
              try {
                const newVal = checked ? 1 : 0
                await updateUser(record.id, { is_admin: newVal })
                message.success('已更新权限')
                // refresh
                load()
              } catch (err: any) {
                const detail = err?.response?.data?.detail || '更新失败'
                message.error(detail)
              }
            }}
          />
        )
      },
      {
        title: '可用',
        dataIndex: 'enabled',
        width: 120,
        render: (v, record) => (
          <Switch
            checked={v === 1}
            disabled={!isAdmin}
            onChange={async (checked) => {
              try {
                const newVal = checked ? 1 : 0
                await updateUser(record.id, { enabled: newVal })
                message.success('已更新状态')
                load()
              } catch (err: any) {
                const detail = err?.response?.data?.detail || '更新失败'
                message.error(detail)
              }
            }}
          />
        )
      },
      { title: '创建时间', dataIndex: 'created_at', width: 180 },
      { title: '最近登录', dataIndex: 'last_login_at', width: 180 },
      {
        title: '操作',
        width: 120,
        render: (_, record) => (
          <Space>
            <Button type="link" onClick={() => openDetail(record.id)}>
              详情
            </Button>
          </Space>
        )
      }
    ],
    [isAdmin]
  )

  const load = async () => {
    setLoading(true)
    try {
      const data = await fetchUsers({ page, pageSize, q: q.trim() || null })
      setItems(Array.isArray(data.items) ? data.items : [])
      setTotal(Number(data.total) || 0)
    } catch (err: any) {
      const detail = err?.response?.data?.detail || '加载失败'
      message.error(detail)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, pageSize])

  // open detail if query contains id
  useEffect(() => {
    const params = new URLSearchParams(location.search)
    const id = params.get('id')
    const nid = id ? Number(id) : NaN
    if (!Number.isNaN(nid) && nid > 0) {
      openDetail(nid)
    }
  }, [location.search])

  const openDetail = async (id: number) => {
    setDetailId(id)
    setDetail(null)
    try {
      const data = await fetchUserDetail(id)
      setDetail(data)
    } catch (err: any) {
      const detail = err?.response?.data?.detail || '拉取详情失败'
      message.error(detail)
    }
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <Space>
          <Input.Search
            allowClear
            placeholder="按邮箱/昵称搜索"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onSearch={() => {
              setPage(1)
              load()
            }}
          />
        </Space>
      </div>
      <Table
        rowKey="id"
        columns={columns}
        loading={loading}
        dataSource={items}
        pagination={{ current: page, pageSize, total, onChange: (p, ps) => { setPage(p); setPageSize(ps) } }}
      />

      <Drawer
        title={detail ? `用户详情：${detail.user.name}` : '加载中...'}
        width={560}
        open={detailId !== null}
        onClose={() => {
          setDetailId(null)
          const params = new URLSearchParams(location.search)
          if (params.has('id')) {
            params.delete('id')
            navigate({ pathname: '/users', search: params.toString() ? `?${params.toString()}` : '' }, { replace: true })
          }
        }}
      >
        {detail && (
          <div>
            <div style={{ marginBottom: 12 }}>
              <Space size={16} wrap>
                <span>邮箱：{detail.user.email}</span>
                <span>昵称：{detail.user.name}</span>
                <span>
                  角色：{detail.user.is_admin === 1 ? <Tag color="blue">管理员</Tag> : <Tag>普通用户</Tag>}
                </span>
              </Space>
            </div>
            <div style={{ margin: '12px 0 6px', fontWeight: 600 }}>归属投递</div>
            <Table
              size="small"
              rowKey="id"
              pagination={false}
              dataSource={detail.pipelines}
              columns={[
                { title: 'ID', dataIndex: 'id', width: 80 },
                { title: '名称', dataIndex: 'name' },
                { title: 'Writer', dataIndex: 'writer_type', width: 120 },
                { title: '方式', dataIndex: 'delivery_kind', width: 120 },
                { title: 'Debug', dataIndex: 'debug_enabled', width: 100, render: (v) => (v === 1 ? <Tag color="gold">ON</Tag> : <Tag>OFF</Tag>) },
                { title: '更新时间', dataIndex: 'updated_at', width: 180 }
              ]}
            />
          </div>
        )}
      </Drawer>
    </div>
  )
}
