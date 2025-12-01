import { useCallback, useEffect, useRef, useState } from 'react'
import { Button, Modal, Popconfirm, Progress, Space, Steps, Switch, Table, Tag, message } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { PlusOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import {
  createPipeline,
  deletePipeline,
  fetchPipeline,
  fetchPipelines,
  pushPipelineNow,
  updatePipeline,
  type PipelineListItem
} from '../api'
import { useAuth } from '../auth'

const PUSH_STAGES = [
  { key: 'collect', label: '源爬取', threshold: 20 },
  { key: 'evaluate', label: 'AI评估', threshold: 50 },
  { key: 'write', label: '撰写', threshold: 80 },
  { key: 'deliver', label: '发送', threshold: 100 }
]

export default function PipelineList() {
  const { user } = useAuth()
  const isAdmin = (user?.is_admin || 0) === 1
  const [list, setList] = useState<PipelineListItem[]>([])
  const [loading, setLoading] = useState(false)
  const [pushingId, setPushingId] = useState<number | null>(null)
  const [duplicatingId, setDuplicatingId] = useState<number | null>(null)
  const [cooling, setCooling] = useState<Record<number, number>>({})
  const [pushProgress, setPushProgress] = useState<{
    visible: boolean
    pipelineName: string
    percent: number
    stageIndex: number
    status: 'active' | 'success' | 'exception'
    error: string
  }>({
    visible: false,
    pipelineName: '',
    percent: 0,
    stageIndex: 0,
    status: 'active',
    error: ''
  })
  const progressTimer = useRef<number | null>(null)
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

  useEffect(
    () => () => {
      if (progressTimer.current) {
        window.clearInterval(progressTimer.current)
      }
    },
    []
  )

  const stageIndexFromPercent = (percent: number) => {
    for (let i = 0; i < PUSH_STAGES.length; i += 1) {
      if (percent < PUSH_STAGES[i].threshold) return i
    }
    return PUSH_STAGES.length - 1
  }

  const clearProgressTimer = () => {
    if (progressTimer.current) {
      window.clearInterval(progressTimer.current)
      progressTimer.current = null
    }
  }

  const startPushProgress = (name: string) => {
    clearProgressTimer()
    setPushProgress({
      visible: true,
      pipelineName: name,
      percent: 6,
      stageIndex: 0,
      status: 'active',
      error: ''
    })
    progressTimer.current = window.setInterval(() => {
      setPushProgress((prev) => {
        if (!prev.visible || prev.status !== 'active') return prev
        const increment = Math.random() * 6 + 2
        const nextPercent = Math.min(prev.percent + increment, 92)
        return {
          ...prev,
          percent: nextPercent,
          stageIndex: stageIndexFromPercent(nextPercent)
        }
      })
    }, 700)
  }

  const finishPushProgress = (ok: boolean, errorMsg?: string) => {
    clearProgressTimer()
    setPushProgress((prev) => {
      const finalPercent = ok ? 100 : Math.max(prev.percent, 45)
      const finalStage = ok ? PUSH_STAGES.length - 1 : stageIndexFromPercent(finalPercent)
      return {
        ...prev,
        percent: finalPercent,
        stageIndex: finalStage,
        status: ok ? 'success' : 'exception',
        error: errorMsg || ''
      }
    })
    if (ok) {
      window.setTimeout(() => {
        setPushProgress((prev) => ({ ...prev, visible: false }))
      }, 1400)
    }
  }

  const closeProgressModal = () => {
    clearProgressTimer()
    setPushProgress((prev) => ({ ...prev, visible: false }))
  }

  const onToggleDebug = async (item: PipelineListItem, enabled: boolean) => {
    try {
      await updatePipeline(item.id, {
        pipeline: {
          name: item.name,
          enabled: item.enabled,
          description: item.description || '',
          debug_enabled: enabled ? 1 : 0
        }
      })
      message.success('已更新 Debug 状态')
      load()
    } catch {
      message.error('更新 Debug 状态失败')
    }
  }

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

  const onPushNow = async (item: PipelineListItem) => {
    setPushingId(item.id)
    startPushProgress(item.name)
    try {
      const res = await pushPipelineNow(item.id)
      const cooldownMs = Math.max((res?.cooldown_seconds ?? 10) * 1000, 0)
      message.success('已触发立即推送')
      setCooling((prev) => ({ ...prev, [item.id]: Date.now() + cooldownMs }))
      window.setTimeout(() => {
        setCooling((prev) => {
          const next = { ...prev }
          delete next[item.id]
          return next
        })
      }, cooldownMs)
      finishPushProgress(true)
    } catch (e: any) {
      const detail = e?.response?.data?.detail
      finishPushProgress(false, detail || '触发失败')
      message.error(detail || '触发失败')
    } finally {
      setPushingId(null)
    }
  }

  const onDuplicate = async (id: number) => {
    setDuplicatingId(id)
    try {
      const data = await fetchPipeline(id)
      if (!data?.pipeline) {
        throw new Error('未获取到管线详情')
      }
      const payload: any = {
        pipeline: { ...data.pipeline },
        filters: data.filters,
        writer: data.writer,
        delivery: data.delivery
      }
      delete payload.pipeline.id
      const originalName = payload.pipeline.name || ''
      payload.pipeline.name = `${originalName}(副本)`
      await createPipeline(payload)
      message.success('已复制管线')
      load()
    } catch (e: any) {
      const detail = e?.response?.data?.detail
      message.error(detail || '复制失败')
    } finally {
      setDuplicatingId(null)
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
            render: (v, record) => (
              <Switch checked={(v ?? 0) === 1} onChange={(val) => onToggleDebug(record, val)} />
            )
          }
        ] as ColumnsType<PipelineListItem>)
      : ([] as ColumnsType<PipelineListItem>)),
    {
      title: 'Writer',
      render: (_, r) => {
        const deliveryLabel = r.delivery_kind === 'feishu' ? 'feishu' : r.delivery_kind === 'email' ? 'email' : null
        const writerLabel =
          r.writer_type === 'feishu_md' ? 'feishu' : r.writer_type === 'info_html' ? 'email' : r.writer_type
        const label = deliveryLabel || writerLabel
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
      width: 320,
      render: (_, r) => (
        <Space size={8} wrap>
          <Button size="small" type="primary" onClick={() => navigate(`/edit/${r.id}`)}>
            编辑
          </Button>
          <Button size="small" type="primary" loading={duplicatingId === r.id} onClick={() => onDuplicate(r.id)}>
            复制
          </Button>
          {(() => {
            const coolingUntil = cooling[r.id] || 0
            const now = Date.now()
            const remain = coolingUntil > now ? Math.ceil((coolingUntil - now) / 1000) : 0
            const disabled = pushingId === r.id || remain > 0
            const label = remain > 0 ? `冷却中(${remain}s)` : '立即推送'
            return (
              <Button
                size="small"
                type="primary"
                loading={pushingId === r.id}
                disabled={disabled}
                onClick={() => onPushNow(r)}
              >
                {label}
              </Button>
            )
          })()}
          <Popconfirm title="确定删除?" onConfirm={() => onDelete(r.id)}>
            <Button size="small" type="primary" danger>
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
        <div>
          <div style={{ fontSize: 18, fontWeight: 500 }}>我的推送</div>
          <div style={{ color: '#6b7280', marginTop: 4 }}>启用后将每天9:30自动推送</div>
        </div>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => navigate('/new')}>
          新建推送
        </Button>
      </div>
      <Table
        rowKey="id"
        columns={columns}
        dataSource={list}
        loading={loading}
        pagination={{ pageSize: 20 }}
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
      <Modal
        width={560}
        title={pushProgress.status === 'exception' ? '推送失败' : '正在推送'}
        open={pushProgress.visible}
        onCancel={closeProgressModal}
        footer={
          pushProgress.status === 'exception'
            ? [
                <Button key="close" onClick={closeProgressModal}>
                  关闭
                </Button>
              ]
            : null
        }
        maskClosable={false}
      >
        <div style={{ marginBottom: 12, fontWeight: 600 }}>{pushProgress.pipelineName || '正在执行推送'}</div>
        <Progress
          percent={Math.round(pushProgress.percent)}
          status={pushProgress.status === 'exception' ? 'exception' : pushProgress.status === 'success' ? 'success' : 'active'}
          style={{ marginBottom: 16 }}
        />
        <Steps
          size="small"
          current={pushProgress.stageIndex}
          items={PUSH_STAGES.map((stage, idx) => ({
            title: stage.label,
            status:
              pushProgress.status === 'exception'
                ? idx === pushProgress.stageIndex
                  ? 'error'
                  : idx < pushProgress.stageIndex
                  ? 'finish'
                  : 'wait'
                : pushProgress.status === 'success'
                ? 'finish'
                : idx < pushProgress.stageIndex
                ? 'finish'
                : idx === pushProgress.stageIndex
                ? 'process'
                : 'wait'
          }))}
        />
        <div style={{ marginTop: 14, color: '#6b7280' }}>执行进度为预计过程，后台将依次完成上述步骤。</div>
        {pushProgress.status === 'exception' && (
          <div style={{ marginTop: 8, color: '#b91c1c' }}>{pushProgress.error || '推送触发失败，请稍后重试。'}</div>
        )}
      </Modal>
    </div>
  )
}
