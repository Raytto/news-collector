import { useEffect, useMemo, useState } from 'react'
import {
  Button,
  Descriptions,
  Input,
  Modal,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
  message
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  fetchCategories,
  fetchInfoDetail,
  fetchInfoReview,
  fetchInfos,
  fetchSources,
  type CategoryItem,
  type InfoDetail,
  type InfoListItem,
  type InfoReview,
  type SourceItem
} from '../api'

const { Search } = Input
const PAGE_SIZE_DEFAULT = 20

function getErrorMessage(err: unknown): string {
  if (err && typeof err === 'object') {
    const anyErr = err as { response?: { data?: { detail?: string } }; message?: string }
    const detail = anyErr?.response?.data?.detail
    if (typeof detail === 'string') return detail
    if (typeof anyErr?.message === 'string' && anyErr.message) return anyErr.message
  }
  return '操作失败'
}

function scoreTagColor(score: number | null | undefined): string {
  if (typeof score !== 'number' || Number.isNaN(score)) return 'default'
  if (score >= 85) return 'green'
  if (score >= 70) return 'blue'
  if (score >= 50) return 'orange'
  return 'red'
}

export default function InfoList() {
  const [list, setList] = useState<InfoListItem[]>([])
  const [loading, setLoading] = useState(false)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(PAGE_SIZE_DEFAULT)
  const [total, setTotal] = useState(0)
  const [category, setCategory] = useState<string | null>(null)
  const [source, setSource] = useState<string | null>(null)
  const [keyword, setKeyword] = useState<string>('')
  const [categories, setCategories] = useState<CategoryItem[]>([])
  const [sources, setSources] = useState<SourceItem[]>([])
  const [detailVisible, setDetailVisible] = useState(false)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailData, setDetailData] = useState<InfoDetail | null>(null)
  const [reviewVisible, setReviewVisible] = useState(false)
  const [reviewLoading, setReviewLoading] = useState(false)
  const [reviewData, setReviewData] = useState<InfoReview | null>(null)
  const [currentInfoId, setCurrentInfoId] = useState<number | null>(null)

  useEffect(() => {
    const loadFilters = async () => {
      try {
        const [cats, srcs] = await Promise.all([fetchCategories(), fetchSources()])
        setCategories(cats)
        setSources(srcs)
      } catch (err) {
        message.error(getErrorMessage(err))
      }
    }
    loadFilters()
  }, [])

  useEffect(() => {
    const load = async () => {
      setLoading(true)
      try {
        const data = await fetchInfos({
          page,
          pageSize,
          category: category || undefined,
          source: source || undefined,
          q: keyword || undefined
        })
        setList(data.items)
        setTotal(data.total)
      } catch (err) {
        message.error(getErrorMessage(err))
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [page, pageSize, category, source, keyword])

  const categoryOptions = useMemo(
    () => categories.map((item) => ({ value: item.key, label: `${item.label_zh} (${item.key})` })),
    [categories]
  )
  const sourceOptions = useMemo(
    () => sources.map((item) => ({ value: item.key, label: `${item.label_zh} (${item.key})` })),
    [sources]
  )

  const openDetail = async (infoId: number) => {
    setCurrentInfoId(infoId)
    setDetailVisible(true)
    setDetailLoading(true)
    try {
      const data = await fetchInfoDetail(infoId)
      setDetailData(data)
    } catch (err) {
      message.error(getErrorMessage(err))
      setDetailVisible(false)
    } finally {
      setDetailLoading(false)
    }
  }

  const openReview = async (infoId: number) => {
    setCurrentInfoId(infoId)
    setReviewVisible(true)
    setReviewLoading(true)
    try {
      const data = await fetchInfoReview(infoId)
      setReviewData(data)
    } catch (err) {
      message.error(getErrorMessage(err))
      setReviewVisible(false)
    } finally {
      setReviewLoading(false)
    }
  }

  const columns: ColumnsType<InfoListItem> = [
    { title: 'ID', dataIndex: 'id', width: 80 },
    {
      title: '标题',
      dataIndex: 'title',
      render: (value: string, record) => (
        <Typography.Link href={record.link} target="_blank" rel="noreferrer">
          {value}
        </Typography.Link>
      )
    },
    {
      title: '来源',
      dataIndex: 'source_label',
      render: (_: unknown, record) => (
        <Tag color="blue">{record.source_label || record.source}</Tag>
      )
    },
    {
      title: '分类',
      dataIndex: 'category_label',
      render: (_: unknown, record) =>
        record.category ? <Tag>{record.category_label || record.category}</Tag> : '—'
    },
    { title: '发布时间', dataIndex: 'publish', width: 160 },
    {
      title: 'AI评分',
      dataIndex: 'final_score',
      width: 120,
      render: (value: number | null | undefined) =>
        typeof value === 'number' ? (
          <Tag color={scoreTagColor(value)}>{value.toFixed(1)}</Tag>
        ) : (
          <Tag>未评估</Tag>
        )
    },
    {
      title: '操作',
      width: 220,
      render: (_, record) => (
        <Space>
          <Button type="link" size="small" onClick={() => openReview(record.id)}>
            AI评估
          </Button>
          <Button type="link" size="small" onClick={() => openDetail(record.id)}>
            文字内容
          </Button>
        </Space>
      )
    }
  ]

  const reviewMetricsColumns = useMemo<ColumnsType<InfoReview['scores'][number]>>(
    () => [
      { title: '指标', dataIndex: 'metric_label' },
      { title: 'Key', dataIndex: 'metric_key', width: 140 },
      { title: '分数', dataIndex: 'score', width: 80 }
    ],
    []
  )

  const keyConcepts = useMemo(
    () =>
      (reviewData?.ai_key_concepts ?? []).filter(
        (item): item is string => typeof item === 'string' && item.trim() !== ''
      ),
    [reviewData]
  )

  const aiSummaryLong = useMemo(() => {
    if (!reviewData) return ''
    const longText = (reviewData.ai_summary_long || '').trim()
    if (longText) return longText
    return (reviewData.ai_summary || '').trim()
  }, [reviewData])

  return (
    <div>
      <Space
        style={{ marginBottom: 16, width: '100%' }}
        size="middle"
        wrap
      >
        <Search
          allowClear
          placeholder="搜索标题或链接"
          onSearch={(value) => {
            setPage(1)
            setKeyword(value.trim())
          }}
          style={{ width: 240 }}
        />
        <Select
          allowClear
          options={categoryOptions}
          placeholder="按分类筛选"
          style={{ width: 220 }}
          value={category ?? undefined}
          onChange={(value) => {
            setPage(1)
            setCategory(value ?? null)
          }}
        />
        <Select
          allowClear
          options={sourceOptions}
          placeholder="按来源筛选"
          style={{ width: 220 }}
          value={source ?? undefined}
          onChange={(value) => {
            setPage(1)
            setSource(value ?? null)
          }}
        />
        <Button
          onClick={() => {
            setPage(1)
            setKeyword('')
            setCategory(null)
            setSource(null)
          }}
        >
          重置筛选
        </Button>
      </Space>
      <Table<InfoListItem>
        rowKey="id"
        columns={columns}
        dataSource={list}
        loading={loading}
        pagination={{
          current: page,
          pageSize,
          total,
          showSizeChanger: true,
          onChange: (nextPage, nextSize) => {
            setPage(nextPage)
            setPageSize(nextSize)
          }
        }}
      />

      <Modal
        destroyOnClose
        title={`文章详情 #${currentInfoId ?? ''}`}
        open={detailVisible}
        onCancel={() => {
          setDetailVisible(false)
          setDetailData(null)
        }}
        footer={null}
        width={720}
      >
        {detailLoading ? (
          <Space style={{ width: '100%', justifyContent: 'center' }}>
            <Spin />
          </Space>
        ) : detailData ? (
          <Space direction="vertical" size="middle" style={{ width: '100%' }}>
            <Descriptions
              size="small"
              column={1}
              items={[
                { key: 'title', label: '标题', children: detailData.title },
                { key: 'source', label: '来源', children: detailData.source_label || detailData.source },
                {
                  key: 'category',
                  label: '分类',
                  children: detailData.category ? detailData.category_label || detailData.category : '—'
                },
                { key: 'publish', label: '发布时间', children: detailData.publish || '—' },
                {
                  key: 'link',
                  label: '链接',
                  children: (
                    <Typography.Link href={detailData.link} target="_blank" rel="noreferrer">
                      {detailData.link}
                    </Typography.Link>
                  )
                }
              ]}
            />
            <Typography.Title level={5}>正文内容</Typography.Title>
            <Typography.Paragraph style={{ whiteSpace: 'pre-wrap' }}>
              {detailData.detail || '无内容'}
            </Typography.Paragraph>
          </Space>
        ) : (
          <Typography.Text>未找到文章内容</Typography.Text>
        )}
      </Modal>

      <Modal
        destroyOnClose
        title={`AI评估详情 #${currentInfoId ?? ''}`}
        open={reviewVisible}
        onCancel={() => {
          setReviewVisible(false)
          setReviewData(null)
        }}
        footer={null}
        width={760}
      >
        {reviewLoading ? (
          <Space style={{ width: '100%', justifyContent: 'center' }}>
            <Spin />
          </Space>
        ) : reviewData && reviewData.has_review ? (
          <Space direction="vertical" size="middle" style={{ width: '100%' }}>
            <Space size="large">
              <Typography.Text strong>综合得分:</Typography.Text>
              <Tag color={scoreTagColor(reviewData.final_score ?? null)}>
                {typeof reviewData.final_score === 'number' ? reviewData.final_score.toFixed(1) : '—'}
              </Tag>
            </Space>
            <Space size="large">
              <Typography.Text strong>更新时间:</Typography.Text>
              <Typography.Text>{reviewData.updated_at || reviewData.created_at || '—'}</Typography.Text>
            </Space>
            <Table
              size="small"
              pagination={false}
              columns={reviewMetricsColumns}
              dataSource={reviewData.scores.map((item, index) => ({
                key: `${item.metric_key}-${index}`,
                ...item
              }))}
            />
            <div>
              <Typography.Title level={5}>AI评价</Typography.Title>
              <Typography.Paragraph style={{ whiteSpace: 'pre-wrap' }}>
                {reviewData.ai_comment || '暂无评价'}
              </Typography.Paragraph>
            </div>
            <div>
              <Typography.Title level={5}>AI概要（一句话）</Typography.Title>
              <Typography.Paragraph style={{ whiteSpace: 'pre-wrap' }}>
                {reviewData.ai_summary || '暂无概要'}
              </Typography.Paragraph>
            </div>
            <div>
              <Typography.Title level={5}>AI摘要（约50字）</Typography.Title>
              <Typography.Paragraph style={{ whiteSpace: 'pre-wrap' }}>
                {aiSummaryLong || '暂无摘要'}
              </Typography.Paragraph>
            </div>
            <div>
              <Typography.Title level={5}>关键概念</Typography.Title>
              {keyConcepts.length > 0 ? (
                <Space size={[8, 8]} wrap>
                  {keyConcepts.map((concept, index) => (
                    <Tag key={`${concept}-${index}`}>{concept}</Tag>
                  ))}
                </Space>
              ) : (
                <Typography.Text>暂无关键概念</Typography.Text>
              )}
            </div>
            {reviewData.raw_response && (
              <div>
                <Typography.Title level={5}>原始响应</Typography.Title>
                <Typography.Paragraph
                  style={{ whiteSpace: 'pre-wrap', background: '#f6f6f6', padding: 12, borderRadius: 4 }}
                >
                  {reviewData.raw_response}
                </Typography.Paragraph>
              </div>
            )}
          </Space>
        ) : (
          <Typography.Text>暂无评估记录</Typography.Text>
        )}
      </Modal>
    </div>
  )
}
