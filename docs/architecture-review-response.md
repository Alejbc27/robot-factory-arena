# Architecture Review — ChatGPT (Sol high) response to the v0 brief

> **Provenance**: verbatim response from the external architecture review session (ChatGPT Web, high-reasoning model), 2026-09-01. Input: `docs/architecture-review-brief.md`. This document is adopted as primary input for the SDD proposal/design phases. The three "top changes" are accepted: multi-seed evaluation + holdout before R1/R2/R3, sandbox claim downgraded, `assemble` removed from v0.

---

# Architecture Review — Robot Factory & Arena v0

## 1. World & task modeling

### Decision

Usar **snapshot canónico + operation/event log append-only**.

El `WorldState` es la fuente de verdad operacional durante la ejecución. Cada operación válida pasa por una única función de transición determinista:

`apply(state, operation) -> (new_state, domain_events)`

El log explica cómo se llegó hasta allí y permite replay, auditoría y fault injection.

No haría event sourcing puro.

El modelo debe contener sólo datos deterministas y serializables: enteros, strings, enums, tuples/lists ordenadas y mappings con orden canónico. Nada de timestamps, objetos Python con identidad, `set`, floats innecesarios o referencias mutables ocultas.

### Alternatives rejected

**Event sourcing puro.**

Lo rechazo porque eleva considerablemente la complejidad sin aportar valor suficiente en este proyecto. Cada lectura del estado dependería de reconstruir una secuencia completa de eventos y los bugs en reducers, eventos duplicados o versiones de schema afectarían directamente al runtime.

Además, bajo fault injection introduces inmediatamente preguntas difíciles:

* ¿se escribió el evento pero no el checkpoint?
* ¿se aplicó dos veces?
* ¿el reducer nuevo interpreta igual un evento antiguo?

Estarías probando tu infraestructura de event sourcing más que el comportamiento de los agentes.

**Sólo snapshots.**

Tampoco. Permitiría saber dónde terminaste, pero no explicar cómo llegaste allí, distinguir retries de efectos duplicados ni reconstruir un run.

**Mutable world object compartido entre runner, tools y policy.**

Debe prohibirse. Es la forma más fácil de romper aislamiento y permitir state corruption accidental o deliberada.

### Risks / failure modes

El riesgo principal es que snapshot y log diverjan.

Si una operación modifica el estado pero el evento no se registra —o viceversa— el replay puede producir un resultado distinto del runtime original.

Otro riesgo es introducir nondeterminismo accidental mediante:

* iteración sobre `set`;
* dependencia del orden del filesystem;
* uso del módulo global `random`;
* floats;
* timestamps;
* UUID aleatorio usado como semántica;
* Python object identity;
* mutaciones realizadas fuera del transition layer.

También hay peligro de que el "operation log" termine conteniendo tanto lógica como resultados derivados y aparezcan dos fuentes de verdad.

### Concrete shape

```text
WorldState
  schema_version
  width
  height
  robot
  shelves
  orders
  completed_orders
  step

Operation
  kind
  arguments

TransitionResult
  state
  events
```

Por ejemplo:

```python
@dataclass(frozen=True)
class WorldState:
    schema_version: int
    width: int
    height: int
    robot: RobotState
    shelves: tuple[ShelfState, ...]
    orders: tuple[OrderState, ...]
    completed_orders: tuple[str, ...]
    step: int
```

El runner conserva:

```text
initial_snapshot
operation log
final_snapshot
```

Y cada transición registra:

```text
state_digest_before
operation
domain_events
state_digest_after
```

El `operation_key` ya existente en vuestro prior art debe identificar la intención idempotente, no la posición de la acción en el log.

---

## 2. Agent loop architecture

### Decision

Mantener la policy como una **función pura de decisión** y hacer que **runner + tool bay posean toda la autoridad**.

La policy:

```text
serialized observation
        ↓
policy
        ↓
ToolCall
```

Nunca recibe `WorldState`, filesystem, ledger, verifier o objetos mutables internos.

El runner controla:

* número de steps;
* action budget;
* grants;
* lifecycle;
* fault injection;
* termination;
* observaciones;
* logging.

El tool bay valida capacidades y traduce un `ToolCall` autorizado a una operación de dominio.

El budget debe cobrarse **antes de ejecutar una acción solicitada por la policy**.

Una llamada inválida o no autorizada también consume el step. De lo contrario el agente obtiene intentos infinitos gratis para explorar el sistema.

### Alternatives rejected

**Budget dentro de cada tool.**

Rechazado porque distribuye la autoridad. Dos tools pueden aplicar reglas diferentes y resulta fácil añadir una tool futura que accidentalmente no cobre budget.

**Budget controlado por la policy.**

No es una frontera de seguridad.

**Runner llamando directamente a métodos del world.**

Acoplaría orchestration y dominio y haría más difícil insertar permissions, fault injection e idempotency.

**Múltiples tool calls en un mismo policy step en v0.**

Lo evitaría. Complica budget, ordering, replay, partial failure y fault classification sin darte nada esencial para un robot.

### Risks / failure modes

La ambigüedad más peligrosa ahora mismo es qué significa exactamente **"100 actions"**. El brief lo usa como presupuesto principal.

Debéis distinguir:

* policy decision;
* policy action attempt;
* internal transport retry;
* committed world effect.

No son equivalentes.

Si un retry provocado por `response_lost_after_commit` vuelve a consumir una acción de policy, estarás midiendo el fault harness, no la policy.

Si no cobras acciones inválidas, un agente puede abusar del contrato gratuitamente.

### Concrete shape

Un loop razonable:

```python
while not terminal(state):
    budget.require_step_available()

    observation = observe(state)
    call = policy.decide(observation)

    budget.consume_policy_step()

    validation = tool_bay.validate(call, grants)

    if not validation.ok:
        trace_violation(...)
        continue

    result = executor.execute_idempotently(call)
    state = result.state
```

Contadores separados:

```text
policy_steps
tool_attempts
committed_effects
internal_retries
contract_violations
```

El hard kill de v0 debería depender de `policy_steps <= 100`.

Los retries internos no incrementan `policy_steps`.

Eso conserva la semántica del experimento y permite medir eficiencia de forma honesta.

---

## 3. Verifier design

### Decision

No llamarlo "cheat-proof".

Diseñar un **independent verifier adversarial y cheat-resistant dentro de un threat model explícito**.

Debe ejecutarse después del run y no importar:

* policy;
* runner;
* tool bay;
* scoring del runtime.

Recibe exclusivamente artifacts:

```text
task specification
seed
run config
trace
final snapshot
receipt
```

y reconstruye o valida independientemente el resultado.

El score oficial debe provenir **sólo del verifier**.

### Alternatives rejected

**Reutilizar `world.score()` del runtime.**

Es cómodo y arquitectónicamente incorrecto. Si el runtime tiene un bug que concede una entrega inválida, el verifier repetiría exactamente el mismo bug.

**Verificar sólo el final state.**

No detecta teleportation, acciones no autorizadas, double-effects ni alteraciones intermedias.

**Verificar sólo el trace.**

Un trace puede afirmar cosas que nunca ocurrieron.

**Ocultar la lógica del verifier para evitar exploits.**

No. El sistema debe ser correcto aunque el agente conozca completamente el verifier. Security-by-obscurity convertiría el benchmark en un secreto, no en un verifier robusto.

### Risks / failure modes

Las clases de reward hacking mínimas que debe cubrir son:

1. **Self-reported success**
   La policy afirma haber completado una orden.

2. **Duplicate reward**
   Una misma entrega se contabiliza más de una vez.

3. **Illegal state transition**
   Teleport, pick remoto, atravesar paredes, depositar un item inexistente.

4. **State corruption**
   Modificación directa de world structures.

5. **Trace forgery**
   El artifact declara acciones distintas de las ejecutadas.

6. **Budget bypass**
   Se producen más operaciones de las permitidas.

7. **Unauthorized capabilities**
   Efecto producido por una tool para la que no existía grant.

8. **Idempotency failure**
   Dos ejecuciones del mismo `operation_key` producen dos efectos.

9. **Fault exploitation**
   Un `response_lost_after_commit` genera una segunda entrega al retry.

10. **Seed/config substitution**
    El score corresponde a otro seed o configuración.

11. **Partial-credit gaming**
    Construir estados formalmente incompletos que el scorer interpreta accidentalmente como éxito.

12. **Artifact substitution**
    Combinar trace de un run con receipt/final state de otro.

La mayor amenaza arquitectónica es que el verifier acabe compartiendo demasiada lógica con el simulator.

### Concrete shape

Separaría:

```text
verification/
    verifier.py
    invariants.py
    scoring.py
```

El verifier debería reconstruir un modelo mínimo propio desde:

```text
seed
+
verified committed operations
```

y comprobar:

```text
expected_final_state == submitted_final_state
expected_score == claimed_score
step_count <= budget
all operations legal
all grants respected
all order IDs unique
all completed deliveries satisfy invariants
```

Salida:

```python
VerificationResult(
    valid=True,
    task_score=7,
    violations=(),
    reconstructed_state_digest="...",
)
```

El receipt jamás contiene un score "trusted"; contiene un score **verified**.

---

## 4. Trace, receipt & replay

### Decision

Separar tres conceptos que ahora mismo están demasiado cerca:

1. **Trace** — qué ocurrió.
2. **Receipt** — qué run fue y cuál fue su resultado verificado.
3. **Replay** — reconstruir el mundo sin volver a ejecutar la policy.

Y añadir una cuarta operación distinta:

1. **Rerun** — ejecutar otra vez seed + policy para probar determinismo.

Replay y rerun **no son lo mismo**.

El trace será JSONL append-only y el receipt un JSON canónico.

Sí usaría SHA-256 chaining porque cuesta muy poco con stdlib, pero sólo lo presentaría como **tamper evidence**, no como seguridad criptográfica contra alguien capaz de reescribir todos los artifacts.

### Alternatives rejected

**Replay llamando otra vez a la policy.**

No es replay; es re-ejecución.

En fases LLM sería directamente incorrecto porque una nueva inferencia no tiene por qué producir exactamente la misma salida. El propio brief ya reconoce esto para phase 4.

**Un único JSON gigantesco.**

Peor para streaming, inspección, fault recovery y golden comparisons.

**Hash chain como mecanismo de autenticidad.**

No lo es. Si un atacante puede modificar trace y receipt puede recalcular la cadena.

### Risks / failure modes

El peligro principal es meter campos nondeterministas dentro del digest reproducible:

* wall-clock timestamp;
* filesystem paths;
* process IDs;
* execution duration;
* host name.

Eso hará que dos runs idénticos produzcan artifacts diferentes.

Otro riesgo es guardar únicamente hashes de observaciones. Eso permite comprobar igualdad, pero imposibilita inspeccionar o reproducir qué vio realmente la policy.

Dado vuestro tamaño, guardar las observaciones completas es barato.

### Concrete shape

Trace JSONL:

```json
{
  "seq": 17,
  "step": 6,
  "event": "tool_committed",
  "operation_key": "run42-step6",
  "tool": "move",
  "arguments": {"direction": "north"},
  "state_before": "sha256:...",
  "state_after": "sha256:...",
  "prev_event_hash": "sha256:...",
  "event_hash": "sha256:..."
}
```

Eventos importantes:

```text
run_started
observation_emitted
policy_decision
tool_rejected
tool_committed
fault_injected
retry
budget_exhausted
run_finished
verification_finished
```

Receipt:

```json
{
  "schema_version": 1,
  "logical_run_id": "...",
  "execution_id": "...",
  "task_version": "...",
  "seed": "...",
  "policy_id": "...",
  "policy_digest": "...",
  "budget": 100,
  "grants": ["move", "pick", "place"],
  "fault_tape_digest": "...",
  "trace_digest": "...",
  "final_state_digest": "...",
  "verifier_version": "...",
  "verified_score": 7,
  "outcome": "completed"
}
```

Conviene distinguir:

```text
logical_run_id
```

determinista a partir de config,

de

```text
execution_id
```

único para cada ejecución física.

Golden test:

```text
rerun(seed, policy) → trace_digest identical
```

Replay test:

```text
replay(seed, trace) → final_state_digest + score identical
```

Son dos garantías distintas.

---

## 5. Package layout

### Decision

Diseñar el paquete alrededor de **pure domain + runtime adapters**, no alrededor de Factory/R1/R2/R3.

El dominio del warehouse no debe saber que existe:

* una Factory;
* un leaderboard;
* R1/R2/R3;
* LLMs;
* fault tapes;
* reports.

Esas son capas superiores.

### Alternatives rejected

**Un módulo por fase del roadmap.**

Acoplaría arquitectura y planificación temporal:

```text
phase1/
phase2/
phase3/
```

No.

**`factory.py` y `arena.py` gigantes.**

Crearía dos god modules.

**Plugin system desde v0.**

YAGNI. Protocols simples y constructors explícitos bastan.

**Framework abstractions para futuros providers.**

No antes de tener un provider real.

### Risks / failure modes

El error más probable es que `runner.py` se convierta en el lugar donde se implementa todo:

* budget;
* faults;
* policy calls;
* permissions;
* world mutation;
* scoring;
* tracing.

Eso convertiría R1/R2/R3 en ramas `if condition == ...`.

Segundo peligro: poner el verifier dentro de `core`, haciendo inevitable la reutilización de lógica que debería ser independiente.

### Concrete shape

```text
robot_factory_arena/
│
├── domain/
│   ├── model.py
│   ├── task.py
│   ├── observation.py
│   └── transition.py
│
├── contracts/
│   ├── tool_call.py
│   ├── policy.py
│   └── outcomes.py
│
├── runtime/
│   ├── runner.py
│   ├── tool_bay.py
│   ├── grants.py
│   ├── budget.py
│   └── ledger.py
│
├── policies/
│   ├── greedy.py
│   └── bounded.py
│
├── faults/
│   ├── model.py
│   ├── tape.py
│   └── injector.py
│
├── evidence/
│   ├── trace.py
│   ├── receipt.py
│   ├── replay.py
│   └── canonical.py
│
├── verification/
│   ├── verifier.py
│   ├── invariants.py
│   └── scoring.py
│
└── cli.py
```

Más adelante:

```text
arena/
    frontier.py
    discoveries.py
    conditions.py

providers/
    ...
```

R1/R2/R3 deben ser **policies/adapters configurados externamente**, nunca variantes del world.

---

## 6. Fault injection mechanics

### Decision

Una fault tape debe ser un **artifact declarativo completamente resuelto antes del run**.

No utilizar RNG durante ejecución.

Si queréis generar la cinta a partir de un seed, haced:

```text
fault_seed → generate tape → persist tape → execute tape
```

El runtime consume después una lista fija.

Los fault points deben corresponder a lifecycle boundaries semánticos:

```text
before_commit
after_commit_before_response
after_commit_before_checkpoint
before_dispatch
```

No a "después de 17 milisegundos".

### Alternatives rejected

**Fault scheduling temporal.**

Nondeterminista y dependiente de hardware.

**RNG consultado durante el run.**

Añade una segunda fuente de evolución del estado que puede desincronizar replay.

**Sólo indexar por tool name.**

Demasiado ambiguo si aparecen múltiples llamadas iguales.

**Hacer que el fault injector escriba arbitrariamente el WorldState.**

Entonces los bugs que encuentres pueden pertenecer al injector, no a la aplicación.

### Risks / failure modes

El fault tape puede acabar convirtiéndose en un lenguaje paralelo de mutaciones del mundo.

Especialmente sospechoso es:

`duplicate_delivery`

Si significa literalmente "el fault framework incrementa el número de deliveries", no estáis probando idempotency: estáis fabricando una corrupción.

El fault debería provocar una condición que **pueda dar lugar** a duplicate effect si el sistema está mal diseñado; no insertar directamente el reward duplicado.

Ejemplo correcto:

```text
operation committed
→ response lost
→ policy retries same operation_key
→ ledger decides whether effect repeats
```

Otro problema: `unresolved_outcome`.

En un mundo completamente local y observable quizá siempre podáis inspeccionar si el commit ocurrió. Si artificialmente ocultáis esa información, debéis documentar cuál es la frontera de conocimiento simulada.

### Concrete shape

```python
@dataclass(frozen=True)
class Fault:
    fault_id: str
    operation_key: str
    hook: FaultHook
    kind: FaultKind
```

Ejemplo:

```json
{
  "fault_id": "f03",
  "operation_key": "run12-step8",
  "hook": "after_commit_before_response",
  "kind": "response_lost"
}
```

Semántica de outcomes:

**completed**
El efecto requerido está demostrado y el run termina válidamente.

**duplicate_effect**
El verifier demuestra que una misma intención produjo más de un efecto.

**safe_abort**
El sistema puede demostrar que no existe efecto incierto pendiente.

**unresolved_outcome**
Con la evidencia disponible no se puede demostrar si el efecto ocurrió.

Para `process_restart_between_commit_and_checkpoint`:

```text
persist ledger/effect
↓
inject restart
↓
instantiate fresh runner
↓
restore from persisted artifacts
↓
continue
```

No "simular restart" limpiando dos variables manualmente.

---

## 7. Biggest risks

### Decision

El proyecto es viable, pero hoy tiene **tres claims más fuertes que la infraestructura prevista puede defender**:

1. "sandbox";
2. "independent/anti-cheat verifier";
3. un experimento significativo con un único evaluation seed.

Debéis reducir o corregir esos tres antes de presentarlo como portfolio evidence.

### Alternatives rejected

**Asumir que porque todo es local es seguro.**

No.

**Asumir que determinismo == corrección.**

Un bug perfectamente determinista sigue siendo un bug.

**Asumir que replay == verificación independiente.**

Tampoco. Puedes reproducir exactamente una ejecución incorrecta.

**Depender exclusivamente de golden traces.**

Los golden tests detectan cambios, pero también congelan bugs conocidos.

Necesitáis invariants y tests pequeños además de golden traces.

### Risks / failure modes

#### Riesgo 1 — el "sandbox"

Python stdlib no proporciona un sandbox seguro para ejecutar Python hostil dentro del mismo intérprete.

Si la policy es código Python arbitrario y está en el mismo proceso, cualquier claim serio de aislamiento es falso.

Puede intentar:

* importar;
* inspeccionar objetos;
* acceder al filesystem;
* monkey patching;
* introspection;
* agotar memoria;
* manipular globals.

Un subprocess con límites mejora fault containment, pero tampoco equivale automáticamente a Firecracker.

En v0 vuestro threat model debería ser:

> **la policy no es confiable semánticamente, pero su código no se considera malware hostil.**

Eso sí es defendible.

#### Riesgo 2 — un evaluation seed

El brief propone un public seed + un evaluation seed.

Eso sirve para probar plumbing.

No sirve para sostener un experimento serio una vez que se permiten múltiples submissions.

Aunque el seed nunca se publique, el leaderboard convierte su score en un **oracle adaptativo**:

```text
submit
→ observe score
→ modify policy
→ submit
→ observe score
```

Con suficiente iteración acabarás haciendo tuning indirecto al mismo instance.

Es una versión menos obvia del problema que REFUGIO sufrió con sus seeds.

#### Riesgo 3 — verifier no realmente independiente

Si comparte transitions o scoring con el runtime, será un segundo caller del mismo bug.

#### Riesgo 4 — falsos claims de determinismo

`PYTHONHASHSEED`, unordered structures, wall clock, RNG global y filesystem ordering pueden romperlo fácilmente.

#### Riesgo 5 — R1/R2/R3 no comparables

R3 recibe información adicional.

Eso es intencionado, pero tenéis que medirla:

```text
number of discoveries
bytes/tokens shared
frontier version available
```

Si no, "shared memory wins" puede significar simplemente "una condición recibió más información".

#### Riesgo 6 — cuatro scores 0–100

Normalizar task quality, efficiency, robustness y hygiene todos a 0–100 produce una apariencia de comparabilidad que quizá no exista.

No los colapsáis, lo cual es correcto, pero seguís perdiendo significado.

"Efficiency 83" dice mucho menos que:

```text
73 policy steps
8.1 actions/delivery
```

#### Riesgo 7 — fault injection se come el proyecto

Con una semana de sprint, podéis acabar construyendo un distributed-systems simulator para un robot de 10×10.

#### Riesgo 8 — `assemble`

`possibly assemble` es una frase peligrosa.

Añade:

* multi-item dependency;
* intermediate state;
* atomicity;
* partial assembly;
* más invariants;
* más verifier surface.

Y no aporta nada esencial a la hipótesis REFUGIO.

### Concrete shape

Para v0 fijaría este threat model:

```text
Trusted:
- task generator
- runner
- verifier executable
- evidence writer

Protocol-untrusted:
- policy outputs
- tool requests

Not supported in v0:
- hostile arbitrary Python code isolation
```

Y estas reglas de determinismo:

```text
no global random
no floating-point scoring
no set iteration in semantics
canonical JSON
sorted collections
explicit schema versions
no wall-clock values in deterministic hashes
```

Además:

```text
golden replay tests
+
unit tests for domain transitions
+
verifier adversarial tests
```

No usaría golden traces como única estrategia de testing.

---

## 8. Discovery-sharing & frontier mechanics

### Decision

Una **discovery no debe formar parte del core**.

Debe ser un artifact de la Arena que referencia evidence producido por uno o más runs.

El runner continúa ejecutando exactamente:

```text
policy(observation) → ToolCall
```

Para R3, un wrapper construye la policy con una **snapshot inmutable de shared discoveries antes de comenzar el run**.

La memoria nunca cambia durante el run.

Eso evita race conditions y hace reproducible la condición R3.

El frontier también pertenece a Arena, después del verifier:

```text
run
→ verify
→ score
→ compare with frontier
→ possibly advance
→ publish artifacts/discovery
```

### Alternatives rejected

**Shared mutable global memory accesible durante el run.**

Rompe determinismo inmediatamente si hay más de un worker.

**Meter discoveries dentro de `WorldState`.**

Una discovery no pertenece al warehouse.

**Dejar que cualquier texto generado se convierta automáticamente en discovery.**

Se convertiría en memoria basura, hallucinations y claims sin evidencia.

**Compartir únicamente el winning policy completo.**

Provocaría convergencia prematura. Los siguientes searchers copiarían el mismo local optimum.

**Hacer que el core conozca R1/R2/R3.**

Violación directa de vuestro hard constraint de no reescribir core.

### Risks / failure modes

El principal riesgo es que "shared discoveries" termine siendo una variable experimental mal definida.

¿Qué recibe exactamente R3?

* ¿texto?
* ¿código?
* ¿traces?
* ¿parámetros?
* ¿mejor policy?
* ¿todos los runs?

Cada opción estudia un mecanismo distinto.

Segundo riesgo: information leakage desde evaluation seeds.

Si una discovery puede decir:

> "en evaluation seed, primero ve a coordenada 4,7"

has reconstruido el seed leak de REFUGIO de forma indirecta.

Una discovery debe describir **estrategias generalizables**, no datos de instancia de evaluation.

Tercer riesgo: frontier multidimensional.

El proyecto tiene cuatro señales, pero el frontier necesita **una única métrica primaria ordenable**.

No intentaría hacer Pareto frontier en v0.

Usaría únicamente:

```text
verified task quality
```

Las demás métricas siguen reportándose, pero no determinan si se bate el record.

Cuarto riesgo: concurrent frontier updates.

Si dos runs parten del mismo frontier y terminan casi simultáneamente, necesitáis reglas deterministas de aceptación.

En v0 podéis serializar commits al leaderboard y evitar complejidad distribuida.

### Concrete shape

```python
@dataclass(frozen=True)
class Discovery:
    schema_version: int
    discovery_id: str
    source_run_id: str
    category: str
    statement: str
    evidence_refs: tuple[str, ...]
    source_trace_digest: str
```

No añadiría veinte campos todavía.

Ejemplo:

```json
{
  "discovery_id": "sha256:...",
  "source_run_id": "run-017",
  "category": "routing",
  "statement": "Prefer completing the nearest currently reachable order before switching targets.",
  "evidence_refs": [
    "trace.jsonl#events=31-57"
  ],
  "source_trace_digest": "sha256:..."
}
```

R3:

```text
DiscoveryStore
      ↓
freeze snapshot
      ↓
SharedMemoryPolicy(base_policy, snapshot)
      ↓
normal runner
```

El receipt registra:

```text
discovery_snapshot_digest
discovery_ids
```

Por tanto una ejecución R3 siempre puede reconstruir exactamente qué conocimiento recibió.

Frontier:

```python
@dataclass(frozen=True)
class FrontierEntry:
    score: int
    run_id: str
    receipt_digest: str
    previous_entry: str | None
```

Regla:

```python
if verification.valid and score > current_frontier.score:
    advance_frontier()
else:
    retain_frontier()
```

Tie = no advance.

No composite score.

No update antes de verificar.

No frontier basado en self-report.

El brief acierta al poner discovery sharing y moving frontier como mecanismo separado del warehouse.

---

# ADR-style decisions

**ADR-001 — World representation**
Adoptar immutable/canonical `WorldState` + append-only operation/event log; rechazar event sourcing puro.

**ADR-002 — Single transition authority**
Toda modificación del warehouse pasa por una función determinista de transición; ninguna policy/tool modifica world state directamente.

**ADR-003 — Pure policy boundary**
La policy consume una observación serializada y produce exactamente una solicitud de acción por policy step.

**ADR-004 — Runner owns budgets**
El runner posee y aplica todos los hard budgets antes del dispatch; acciones inválidas/no autorizadas consumen policy step.

**ADR-005 — Retry accounting**
Distinguir policy steps, tool attempts, committed effects e internal retries. Los retries internos no consumen policy budget adicional.

**ADR-006 — Verifier authority**
Sólo el verifier independiente puede producir el score oficial.

**ADR-007 — Independent verification implementation**
El verifier no importa policy, runner, tool bay ni scoring del runtime y valida/reconstruye desde artifacts.

**ADR-008 — Evidence split**
Trace, receipt, replay y rerun son conceptos y comandos distintos.

**ADR-009 — Canonical evidence**
JSON/JSONL canónico con schema versions y SHA-256 digests. Hash chaining se usa sólo como tamper evidence, nunca como autenticación.

**ADR-010 — Replay semantics**
Replay consume operaciones registradas; no vuelve a ejecutar la policy.

**ADR-011 — Domain isolation**
Warehouse domain no conoce Factory, Arena, frontier, R1/R2/R3, LLM providers ni fault injection.

**ADR-012 — Declarative fault tapes**
Los fault tapes quedan completamente resueltos y persistidos antes del run; no hay RNG de fault scheduling durante ejecución.

**ADR-013 — Semantic fault hooks**
Fault injection ocurre en lifecycle hooks de commit/response/checkpoint, nunca según tiempo físico.

**ADR-014 — Explicit threat model**
v0 protege frente a outputs/policies semánticamente incorrectos, no pretende ejecutar Python hostil con garantías de security sandbox.

**ADR-015 — Frontier metric**
El frontier se ordena exclusivamente por verified task quality. Las demás señales permanecen separadas.

**ADR-016 — Verified frontier updates**
Una run sólo puede avanzar el frontier después de verification exitosa y sólo si `score > historical_best`.

**ADR-017 — Discovery as Arena artifact**
Una discovery es un artifact evidence-backed fuera del core.

**ADR-018 — Frozen discovery snapshot**
R3 recibe una snapshot inmutable de discoveries al inicio de cada run; esa snapshot queda identificada en el receipt.

**ADR-019 — No evaluation-instance discoveries**
Shared memory no puede incluir datos específicos de evaluation seeds.

**ADR-020 — Test strategy**
Golden traces se complementan obligatoriamente con unit tests de transitions/invariants y adversarial verifier tests.
