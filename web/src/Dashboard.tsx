import { useCallback, useEffect, useMemo, useState } from "react";
import BarChart from "@cloudscape-design/components/bar-chart";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Cards from "@cloudscape-design/components/cards";
import ColumnLayout from "@cloudscape-design/components/column-layout";
import Container from "@cloudscape-design/components/container";
import ContentLayout from "@cloudscape-design/components/content-layout";
import ExpandableSection from "@cloudscape-design/components/expandable-section";
import Flashbar, { FlashbarProps } from "@cloudscape-design/components/flashbar";
import Grid from "@cloudscape-design/components/grid";
import Header from "@cloudscape-design/components/header";
import KeyValuePairs from "@cloudscape-design/components/key-value-pairs";
import Select, { SelectProps } from "@cloudscape-design/components/select";
import SpaceBetween from "@cloudscape-design/components/space-between";
import StatusIndicator, { StatusIndicatorProps } from "@cloudscape-design/components/status-indicator";
import Table from "@cloudscape-design/components/table";
import { AlertRow, AuthError, Device, EventRow, Privacy, State, StatsBucket, ago, api, clock } from "./api";

//Polling time so this Everything is calculated in 2 second intervals
const POLL_MS = 2000;

const STATE_UI: Record<State, { type: StatusIndicatorProps.Type; label: string }> = {
  IDLE: { type: "success", label: "Idle" },
  FALLING: { type: "warning", label: "Falling" },
  LAYING: { type: "warning", label: "Person On the floor" },
  ALERT: { type: "error", label: "ALERT: fall detected" },
};

function StateBadge({ state }: { state: State | null }) {
  if (!state) return <StatusIndicator type="pending">No data yet</StatusIndicator>;
  const ui = STATE_UI[state];
  return <StatusIndicator type={ui.type}>{ui.label}</StatusIndicator>;
}

export default function Dashboard({ onAuthLost }: { onAuthLost: () => void }) {
  const [devices, setDevices] = useState<Device[]>([]);
  const [alerts, setAlerts] = useState<AlertRow[]>([]);
  const [events, setEvents] = useState<EventRow[]>([]);
  const [stats, setStats] = useState<StatsBucket[]>([]);
  const [privacy, setPrivacy] = useState<Privacy | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [deviceFilter, setDeviceFilter] = useState<SelectProps.Option>({ value: "", label: "All devices" });
  const [, setTick] = useState(0); // re-render so "x s ago" stays fresh

  const load = useCallback(async () => {
    try {
      const [d, a, e, s] = await Promise.all([
        api.devices(), api.alerts(), api.events(deviceFilter.value || undefined), api.stats(24),
      ]);
      setDevices(d.devices);
      setAlerts(a.alerts);
      setEvents(e.events);
      setStats(s.buckets);
      setError(null);
    } catch (err) {
      if (err instanceof AuthError) onAuthLost();
      else setError("Can't reach the server. Retrying...");
    }
  }, [deviceFilter, onAuthLost]);

  useEffect(() => {
    load();
    const id = setInterval(() => { load(); setTick((t) => t + 1); }, POLL_MS);
    return () => clearInterval(id);
  }, [load]);

  useEffect(() => { api.privacy().then(setPrivacy).catch(() => {}); }, []);

  const ack = async (id: string) => { await api.ack(id); load(); };

  const openAlerts = alerts.filter((a) => a.acked_at == null);
  const flash: FlashbarProps.MessageDefinition[] = [
    ...openAlerts.map((a): FlashbarProps.MessageDefinition => ({
      id: `alert-${a.id}`,
      type: "error",
      header: `Fall alert: ${a.device_name}`,
      content: `Person on the floor and not getting up. Raised ${ago(a.ts)} (${clock(a.ts)}).`,
      action: <Button onClick={() => ack(a.id)}>Acknowledge</Button>,
    })),
    ...devices.filter((d) => !d.online).map((d): FlashbarProps.MessageDefinition => ({
      id: `offline-${d.id}`,
      type: "warning",
      header: `${d.name} is offline`,
      content: `No signal since ${ago(d.last_seen)}. Falls in this room are NOT being reported until it reconnects.`,
    })),
    ...(error ? [{ id: "err", type: "warning" as const, content: error }] : []),
  ];

  const chartX = useMemo(
    () => stats.map((b) => new Date(b.hour_start * 1000).toLocaleTimeString([], { hour: "2-digit" })),
    [stats],
  );

  const deviceOptions: SelectProps.Option[] = [
    { value: "", label: "All devices" },
    ...devices.map((d) => ({ value: d.id, label: d.name })),
  ];

  const online = devices.filter((d) => d.online).length;

  return (
    <ContentLayout header={<Header variant="h1" description="Live fall-detection status. No camera images ever reach this page.">Monitoring</Header>}>
      <SpaceBetween size="l">
        {flash.length > 0 && <Flashbar items={flash} stackItems={flash.length > 3} />}

        <Container>
          <ColumnLayout columns={3} variant="text-grid">
            <div><Box variant="awsui-key-label">Open alerts</Box><Box variant="awsui-value-large" color={openAlerts.length ? "text-status-error" : undefined}>{openAlerts.length}</Box></div>
            <div><Box variant="awsui-key-label">Possible falls (24 h)</Box><Box variant="awsui-value-large">{stats.reduce((n, b) => n + b.falls, 0)}</Box></div>
            <div><Box variant="awsui-key-label">Alerts (24 h)</Box><Box variant="awsui-value-large">{stats.reduce((n, b) => n + b.alerts, 0)}</Box></div>
          </ColumnLayout>
        </Container>

        <Container>
          <ColumnLayout columns={1} variant="text-grid">
            <div><Box variant="awsui-key-label">Devices online</Box><Box variant="awsui-value-large">{online}/{devices.length}</Box></div>
          </ColumnLayout>
        </Container>

        <Cards
          header={<Header counter={`(${devices.length})`}>Devices</Header>}
          items={devices}
          trackBy="id"
          cardsPerRow={[{ cards: 1 }, { minWidth: 500, cards: 2 }, { minWidth: 900, cards: 3 }]}
          empty={<Box textAlign="center" color="inherit">No devices registered. Use <code>manage.py add-device</code>.</Box>}
          cardDefinition={{
            header: (d) => d.name,
            sections: [
              { id: "state", header: "Current state", content: (d) => <StateBadge state={d.online ? d.current_state : null} /> },
              {
                id: "health", header: "Connection",
                content: (d) => d.online
                  ? <StatusIndicator type="success">Online</StatusIndicator>
                  : <StatusIndicator type="stopped">Offline (last seen {ago(d.last_seen)})</StatusIndicator>,
              },
              { id: "id", header: "Device ID", content: (d) => <Box variant="code">{d.id}</Box> },
            ],
          }}
        />

        <Grid gridDefinition={[{ colspan: { default: 12, l: 7 } }, { colspan: { default: 12, l: 5 } }]}>
          <Container header={<Header description="Per hour, last 24 hours. A possible fall that ends with the person getting up is not an alert.">Falls and alerts</Header>}>
            <BarChart
              series={[
                { title: "Possible falls (entered FALLING)", type: "bar", data: stats.map((b, i) => ({ x: chartX[i], y: b.falls })) },
                { title: "Confirmed alerts", type: "bar", data: stats.map((b, i) => ({ x: chartX[i], y: b.alerts })) },
              ]}
              xScaleType="categorical"
              xTitle="Hour"
              yTitle="Count"
              height={220}
              hideFilter
              empty={<Box textAlign="center" color="inherit">No data yet</Box>}
            />
          </Container>

          <Container header={<Header description="What this system collects, read live from the database">Privacy boundary</Header>}>
            {privacy && (
              <SpaceBetween size="m">
                <KeyValuePairs
                  columns={1}
                  items={[
                    { label: "Camera images stored", value: <StatusIndicator type="success">Never. There is no image field anywhere.</StatusIndicator> },
                    { label: "Data kept per event", value: privacy.camera_derived_fields.join(", ") },
                    { label: "Only payload accepted from a device", value: `${privacy.accepted_device_payload.join(", ")} (max ${privacy.max_device_frame_bytes} bytes, anything else rejected)` },
                    { label: "History kept for", value: `${privacy.retention_days} days, then MongoDB deletes it automatically` },
                    { label: "Enforced by", value: privacy.enforced_by },
                  ]}
                />
                <ExpandableSection headerText="Every field the database will accept">
                  <SpaceBetween size="xs">
                    {privacy.schema.map((t) => (
                      <Box key={t.collection} variant="code">{t.collection} {"{"} {t.fields.join(", ")} {"}"}</Box>
                    ))}
                  </SpaceBetween>
                </ExpandableSection>
              </SpaceBetween>
            )}
          </Container>
        </Grid>

        <Table
          header={<Header counter={`(${alerts.length})`}>Alert history</Header>}
          items={alerts}
          trackBy="id"
          variant="container"
          empty={<Box textAlign="center" color="inherit">No alerts</Box>}
          columnDefinitions={[
            { id: "ts", header: "Raised", cell: (a) => `${clock(a.ts)} (${ago(a.ts)})` },
            { id: "device", header: "Device", cell: (a) => a.device_name },
            {
              id: "status", header: "Status",
              cell: (a) => a.acked_at
                ? <StatusIndicator type="success">Acknowledged by {a.acked_by} at {clock(a.acked_at)}</StatusIndicator>
                : <StatusIndicator type="error">Open</StatusIndicator>,
            },
            { id: "action", header: "", cell: (a) => a.acked_at ? null : <Button variant="inline-link" onClick={() => ack(a.id)}>Acknowledge</Button> },
          ]}
        />

        <Table
          header={
            <Header
              counter={`(${events.length})`}
              description="State changes only. Repeated frames are counted as a heartbeat and not stored."
              actions={<Select selectedOption={deviceFilter} options={deviceOptions} onChange={({ detail }) => setDeviceFilter(detail.selectedOption)} />}
            >
              State history
            </Header>
          }
          items={events}
          trackBy="id"
          variant="container"
          empty={<Box textAlign="center" color="inherit">No state changes yet</Box>}
          columnDefinitions={[
            { id: "ts", header: "Time", cell: (e) => clock(e.ts) },
            { id: "device", header: "Device", cell: (e) => e.device_name },
            { id: "from", header: "Previous state", cell: (e) => e.prev_state ? <StateBadge state={e.prev_state} /> : <Box color="text-status-inactive">(first report)</Box> },
            { id: "state", header: "New state", cell: (e) => <StateBadge state={e.state} /> },
          ]}
        />
      </SpaceBetween>
    </ContentLayout>
  );
}
