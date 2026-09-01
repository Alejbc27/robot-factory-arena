# Phase 4 (LLM integration) — ChatGPT response: layering, stretch targets, and R1/R2/R3 reframe

> **Provenance**: verbatim response from the external architecture review session (ChatGPT Web, high-reasoning model), 2026-09-01. Context: follow-up question asked after adopting ADR-001..020 from `docs/architecture-review-response.md` — "partiendo de esos ADRs, ¿cuál es la vía mínima creíble para conectar después un LLM real (OpenAI y GLM), LangGraph, Langfuse y R1/R2/R3, sin reescribir el núcleo?".
>
> **Adopted**: the layering rule (core never knows about OpenAI/GLM/LangGraph/Langfuse/R1-R3), LLMPolicy-first ordering, pinned-provider normalization to the common minimum, replay-from-recorded-ToolCall, Langfuse as observer-only, LangGraph coordinating attempts (not robot moves), R1 human checkpoints between attempts, deterministic coordinator for the first R3, the 11-step build order with gates, and the stretch targets. The R1/R2/R3 reframe ("search across bounded runs") will be reflected in the SDD proposal.

---

Sí. Partiendo de los ADR-001..020, la vía mínima creíble es añadir capas por fuera del núcleo. La regla arquitectónica sería:

El core no sabe qué es OpenAI, GLM, LangGraph, Langfuse, R1, R2 o R3.

Eso encaja además con el brief actual: el LLM debe decidir mediante una interfaz bounded y nunca mutar directamente el world ni saltarse el tool bay.

## Qué envuelve a qué

Yo dejaría la arquitectura así:

```text
┌──────────────────────────────────────────────────────┐
│               EXPERIMENT / CONDITION LAYER           │
│                                                      │
│  LangGraph                                           │
│  ├── R1 human-directed search                        │
│  ├── R2 autonomous search                            │
│  └── R3 shared-discovery search                      │
│                                                      │
│  DiscoveryStore / Frontier / attempt scheduling      │
└─────────────────┬────────────────────────────────────┘
                  │ builds/configures
                  ▼
┌──────────────────────────────────────────────────────┐
│                    POLICY LAYER                      │
│                                                      │
│  LLMPolicy implements existing Policy protocol       │
│                                                      │
│  observation + frozen StrategyContext                │
│                 ↓                                    │
│          ProviderAdapter                             │
│        ┌────────┴────────┐                           │
│    OpenAIAdapter     GLMAdapter                      │
│                 ↓                                    │
│             ToolCall                                 │
└─────────────────┬────────────────────────────────────┘
                  │ existing interface
                  ▼
┌──────────────────────────────────────────────────────┐
│             DETERMINISTIC CORE — UNCHANGED           │
│                                                      │
│  Runner                                              │
│    ↓                                                 │
│  Budget → Grants → ToolBay → Domain transition       │
│                          ↓                           │
│                     WorldState                       │
│                          ↓                           │
│                  Evidence / Receipt                  │
│                          ↓                           │
│                  Independent Verifier                │
└──────────────────────────────────────────────────────┘

              │ events / metadata only
              ▼

┌──────────────────────────────────────────────────────┐
│                    OBSERVABILITY                     │
│                                                      │
│  Langfuse                                            │
│  observer only — never source of truth               │
└──────────────────────────────────────────────────────┘
```

Ese es el boundary que protegería.

LangGraph está pensado precisamente como una capa de orchestration de bajo nivel que puede combinar pasos deterministas y LLM-driven, con persistence y human-in-the-loop. Eso justifica usarlo alrededor de vuestros runs, pero no reemplazar vuestro Runner.

## 1. Primer añadido: LLMPolicy, no LangGraph

El primer paso de phase 4 debería ser simplemente reemplazar:

```text
ProgrammaticPolicy
```

por:

```text
LLMPolicy
```

sin cambiar absolutamente nada downstream.

Hoy conceptual:

```text
observation
    ↓
GreedyPolicy.decide()
    ↓
ToolCall
    ↓
Runner
```

Phase 4:

```text
observation
    ↓
LLMPolicy.decide()
    ↓
ProviderAdapter
    ↓
real model
    ↓
normalized ToolCall
    ↓
Runner
```

Desde la perspectiva del core ambos deberían ser indistinguibles.

### El contrato importante

Algo conceptualmente así:

```python
Policy.decide(observation) -> ToolCall
```

y:

```python
ProviderAdapter.invoke(request) -> ProviderResult
```

El ProviderResult contiene provider-specific evidence:

```text
provider
model
request_id
usage
latency
raw visible response
tool call
error
```

pero el core recibe únicamente el ToolCall.

Eso conserva ADR-003, ADR-004 y ADR-006.

## 2. OpenAI y GLM: adapter común, pero no falsa equivalencia

Aquí vuestro planteamiento de un provider fijado por run es exactamente el correcto. El brief ya establece que no debe haber silent fallback dentro del run.

Usaría:

```text
ProviderAdapter
    invoke(ProviderRequest) -> ProviderResult
```

con:

```text
OpenAIProvider
GLMProvider
```

y un RunConfig:

```text
provider = "openai"
model = "..."
provider_config = {...}
```

Ese config queda congelado y registrado en el receipt.

Ambos proveedores soportan function/tool calling definido mediante JSON Schema, así que ese es un denominador común razonable. OpenAI permite function tools con schemas JSON y strict mode. Z.AI/GLM también expone tools con funciones y parámetros JSON Schema.

Pero no intentaría fingir que sus APIs son idénticas.

Por ejemplo, actualmente OpenAI ofrece controles richer de tool_choice, incluyendo required, forced function y control de parallel calls. La documentación actual de GLM que he comprobado expone tool_choice: auto en esa API.

Por tanto, para la comparación:

**normaliza al mínimo común, no al máximo de OpenAI.**

Para esta arena usaría conceptualmente:

```text
one action decision
one tool maximum
no parallel tool calls
same logical JSON schema
same observation
same action budget
same verifier
provider-specific generation settings recorded
```

No hace falta que los parámetros internos tengan exactamente el mismo nombre. Lo importante es no darle a un provider capacidades funcionales que el otro no recibe.

## 3. Replay no cambia absolutamente nada

Este punto es crítico.

Con un LLM real aparecen dos ejecuciones diferentes:

```text
Live execution

observation
↓
OpenAI / GLM
↓
ToolCall
↓
core
```

```text
Replay

recorded ToolCall
↓
core
```

Nunca:

```text
recorded observation
↓
call OpenAI again
↓
hope it says the same thing
```

El propio brief ya define correctamente esa claim: lo reproducible es la ejecución registrada contra el environment determinista, no los tokens de una inferencia fresca.

Eso significa que phase 4 no altera ADR-010.

Añade otro artifact:

```text
provider_events.jsonl
```

o eventos equivalentes dentro del trace:

```text
model_request
model_response
policy_decision
```

Pero el replay autoritativo comienza en:

```text
policy_decision -> ToolCall
```

El verifier tampoco consulta jamás al modelo.

## 4. Langfuse va después del primer LLM run

Langfuse debe ser una proyección de la ejecución, no una dependencia del experimento.

Vuestro truth path:

```text
JSONL
+
receipt
+
verifier artifacts
```

Langfuse:

```text
same events
     ↓
observability export
```

Si Langfuse está caído:

```text
run = válido
trace local = válido
receipt = válida
verification = válida
Langfuse export = failed
```

No:

```text
Langfuse failed → experiment failed
```

Esto es importante porque Langfuse actualmente se apoya fuertemente en OpenTelemetry y ofrece integrations tanto para OpenAI como para LangGraph.

Yo instrumentaría:

```text
logical_run_id
execution_id
condition
provider/model
LLM request
LLM response
ToolCall
budget counters
fault events
verification result
```

pero el SHA del receipt no depende de que Langfuse haya recibido el span.

Otra precaución: Langfuse puede registrar prompts y model outputs completos. Por tanto, la redacción de secrets debe suceder antes del exporter, no confiar en que después alguien los borre.

## 5. LangGraph va todavía más afuera

Aquí cambiaría ligeramente el brief anterior.

No haría esto:

```text
LangGraph:
observe
→ decide
→ authorize
→ execute
→ verify
```

porque eso replica responsabilidades que ya posee el core.

Terminarías con dos runners:

```text
Core Runner
LangGraph Runner
```

y tarde o temprano divergirían.

Haría esto:

```text
LangGraph:
prepare attempt
→ build policy/context
→ invoke core run
→ inspect verified result
→ update search state
→ next attempt / stop
```

Es decir, LangGraph controla la búsqueda sobre runs, no los movimientos del robot.

Esto además es bastante más cercano al mecanismo de REFUGIO.

## 6. R1 cambia: human checkpoints entre attempts, no entre movimientos

Aquí creo que aparece una simplificación bastante potente.

No necesitas:

```text
human
↓
10 robot moves
↓
human
↓
10 robot moves
```

Eso obligaría a LangGraph a entrar dentro del core.

Para tu experimento sobre REFUGIO es incluso más limpio:

```text
human strategy
↓
full bounded run
↓
verified evidence
↓
human sees result
↓
changes strategy
↓
next full bounded run
```

Eso sí representa human-directed search.

Y preserva perfectamente el core.

LangGraph tiene interrupt() y checkpointing explícitamente pensado para suspender workflows y reanudarlos con human input.

Tu R1 podría ser:

```text
initial human strategy
        ↓
 build StrategyContext
        ↓
    core.run()
        ↓
verified result
        ↓
 LangGraph interrupt
        ↓
human:
  continue
  change strategy
  reject direction
        ↓
next attempt
```

El StrategyContext queda congelado durante cada run.

Eso mejora además reproducibilidad.

## 7. R2

R2 es el más fácil una vez existe LLMPolicy.

Primera versión:

```text
fixed strategy prompt
       ↓
LLMPolicy
       ↓
core.run()
```

Después puedes introducir search across attempts:

```text
attempt N evidence
       ↓
LLM reflects on result
       ↓
new StrategyContext
       ↓
attempt N+1
```

LangGraph coordina ese outer loop.

Pero cada individual attempt sigue siendo:

```text
immutable config
immutable provider/model
immutable discovery snapshot
↓
core.run()
```

La no-determinism queda completamente al lado izquierdo de la frontera.

## 8. R3: no empezaría con tres LLMs hablando

La mínima R3 defendible sería:

```text
         shared DiscoveryStore
               ↑        ↑
               │        │
        explorer A   explorer B
               \        /
                \      /
             coordinator
                  ↓
             next attempts
```

Pero haría al coordinator determinista al principio.

Es una distinción importante.

Si haces:

```text
R2 = 1 LLM

R3 =
LLM explorer A
+ LLM explorer B
+ LLM coordinator
```

y R3 gana, no sabes si se debe a:

```text
sharing de discoveries
```

o simplemente a:

```text
tres veces más inference/search capacity.
```

Tu primera R3 debería aislar el mecanismo:

```text
Explorer A = LLM
Explorer B = LLM

Coordinator =
deterministic scheduler
+
verified-score selection
```

Ambos reciben iguales budgets totales definidos a nivel experimental.

Sólo después convertiría coordinator en LLM.

### Discovery flow

Aquí se conserva exactamente ADR-017/018:

```text
run
↓
verification
↓
evidence-backed Discovery
↓
append DiscoveryStore
↓
freeze DiscoverySnapshot
↓
next explorer run
```

Nunca:

```text
Explorer A writes mutable memory
while Explorer B is running
```

Cada run recibe:

```text
discovery_snapshot_digest
```

y ese digest aparece en el receipt.

## 9. Qué papel tiene entonces LangGraph en R3

Precisamente scheduling.

Algo así:

```text
START
  ↓
freeze_frontier
  ↓
freeze_discovery_snapshot
  ↓
┌───────────────┬───────────────┐
│ propose A     │ propose B     │
└───────┬───────┴───────┬───────┘
        ↓               ↓
     core run A       core run B
        ↓               ↓
      verify           verify
        └──────┬────────┘
               ↓
       deterministic select
               ↓
       publish discoveries
               ↓
       update frontier
               ↓
          next round
```

LangGraph soporta subgraphs, persistence y stateful orchestration, pero eso no implica que su state deba convertirse en vuestro WorldState.

Son estados distintos:

```text
Core WorldState
≠
LangGraph ExperimentState
```

Muy importante.

## 10. Orden exacto en que lo construiría

Una vez v0 está realmente verde —replay, verifier, faults, receipts y ADR tests—:

| Orden | Añadido | Gate antes de avanzar |
|-------|---------|----------------------|
| 1 | ProviderAdapter contract | fake provider pasa tests sin tocar core |
| 2 | LLMPolicy | fake provider → ToolCall → run verificado |
| 3 | OpenAI adapter | un live R2 run completo + replay sin API |
| 4 | provider evidence | prompt/response/usage/model quedan registrados y secrets fuera |
| 5 | Langfuse observer | desactivarlo no cambia trace, score ni receipt |
| 6 | GLM adapter | mismo LLMPolicy, sin cambios en core |
| 7 | LangGraph outer graph | consigue ejecutar exactamente el mismo R2 run |
| 8 | R1 | human guidance sólo entre attempts |
| 9 | DiscoveryStore + frontier adapter | evidence-backed + frozen snapshots |
| 10 | R3 | 2 explorers + deterministic coordinator |
| 11 | optional LLM coordinator | sólo después de tener R2/R3 comparables |

Hay una regla de oro en esa tabla:

**Cada capa nueva debe poder eliminarse y dejar intacta la capa inferior.**

## Qué es realista en la misma semana

Si por "misma semana" hablamos de la semana en la que estás construyendo el núcleo staged, no considero creíble terminar además una phase 4 completa con:

```text
OpenAI
+ GLM
+ LangGraph
+ Langfuse
+ R1
+ R2
+ R3
```

y mantener el nivel de evidencia que exigen los ADRs.

La extensión que sí consideraría un stretch creíble si el core queda cerrado pronto es:

```text
OpenAI
   ↓
LLMPolicy
   ↓
existing Runner
   ↓
verified run
   ↓
recorded provider evidence
   ↓
replay WITHOUT OpenAI
```

Eso es extremadamente valioso porque demuestra que vuestra arquitectura realmente era LLM-compatible y no sólo lo decía.

Si queda margen, el siguiente añadido sería Langfuse, porque es side-effect-only y no cambia la semántica.

Después escogería una de estas dos cosas:

```text
GLM adapter
```

o

```text
minimal LangGraph R2 wrapper
```

No intentaría cerrar R1 y R3 esa misma semana a costa de debilitar verifier/replay.

## Mi stretch target

Yo pondría el corte así:

```text
Minimum stretch
OpenAI → LLMPolicy → unchanged core → replay

Strong stretch
+ Langfuse
+ GLM adapter

Excellent but unnecessary stretch
+ minimal LangGraph R2 outer loop

Next phase
R1
R3
frontier search across attempts
shared discoveries
```

## La arquitectura final resumida en una frase

OpenAI/GLM están debajo de LLMPolicy; LLMPolicy se enchufa al Policy contract del core; LangGraph está por encima del core coordinando attempts y condiciones R1/R2/R3; Langfuse está al lado observando todo, pero no controla nada; JSONL + receipt + verifier siguen siendo la evidencia autoritativa.

Y haría un cambio respecto al brief de phase 4: R1/R2/R3 deberían describir cómo se busca a través de varios bounded runs, no tres maneras distintas de ejecutar internamente el warehouse. Eso preserva completamente los ADRs y conecta bastante mejor con el mecanismo de REFUGIO.
