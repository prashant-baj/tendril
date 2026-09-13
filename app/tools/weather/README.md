# Weather Tool (AF-03 reference implementation)

A single-operation Tool API: `GET ?lat=&lon=` → current forecast, backed by
[Open-Meteo](https://open-meteo.com) (free, keyless).

Deployed as a Lambda Function URL with `AuthType: AWS_IAM` (`infra/stacks/agentcore_stack.py`),
not API Gateway — see `handler.py`'s module docstring for why. `openapi.yaml` is still the
contract; it just isn't a CDK deploy artifact for this tool.

A specialist calls this by declaring `"tools": ["weather"]` in its `agents/registry/*.json`
entry — `AgentCoreStack` then grants that specialist's execution role `lambda:InvokeFunctionUrl`
scoped to this function, and injects the resolved URL via `TOOL_ENDPOINTS`. The shared agent
template (`agents/hello_agent/agent.py`) turns that into a real Strands tool at cold start; see
its module docstring for the (tool-agnostic) binding mechanism.

## Local test

```
pytest app/tools/weather/tests
```

## Manual smoke test (dev)

```
curl -H "Authorization: <SigV4>" "https://<function-url>/?lat=12.97&lon=77.59"
```

(A plain `curl` without a SigV4 signature 403s — `AuthType: AWS_IAM` is enforced.)
