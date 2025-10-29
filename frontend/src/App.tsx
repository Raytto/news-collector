import { Layout, Menu, theme } from 'antd'
import { Route, Routes, Link } from 'react-router-dom'
import { UnorderedListOutlined } from '@ant-design/icons'
import PipelineList from './pages/PipelineList'
import PipelineForm from './pages/PipelineForm'

const { Header, Content } = Layout

export default function App() {
  const {
    token: { colorBgContainer }
  } = theme.useToken()

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header style={{ display: 'flex', alignItems: 'center' }}>
        <div style={{ color: '#fff', fontWeight: 600, marginRight: 24 }}>资讯管理</div>
        <Menu
          theme="dark"
          mode="horizontal"
          defaultSelectedKeys={['list']}
          items={[
            {
              key: 'list',
              label: <Link to="/">投递列表</Link>,
              icon: <UnorderedListOutlined />
            }
          ]}
        />
      </Header>
      <Content style={{ padding: 24, background: colorBgContainer }}>
        <Routes>
          <Route path="/" element={<PipelineList />} />
          <Route path="/new" element={<PipelineForm />} />
          <Route path="/edit/:id" element={<PipelineForm />} />
        </Routes>
      </Content>
    </Layout>
  )
}
