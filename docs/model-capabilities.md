# Model capabilities

No model capability is verified in this milestone.

| Capability | State | Reason |
|---|---|---|
| Ollama reachability | UNAVAILABLE on this host | No service at the configured loopback endpoint |
| Text inference | NOT RUN | No model/runtime installed |
| Vision | NOT RUN | No real image request |
| Tool calling | NOT RUN | No model-emitted tool call and continuation |
| Structured output | NOT RUN | No schema-constrained response validated |
| Throughput/RAM/VRAM | NOT RUN | No authorised model download or benchmark |

The UI deliberately separates endpoint status from capability status. Upstream model-card claims are evidence for planning, not local verification. Ordinary app inference will remain separate from privileged development agents.
