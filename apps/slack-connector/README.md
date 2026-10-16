# Slack connector

Lets Nova OS personas read messages and user profiles from a Slack workspace on the operator's behalf, gated per-persona via the `connectors:` YAML field. Slice 1 ships **five read-only tools** — `slack_search`, `slack_read_channel`, `slack_read_thread`, `slack_read_user_profile`, `slack_search_users` — implemented as a built-in Slack OAuth client + encrypted token store inside the running Nova OS process (no extra container).

## How the pieces fit

1. **One-time:** an admin registers a Slack App in the operator's own Slack workspace and sets two env vars on the Nova OS deployment.
2. **Per workspace:** an admin clicks "Connect Slack" on `/admin/integrations.html`, completes Slack's consent screen, and Nova OS stores the workspace bot token encrypted at rest.
3. **Per persona:** an admin opens a persona in the dashboard, ticks "slack" under Tools & Connectors, saves. That persona's agent now resolves the five Slack tools at runtime.

The connector runs with **bot-token identity** — calls authenticate as the workspace bot user, with whatever scopes the admin granted and whatever channels the bot has been added to. Per-user scoping is intentionally out of scope for v1 (would require per-user OAuth and re-introduce the SSO friction this connector avoids).

## One-time Slack App registration

The Nova OS deployment becomes a Slack OAuth client. Each operator registers their own internal Slack App once, then all subsequent connections use the in-dashboard click-to-connect flow.

1. Visit <https://api.slack.com/apps> → **Create New App** → **From scratch**. Pick a name (e.g., "Nova OS") and the workspace you want to install into.

2. **OAuth & Permissions** → under **Bot Token Scopes**, add:

   ```
   channels:read
   channels:history
   groups:history
   users:read
   search:read
   im:history
   mpim:history
   ```

   Scope minimisation: if your operators only need channel-history reads and not search, drop `search:read`. If you don't need DMs, drop `im:history` + `mpim:history`. The smallest set that satisfies your personas is the right one.

3. **OAuth & Permissions** → **Redirect URLs** → add exactly one URL:

   ```
   ${NOVA_OS_PUBLIC_URL}/oauth/slack/callback
   ```

   Substitute `NOVA_OS_PUBLIC_URL` with your deployment's public URL (e.g., `https://nova.acme.example`). Slack will reject the redirect later if the URL doesn't match byte-for-byte, including trailing slash.

4. **Install to Workspace** (button near the top of the OAuth & Permissions page). Approve the bot-token consent screen. This verifies the App registration is correct; the bot token Slack shows you is only used for verification — Nova OS itself will obtain a fresh token via the OAuth flow in step 7.

5. **Basic Information** → **App Credentials** → copy `Client ID` and `Client Secret`.

6. **On the Nova OS deployment**, set:

   ```bash
   NOVA_OS_SLACK_CLIENT_ID=<client id from step 5>
   NOVA_OS_SLACK_CLIENT_SECRET=<client secret from step 5>
   ```

   Restart Nova OS so the new env reaches the OAuth client.

7. Done. The **Connect Slack** button on `/admin/integrations.html` is now wired and ready for any admin to click.

## NOVA_OS_INTEGRATION_KEY

The integration store encrypts the workspace token at rest with a 32-byte symmetric key. Generate once per deployment and persist it:

```bash
openssl rand -hex 32   # 64-character hex; this is your NOVA_OS_INTEGRATION_KEY
```

Set `NOVA_OS_INTEGRATION_KEY=<that hex string>` as an env var on the Nova OS container and restart. If the env var is absent at boot, Nova OS generates one on first start and writes it to the daemon's persistent state — fine for single-instance deployments, but for multi-replica or container-recreated setups you should set it explicitly so all replicas use the same key.

**Treat this key like a database password.** Rotating it invalidates every stored integration token (each connection needs to be re-authorized).

## Connect from the dashboard

1. Open `/admin/integrations.html` on the Nova OS deployment. Sign in as an admin.
2. Click **Connect Slack** under "Available connectors".
3. Slack's consent screen opens. Confirm the workspace and bot scopes.
4. Slack redirects back to `${NOVA_OS_PUBLIC_URL}/oauth/slack/callback`. Nova OS exchanges the code for a token, encrypts it, and stores it. You'll land back on `/admin/integrations.html` with the workspace listed under "Installed connectors".

To grant a persona access:

1. **Personas** page in the dashboard → click the persona.
2. **Tools & Connectors** section → tick **slack**.
3. **Save**. The dashboard rewrites the persona's YAML frontmatter with `connectors: [slack]` and POSTs through the existing `PUT /v1/agents/:id` write path. The persona's agent now resolves the five Slack tools at its next construction.

Personas without `connectors: [slack]` in their frontmatter cannot call Slack tools even after the workspace is connected — the per-persona binding is the primary access control.

## Smoke test

Replace `$BASE` with your Nova OS URL (e.g., `https://nova.acme.example`) and `$TOKEN` with an admin bearer JWT.

```bash
BASE=https://nova.acme.example
TOKEN=...

# 1. Confirm the integration is installed
curl -s -H "Authorization: Bearer $TOKEN" "$BASE/api/admin/integrations" | jq '.[] | select(.kind == "slack")'
# expect: one row with team_id, team_name, scopes[], installed_at, revoked_at: null

# 2. Confirm a persona has the binding
curl -s -H "Authorization: Bearer $TOKEN" "$BASE/v1/agents/legal-assistant" | jq '.connectors'
# expect: ["slack"]   (or fails with [] / null if not bound — go back and tick the box)

# 3. Exercise slack_search via a chat call
curl -s -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     "$BASE/v1/managed/agents/legal-assistant/messages" \
     -d '{
       "messages": [
         {"role": "user", "content": "Search Slack for messages about the renewal deadline"}
       ]
     }' | jq '.content'
# expect: response text referencing actual messages from the workspace's channels
#         the bot has been added to. tool_use entries in .content will name
#         slack_search and carry the structured result.
```

## Revoke

To disconnect a workspace (token gets marked revoked, future tool calls return an error until reconnected):

```bash
# 1. Find the integration id
INTEGRATION_ID=$(curl -s -H "Authorization: Bearer $TOKEN" "$BASE/api/admin/integrations" \
  | jq -r '.[] | select(.kind == "slack") | .id')

# 2. Revoke
curl -X DELETE -H "Authorization: Bearer $TOKEN" "$BASE/api/admin/integrations/$INTEGRATION_ID"
# expect: 200 + empty body
```

Personas with `connectors: [slack]` continue to load, but their slack tools return `connector_revoked` errors until an admin re-runs the **Connect Slack** flow.

To remove a persona's Slack access without revoking the workspace integration, untick "slack" in the persona editor and save — the YAML frontmatter loses the entry, the persona's next construction skips loading the Slack tools.

## docker-compose

No compose file ships with this app — the Slack connector lives entirely inside the running `nova-os` container (OAuth handler, integration store, tool implementations are all built into the binary). You only need the **env vars** from the sections above on whatever compose / k8s / systemd unit runs `nova-os` itself.

A future `apps/slack-connector/docker-compose.yaml` may appear if a separate event-bridge daemon ships (slice 4 in the spec — the "bot in Slack" UI bridge); slice 1 doesn't need one.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| **"Connect Slack" button missing on `/admin/integrations.html`** | `NOVA_OS_SLACK_CLIENT_ID` or `NOVA_OS_SLACK_CLIENT_SECRET` unset on the running Nova OS. Re-check the env vars in step 6 of the Slack App registration, then restart. |
| **Slack consent screen errors with `redirect_uri did not match`** | The Slack App's Redirect URL doesn't byte-match `${NOVA_OS_PUBLIC_URL}/oauth/slack/callback`. Trailing slashes, http-vs-https, and reverse-proxy rewrites all matter — check both sides. |
| **Personas can chat normally but slack tools don't appear in their tool list** | The persona's YAML frontmatter is missing `connectors: [slack]`. Re-open the persona editor, tick the box, save. Confirm via `GET /v1/agents/<id>` → `.connectors`. |
| **`slack_search` returns `invalid_auth`** | Workspace token was rotated or admin removed the App from Slack. `/admin/integrations.html` will show the integration as needing reconnect; click **Reconnect Slack**. |
| **Empty results from `slack_search` despite confirmed installation** | The Slack bot needs to be **added as a member** of each channel it should be able to search. By design — Slack scopes channel visibility to channel membership, even for bots with workspace-wide scopes. |

## What slice 1 doesn't ship

The connector is **read-only in slice 1.** Write tools (`slack_send_message`, `slack_schedule_message`, `slack_react`) land in slice 2 — they need the same OAuth + integration store but additional scopes (`chat:write`, `reactions:write`).

The chat-bridge (bot-in-Slack UI: mention `@NovaBot` in a channel, get a persona reply) is a separate concern from the connector and lives at slice 4 of the spec — a different deployment unit (probably its own container under this repo) with a different threat model. Tracked separately.
