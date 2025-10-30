import { useEffect, useState } from 'react'
import { Button, Form, Input, Modal, Tabs, message } from 'antd'
import { requestLoginCode, requestSignupCode, verifyLogin, verifySignup } from './api'
import { useAuth } from './auth'

export default function LoginModal() {
  const { loginVisible, setLoginVisible, setUser } = useAuth()
  const [tab, setTab] = useState<'login' | 'signup'>('login')
  const [loading, setLoading] = useState(false)
  const [form] = Form.useForm()
  const [codeSent, setCodeSent] = useState(false)

  useEffect(() => {
    if (!loginVisible) {
      form.resetFields()
      setCodeSent(false)
      setTab('login')
    }
  }, [loginVisible])

  const onSendCode = async () => {
    const email = (form.getFieldValue('email') || '').trim()
    const name = (form.getFieldValue('name') || '').trim()
    if (!email) {
      message.error('请输入邮箱')
      return
    }
    setLoading(true)
    try {
      if (tab === 'login') {
        await requestLoginCode(email)
      } else {
        if (!name) {
          message.error('请输入昵称')
          setLoading(false)
          return
        }
        await requestSignupCode(email, name)
      }
      message.success('验证码已发送，请查收邮箱')
      setCodeSent(true)
    } catch (err: any) {
      const detail = err?.response?.data?.detail || '发送失败'
      message.error(detail)
    } finally {
      setLoading(false)
    }
  }

  const onVerify = async () => {
    const email = (form.getFieldValue('email') || '').trim()
    const code = (form.getFieldValue('code') || '').trim()
    const name = (form.getFieldValue('name') || '').trim()
    if (!email || !code) {
      message.error('请输入邮箱与验证码')
      return
    }
    setLoading(true)
    try {
      const u = tab === 'login' ? await verifyLogin(email, code) : await verifySignup(email, code, name)
      setUser(u)
      setLoginVisible(false)
      message.success('登录成功')
    } catch (err: any) {
      const detail = err?.response?.data?.detail || '校验失败'
      message.error(detail)
    } finally {
      setLoading(false)
    }
  }

  return (
    <Modal
      destroyOnClose
      title={tab === 'login' ? '登录' : '注册'}
      open={loginVisible}
      onCancel={() => setLoginVisible(false)}
      footer={null}
    >
      <Tabs
        activeKey={tab}
        onChange={(k) => {
          setTab(k as 'login' | 'signup')
          setCodeSent(false)
          form.resetFields()
        }}
        items={[
          { key: 'login', label: '登录' },
          { key: 'signup', label: '注册' }
        ]}
      />
      <Form form={form} layout="vertical">
        <Form.Item name="email" label="邮箱" rules={[{ required: true, message: '请输入邮箱' }]}> 
          <Input placeholder="you@example.com" />
        </Form.Item>
        {tab === 'signup' && (
          <Form.Item name="name" label="昵称" rules={[{ required: true, message: '请输入昵称' }]}> 
            <Input placeholder="你的昵称" />
          </Form.Item>
        )}
        {codeSent && (
          <Form.Item name="code" label="验证码" rules={[{ required: true, message: '请输入验证码' }]}> 
            <Input placeholder="4位数字" maxLength={6} />
          </Form.Item>
        )}
        <div style={{ display: 'flex', gap: 12 }}>
          {!codeSent ? (
            <Button type="primary" onClick={onSendCode} loading={loading}>
              发送验证码
            </Button>
          ) : (
            <Button type="primary" onClick={onVerify} loading={loading}>
              校验并登录
            </Button>
          )}
          {codeSent && (
            <Button onClick={onSendCode} disabled={loading}>
              重新发送
            </Button>
          )}
        </div>
      </Form>
    </Modal>
  )
}

