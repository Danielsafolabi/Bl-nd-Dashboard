import { useState } from "react";
import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Container from "@cloudscape-design/components/container";
import Form from "@cloudscape-design/components/form";
import FormField from "@cloudscape-design/components/form-field";
import Header from "@cloudscape-design/components/header";
import Input from "@cloudscape-design/components/input";
import SpaceBetween from "@cloudscape-design/components/space-between";
import { api } from "./api";

export default function Login({ onLogin }: { onLogin: (username: string) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await api.login(username, password);
      onLogin(r.username);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Login failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Box padding={{ top: "xxxl" }}>
      <div style={{ maxWidth: 420, margin: "0 auto" }}>
        <form onSubmit={(e) => { e.preventDefault(); submit(); }}>
          <Form actions={<Button variant="primary" loading={busy} formAction="submit">Sign in</Button>}>
            <Container header={<Header variant="h1" description="Privacy-preserving fall detection">Bl!nd</Header>}>
              <SpaceBetween size="m">
                {error && <Alert type="error">{error}</Alert>}
                <FormField label="Username">
                  <Input value={username} onChange={({ detail }) => setUsername(detail.value)} autoFocus />
                </FormField>
                <FormField label="Password">
                  <Input type="password" value={password} onChange={({ detail }) => setPassword(detail.value)} />
                </FormField>
                <Box variant="small" color="text-body-secondary">
                  This dashboard never shows camera images. It only receives the fall-detection state from each device.
                </Box>
              </SpaceBetween>
            </Container>
          </Form>
        </form>
      </div>
    </Box>
  );
}
