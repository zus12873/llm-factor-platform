/**
 * Application shell: navigation, status banner, and the outlet for a route.
 *
 * The health banner is not decoration. This platform runs offline by design, and
 * a researcher who does not know Wind is disconnected will read a fake-data run
 * as a real one. Degraded state is shown continuously rather than only on error.
 */
import { Layout, Menu, Tag, Typography } from "antd"
import { Link, Outlet, useLocation } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import { apiClient } from "../api/client"

const { Header, Content } = Layout

export const NAV_ITEMS = [
  { key: "/workbench", label: "因子工作台" },
  { key: "/reports", label: "研报提取" },
  { key: "/library", label: "因子库" },
] as const

function HealthBadge() {
  const { data, isError } = useQuery({
    queryKey: ["health"],
    queryFn: apiClient.health,
    refetchInterval: 30_000,
  })

  if (isError) return <Tag color="red">后端不可达</Tag>
  if (!data) return <Tag>检查中…</Tag>

  // Name the components that are off, rather than a single green light: "ok"
  // with Wind disabled would tell a researcher their run used real data.
  const offline = data.components
    .filter((c) => c.status !== "ok")
    .map((c) => c.name)

  if (offline.length === 0) return <Tag color="green">全部就绪</Tag>
  return <Tag color="orange">离线模式：{offline.join("、")}</Tag>
}

export function AppShell() {
  const location = useLocation()
  const selected =
    NAV_ITEMS.find((item) => location.pathname.startsWith(item.key))?.key ??
    NAV_ITEMS[0].key

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Header style={{ display: "flex", alignItems: "center", gap: 24 }}>
        <Typography.Text strong style={{ color: "#fff", whiteSpace: "nowrap" }}>
          因子研究平台
        </Typography.Text>
        <Menu
          theme="dark"
          mode="horizontal"
          selectedKeys={[selected]}
          style={{ flex: 1, minWidth: 0 }}
          items={NAV_ITEMS.map((item) => ({
            key: item.key,
            label: <Link to={item.key}>{item.label}</Link>,
          }))}
        />
        <HealthBadge />
      </Header>
      <Content style={{ padding: 24 }}>
        <Outlet />
      </Content>
    </Layout>
  )
}
