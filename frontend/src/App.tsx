import { lazy, Suspense } from 'react'
import { Avatar, Dropdown, Layout, Menu, theme } from 'antd'
import { Route, Routes, Link, useLocation } from 'react-router-dom'
import {
  ApiOutlined,
  BranchesOutlined,
  ExperimentOutlined,
  FileTextOutlined,
  TagsOutlined,
  UnorderedListOutlined,
  UserOutlined
} from '@ant-design/icons'
import { AuthProvider, useAuth } from './auth'
import LoginModal from './LoginModal'

// Lazy-load route components to reduce the initial bundle.
const PipelineList = lazy(() => import('./pages/PipelineList'))
const PipelineForm = lazy(() => import('./pages/PipelineForm'))
const SourceList = lazy(() => import('./pages/SourceList'))
const CategoryList = lazy(() => import('./pages/CategoryList'))
const InfoList = lazy(() => import('./pages/InfoList'))
const AiMetrics = lazy(() => import('./pages/AiMetrics'))
const Evaluators = lazy(() => import('./pages/Evaluators'))
const UnsubscribePage = lazy(() => import('./pages/Unsubscribe'))
const Users = lazy(() => import('./pages/Users'))
const PipelineClassList = lazy(() => import('./pages/PipelineClassList'))

const { Header, Content } = Layout

function AppShell() {
  const {
    token: { colorBgContainer }
  } = theme.useToken()
  const { pathname } = useLocation()
  const { user, signOut, setLoginVisible } = useAuth()
  const isAdmin = (user?.is_admin || 0) === 1
  const pageFallback = <div style={{ padding: 24, textAlign: 'center' }}>加载中...</div>

  // Public unsubscribe page: allow访问无需登录
  if (pathname.startsWith('/unsubscribe')) {
    return (
      <Layout style={{ minHeight: '100vh' }}>
        <Content style={{ padding: 24, background: colorBgContainer }}>
          <Suspense fallback={pageFallback}>
            <UnsubscribePage />
          </Suspense>
        </Content>
      </Layout>
    )
  }

  // 未登录：只显示登录弹窗，不渲染其它内容
  if (!user) {
    return (
      <Layout style={{ minHeight: '100vh' }}>
        <Content
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center'
          }}
        >
          <LoginModal />
        </Content>
      </Layout>
    )
  }

  let selectedKey = 'pipelines'
  if (pathname.startsWith('/infos')) {
    selectedKey = 'infos'
  } else if (pathname.startsWith('/sources')) {
    selectedKey = 'sources'
  } else if (pathname.startsWith('/categories')) {
    selectedKey = 'categories'
  } else if (pathname.startsWith('/pipeline-classes')) {
    selectedKey = 'pipeline-classes'
  } else if (pathname.startsWith('/ai-metrics')) {
    selectedKey = 'ai-metrics'
  } else if (pathname.startsWith('/evaluators')) {
    selectedKey = 'evaluators'
  } else if (pathname.startsWith('/users')) {
    selectedKey = 'users'
  }

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', alignItems: 'center', flex: 1, minWidth: 0 }}>
          <div style={{ color: '#fff', fontWeight: 600, marginRight: 24 }}>资讯管理</div>
        <Menu
          theme="dark"
          mode="horizontal"
          style={{ flex: 1, minWidth: 0 }}
          selectedKeys={[selectedKey]}
          items={[
            {
              key: 'sources',
              label: <Link to="/sources">所有来源</Link>,
              icon: <BranchesOutlined />
            },
            ...(isAdmin
              ? ([
                  {
                    key: 'categories',
                    label: <Link to="/categories">来源类别</Link>,
                    icon: <TagsOutlined />
                  }
                ] as const)
              : []),
            {
              key: 'evaluators',
              label: <Link to="/evaluators">评估器</Link>,
              icon: <ApiOutlined />
            },
            {
              key: 'ai-metrics',
              label: <Link to="/ai-metrics">AI指标</Link>,
              icon: <ExperimentOutlined />
            },
            {
              key: 'infos',
              label: <Link to="/infos">所有资讯</Link>,
              icon: <FileTextOutlined />
            },
            ...(isAdmin
              ? ([
                  {
                    key: 'users',
                    label: <Link to="/users">用户管理</Link>,
                    icon: <UserOutlined />
                  },
                  {
                    key: 'pipeline-classes',
                    label: <Link to="/pipeline-classes">推送类别</Link>,
                    icon: <TagsOutlined />
                  }
                ] as const)
              : []),
            {
              key: 'pipelines',
              label: <Link to="/">我的推送</Link>,
              icon: <UnorderedListOutlined />
            }
          ]}
        />
        </div>
        <div>
          {user ? (
            <Dropdown
              menu={{
                items: [
                  { key: 'name', label: <span>您好，{user.name}</span> },
                  { type: 'divider' },
                  { key: 'logout', label: '退出登录', onClick: () => signOut() }
                ]
              }}
            >
              <div style={{ color: '#fff', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 8 }}>
                <Avatar size={28}>{user.name?.[0] || 'U'}</Avatar>
              </div>
            </Dropdown>
          ) : (
            <a style={{ color: '#fff' }} onClick={() => setLoginVisible(true)}>
              登录/注册
            </a>
          )}
        </div>
      </Header>
      <Content style={{ padding: 24, background: colorBgContainer }}>
        <Suspense fallback={pageFallback}>
          <Routes>
            <Route path="/" element={<PipelineList />} />
            <Route path="/new" element={<PipelineForm />} />
            <Route path="/edit/:id" element={<PipelineForm />} />
            <Route path="/infos" element={<InfoList />} />
            <Route path="/sources" element={<SourceList />} />
            <Route path="/categories" element={<CategoryList />} />
            <Route path="/pipeline-classes" element={<PipelineClassList />} />
            <Route path="/ai-metrics" element={<AiMetrics />} />
            <Route path="/evaluators" element={<Evaluators />} />
            <Route path="/users" element={<Users />} />
          </Routes>
        </Suspense>
      </Content>
    </Layout>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <AppShell />
    </AuthProvider>
  )
}
