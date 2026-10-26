# netmesh — local network lab harness

A single-file Python tool + tiny web UI for **testing your own network
hardening**: latency, traceroute, DNS, throughput, port scanning, and
rate-limited load tests (HTTP / TCP / UDP / SYN) with jitter, UA rotation
and fragmentation options so you can see how *your* rate-limiters, WAFs
and IDS rules classify traffic.

**Use only on networks and hosts you own or have explicit permission to test.**

## Run

bash
python3 netmesh.py serve # API at :8000, UI at /ui
python3 netmesh.py run ping host=1.1.1.1 count=5
python3 netmesh.py run scan host=192.168.1.1 start=1 end=1024


## Modules
| module     | purpose | key args |
|------------|---------|----------|
| ping       | ICMP echo latency | host, count, size, df |
| trace      | ICMP traceroute   | host, maxhops |
| dns        | A/AAAA/CNAME/MX/TXT/PTR lookups | host, type |
| speed      | HTTP download throughput | url, seconds |
| scan       | threaded TCP port scan | host, start, end |
| httpflood  | paced GET load test    | url, rate, duration, jitter |
| slowloris  | slow-client connection test | host, port, sockets, interval |
| tcpflood   | paced TCP connect+payload | host, port, port_end, rate |
| udpflood   | paced UDP, optional fragmentation | host, port, size, fragment |
| synflood   | raw SYN (root) w/ fallback | host, port, rate, spoof |

## API
- `GET /api/modules` — list modules + args
- `POST /api/run` — `{"module": "ping", "args": {"host": "1.1.1.1"}}` → `{task_id}`
- `GET /api/task/<id>` — status/result
- `POST /api/stop/<id>` — cancel a running task

All tasks write JSON to `runs/` and auto-expire after 24h.
Rate presets: `slow=1/s`, `normal=10/s`, `aggressive=100/s`. Jitter adds
±20% random delay per tick. `synflood` needs root/admin (raw sockets);
without privileges it degrades to a paced connect flood automatically.

📄 Folder layout

netmesh/
├── netmesh.py          
├── ui.html             
├── requirements.txt    
├── run.sh              
├── run.bat             
└── README.md

