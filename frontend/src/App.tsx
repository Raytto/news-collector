import { Layout, Menu, theme } from 'antd'
import { Route, Routes, Link, useLocation } from 'react-router-dom'
import { BranchesOutlined, ExperimentOutlined, FileTextOutlined, TagsOutlined, UnorderedListOutlined } from '@ant-design/icons'
import PipelineList from './pages/PipelineList'
import PipelineForm from './pages/PipelineForm'
import SourceList from './pages/SourceList'
import CategoryList from './pages/CategoryList'
import InfoList from './pages/InfoList'
import AiMetrics from './pages/AiMetrics'

const { Header, Content } = Layout

export default function App() {
  const {
    token: { colorBgContainer }
  } = theme.useToken()
  const { pathname } = useLocation()

  let selectedKey = 'pipelines'
  if (pathname.startsWith('/infos')) {
    selectedKey = 'infos'
  } else if (pathname.startsWith('/sources')) {
    selectedKey = 'sources'
  } else if (pathname.startsWith('/categories')) {
    selectedKey = 'categories'
  } else if (pathname.startsWith('/ai-metrics')) {
    selectedKey = 'ai-metrics'
  }

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header style={{ display: 'flex', alignItems: 'center' }}>
        <div style={{ color: '#fff', fontWeight: 600, marginRight: 24 }}>资讯管理</div>
        <Menu
          theme="dark"
          mode="horizontal"
          selectedKeys={[selectedKey]}
          items={[
            {
              key: 'pipelines',
              label: <Link to="/">投递列表</Link>,
              icon: <UnorderedListOutlined />
            },
            {
              key: 'infos',
              label: <Link to="/infos">资讯管理</Link>,
              icon: <FileTextOutlined />
            },
            {
              key: 'sources',
              label: <Link to="/sources">来源管理</Link>,
              icon: <BranchesOutlined />
            },
            {
              key: 'categories',
              label: <Link to="/categories">类别管理</Link>,
              icon: <TagsOutlined />
            },
            {
              key: 'ai-metrics',
              label: <Link to="/ai-metrics">AI评估维度</Link>,
              icon: <ExperimentOutlined />
            }
          ]}
        />
      </Header>
      <Content style={{ padding: 24, background: colorBgContainer }}>
        <Routes>
          <Route path="/" element={<PipelineList />} />
          <Route path="/new" element={<PipelineForm />} />
          <Route path="/edit/:id" element={<PipelineForm />} />
          <Route path="/infos" element={<InfoList />} />
          <Route path="/sources" element={<SourceList />} />
          <Route path="/categories" element={<CategoryList />} />
          <Route path="/ai-metrics" element={<AiMetrics />} />
        </Routes>
      </Content>
    </Layout>
  )
}
