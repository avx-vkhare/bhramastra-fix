# Jira component → feature-map rows

The code map lives in the repo, not here:

- `agents/feature-map.md` (cloudn repo root) — feature → translator / service / Python paths.
- `agents/architecture.md` (cloudn repo root) — translator ↔ service pattern, 5-phase
  GatewayService contract, PushConfig invariant.

This file only adds what those docs lack: which **feature-map rows** a Jira
component corresponds to, plus the few directories feature-map doesn't list.
`scripts/context_docs.py` parses this table and pulls the paths from
feature-map.md at run time, so path changes there are picked up automatically.

| Component | Feature-map rows | Extra dirs (not in feature-map) |
|---|---|---|
| Routing | BGP / Routing; Transit / Spoke / Peering; Underlay | `cloudx-local/common` |
| BGP | BGP / Routing; BFD | `cloudx-local/common` |
| Gateway-Platform | Gateway config / DNS; Interface / kernel config; Keepalive / HA; Upgrade | `go/aviatrix.com/gateway`, `go/aviatrix.com/controller` |
| Controller Infrastructure | Persistence (etcd / Mongo / Postgres); Migration | `cloudx-local/common`, `go/aviatrix.com/cmd`, `go/aviatrix.com/util` |
| API | — | `go/aviatrix.com/avxapi`, `api/proto` |
| DCF | Microseg / DFW / Security Policy; iptables / ipset / iprules | `go/aviatrix.com/dcf` |
| Micro-segmentation | Microseg / DFW / Security Policy | `go/aviatrix.com/conduit/v2/csp_conduit` |

Row names must match the first column of a feature-map table exactly;
`context_docs.py --check-map` reports any that don't.

## Hints

- Which side is wrong? See the translator ↔ service section of `architecture.md`:
  gateway misbehaves with correct desired state → `gateway-conduit/<feat>_service.go`;
  wrong desired state → `controller-conduit/<feat>_translator.go` or the Python
  that writes Mongo.
- Controller log paths: `agents/operations.md`. In a tracelog bundle the gateway
  conduit log is `var/log/cloudx/avx-gw-state-sync.log`, diag is
  `etc/localgateway/cloudxd_diag.pretty`, topology is `etc/localgateway/etcd_data.txt`.
- Ownership for reviewers: `CODEOWNERS`.
