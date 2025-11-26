import { useEffect, useMemo, useState } from 'react'
import { Button, Card, Result, Spin, Typography } from 'antd'
import { useSearchParams } from 'react-router-dom'
import { unsubscribeEmail, UnsubscribeResult } from '../api'

export default function UnsubscribePage() {
  const [searchParams] = useSearchParams()
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [data, setData] = useState<UnsubscribeResult | null>(null)

  const email = searchParams.get('email') || ''
  const pidRaw = searchParams.get('pipeline_id')
  const reason = searchParams.get('reason') || undefined
  const pipelineId = useMemo(() => {
    if (!pidRaw) return null
    const n = Number(pidRaw)
    return Number.isNaN(n) ? null : n
  }, [pidRaw])

  useEffect(() => {
    setLoading(true)
    setError(null)
    if (!email) {
      setError('缺少邮箱参数，无法退订')
      setLoading(false)
      return
    }
    unsubscribeEmail(email, pipelineId, reason)
      .then((resp) => setData(resp))
      .catch((err) => {
        const detail = err?.response?.data?.detail || err?.message || '退订失败'
        setError(detail)
      })
      .finally(() => setLoading(false))
  }, [email, pipelineId, reason])

  const owner = data?.owner_name || data?.owner_email || '创建者'
  const pipelineName = data?.pipeline_name || '该管线'

  return (
    <div style={{ maxWidth: 720, margin: '32px auto', padding: '0 16px' }}>
      <Card>
        {loading ? (
          <div style={{ textAlign: 'center', padding: '32px 0' }}>
            <Spin />
            <div style={{ marginTop: 12, color: '#64748b' }}>正在退订...</div>
          </div>
        ) : error ? (
          <Result status="error" title="退订失败" subTitle={error} />
        ) : (
          <Result
            status="success"
            title="退订成功"
            subTitle={`已成功退订 “${owner}” 的管线 “${pipelineName}”。`}
            extra={[
              <Button type="primary" key="manage" href="/">
                前往管理
              </Button>
            ]}
          >
            <Typography.Paragraph style={{ marginBottom: 8 }}>
              邮箱 <strong>{data?.email}</strong> 将不再接收该管线的推送。
            </Typography.Paragraph>
            <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
              如需重新订阅，请登录后在「我的推送」中恢复。
            </Typography.Paragraph>
          </Result>
        )}
      </Card>
    </div>
  )
}
