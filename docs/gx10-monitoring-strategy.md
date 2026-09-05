# Grafana Monitoring Strategy — Asus Ascent GX10 Pair

Created: 2026-08-14 (updated with corrected topology)
Author: Rowbutt / Hermes Agent

---

## 1. Purpose

Establish a Grafana dashboard to track **power utilization**, **memory use**, and **token metrics** for a pair of Asus Ascent GX10 machines on the home network.

---

## 2. Discovered Network Topology

### GX10 Targets

| IP | Hardware | OS | Key Open Ports | Notes |
|----|----------|----|----------------|-------|
| `192.168.1.11` | GX10 #1 | Debian 12 (from SSH banner) | 8000 (inference API?), 22 | Web service responds on :8000 |
| `192.168.1.12` | GX10 #2 | Debian 12 (assumed) | Unknown | Not yet probed; likely same profile as .11 |

**Note:** `.11:8000` returns an HTML page — needs investigation to determine if this is an inference server (vLLM, Triton, Ollama) or some other web service. The GX10s and the Raspberry Pis are on the same subnet with no VLAN separation, but there is no reason they would naturally communicate.

### Raspberry Pi 5 Cluster (4 machines)

| IP | Hardware | OS | Key Open Ports | Notes |
|----|----------|----|----------------|-------|
| `192.168.1.240` | Raspberry Pi 5 #1 | Debian 12 | 22, 7946, 9001, 24007, 55390 | HTTPS API on :9001 requires signed headers |
| `192.168.1.241` | Raspberry Pi 5 #2 | Debian 12 | 22, **2377** (Docker swarm), 7946, 9001, 24007, 58525 | HTTPS API on :9001 requires signed headers |
| `192.168.1.242` | Raspberry Pi 5 #3 | Debian 12 | 22, 2377, 7946, 9001, 24007, 49277 | HTTPS API on :9001 requires signed headers |
| `192.168.1.243` | Raspberry Pi 5 #4 | Debian 12 | 22, 2377, 7946, 8080, 9001, 24007, 41841, 58771 | qBittorrent Web UI on :8080; also has :9001 API |

**Note:** All four Pi 5s expose the same signed-request API on port 9001 — this could be an Asus management layer, a Docker swarm management endpoint, or a custom application. The Pi 5s are **not** monitoring targets for this project; they are listed here for awareness since they share the same IP range.

### Existing Monitoring Infrastructure

| IP | Hardware | Services | Authentication |
|----|----------|----------|---------------|
| `192.168.1.9` | HP Enterprise (OUI `9C:6B:00`) | **Grafana on :3000** + **Prometheus on :9090** | Grafana requires login; Prometheus API is unauthenticated |
| `192.168.1.245` | HP Enterprise (same range) | node_exporter on :9100 | Unauthenticated |

### Other Notable Hosts

| IP | Hardware | Notes |
|----|----------|-------|
| `192.168.1.1` | Router | Gateway |
| `192.168.1.52` | QEMU VM (OUI `BC:24:11`) | Ubuntu SSH-only |
| `192.168.1.53` | Unknown — Intel NIC (OUI `B0:82:E2`) | Debian 10, SSH-only |
| `192.168.1.57` | **This host (Hermes)** | Docker available (26.1.5), 24GB free disk |
| `192.168.1.58` | QEMU VM (OUI `BC:24:11`) | Debian 13, SSH-only |

---

## 3. Current State

- **Prometheus** is already running at `192.168.1.9:9090` (unauthenticated, scrapeable)
  - Currently scraping: `cadvisor:8080` (UP), `localhost:9090` (UP)
  - Stale/down targets: `192.168.1.66:9400` (old GPU/DCGM), `192.168.1.66:8080` (old cAdvisor) — machine .66 is gone
  - **No GPU metrics flowing at all**
  - Retention: 15 days (`storage.tsdb.retention.time: "15d"`)
  - Running in agent mode (Prometheus 3.11.2)
- **Grafana** is running at `192.168.1.9:3000` but requires authentication
- **The GX10s (.11, .12) have no monitoring exporters installed** (no DCGM, node_exporter, or inference metrics endpoints confirmed)
- **Lifecycle management is DISABLED** on the Prometheus instance — the `/-/reload` API endpoint will NOT work (see Phase 3 workaround)

### GX10 Port 9001 — Correction

The port 9001 signed-request API discovered earlier belongs to the **Raspberry Pi 5s**, not the GX10s. The GX10s at `.11` and `.12` have not yet been fully port-scanned.

---

## 4. Proposed Architecture

### Layer 1 — Data Collection (on each GX10)

Three exporters needed on each of the two GX10 machines:

| Exporter | Port | Container Image | Metrics Exposed |
|----------|------|----------------|-----------------|
| **DCGM Exporter** | 9400 | `nvcr.io/nvidia/k8s-dcgm-exporter` | GPU power (W), GPU mem used/total, GPU util %, temperature, clocks, PCIe throughput |
| **Node Exporter** | 9100 | `prom/node-exporter` | System RAM, CPU, disk, network I/O, load |
| **Inference Server Exporter** | varies | depends on engine | Tokens/sec, requests/sec, latency p50/p95/p99 |

**Power notes:**
- DCGM gives **GPU board power only** (150-250W typical for RTX-class), not total AC draw
- For system-level AC power: need smart plug/PDU (TP-Link Kasa, Shelly PM) with Prometheus exporter
- Alternative: IPMI/BMC metrics if the GX10 supports it

### Layer 2 — Metrics Pipeline

```
GX10 .11 (:9400 DCGM, :9100 node_exporter, :8xxx inference)
GX10 .12 (:9400 DCGM, :9100 node_exporter, :8xxx inference)
        │
        ▼
Prometheus (:9090) ◄── currently at .9, or deploy new instance
        │
        ▼
Grafana (:3000) ◄── currently at .9 (auth), or deploy new instance
```

**Three options for where the stack lives:**

**Option A (recommended): Piggyback on .9's existing Prometheus**
- Pros: Infra already running, Prometheus API is open, just needs new scrape targets
- Cons: Need Grafana access (credentials for .9:3000, or deploy a second Grafana); also need a way to reload the Prometheus config (lifecycle API is disabled)
- Action: Add scrape configs to Prometheus on .9 for `.11:9400`, `.11:9100`, `.12:9400`, `.12:9100`, then apply config via SIGHUP on the .9 host

**Option B: Deploy fresh stack on Hermes (this host)**
- Pros: Full control, 24GB free disk, Docker ready, `docker compose` available
- Cons: Duplicates infra, needs maintenance
- Action: `docker compose up` with prometheus + grafana on `192.168.1.57`

**Option C: Deploy stack on one GX10**
- Pros: Closest to metrics source
- Cons: Adds monitoring overhead to inference machines

### Layer 3 — Dashboard Design

Proposed Grafana dashboard layout (single dashboard, row-organized):

```
Row 1: SYSTEM OVERVIEW
  ├── CPU Utilization % (time series, both GX10s overlaid)
  ├── System Memory Used / Total (gauge + time series per GX10)
  └── Network Throughput RX/TX (per GX10)

Row 2: GPU POWER
  ├── Instant Power Draw (W) — stat panel per GPU
  ├── Power Draw Over Time — time series, both overlaid
  └── Energy Consumed (kWh) — running total / day

Row 3: GPU MEMORY
  ├── GPU Memory Used % — gauge per GPU
  ├── GPU Memory Used (GiB) — time series
  └── GPU Memory Temperature

Row 4: GPU UTILIZATION
  ├── GPU Core Utilization % — time series
  ├── GPU Memory Utilization %
  └── GPU Clock Frequencies (core + memory)

Row 5: INFERENCE TOKENS (if available)
  ├── Tokens/sec (prompt vs generation)
  ├── Requests/sec
  ├── TTFT (Time to First Token)
  └── Inter-token Latency p50/p95
```

---

## 5. Implementation Roadmap

### Phase 1: Access & Recon
1. Probe `.11` and `.12` to identify the inference engine (vLLM :8000/metrics, Triton :8000/metrics, Ollama :11434, etc.)
2. Get SSH access to the GX10s
3. Inspect Docker containers to identify the inference engine
4. Determine the inference engine's metrics endpoint
5. Get Grafana credentials for .9 or decide to deploy a new Grafana

### Phase 2: Deploy Exporters
1. Install Docker (if not already present) on each GX10
2. Deploy **DCGM Exporter** container (`nvcr.io/nvidia/k8s-dcgm-exporter:latest`)
   - `docker run -d --restart=always --gpus all -p 9400:9400 nvcr.io/nvidia/k8s-dcgm-exporter:latest`
3. Deploy **Node Exporter** container (`prom/node-exporter:latest`)
   - `docker run -d --restart=always -p 9100:9100 --net=host prom/node-exporter`
4. Verify exports respond: `curl http://<GX10-IP>:9400/metrics`

### Phase 3: Configure Prometheus
1. Add scrape targets to Prometheus config:
   ```yaml
   - job_name: 'gx10-power'
     static_configs:
       - targets: ['192.168.1.11:9400', '192.168.1.12:9400']
         labels:
           group: gx10
   - job_name: 'gx10-system'
     static_configs:
       - targets: ['192.168.1.11:9100', '192.168.1.12:9100']
         labels:
           group: gx10
   - job_name: 'gx10-inference'
     static_configs:
       - targets: ['192.168.1.11:8000', '192.168.1.12:8000']
         labels:
           group: gx10
   ```
2. Reload Prometheus config — **⚠️ API reload won't work.** The Prometheus server at .9 has `web.enable-lifecycle: false`, so `POST /-/reload` returns 404. You must send a **SIGHUP** to the Prometheus process on the .9 host:
   ```bash
   # Find the PID and reload:
   ps aux | grep prometheus | grep -v grep
   kill -HUP <PID>
   ```
   Or if Prometheus is running in a Docker container on .9:
   ```bash
   docker kill -s HUP <prometheus-container>
   ```
3. Verify targets are UP in Prometheus status page: `http://192.168.1.9:9090/targets`

### Phase 4: Build Grafana Dashboard
1. Connect Grafana to Prometheus data source
2. Import or build dashboard using panels described above
3. Set up variable dropdowns for GX10 selection
4. Test with live data

### Phase 5: Alerts (Future)
- Power exceeding 90% TDP
- GPU memory near capacity
- High temperature warnings
- Token throughput anomaly detection

---

## 6. Key Questions for Robert

1. **What inference engine is running on the GX10s?** (vLLM, TensorRT-LLM, Ollama, NVIDIA NIM, Triton?) This determines the token metrics endpoint. Port 8000 on `.11` returns an HTML page — what's actually there?

2. **Existing Grafana on .9** — do you have admin credentials? If not, should I deploy a new Grafana instance (on this Hermes host or elsewhere)?

3. **Power scope**: Do you want:
   - (a) GPU board power only (via DCGM — easy, accurate)
   - (b) System AC power draw too (needs smart plug / PDU hardware)
   - (c) Both

4. **Where should the monitoring stack live?**
   - Option A: Add scrape targets to the existing Prometheus on .9 (needs Grafana access, and someone to SIGHUP the Prometheus process)
   - Option B: Deploy a fresh Prometheus+Grafana on this Hermes host (.57)
   - Option C: Something else?

5. **Do you have SSH credentials** for the GX10s? We need access to deploy exporters and inspect Docker containers.

6. **What's the port 9001 signed-request API** on the Raspberry Pi 5s? Not relevant to the monitoring project, just curious.

---

## 7. Quick-Reference Commands

```bash
# Check if DCGM exporter is running on a GX10
curl http://192.168.1.11:9400/metrics | head -20

# Probe for inference API on GX10
curl http://192.168.1.11:8000/v1/models  # vLLM/Ollama
curl http://192.168.1.11:8000/metrics    # Prometheus metrics endpoint

# Deploy DCGM exporter (on GX10)
docker run -d --restart=always --gpus all -p 9400:9400 \
  nvcr.io/nvidia/k8s-dcgm-exporter:latest

# Deploy node_exporter (on GX10)
docker run -d --restart=always -p 9100:9100 --net=host \
  prom/node-exporter:latest

# Verify Prometheus targets
curl http://192.168.1.9:9090/api/v1/targets | python3 -m json.tool

# Reload Prometheus config — SIGHUP required (API reload is disabled)
# On the .9 host:
kill -HUP $(pgrep prometheus)
```

---

## 8. Appendix: Prometheus on .9 — Current Config

The existing Prometheus at `192.168.1.9:9090` has these scrape jobs configured:

| Job | Target | Status | Labels |
|-----|--------|--------|--------|
| `prometheus` | localhost:9090 | UP | — |
| `cadvisor-monitoring` | cadvisor:8080 | UP | instance=monitoring-server |
| `gpu-ai-server` | 192.168.1.66:9400 | DOWN | instance=ai-server, server=threadripper |
| `cadvisor-ai-server` | 192.168.1.66:8080 | DOWN | instance=ai-server, server=threadripper |

- **Version:** Prometheus 3.11.2
- **Retention:** 15 days (`storage.tsdb.retention.time: "15d"`)
- **Scrape interval:** 15s, **Scrape timeout:** 10s
- **Mode:** Agent mode (`storage.agent.path: "data-agent/"`)
- **Lifecycle API:** Disabled (reload via SIGHUP only)
- **Config file:** `/etc/prometheus/prometheus.yml` (on the .9 host)
